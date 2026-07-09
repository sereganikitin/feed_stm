"""Админ-панель для коллег: управление фото и настройками 4 фидов.

Доступ — один общий пароль (env ADMIN_PASSWORD), сессия в cookie.
Всё под тем же nginx/TLS, отдельный префикс /admin.

Возможности:
  • Дашборд: статус ЦИАН/Авито фидов обоих проектов, последний refresh.
  • Фото карточки Авито по каждому фиду: загрузка файлов, удаление, порядок,
    кнопка синхронизации с Яндекс.Диска. Фото у каждого фида свои (per-feed).
  • Настройки: отделка по умолчанию, материал дома, тип рынка, замена building_image,
    приписка к описанию, формула рассрочки (где есть).
  • Ручной refresh фида, превью первой карточки, проверка обязательных полей Авито.

Редактируемые настройки складываются в overrides.json (volume) и перекрывают код.
"""
import hmac
import os
import time
import xml.etree.ElementTree as ET
from functools import wraps
from pathlib import Path

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template_string,
                   request, session, url_for)
from werkzeug.utils import secure_filename

from .config import (PROJECTS, PUBLIC_BASE_URL, ADMIN_DIR, project_dirs,
                     get_project, set_override, load_overrides,
                     excluded_photos, add_excluded_photo)
from .yadisk import save_resized_jpeg, sync_public_folder
from .assembler_avito import enrich_pb_avito_feed
from .assembler_yandex import assemble_yandex_feed, coords_from_avito
from .assembler_yandex_realty import assemble_yandex_realty_feed
from .assembler import assemble_feed
from .enricher import enrich_lot
from .parser import parse_feed
from . import commercial as comm

# Наборы фото карточки: Авито, Яндекс, ЦИАН — общий код, разные каталоги/настройки/фид.
# mirror=True (ЦИАН) — синк зеркалит ЯД (удаления подхватываются).
_KINDS = {
    "avito":  {"dir": "extra",        "order_key": "extra_photo_order",
               "cfg_key": "avito_extra_photos",  "url": "extra",        "title": "Авито", "mirror": False},
    "yandex": {"dir": "extra_yandex", "order_key": "extra_photo_order_yandex",
               "cfg_key": "yandex_extra_photos", "url": "extra_yandex", "title": "Яндекс.Недвижимость", "mirror": False},
    "cian":   {"dir": "extra_cian",   "order_key": "extra_photo_order_cian",
               "cfg_key": "cian_extra_photos",   "url": "extra_cian",   "title": "ЦИАН", "mirror": True},
}

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# Обязательные поля Авито для проверки карточки (категория Квартиры)
_REQUIRED = ["Id", "Category", "OperationType", "Price", "Rooms", "Square",
             "Floor", "Floors", "Decoration", "Images"]

# Подсказки значений
_DECOR = ["Без отделки", "Черновая", "Чистовая", "Предчистовая (White box)"]
_HOUSE = ["Монолитный", "Монолитно-кирпичный", "Кирпичный", "Панельный", "Блочный"]


# ──────────────── авторизация ────────────────

def _check_password(pw: str) -> bool:
    if not _ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(pw, _ADMIN_PASSWORD)


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("auth"):
            return redirect(url_for("admin.login", next=request.path))
        return f(*a, **kw)
    return wrap


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if _check_password(request.form.get("password", "")):
            session["auth"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("admin.dashboard"))
        flash("Неверный пароль")
    return render_template_string(_LOGIN_HTML)


@admin_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


# ──────────────── вспомогательное ────────────────

def _status() -> dict:
    try:
        import json
        return json.loads((ADMIN_DIR / "status.json").read_text("utf-8"))
    except Exception:
        return {}


def _count(path: Path, tag: str) -> int:
    # сравниваем по локальному имени тега — Яндекс-фид в namespace (offer => {...}offer)
    try:
        return sum(1 for e in ET.parse(path).getroot().iter()
                   if e.tag.split("}")[-1] == tag)
    except Exception:
        return 0


def _rebuild_avito(slug: str) -> None:
    """Быстро пересобрать Авито-фид после правок фото — без перекачки из ProfitBase.

    Берём сохранённый original_avito.xml и заново прогоняем подмену картинок.
    Если исходника нет (или вариант B) — полноценный refresh.
    """
    d = project_dirs(slug)
    src = d["feeds"] / "original_avito.xml"
    if PROJECTS[slug].get("pb_avito_feed_url") and src.exists():
        enrich_pb_avito_feed(slug, src.read_bytes(), d["feeds"] / "avito.xml")
    else:
        from .server import refresh_project
        refresh_project(slug)


def _rebuild_yandex(slug: str) -> None:
    """Пересобрать Яндекс-фид после правок фото — из кэшированных исходников."""
    d = project_dirs(slug)
    cian = d["feeds"] / "original.xml"
    if not cian.exists():
        from .server import refresh_project
        refresh_project(slug)
        return
    lots = parse_feed(cian.read_bytes())
    av = d["feeds"] / "original_avito.xml"
    coords = coords_from_avito(av.read_bytes()) if av.exists() else {}
    now = time.strftime("%Y-%m-%dT%H:%M:%S+03:00")
    assemble_yandex_feed(slug, lots, coords, d["feeds"] / "yandex.xml", now)
    assemble_yandex_realty_feed(slug, lots, coords, d["feeds"] / "yandex_realty.xml", now)


def _rebuild_cian(slug: str) -> None:
    """Пересобрать ЦИАН-фид после правок фото — из кэшированного исходника."""
    d = project_dirs(slug)
    cian = d["feeds"] / "original.xml"
    if not cian.exists():
        from .server import refresh_project
        refresh_project(slug)
        return
    raw = cian.read_bytes()
    assemble_feed(slug, raw, parse_feed(raw), d["feeds"] / "feed.xml")


def _rebuild(slug: str, kind: str) -> None:
    if kind == "yandex":
        _rebuild_yandex(slug)
    elif kind == "cian":
        _rebuild_cian(slug)
    else:
        _rebuild_avito(slug)


