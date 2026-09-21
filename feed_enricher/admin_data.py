"""Данные для информационной админки: вкладки, фиды площадок, лоты с галереями фото.

Из каждого нашего фида (ЦИАН / Авито / Яндекс / ДомКлик / сайт) достаём для каждого лота
упорядоченный список фото ровно в том порядке, в каком их получит площадка.
"""
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import PROJECTS, PUBLIC_BASE_URL, CACHE_DIR, ADMIN_DIR, project_dirs, get_project

# ──────────────── площадки ────────────────
# layout — как рисуем галерею в превью; limit — сколько фото площадка реально берёт (по нашему коду)
PLATFORMS = {
    "cian":          {"name": "ЦИАН",                 "color": "#2563eb", "layout": "cian",   "limit": None},
    "avito":         {"name": "Авито",                "color": "#16a34a", "layout": "avito",  "limit": 40},
    "yandex":        {"name": "Яндекс.Недвижимость",  "color": "#fc3f1d", "layout": "mosaic", "limit": 30},
    "yandex_realty": {"name": "Яндекс Поиск",         "color": "#f59e0b", "layout": "mosaic", "limit": 30},
    "domclick":      {"name": "ДомКлик",              "color": "#0d9488", "layout": "cian",   "limit": None},
    "site":          {"name": "Сайт",                 "color": "#7c3aed", "layout": "cian",   "limit": None},
}

# Виды фото → (подпись, цвет плашки)
KINDS = {
    "plan_ours": ("Планировка (наша)", "#2563eb"),
    "interior":  ("Интерьер", "#c2410c"),
    "visual":    ("Визуализация", "#7c3aed"),
    "view":      ("Вид из окна", "#0e7490"),
    "common":    ("Общее фото проекта", "#475569"),
    "pb_plan":   ("Планировка ProfitBase", "#1d4ed8"),
    "pb_floor":  ("План этажа ProfitBase", "#4f46e5"),
    "pb_photo":  ("Фото дома ProfitBase", "#64748b"),
    "other":     ("Фото", "#94a3b8"),
}

STATUS_RU = {"AVAILABLE": "в продаже", "BOOKED": "бронь", "SOLD": "продан",
             "UNAVAILABLE": "не для продажи", "EXECUTION": "в исполнении"}

_ln = lambda tag: tag.split("}")[-1]


def classify(url: str, type_attr: str = "") -> str:
    """Тип фото по URL (наши пути) или по type из profitbase_xml."""
    u = url or ""
    if "/enriched/" in u or "-img/" in u and ("comm-" in u or "parking" in u):
        return "plan_ours"
    if "/views/" in u:
        n = u.rsplit("/", 1)[-1]
        return "interior" if n.startswith("int_") else "visual" if n.startswith("vis_") else "view"
    if "/soho-img/int/" in u:
        return "interior"
    if "/extra_cian/" in u or "/extra_yandex/" in u or "/extra/" in u or "/soho-img/common/" in u:
        return "common"
    t = (type_attr or "").strip()
    if t == "plan":
        return "pb_plan"
    if t == "plan floor":
        return "pb_floor"
    if t in ("house", "facade", "building"):
        return "pb_photo"
    return "other"


def _money(v) -> str:
    try:
        return f"{int(float(v)):,}".replace(",", " ") + " ₽"
    except Exception:
        return ""


def _lot(lot_id, title, meta, status, photos) -> dict:
    """photos: [(url, type_attr)] → photos [{u, k}]"""
    ph = [{"u": u, "k": classify(u, t)} for u, t in photos if u]
    return {"id": str(lot_id or ""), "title": title, "meta": [m for m in meta if m],
            "status": status, "photos": ph}


# ──────────────── извлечение лотов по форматам ────────────────

def _first(el, tag) -> str:
    for c in el:
        if _ln(c.tag) == tag:
            return (c.text or "").strip()
    return ""


def _cian(root, feed) -> list:
    out = []
    for o in root.iter("object"):
        urls = [(p.findtext("FullUrl") or "").strip() for p in o.iter("PhotoSchema")]
        urls = [u for u in urls if u]
        if not urls:
            lp = (o.findtext("LayoutPhoto/FullUrl") or "").strip()
            urls = [lp] if lp else []
        cat = o.findtext("Category") or ""
        rooms = o.findtext("FlatRoomsCount")
        if rooms:
            title = {"9": "Студия", "7": "Своб. планировка", "10": "6+ комнат"}.get(rooms, f"{rooms}-комн.")
        elif "garage" in cat.lower():
            title = "Машиноместо"
        else:
            sp = o.find("Specialty")
            spec = next((s.text for s in sp.iter("String")), "") if sp is not None else ""
            title = spec or "Помещение"
        area = o.findtext("TotalArea") or ""
        fl = o.findtext("FloorNumber") or ""
        meta = [f"{area} м²" if area else "", f"эт. {fl}" if fl else "",
                _money(o.findtext("BargainTerms/Price")), o.findtext("Address") or ""]
        out.append(_lot(o.findtext("ExternalId"), title, meta, "", [(u, "") for u in urls]))
    return out