def _rebuild_all_feeds(slug: str) -> None:
    """Пересобрать ЦИАН/Авито/Яндекс из кэша (после ручной правки видов)."""
    d = project_dirs(slug)
    cian = d["feeds"] / "original.xml"
    if not cian.exists():
        return
    raw = cian.read_bytes()
    assemble_feed(slug, raw, parse_feed(raw), d["feeds"] / "feed.xml")
    _rebuild_avito(slug)
    _rebuild_yandex(slug)


def _regenerate_plans(slug: str) -> int:
    """Перерисовать все обогащённые планировки (после смены рассрочки/шаблона).
    Из кэша: берём сохранённый CIAN-фид, чистим PNG, рисуем заново, пересобираем 3 фида.
    Без перекачки фидов/фото — быстро и без риска 429. Возвращает число перерисованных."""
    d = project_dirs(slug)
    cian = d["feeds"] / "original.xml"
    if not cian.exists():
        from .server import refresh_project
        refresh_project(slug)
        return len(list(d["enriched"].glob("*.png")))
    raw = cian.read_bytes()
    lots = parse_feed(raw)
    for png in d["enriched"].glob("*.png"):
        png.unlink()
    ok = 0
    for lot in lots:
        if lot.plan_url and lot.price and lot.area_total:
            try:
                enrich_lot(slug, lot)
                ok += 1
            except Exception as e:
                print(f"[{slug}] regen error {lot.internal_id}: {e}")
    assemble_feed(slug, raw, lots, d["feeds"] / "feed.xml")
    _rebuild_avito(slug)
    _rebuild_yandex(slug)
    return ok


def _photos(slug: str, kind: str = "avito") -> list[str]:
    """Имена фото набора в порядке из настроек (новые — в конец)."""
    k = _KINDS[kind]
    files = {p.name for p in project_dirs(slug)[k["dir"]].glob("*.jpg")} - excluded_photos(slug, kind)
    order = [n for n in (get_project(slug).get(k["order_key"]) or []) if n in files]
    return order + sorted(files - set(order))


def _views_count(slug: str) -> int:
    """Сколько лотов имеют виды (подпапок в cache/<slug>/views)."""
    vdir = project_dirs(slug)["views"]
    return sum(1 for p in vdir.glob("*") if p.is_dir() and any(p.glob("*.jpg"))) if vdir.exists() else 0


def _views_coverage(slug: str) -> list:
    """По каждому лоту: id, метка, число видов (0 = нет). Сортировка: сначала без видов."""
    d = project_dirs(slug)
    cian = d["feeds"] / "original.xml"
    if not cian.exists():
        return []
    vdir = d["views"]
    rows = []
    for l in parse_feed(cian.read_bytes()):
        vd = vdir / l.internal_id
        files = sorted(p.name for p in vd.glob("*.jpg")) if vd.exists() else []
        lbl = "Ст." if l.rooms == 0 else ("СП" if l.rooms < 0 else f"{l.rooms}К")
        rows.append({"id": l.internal_id, "house": l.house_name, "floor": l.floor,
                     "label": lbl, "area": f"{l.area_total:.1f}", "n": len(files), "files": files})
    rows.sort(key=lambda r: (r["n"] > 0, r["house"], r["id"]))   # без видов — наверх
    return rows


def _avito_check(slug: str) -> dict:
    """Сводка по собранному Авито-фиду: число объявлений и пропуски обяз. полей."""
    p = project_dirs(slug)["feeds"] / "avito.xml"
    out = {"ads": 0, "issues": {}, "first": None}
    try:
        root = ET.parse(p).getroot()
    except Exception:
        return out
    ads = list(root.iter("Ad"))
    out["ads"] = len(ads)
    for ad in ads:
        for tag in _REQUIRED:
            el = ad.find(tag)
            empty = el is None or (tag != "Images" and not (el.text or "").strip()) \
                    or (tag == "Images" and len(el) == 0)
            if empty:
                out["issues"][tag] = out["issues"].get(tag, 0) + 1
    if ads:
        a = ads[0]
        imgs = [im.get("url") for im in (a.find("Images") or [])]
        out["plan"] = next((u for u in imgs if "/enriched/" in u), None)
        out["first"] = {
            "id": a.findtext("Id"), "rooms": a.findtext("Rooms"),
            "square": a.findtext("Square"), "price": a.findtext("Price"),
            "decoration": a.findtext("Decoration"), "images": imgs,
        }
    return out


# ──────────────── страницы ────────────────

@admin_bp.route("/")
@login_required
def dashboard():
    rows = []
    st = _status()
    for slug, p in PROJECTS.items():
        d = project_dirs(slug)
        rows.append({
            "slug": slug, "name": p["name"],
            "cian": _count(d["feeds"] / "feed.xml", "object"),
            "avito": _count(d["feeds"] / "avito.xml", "Ad"),
            "yandex": _count(d["feeds"] / "yandex.xml", "offer"),
            "yandex_realty": _count(d["feeds"] / "yandex_realty.xml", "offer"),
            "domclick": _count(d["feeds"] / "domclick.xml", "flat"),
            "photos": len(_photos(slug, "avito")),
            "photos_y": len(_photos(slug, "yandex")),
            "photos_c": len(_photos(slug, "cian")),
            "views": _views_count(slug),
            "status": st.get(slug, {}),
        })
    return render_template_string(_DASH_HTML, rows=rows, base=PUBLIC_BASE_URL)


@admin_bp.route("/<slug>")
@login_required
def project(slug: str):
    if slug not in PROJECTS:
        abort(404)
    proj = get_project(slug)
    check = _avito_check(slug)
    galleries = []
    for kk, v in _KINDS.items():
        has_yd = bool((PROJECTS[slug].get(v["cfg_key"]) or {}).get("yadisk_public_key"))
        if kk == "cian" and not has_yd:
            continue  # ЦИАН-набор показываем только если задана папка ЯД
        feed = f"{PUBLIC_BASE_URL}/feed/{slug}.xml" if kk == "cian" \
            else f"{PUBLIC_BASE_URL}/feed/{slug}-{kk}.xml"
        galleries.append({
            "kind": kk, "title": v["title"], "url": v["url"],
            "photos": _photos(slug, kk), "feed": feed,
            "has_yd": has_yd, "mirror": v.get("mirror", False),
        })
    return render_template_string(
        _PROJ_HTML, slug=slug, proj=proj, base=PUBLIC_BASE_URL,
        galleries=galleries, plan=check.get("plan"), check=check,
        decor=_DECOR, house=_HOUSE,
        has_installment=isinstance(PROJECTS[slug].get("installment"), dict),
        status=_status().get(slug, {}),
    )


@admin_bp.route("/commercial")
@login_required
def commercial_page():
    projs = comm.load_projects()
    rows = []
    for slug, p in projs.items():
        d = comm.comm_dirs(slug)
        try:
            import xml.etree.ElementTree as _ET
            n = len(_ET.parse(d["feeds"] / "yandex.xml").getroot()) - 1 if (d["feeds"] / "yandex.xml").exists() else 0
        except Exception:
            n = 0
        rows.append({"slug": slug, "p": p, "n": max(n, 0),
                     "enriched": len(list(d["enriched"].glob("*.png")))})
    # Отдельные фиды (собираются кодом, не через мастер)
    import xml.etree.ElementTree as _ET
    from . import comm_zorge_cian, comm_cian_rent
    dedicated = []
    for key, name, refr, feed, path in [
        ("comm-zorge", "Зорге 9 — коммерция (ЦИАН)", "/refresh-comm-zorge",
         "/feed/comm/zorge-cian.xml", comm_zorge_cian.OUT),
        ("comm-b37rent", "Б37 — коммерция аренда (ЦИАН)", "/refresh-comm-rent",
         "/feed/comm/b37-rent-cian.xml", comm_cian_rent.OUT),
    ]:
        try:
            n = len(list(_ET.parse(path).getroot().iter("object"))) if path.exists() else 0
        except Exception:
            n = 0
        dedicated.append({"key": key, "name": name, "refresh": refr, "feed": feed, "n": n})
    return render_template_string(_COMM_HTML, rows=rows, dedicated=dedicated, base=PUBLIC_BASE_URL)


@admin_bp.route("/commercial/save", methods=["POST"])
@login_required
def commercial_save():
    f = request.form
    slug = re.sub(r"[^a-z0-9]", "", (f.get("slug", "").strip().lower())) or f"comm{int(time.time())}"
    plats = [p for p in ("cian", "avito", "yandex") if f.get(p) == "on"]
    projs = comm.load_projects()
    projs[slug] = {
        "name": f.get("name", "").strip() or slug,
        "source_url": f.get("source_url", "").strip(),
        "platforms": plats,
        "address": f.get("address", "").strip(),
        "sales_agent": {"organization": f.get("org", "").strip(), "category": "застройщик",
                        "phone": f.get("phone", "").strip(), "url": f.get("url", "").strip()},
        "yadisk_fallback": f.get("yadisk_fallback", "").strip(),
    }
    comm.save_projects(projs)
    try:
        r = comm.refresh_commercial(slug)
        flash(f"Фид «{projs[slug]['name']}» сформирован: лотов {r.get('lots')}, площадки {', '.join(plats) or '—'}.")
    except Exception as e:
        flash(f"Сохранено, но при формировании ошибка: {e}")
    return redirect(url_for("admin.commercial_page"))


@admin_bp.route("/commercial/<slug>/refresh", methods=["POST"])
@login_required
def commercial_refresh(slug: str):
    if slug not in comm.load_projects():
        abort(404)
    try:
        r = comm.refresh_commercial(slug)
        flash(f"Обновлено: лотов {r.get('lots')}, обогащено {r.get('enriched')}.")
    except Exception as e:
        flash(f"Ошибка: {e}")
    return redirect(url_for("admin.commercial_page"))


@admin_bp.route("/commercial/<slug>/delete", methods=["POST"])
@login_required
def commercial_delete(slug: str):
    projs = comm.load_projects()
    if slug in projs:
        del projs[slug]
        comm.save_projects(projs)
        flash(f"Удалён проект {slug}.")
    return redirect(url_for("admin.commercial_page"))


@admin_bp.route("/<slug>/views")
@login_required
def views_page(slug: str):
    if slug not in PROJECTS:
        abort(404)
    rows = _views_coverage(slug)
    have = sum(1 for r in rows if r["n"] > 0)
    from .server import view_sync_status
    syncing = request.args.get("started") == "1" or view_sync_status(slug).get("state") == "running"
    return render_template_string(_VIEWS_HTML, slug=slug, name=PROJECTS[slug]["name"],
                                  rows=rows, have=have, total=len(rows), base=PUBLIC_BASE_URL,
                                  syncing=syncing)


@admin_bp.route("/<slug>/views/resync", methods=["POST"])
@login_required
def views_resync(slug: str):
    if slug not in PROJECTS:
        abort(404)
    # Обход Я.Диска долгий (>120с) → запускаем в фоне, страница опрашивает статус
    # и показывает попап с итогами по завершении (см. JS в _VIEWS_HTML).
    from .server import resync_views_async
    resync_views_async(slug)
    return redirect(url_for("admin.views_page", slug=slug, started=1))


@admin_bp.route("/<slug>/views/sync_status")
@login_required
def views_sync_status(slug: str):
    if slug not in PROJECTS:
        abort(404)
    from .server import view_sync_status
    return jsonify(view_sync_status(slug))


@admin_bp.route("/<slug>/views/<lot>/upload", methods=["POST"])
@login_required
def views_upload(slug: str, lot: str):
    if slug not in PROJECTS:
        abort(404)
    lot = secure_filename(lot)
    dest = project_dirs(slug)["views"] / lot
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for fs in request.files.getlist("photos"):
        if fs and fs.filename:
            try:
                save_resized_jpeg(fs.read(), dest / f"u{int(time.time()*1000)}{n}.jpg")
                n += 1
            except Exception as e:
                flash(f"{fs.filename}: {e}")
    _rebuild_all_feeds(slug)
    flash(f"Лот {lot}: добавлено видов {n}.")
    return redirect(url_for("admin.views_page", slug=slug))