def _avito(root, feed) -> list:
    out = []
    for ad in root.iter("Ad"):
        urls = [(i.get("url") or "").strip() for i in ad.iter("Image")]
        r = ad.findtext("Rooms") or ""
        cat = ad.findtext("Category") or ""
        title = ("Студия" if r == "Студия" else f"{r}-комн." if r else ad.findtext("ObjectType") or cat or "Объявление")
        fl = ad.findtext("Floor") or ""
        sq = ad.findtext("Square") or ""
        meta = [f"{sq} м²" if sq else "", f"эт. {fl}" if fl else "", _money(ad.findtext("Price")),
                ad.findtext("OperationType") or ""]
        out.append(_lot(ad.findtext("Id"), title, meta, "", [(u, "") for u in urls]))
    return out


def _yandex(root, feed) -> list:
    out = []
    for o in root.iter():
        if _ln(o.tag) != "offer":
            continue
        urls = [(c.text or "").strip() for c in o if _ln(c.tag) == "image"]
        rooms = _first(o, "rooms")
        title = ("Студия" if _first(o, "studio") in ("да", "1") else f"{rooms}-комн." if rooms
                 else _first(o, "category") or "Объект")
        area_el = next((c for c in o if _ln(c.tag) == "area"), None)
        price_el = next((c for c in o if _ln(c.tag) == "price"), None)
        fl = _first(o, "floor")
        meta = [f"{_first(area_el, 'value')} м²" if area_el is not None else "",
                f"эт. {fl}" if fl else "",
                _money(_first(price_el, "value")) if price_el is not None else "",
                _first(o, "building-name")]
        out.append(_lot(o.get("internal-id"), title, meta, "", [(u, "") for u in urls]))
    return out


def _domclick(root, feed) -> list:
    out = []
    for f in root.iter("flat"):
        urls = [(p.text or "").strip() for p in f.iter("plan")]
        room = f.findtext("room") or ""
        title = "Студия" if room == "0" else f"{room}-комн." if room else "Квартира"
        meta = [f"{f.findtext('area')} м²" if f.findtext("area") else "",
                f"эт. {f.findtext('floor')}" if f.findtext("floor") else "",
                _money(f.findtext("price")), f.findtext("apartment") or ""]
        out.append(_lot(f.findtext("flat_id"), title, meta, "", [(u, "") for u in urls]))
    return out


def _site(root, feed) -> list:
    from .site_feed import _slug_of
    want = feed.get("filter")
    out = []
    for o in root.iter():
        if _ln(o.tag) != "offer":
            continue
        name = o.findtext("{*}object/{*}name") or ""
        if want and _slug_of(name) != want:
            continue
        imgs = [((c.text or "").strip(), c.get("type") or "") for c in o if _ln(c.tag) == "image"]
        rooms = _first(o, "rooms")
        title = ("Студия" if _first(o, "studio") in ("1", "да") else f"{rooms}-комн." if rooms else "Лот")
        area_el = next((c for c in o if _ln(c.tag) == "area"), None)
        fl = _first(o, "floor")
        st = _first(o, "status")
        meta = [name, f"{_first(area_el, 'value')} м²" if area_el is not None else "",
                f"эт. {fl}" if fl else "", _first(o, "number")]
        out.append(_lot(o.get("internal-id"), title, meta, st, imgs))
    return out


_EXTRACT = {"cian": _cian, "avito": _avito, "yandex": _yandex, "domclick": _domclick, "site": _site}
_LOTS_CACHE: dict = {}      # (путь, формат, фильтр) → (mtime_ns, лоты)


def load_lots(feed: dict) -> list:
    """Лоты фида с галереями (кэш по mtime файла)."""
    path = feed["path"]
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return []
    key = (str(path), feed["fmt"], feed.get("filter"))
    hit = _LOTS_CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        lots = _EXTRACT[feed["fmt"]](ET.parse(path).getroot(), feed)
    except Exception:
        lots = []
    _LOTS_CACHE[key] = (mt, lots)
    return lots