@admin_bp.route("/<slug>/views/<lot>/delete", methods=["POST"])
@login_required
def views_delete(slug: str, lot: str):
    if slug not in PROJECTS:
        abort(404)
    lot = secure_filename(lot)
    name = secure_filename(request.form.get("name", ""))
    vdir = project_dirs(slug)["views"] / lot
    p = vdir / name
    if p.exists():
        p.unlink()
        # сброс манифеста — чтобы следующий синк с ЯД пересверился (если в ЯД ещё есть — вернётся)
        (vdir / "_src.json").unlink(missing_ok=True)
    _rebuild_all_feeds(slug)
    flash(f"Удалено: {name}. (Чтобы убрать навсегда — удалите и в папке Я.Диска.)")
    return redirect(url_for("admin.views_page", slug=slug))


@admin_bp.route("/<slug>/settings", methods=["POST"])
@login_required
def save_settings(slug: str):
    if slug not in PROJECTS:
        abort(404)
    f = request.form
    set_override(slug, "avito_default_decoration", f.get("decoration", "").strip())
    set_override(slug, "avito_house_type", f.get("house_type", "").strip())
    set_override(slug, "avito_market_type", f.get("market_type", "Новостройка").strip())
    set_override(slug, "avito_replace_building_image", f.get("replace_bi") == "on")
    set_override(slug, "description_suffix", f.get("description_suffix", "").strip())
    try:
        set_override(slug, "price_discount_pct", float(f.get("discount", "0").replace(",", ".") or 0))
    except ValueError:
        flash("Скидка: ожидалось число — не сохранено")
    # рассрочка (если у проекта она есть) — при изменении сразу перерисовываем планировки
    inst_changed = False
    if isinstance(PROJECTS[slug].get("installment"), dict):
        old = get_project(slug).get("installment")
        try:
            new = {
                "feed_to_base_divisor": float(f.get("inst_div", "0.8").replace(",", ".")),
                "down_payment_pct": float(f.get("inst_pv", "0.10").replace(",", ".")),
                "monthly_pct_of_base": float(f.get("inst_m", "0.005").replace(",", ".")),
            }
            set_override(slug, "installment", new)
            inst_changed = (new != old)
        except ValueError:
            flash("Рассрочка: ожидались числа — не сохранено")
    if inst_changed:
        n = _regenerate_plans(slug)
        flash(f"Настройки сохранены. Планировки перерисованы с новой рассрочкой ({n} шт).")
    else:
        flash("Настройки сохранены. Нажмите «Обновить фид», чтобы применить.")
    return redirect(url_for("admin.project", slug=slug))


@admin_bp.route("/<slug>/<kind>/photos/upload", methods=["POST"])
@login_required
def upload_photos(slug: str, kind: str):
    if slug not in PROJECTS or kind not in _KINDS:
        abort(404)
    k = _KINDS[kind]
    extra = project_dirs(slug)[k["dir"]]
    order = list(get_project(slug).get(k["order_key"]) or _photos(slug, kind))
    added = 0
    for fs in request.files.getlist("photos"):
        if not fs or not fs.filename:
            continue
        stem = Path(secure_filename(fs.filename)).stem or f"photo{int(time.time())}"
        name = f"{stem}.jpg"
        try:
            save_resized_jpeg(fs.read(), extra / name)
            if name not in order:
                order.append(name)
            added += 1
        except Exception as e:
            flash(f"Не удалось обработать {fs.filename}: {e}")
    set_override(slug, k["order_key"], order)
    _rebuild(slug, kind)
    flash(f"Загружено фото ({k['title']}): {added}.")
    return redirect(url_for("admin.project", slug=slug))


@admin_bp.route("/<slug>/<kind>/photos/delete", methods=["POST"])
@login_required
def delete_photo(slug: str, kind: str):
    if slug not in PROJECTS or kind not in _KINDS:
        abort(404)
    k = _KINDS[kind]
    name = secure_filename(request.form.get("name", ""))
    p = project_dirs(slug)[k["dir"]] / name
    if p.exists():
        p.unlink()
    order = [n for n in (get_project(slug).get(k["order_key"]) or []) if n != name]
    set_override(slug, k["order_key"], order)
    add_excluded_photo(slug, kind, name)   # чёрный список — синк с ЯД больше не вернёт
    _rebuild(slug, kind)
    flash(f"Удалено: {name}. Из Я.Диска больше не вернётся (в чёрном списке).")
    return redirect(url_for("admin.project", slug=slug))


@admin_bp.route("/<slug>/<kind>/photos/order", methods=["POST"])
@login_required
def reorder_photos(slug: str, kind: str):
    if slug not in PROJECTS or kind not in _KINDS:
        abort(404)
    order = [secure_filename(n) for n in request.form.getlist("order") if n.strip()]
    set_override(slug, _KINDS[kind]["order_key"], order)
    _rebuild(slug, kind)
    return ("", 204)


@admin_bp.route("/<slug>/<kind>/photos/sync_yd", methods=["POST"])
@login_required
def sync_yd(slug: str, kind: str):
    if slug not in PROJECTS or kind not in _KINDS:
        abort(404)
    k = _KINDS[kind]
    cfg = PROJECTS[slug].get(k["cfg_key"]) or {}
    if not cfg.get("yadisk_public_key"):
        flash("Для этого набора не задана папка Яндекс.Диска")
        return redirect(url_for("admin.project", slug=slug))
    try:
        n = len(sync_public_folder(cfg["yadisk_public_key"], cfg["yadisk_path"],
                                   project_dirs(slug)[k["dir"]], mirror=k.get("mirror", False),
                                   exclude=excluded_photos(slug, kind)))
        _rebuild(slug, kind)
        flash(f"С Яндекс.Диска синхронизировано ({k['title']}): {n}.")
    except Exception as e:
        flash(f"Ошибка синка с Я.Диска: {e}")
    return redirect(url_for("admin.project", slug=slug))


@admin_bp.route("/<slug>/refresh", methods=["POST"])
@login_required
def refresh(slug: str):
    if slug not in PROJECTS:
        abort(404)
    from .server import refresh_project  # ленивый импорт — избегаем цикла
    # принудительная перегенерация планировок (если менялась рассрочка/шаблон)
    if request.form.get("force") == "on":
        for png in project_dirs(slug)["enriched"].glob("*.png"):
            png.unlink()
    try:
        res = refresh_project(slug)
        vdir = project_dirs(slug)["views"]
        views_n = sum(1 for sub in vdir.iterdir()
                      if sub.is_dir() and any(sub.glob("*.jpg"))) if vdir.exists() else 0
        flash(f"Фид обновлён. Лотов: {res.get('lots_total', '?')}, "
              f"планировок обогащено: {res.get('enriched_ok', 0)}, "
              f"лотов с видами из окон: {views_n}, "
              f"объявлений в Авито: {_avito_check(slug)['ads']}.")
    except Exception as e:
        flash(f"Ошибка обновления: {e}")
    return redirect(url_for("admin.project", slug=slug))


# ──────────────── шаблоны ────────────────

_CSS = """
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:1480px;margin:0 auto;padding:24px;color:#1c2430;background:#f4f6f9}
 a{color:#2563eb;text-decoration:none} a:hover{text-decoration:underline}
 h1{font-size:26px;margin:0 0 18px} h2{font-size:19px;margin:24px 0 12px}
 .card{background:#fff;border-radius:10px;padding:18px 22px;box-shadow:0 1px 3px rgba(0,0,0,.07);margin-bottom:18px}
 .btn{display:inline-block;background:#2563eb;color:#fff;border:0;border-radius:7px;padding:9px 16px;cursor:pointer;font-size:15px}
 .btn.gray{background:#6b7280}.btn.red{background:#dc2626}.btn.green{background:#16a34a}
 label{display:block;font-size:13px;color:#555;margin:10px 0 4px}
 input[type=text],select,textarea{width:100%;box-sizing:border-box;padding:9px;border:1px solid #cbd5e1;border-radius:6px;font-size:15px}
 table{border-collapse:collapse;width:100%}.td td,td,th{padding:10px;border-bottom:1px solid #eef1f5;text-align:left;font-size:15px}
 .flash{background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:10px 14px;margin-bottom:14px}
 .pill{display:inline-block;background:#e8f0fe;color:#1a56db;border-radius:20px;padding:2px 10px;font-size:12px}
 .ok{color:#16a34a;font-weight:600}.bad{color:#dc2626;font-weight:600}
 .ph{display:inline-block;margin:5px;vertical-align:top;text-align:center;font-size:12px;color:#666}
 .ph img{width:172px;height:129px;object-fit:cover;border-radius:6px;border:1px solid #ddd;display:block}
 .row{display:flex;gap:18px;flex-wrap:wrap}.col{flex:1;min-width:300px}
 .muted{color:#94a3b8;font-size:13px}
 .strip{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}
 .modal-bg{position:fixed;inset:0;background:rgba(15,23,42,.5);display:flex;align-items:center;justify-content:center;z-index:50}
 .modal{background:#fff;border-radius:16px;padding:26px 30px;max-width:480px;box-shadow:0 16px 48px rgba(0,0,0,.28);text-align:center}
 .modal-h{font-size:21px;font-weight:700;margin-bottom:12px}
 .modal-b{font-size:16px;color:#334155;line-height:1.55;margin-bottom:20px}
 .tile{position:relative;width:200px;height:150px;border-radius:8px;overflow:hidden;border:1px solid #d7dee7;background:#fff;cursor:grab}
 .tile img{width:100%;height:100%;object-fit:cover;display:block}
 .tile .del{position:absolute;top:4px;right:4px;width:22px;height:22px;border:0;border-radius:50%;background:rgba(220,38,38,.92);color:#fff;font-size:13px;line-height:22px;cursor:pointer;padding:0}
 .tile .num{position:absolute;left:4px;bottom:4px;background:rgba(0,0,0,.6);color:#fff;font-size:11px;border-radius:4px;padding:1px 6px}
 .tile.locked{cursor:default;border-style:dashed;opacity:.95}
 .tile.locked .lbl{position:absolute;left:4px;bottom:4px;background:rgba(37,99,235,.85);color:#fff;font-size:11px;border-radius:4px;padding:1px 6px}
 .tile.add{display:flex;align-items:center;justify-content:center;font-size:34px;color:#94a3b8;cursor:pointer;border-style:dashed}
 .tile.dragover{outline:3px solid #2563eb;outline-offset:-3px}
 .tile.dragging{opacity:.4}
</style>
"""

_FLASH = """{% with m=get_flashed_messages() %}{% if m %}
{% set _j = (m|join(' '))|lower %}
{% set _err = ('ошибк' in _j) or ('неверн' in _j) or ('не сохранено' in _j) or ('не удалось' in _j) or ('не задана' in _j) %}
<div class="modal-bg" id="popup" onclick="if(event.target===this)this.remove()">
 <div class="modal">
  <div class="modal-h" style="color:{{'#dc2626' if _err else '#16a34a'}}">{{'⚠ Внимание' if _err else '✓ Готово'}}</div>
  <div class="modal-b">{{m|join('<br>')|safe}}</div>
  <button class="btn{{' red' if _err else ' green'}}" onclick="document.getElementById('popup').remove()">OK</button>
 </div>
</div>
<script>document.addEventListener('keydown',function(e){if(e.key==='Escape'){var p=document.getElementById('popup');if(p)p.remove();}});</script>
{% endif %}{% endwith %}"""

_LOGIN_HTML = _CSS + """<title>Вход — фиды</title><h1>Панель управления фидами</h1>""" + _FLASH + """
<div class=card style="max-width:360px">
 <form method=post>
  <label>Пароль</label>
  <input type=password name=password autofocus>
  <div style="margin-top:14px"><button class=btn>Войти</button></div>
 </form>
</div>"""