def lot_label(l: dict) -> str:
    st = f" · {STATUS_RU.get(l['status'], l['status'])}" if l["status"] else ""
    meta = " · ".join(l["meta"][:3])
    return f"{l['id']} · {l['title']} · {meta}{st} · {len(l['photos'])} фото"


# ──────────────── вкладки и фиды ────────────────

def _feed(key, platform, title, url, path, fmt, note="", filt=None, source_url=None):
    return {"key": key, "platform": platform, "title": title, "url": url, "path": path,
            "fmt": fmt, "note": note, "filter": filt, "source_url": source_url}


def tabs() -> list:
    return [
        {"key": "zorge9", "title": "Зорге 9", "kind": "project", "slug": "zorge9",
         "sub": "апартаменты · ЦИАН, Авито, Яндекс, ДомКлик"},
        {"key": "zorge9-comm", "title": "Зорге 9 · коммерция", "kind": "comm",
         "sub": "коммерческие помещения и машиноместа"},
        {"key": "b37", "title": "Серебряный бор", "kind": "project", "slug": "b37",
         "sub": "квартиры · ЦИАН, Авито, Яндекс, ДомКлик"},
        {"key": "b37-comm", "title": "Серебряный бор · коммерция", "kind": "comm",
         "sub": "коммерческие помещения (аренда)"},
        {"key": "sites", "title": "Сайты", "kind": "sites",
         "sub": "stmichael.ru и сайт Зорге 9"},
    ]


def tab_feeds(key: str) -> list:
    if key in ("zorge9", "b37"):
        d = project_dirs(key)["feeds"]
        f = [
            _feed(f"{key}-cian", "cian", "ЦИАН", f"/feed/{key}.xml", d / "feed.xml", "cian"),
            _feed(f"{key}-avito", "avito", "Авито", f"/feed/{key}-avito.xml", d / "avito.xml", "avito"),
            _feed(f"{key}-yandex", "yandex", "Яндекс.Недвижимость (старый формат)",
                  f"/feed/{key}-yandex.xml", d / "yandex.xml", "yandex"),
            _feed(f"{key}-yr", "yandex_realty", "Яндекс Поиск (новый формат)",
                  f"/feed/{key}-yandex-realty.xml", d / "yandex_realty.xml", "yandex"),
            _feed(f"{key}-domclick", "domclick", "ДомКлик", f"/feed/{key}-domclick.xml",
                  d / "domclick.xml", "domclick", note="ДомКлик получает только планировку — фото в этот фид не входят"),
            _feed("yr-combined", "yandex_realty", "Яндекс Поиск — общий (Зорге + Б37)", "/feed/yandex-realty.xml",
                  CACHE_DIR / "combined" / "yandex_realty.xml", "yandex",
                  note="один файл на оба проекта — его и отдаём Яндексу"),
        ]
        if key == "zorge9":
            from . import zorge_soho_avito
            f.append(_feed("zorge9-soho", "avito", "Авито — вторичка Soho (корпус 3)",
                           "/feed/zorge9-soho-avito.xml", zorge_soho_avito.OUT, "avito",
                           note="15 лотов вторички, свои фото (интерьер по лоту + общие)"))
        return f
    if key == "zorge9-comm":
        from . import comm_zorge_cian, comm_zorge_classified as czcls, comm_avito
        return [
            _feed("comm-zorge-cian", "cian", "ЦИАН — коммерция Зорге", "/feed/comm/zorge-cian.xml",
                  comm_zorge_cian.OUT, "cian"),
            _feed("comm-zorge-cls-cian", "cian", "ЦИАН — классифайд (свои тексты и порядок)",
                  "/feed/comm/zorge-cls-cian.xml", czcls.OUT_CIAN, "cian",
                  note="9 аренда → 6 продажа → 2 машиноместа → 5 лотов Б37"),
            _feed("comm-zorge-cls-avito", "avito", "Авито — классифайд (свои тексты и порядок)",
                  "/feed/comm/zorge-cls-avito.xml", czcls.OUT_AVITO, "avito"),
            _feed("comm-avito", "avito", "Авито — общий коммерции (Зорге + Б37)", "/feed/comm/avito.xml",
                  comm_avito.OUT, "avito", note="общий для обоих проектов"),
        ]
    if key == "b37-comm":
        from . import comm_cian_rent, comm_avito
        return [
            _feed("comm-b37-cian", "cian", "ЦИАН — аренда Б37", "/feed/comm/b37-rent-cian.xml",
                  comm_cian_rent.OUT, "cian"),
            _feed("comm-avito-b37", "avito", "Авито — общий коммерции (Зорге + Б37)", "/feed/comm/avito.xml",
                  comm_avito.OUT, "avito", note="общий для обоих проектов"),
        ]
    if key == "sites":
        from . import site_feed
        return [
            _feed("site-stm", "site", "stmichael.ru — квартиры и апартаменты (общий фид)",
                  "/feed/profitbase-site.xml", site_feed.OUT, "site",
                  note="profitbase_xml из ProfitBase + наши фото по лотам после планировок"),
            _feed("site-zorge", "site", "Сайт Зорге 9 (напрямую из ProfitBase, для сравнения)",
                  "", site_feed.ORIG, "site", filt="zorge9",
                  note="в сайт Зорге 9 данные идут прямо из ProfitBase, мимо нас; здесь — тот же выгруженный "
                       "profitbase_xml по лотам Зорге без наших фото",
                  source_url=PROJECTS["zorge9"].get("euro_source_url")),
        ]
    return []