_DASH_HTML = _CSS + """<title>Фиды</title>
<h1>Фиды квартир — панель</h1>""" + _FLASH + """
<div class=card>
 <table>
  <tr><th>Проект</th><th>ЦИАН</th><th>Авито</th><th>Яндекс</th><th>Я.Поиск</th><th>ДомКлик</th><th>Фото А/Я/Ц</th><th>Виды</th><th>Обновлено</th><th></th></tr>
  {% for r in rows %}
  <tr>
   <td><b>{{r.name}}</b><div class=muted>{{r.slug}}</div></td>
   <td>{{r.cian}} <div class=muted><a href="{{base}}/feed/{{r.slug}}.xml" target=_blank>xml</a></div></td>
   <td>{{r.avito}} <div class=muted><a href="{{base}}/feed/{{r.slug}}-avito.xml" target=_blank>xml</a></div></td>
   <td>{{r.yandex}} <div class=muted><a href="{{base}}/feed/{{r.slug}}-yandex.xml" target=_blank>xml</a></div></td>
   <td>{{r.yandex_realty}} <div class=muted><a href="{{base}}/feed/{{r.slug}}-yandex-realty.xml" target=_blank>xml</a></div></td>
   <td>{{r.domclick}} <div class=muted><a href="{{base}}/feed/{{r.slug}}-domclick.xml" target=_blank>xml</a></div></td>
   <td>{{r.photos}} / {{r.photos_y}} / {{r.photos_c}}</td>
   <td>{{r.views}} <div class=muted><a href="{{url_for('admin.views_page',slug=r.slug)}}">список</a></div></td>
   <td class=muted>{% if r.status.get('ts') %}{{r.status.ts}}<br>планировок: {{r.status.get('enriched_ok','?')}}{% else %}—{% endif %}</td>
   <td><a class=btn href="{{url_for('admin.project',slug=r.slug)}}">Открыть</a></td>
  </tr>
  {% endfor %}
 </table>
</div>
<p><a class=btn gray href="{{url_for('admin.commercial_page')}}">🏢 Коммерческие фиды (мастер)</a></p>
<p class=muted><a href="{{url_for('admin.logout')}}">Выйти</a></p>"""

_VIEWS_HTML = _CSS + """<title>Виды — {{name}}</title>
<p><a href="{{url_for('admin.dashboard')}}">← все фиды</a> · <a href="{{url_for('admin.project',slug=slug)}}">{{name}}</a></p>
<h1>Виды из окон — {{name}}</h1>""" + _FLASH + """
<div class=card>
 <p>Лотов с видами: <b class=ok>{{have}}</b> из {{total}}. Без видов — <b class=bad>{{total-have}}</b> (вверху).
   <form method=post action="{{url_for('admin.views_resync',slug=slug)}}" style="display:inline;margin-left:10px" id=syncform>
     <button class="btn green" id=syncbtn>↻ Синхронизировать с Я.Диска</button></form>
   <span id=syncwait style="display:none;margin-left:10px;color:#6b7280">⏳ Идёт синхронизация с Я.Диском… (1–2 мин, можно не ждать)</span></p>
<script>
(function(){
 var syncing = {{ 'true' if syncing else 'false' }};
 function popup(ok, text){
   var bg=document.createElement('div'); bg.className='modal-bg';
   bg.onclick=function(e){if(e.target===bg)bg.remove();};
   bg.innerHTML='<div class="modal"><div class="modal-h" style="color:'+(ok?'#16a34a':'#dc2626')+'">'+
     (ok?'✓ Готово':'⚠ Внимание')+'</div><div class="modal-b">'+text+'</div>'+
     '<button class="btn '+(ok?'green':'red')+'">OK</button></div>';
   bg.querySelector('button').onclick=function(){bg.remove();};
   document.body.appendChild(bg);
 }
 if(syncing){
   var b=document.getElementById('syncbtn'); if(b){b.disabled=true;}
   document.getElementById('syncwait').style.display='inline';
   var poll=setInterval(function(){
     fetch('{{url_for("admin.views_sync_status",slug=slug)}}',{credentials:'same-origin'})
      .then(function(r){return r.json();})
      .then(function(s){
        if(s.state==='done'){
          clearInterval(poll);
          popup(true,'Синхронизация с Я.Диском завершена.<br>Лотов в фиде: <b>'+(s.lots)+
            '</b><br>Из них с видами из окон: <b>'+(s.lots_with_views)+
            '</b><br>Всего фото видов: <b>'+(s.view_files)+'</b>');
          document.getElementById('syncwait').style.display='none';
        } else if(s.state==='error'){
          clearInterval(poll);
          popup(false,'Ошибка синхронизации: '+(s.error||''));
          document.getElementById('syncwait').style.display='none';
        }
      }).catch(function(){});
   },3000);
 }
})();
</script>
 <p class=muted>Виды зеркалятся из Я.Диска (удаления в ЯД подхватываются; авто-синк раз в час). Можно править вручную: <b>✕</b> — удалить, <b>＋</b> — загрузить (ручные сохраняются как «u…», синк с ЯД их не трогает).</p>
 <table>
  <tr><th>Лот · Корпус · Этаж · Тип · Площадь</th><th>Виды (✕ удалить, ＋ добавить)</th></tr>
  {% for r in rows %}
  <tr style="{% if r.n==0 %}background:#fff5f5{% endif %}">
   <td><b>{{r.id}}</b> · {{r.house}} · эт.{{r.floor}} · {{r.label}} · {{r.area}} м²<br>
       <span class="{% if r.n %}ok{% else %}bad{% endif %}">видов: {{r.n or 'нет'}}</span></td>
   <td>
     {% for fn in r.files %}
     <span style="position:relative;display:inline-block;margin:2px">
       <img src="{{base}}/views/{{slug}}/{{r.id}}/{{fn}}" style="width:132px;height:96px;object-fit:cover;border-radius:6px;border:1px solid #ddd">
       <form method=post action="{{url_for('admin.views_delete',slug=slug,lot=r.id)}}" style="position:absolute;top:2px;right:2px;margin:0">
         <input type=hidden name=name value="{{fn}}">
         <button class=del onclick="return confirm('Удалить {{fn}}?')">✕</button></form>
     </span>
     {% endfor %}
     <form method=post action="{{url_for('admin.views_upload',slug=slug,lot=r.id)}}" enctype=multipart/form-data style="display:inline">
       <input type=file name=photos accept="image/*" multiple style="width:200px;font-size:13px">
       <button class=btn style="padding:5px 9px">＋</button></form>
   </td>
  </tr>
  {% endfor %}
 </table>
</div>
<p class=muted><a href="{{url_for('admin.logout')}}">Выйти</a></p>"""

_COMM_HTML = _CSS + """<title>Коммерческие фиды</title>
<p><a href="{{url_for('admin.dashboard')}}">← все фиды</a></p>
<h1>Мастер фидов (коммерция / аренда)</h1>""" + _FLASH + """
<div class=card>
 <h2 style="margin-top:0">Готовые фиды</h2>
 {% if not rows %}<p class=muted>Пока нет. Добавьте ниже.</p>{% endif %}
 {% for r in rows %}
  <div style="border-bottom:1px solid #eef1f5;padding:10px 0">
   <b>{{r.p.name}}</b> <span class=pill>{{r.slug}}</span> · лотов ~{{r.n}}, планировок {{r.enriched}}
   <div class=muted style="margin:4px 0">{{r.p.source_url[:70]}}…</div>
   <div>Фиды:
    {% for pl in r.p.platforms %}<a href="{{base}}/feed/comm/{{r.slug}}-{{pl}}.xml" target=_blank>{{pl}}</a>{% if not loop.last %} · {% endif %}{% endfor %}
    {% if not r.p.platforms %}<span class=muted>площадки не выбраны</span>{% endif %}
   </div>
   <form method=post action="{{url_for('admin.commercial_refresh',slug=r.slug)}}" style="display:inline">
     <button class="btn green">Пересформировать</button></form>
   <form method=post action="{{url_for('admin.commercial_delete',slug=r.slug)}}" style="display:inline">
     <button class="btn red" onclick="return confirm('Удалить {{r.slug}}?')">Удалить</button></form>
  </div>
 {% endfor %}
</div>
<div class=card>
 <h2 style="margin-top:0">Отдельные фиды (через ProfitBase API)</h2>
 <p class=muted>Собираются кодом, не через мастер. Состав/назначения правятся в коде.</p>
 {% for d in dedicated %}
  <div style="border-bottom:1px solid #eef1f5;padding:10px 0">
   <b>{{d.name}}</b> · лотов {{d.n}}
   <div style="margin:4px 0">
     <a href="{{base}}{{d.feed}}" target=_blank>XML</a> ·
     <a href="{{base}}/?feed={{d.key}}" target=_blank>превью карточек</a>
   </div>
   <form method=post action="{{base}}{{d.refresh}}" style="display:inline">
     <button class="btn green">Обновить</button></form>
  </div>
 {% endfor %}
</div>
<div class=card>
 <h2 style="margin-top:0">Добавить / обновить фид</h2>
 <form method=post action="{{url_for('admin.commercial_save')}}">
  <label>Название</label><input type=text name=name placeholder="Например: Б37 Коммерция">
  <label>Код (slug, латиницей; пусто = авто)</label><input type=text name=slug placeholder="b37comm">
  <label>Ссылка на фид ProfitBase (profitbase_xml)</label><input type=text name=source_url placeholder="https://pb7828.profitbase.ru/export/profitbase_xml/...">
  <label>Площадки</label>
  <div><label style="display:inline"><input type=checkbox name=cian checked> ЦИАН</label>
   <label style="display:inline;margin-left:14px"><input type=checkbox name=avito checked> Авито</label>
   <label style="display:inline;margin-left:14px"><input type=checkbox name=yandex checked> Яндекс</label></div>
  <label>Адрес</label><input type=text name=address placeholder="Москва, улица Берзарина, 37">
  <div class=row><div class=col><label>Телефон</label><input type=text name=phone placeholder="+74952924193"></div>
   <div class=col><label>Организация</label><input type=text name=org placeholder="St MICHAEL"></div></div>
  <label>Сайт (опц.)</label><input type=text name=url placeholder="https://stmichael.ru">
  <label>Папка Я.Диска с картинками — фолбэк, если в ProfitBase их нет (опц.)</label>
  <input type=text name=yadisk_fallback placeholder="https://disk.360.yandex.ru/d/...">
  <div style="margin-top:14px"><button class=btn>Сформировать фид</button></div>
 </form>
 <p class=muted style="margin-top:10px">⚠️ Коммерческие схемы площадок строже жилья — после формирования прогоните фид через валидатор площадки (например autoload.avito.ru/format/xmlcheck). Обогащённые планировки — на шаблоне с подписями Площадь/Высота/Мощность.</p>
</div>
<p class=muted><a href="{{url_for('admin.logout')}}">Выйти</a></p>"""