def feed_info(feed: dict) -> dict:
    lots = load_lots(feed)
    p = feed["path"]
    try:
        upd = time.strftime("%d.%m %H:%M", time.localtime(p.stat().st_mtime))
    except OSError:
        upd = ""
    return {"count": len(lots), "photos": sum(len(l["photos"]) for l in lots), "updated": upd,
            "public": (PUBLIC_BASE_URL + feed["url"]) if feed["url"] else ""}


# ──────────────── источники фото (справка) ────────────────

def photo_sources(key: str) -> list:
    """[(что, ссылка, папка, куда идёт)] — откуда берём фото для вкладки (только справка)."""
    rows = []
    if key in ("zorge9", "b37"):
        p = PROJECTS[key]
        for s in p.get("views_sources", []):
            rows.append(("Виды / интерьер / визуализация по лотам", s["public_key"], "",
                         "ЦИАН, Авито, Яндекс, сайт"))
        for cfg_key, where in (("cian_extra_photos", "ЦИАН"), ("avito_extra_photos", "Авито"),
                               ("yandex_extra_photos", "Яндекс (обе версии)")):
            cfg = p.get(cfg_key)
            if cfg:
                rows.append(("Общий набор фото проекта", cfg["yadisk_public_key"],
                             cfg.get("yadisk_path", ""), where))
        if key == "zorge9":
            from . import zorge_soho_avito as sh
            rows.append(("Вторичка Soho: интерьер по усл. номеру", sh.INTERIOR_YD, "", "Авито Soho"))
            rows.append(("Вторичка Soho: общие фото", sh.COMMON_YD, "", "Авито Soho"))
    return rows


# ──────────────── статус и здоровье ────────────────

def status() -> dict:
    import json
    try:
        return json.loads((ADMIN_DIR / "status.json").read_text("utf-8"))
    except Exception:
        return {}


def feed_health(slug: str) -> dict:
    """Новые корпуса без привязки к Яндексу (house-id обязателен для Яндекс Поиска)."""
    out = {"ok": True, "missing": []}
    proj = get_project(slug)
    if not proj.get("yandex_building_id"):
        return out
    src = project_dirs(slug)["feeds"] / "original.xml"
    if not src.exists():
        return out
    from .parser import parse_feed
    from .assembler_yandex import _korpus_no
    house_ids = proj.get("yandex_house_ids", {}) or {}
    miss: dict = {}
    for l in parse_feed(src.read_bytes()):
        if not (l.price and l.area_total):
            continue
        if not house_ids.get(_korpus_no(l.house_name)):
            k = l.house_name or "(корпус не указан)"
            miss[k] = miss.get(k, 0) + 1
    out["missing"] = [{"house": h, "lots": c} for h, c in sorted(miss.items())]
    out["ok"] = not out["missing"]
    return out


def pick_lot(lots: list, lot_id: str = "") -> dict:
    """Выбранный лот; по умолчанию — с самой длинной галереей (видно всё, что мы добавляем)."""
    if not lots:
        return {}
    if lot_id:
        for l in lots:
            if l["id"] == lot_id:
                return l
    return max(lots, key=lambda l: (len(l["photos"]), l["status"] == "AVAILABLE"))


def lot_options(lots: list, feed_platform: str, limit: int = 400) -> list:
    """Список лотов для выбора: сначала «в продаже», затем с большим числом фото."""
    order = sorted(lots, key=lambda l: (l["status"] not in ("", "AVAILABLE"), -len(l["photos"])))
    return order[:limit]