_PROJ_HTML = _CSS + """<title>{{proj.name}}</title>
<p><a href="{{url_for('admin.dashboard')}}">← все фиды</a> · <a href="{{url_for('admin.views_page',slug=slug)}}">виды из окон</a></p>
<h1>{{proj.name}} <span class=pill>{{slug}}</span></h1>""" + _FLASH + """

<div class=row>
 <div class=col>
  <div class=card>
   <h2 style="margin-top:0">Авито-фид</h2>
   <p>Объявлений: <b>{{check.ads}}</b> · <a href="{{base}}/feed/{{slug}}-avito.xml" target=_blank>открыть XML</a></p>
   {% if check.issues %}
     <p class=bad>Проблемы обязательных полей:</p>
     <ul>{% for k,v in check.issues.items() %}<li>{{k}}: пропусков {{v}}</li>{% endfor %}</ul>
   {% else %}<p class=ok>✓ Обязательные поля Авито заполнены у всех объявлений</p>{% endif %}
   <form method=post action="{{url_for('admin.refresh',slug=slug)}}">
     <label><input type=checkbox name=force> заодно перерисовать планировки (если меняли рассрочку)</label>
     <button class=btn green>Обновить фид</button>
   </form>
  </div>
 </div>

 <div class=col>
  <div class=card>
   <h2 style="margin-top:0">Настройки</h2>
   <form method=post action="{{url_for('admin.save_settings',slug=slug)}}">
    <label>Отделка по умолчанию (где ProfitBase не отдал)</label>
    <input type=text name=decoration list=decor value="{{proj.get('avito_default_decoration','')}}">
    <datalist id=decor>{% for d in decor %}<option value="{{d}}">{% endfor %}</datalist>
    <label>Материал дома (HouseType, необязательно)</label>
    <input type=text name=house_type list=house value="{{proj.get('avito_house_type','')}}">
    <datalist id=house>{% for h in house %}<option value="{{h}}">{% endfor %}</datalist>
    <label>Тип рынка</label>
    <select name=market_type>
     {% for m in ['Новостройка','Вторичка'] %}<option {% if proj.get('avito_market_type')==m %}selected{% endif %}>{{m}}</option>{% endfor %}
    </select>
    <label>Скидка к цене из фида, %</label>
    <input type=text name=discount value="{{proj.get('price_discount_pct',0)}}">
    <div class=muted>0 = цену не трогаем. Ставить 20, когда ProfitBase начнёт отдавать полную (несо&shy;скидочную) стоимость, иначе будет двойная скидка.</div>
    <label style="margin-top:12px"><input type=checkbox name=replace_bi {% if proj.get('avito_replace_building_image',True) %}checked{% endif %}> заменять остальные фото ProfitBase нашими</label>
    <label>Приписка к описанию (добавится в конец каждого)</label>
    <textarea name=description_suffix rows=3>{{proj.get('description_suffix','')}}</textarea>
    {% if has_installment %}
     <h2>Рассрочка</h2>
     <label>Делитель цены (feed→base)</label><input type=text name=inst_div value="{{proj.installment.feed_to_base_divisor}}">
     <label>Первый взнос, доля</label><input type=text name=inst_pv value="{{proj.installment.down_payment_pct}}">
     <label>Платёж/мес, доля от base</label><input type=text name=inst_m value="{{proj.installment.monthly_pct_of_base}}">
    {% endif %}
    <div style="margin-top:14px"><button class=btn>Сохранить настройки</button></div>
   </form>
  </div>
 </div>
</div>

{% if check.first %}<p class=muted>Превью первого лота {{check.first.id}}: {{check.first.rooms}} · {{check.first.square}} м² · {{check.first.price}} ₽. Первой в карточке всегда идёт наша планировка{% if plan %} (пунктирная плитка){% endif %}.</p>{% endif %}
{% for g in galleries %}
<div class=card>
 <h2 style="margin-top:0">Фото — {{g.title}} ({{g.photos|length}})</h2>
 <p class=muted>Перетаскивайте мышкой — порядок · <b>✕</b> — удалить · плитка <b>＋</b> — загрузить. Применяется сразу.
   · <a href="{{g.feed}}" target=_blank>открыть XML</a></p>
 {% if g.mirror %}<p class=muted>Это зеркало папки Я.Диска: набор синхронизируется раз в час (добавления и удаления в ЯД подхватываются) и по кнопке ниже. Карточка ЦИАН: <b>планировка → эти фото → виды из окон</b>. Удаление здесь временно — если фото осталось в ЯД, синк вернёт его (убирайте в самой папке Я.Диска).</p>{% endif %}
 <div class=strip data-kind="{{g.kind}}"
      data-upload="{{url_for('admin.upload_photos',slug=slug,kind=g.kind)}}"
      data-delete="{{url_for('admin.delete_photo',slug=slug,kind=g.kind)}}"
      data-order="{{url_for('admin.reorder_photos',slug=slug,kind=g.kind)}}">
   {% if plan %}<div class="tile locked"><img src="{{plan}}"><span class=lbl>планировка</span></div>{% endif %}
   {% for n in g.photos %}
   <div class=tile draggable=true data-name="{{n}}">
     <img src="{{base}}/{{g.url}}/{{slug}}/{{n}}" loading=lazy>
     <button class=del title="Удалить">✕</button>
     <span class=num>{{loop.index}}</span>
   </div>
   {% endfor %}
   <label class="tile add" title="Загрузить фото">＋<input type=file accept="image/*" multiple hidden></label>
 </div>
 {% if g.has_yd %}<form method=post action="{{url_for('admin.sync_yd',slug=slug,kind=g.kind)}}" style="margin-top:12px">
  <button class="btn gray">Синхронизировать с Яндекс.Диска</button>
 </form>{% endif %}
</div>
{% endfor %}
<div id=busy class=muted style="display:none;margin:10px 0">Сохраняю, обновляю фид…</div>
<p class=muted><a href="{{url_for('admin.logout')}}">Выйти</a></p>
<script>
(function(){
 var busy=document.getElementById('busy');
 function post(url,body){ busy.style.display='block';
   fetch(url,{method:'POST',body:body,credentials:'same-origin'}).then(function(){location.reload();})
   .catch(function(){busy.textContent='Ошибка, попробуйте ещё раз';}); }
 document.querySelectorAll('.strip').forEach(function(strip){
   var drag=null;
   strip.addEventListener('click',function(e){
     var b=e.target.closest('.del'); if(!b) return;
     var tile=b.closest('.tile'); if(!confirm('Удалить это фото?')) return;
     var fd=new FormData(); fd.append('name',tile.dataset.name);
     post(strip.dataset.delete,fd); });
   var up=strip.querySelector('input[type=file]');
   if(up) up.addEventListener('change',function(){
     if(!up.files.length) return; var fd=new FormData();
     for(var i=0;i<up.files.length;i++) fd.append('photos',up.files[i]);
     post(strip.dataset.upload,fd); });
   strip.addEventListener('dragstart',function(e){
     var t=e.target.closest('.tile[draggable=true]'); if(!t) return;
     drag=t; t.classList.add('dragging'); });
   strip.addEventListener('dragend',function(){ if(drag) drag.classList.remove('dragging'); drag=null;
     strip.querySelectorAll('.dragover').forEach(function(x){x.classList.remove('dragover');}); });
   strip.addEventListener('dragover',function(e){ e.preventDefault();
     var t=e.target.closest('.tile[draggable=true]');
     strip.querySelectorAll('.dragover').forEach(function(x){x.classList.remove('dragover');});
     if(t&&t!==drag) t.classList.add('dragover'); });
   strip.addEventListener('drop',function(e){ e.preventDefault();
     var t=e.target.closest('.tile[draggable=true]'); if(!drag||!t||t===drag) return;
     var ts=[].slice.call(strip.querySelectorAll('.tile[draggable=true]'));
     if(ts.indexOf(drag)<ts.indexOf(t)) t.after(drag); else t.before(drag);
     var order=[].slice.call(strip.querySelectorAll('.tile[draggable=true]')).map(function(x){return x.dataset.name;});
     var fd=new FormData(); order.forEach(function(n){fd.append('order',n);});
     post(strip.dataset.order,fd); });
 });
})();
</script>"""
