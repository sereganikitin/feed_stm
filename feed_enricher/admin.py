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

from flask import (Blueprint, abort, flash, redirect, render_template_string,
                   request, session, url_for)
from werkzeug.utils import secure_filename

from .config import (PROJECTS, PUBLIC_BASE_URL, ADMIN_DIR, project_dirs,
                     get_project, set_override, load_overrides)
from .yadisk import save_resized_jpeg, sync_public_folder
from .assembler_avito import enrich_pb_avito_feed

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
    try:
        return len(list(ET.parse(path).getroot().iter(tag)))
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


def _photos(slug: str) -> list[str]:
    """Имена фото в /extra в порядке из настроек (новые — в конец)."""
    files = {p.name for p in project_dirs(slug)["extra"].glob("*.jpg")}
    order = [n for n in (get_project(slug).get("extra_photo_order") or []) if n in files]
    rest = sorted(files - set(order))
    return order + rest


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
            "photos": len(_photos(slug)),
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
    return render_template_string(
        _PROJ_HTML, slug=slug, proj=proj, base=PUBLIC_BASE_URL,
        photos=_photos(slug), check=check,
        decor=_DECOR, house=_HOUSE,
        has_installment=isinstance(PROJECTS[slug].get("installment"), dict),
        status=_status().get(slug, {}),
    )


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
    # рассрочка (если у проекта она есть)
    if isinstance(PROJECTS[slug].get("installment"), dict):
        try:
            set_override(slug, "installment", {
                "feed_to_base_divisor": float(f.get("inst_div", "0.8")),
                "down_payment_pct": float(f.get("inst_pv", "0.10")),
                "monthly_pct_of_base": float(f.get("inst_m", "0.005")),
            })
        except ValueError:
            flash("Рассрочка: ожидались числа — не сохранено")
    flash("Настройки сохранены. Нажмите «Обновить фид», чтобы применить.")
    return redirect(url_for("admin.project", slug=slug))


@admin_bp.route("/<slug>/photos/upload", methods=["POST"])
@login_required
def upload_photos(slug: str):
    if slug not in PROJECTS:
        abort(404)
    extra = project_dirs(slug)["extra"]
    order = list(get_project(slug).get("extra_photo_order") or [n for n in _photos(slug)])
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
    set_override(slug, "extra_photo_order", order)
    _rebuild_avito(slug)
    flash(f"Загружено фото: {added}.")
    return redirect(url_for("admin.project", slug=slug))


@admin_bp.route("/<slug>/photos/delete", methods=["POST"])
@login_required
def delete_photo(slug: str):
    if slug not in PROJECTS:
        abort(404)
    name = secure_filename(request.form.get("name", ""))
    p = project_dirs(slug)["extra"] / name
    if p.exists():
        p.unlink()
    order = [n for n in (get_project(slug).get("extra_photo_order") or []) if n != name]
    set_override(slug, "extra_photo_order", order)
    _rebuild_avito(slug)
    flash(f"Удалено: {name}.")
    return redirect(url_for("admin.project", slug=slug))


@admin_bp.route("/<slug>/photos/order", methods=["POST"])
@login_required
def reorder_photos(slug: str):
    if slug not in PROJECTS:
        abort(404)
    order = [secure_filename(n) for n in request.form.getlist("order") if n.strip()]
    set_override(slug, "extra_photo_order", order)
    _rebuild_avito(slug)
    return ("", 204)


@admin_bp.route("/<slug>/photos/sync_yd", methods=["POST"])
@login_required
def sync_yd(slug: str):
    if slug not in PROJECTS:
        abort(404)
    cfg = PROJECTS[slug].get("avito_extra_photos") or {}
    if not cfg.get("yadisk_public_key"):
        flash("Для этого фида не задана папка Яндекс.Диска")
        return redirect(url_for("admin.project", slug=slug))
    try:
        n = len(sync_public_folder(cfg["yadisk_public_key"], cfg["yadisk_path"],
                                   project_dirs(slug)["extra"]))
        flash(f"С Яндекс.Диска синхронизировано фото: {n}. Нажмите «Обновить фид».")
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
        flash(f"Фид обновлён: {res.get('enriched_ok', 0)} планировок, "
              f"объявлений в Авито — {_avito_check(slug)['ads']}.")
    except Exception as e:
        flash(f"Ошибка обновления: {e}")
    return redirect(url_for("admin.project", slug=slug))


# ──────────────── шаблоны ────────────────

_CSS = """
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:1000px;margin:0 auto;padding:20px;color:#1c2430;background:#f4f6f9}
 a{color:#2563eb;text-decoration:none} a:hover{text-decoration:underline}
 h1{font-size:22px;margin:0 0 16px} h2{font-size:17px;margin:22px 0 10px}
 .card{background:#fff;border-radius:10px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.07);margin-bottom:16px}
 .btn{display:inline-block;background:#2563eb;color:#fff;border:0;border-radius:7px;padding:8px 14px;cursor:pointer;font-size:14px}
 .btn.gray{background:#6b7280}.btn.red{background:#dc2626}.btn.green{background:#16a34a}
 label{display:block;font-size:13px;color:#555;margin:10px 0 4px}
 input[type=text],select,textarea{width:100%;box-sizing:border-box;padding:8px;border:1px solid #cbd5e1;border-radius:6px;font-size:14px}
 table{border-collapse:collapse;width:100%}.td td,td,th{padding:8px;border-bottom:1px solid #eef1f5;text-align:left;font-size:14px}
 .flash{background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:10px 14px;margin-bottom:14px}
 .pill{display:inline-block;background:#e8f0fe;color:#1a56db;border-radius:20px;padding:2px 10px;font-size:12px}
 .ok{color:#16a34a;font-weight:600}.bad{color:#dc2626;font-weight:600}
 .ph{display:inline-block;margin:4px;vertical-align:top;text-align:center;font-size:11px;color:#666}
 .ph img{width:120px;height:90px;object-fit:cover;border-radius:6px;border:1px solid #ddd;display:block}
 .row{display:flex;gap:16px;flex-wrap:wrap}.col{flex:1;min-width:240px}
 .muted{color:#94a3b8;font-size:13px}
 .strip{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px}
 .tile{position:relative;width:140px;height:104px;border-radius:8px;overflow:hidden;border:1px solid #d7dee7;background:#fff;cursor:grab}
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

_FLASH = "{% with m=get_flashed_messages() %}{% if m %}<div class=flash>{{m|join('<br>')|safe}}</div>{% endif %}{% endwith %}"

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
  <tr><th>Проект</th><th>ЦИАН</th><th>Авито</th><th>Фото Авито</th><th>Последнее обновление</th><th></th></tr>
  {% for r in rows %}
  <tr>
   <td><b>{{r.name}}</b><div class=muted>{{r.slug}}</div></td>
   <td>{{r.cian}} <div class=muted><a href="{{base}}/feed/{{r.slug}}.xml" target=_blank>xml</a></div></td>
   <td>{{r.avito}} <div class=muted><a href="{{base}}/feed/{{r.slug}}-avito.xml" target=_blank>xml</a></div></td>
   <td>{{r.photos}}</td>
   <td class=muted>{% if r.status.get('ts') %}{{r.status.ts}}<br>планировок: {{r.status.get('enriched_ok','?')}}{% else %}—{% endif %}</td>
   <td><a class=btn href="{{url_for('admin.project',slug=r.slug)}}">Открыть</a></td>
  </tr>
  {% endfor %}
 </table>
</div>
<p class=muted><a href="{{url_for('admin.logout')}}">Выйти</a></p>"""

_PROJ_HTML = _CSS + """<title>{{proj.name}}</title>
<p><a href="{{url_for('admin.dashboard')}}">← все фиды</a></p>
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

<div class=card>
 <h2 style="margin-top:0">Фото карточки Авито</h2>
 <p class=muted>Так будет выглядеть карточка. Перетаскивайте наши фото мышкой — меняется порядок;
   наведите и нажмите <b>✕</b> — удалить; плитка <b>＋</b> — загрузить новые. Изменения применяются сразу.
   Первой всегда идёт наша планировка{% if check.plan %} (пунктирная плитка, её не трогаем){% endif %}.</p>
 {% if check.first %}<p class=muted>Пример (лот {{check.first.id}}): {{check.first.rooms}} · {{check.first.square}} м² · {{check.first.price}} ₽</p>{% endif %}
 <div class=strip id=strip>
   {% if check.plan %}<div class="tile locked"><img src="{{check.plan}}"><span class=lbl>планировка</span></div>{% endif %}
   {% for n in photos %}
   <div class=tile draggable=true data-name="{{n}}">
     <img src="{{base}}/extra/{{slug}}/{{n}}" loading=lazy>
     <button class=del title="Удалить">✕</button>
     <span class=num>{{loop.index}}</span>
   </div>
   {% endfor %}
   <label class="tile add" title="Загрузить фото">＋<input type=file id=up accept="image/*" multiple hidden></label>
 </div>
 <div id=busy class=muted style="display:none;margin-top:10px">Сохраняю, обновляю фид…</div>
 <form method=post action="{{url_for('admin.sync_yd',slug=slug)}}" style="margin-top:12px">
  <button class="btn gray">Синхронизировать с Яндекс.Диска</button>
 </form>
</div>
<p class=muted><a href="{{url_for('admin.logout')}}">Выйти</a></p>
<script>
(function(){
 var strip=document.getElementById('strip'); if(!strip) return;
 var busy=document.getElementById('busy');
 function post(url,body){ busy.style.display='block';
   fetch(url,{method:'POST',body:body,credentials:'same-origin'}).then(function(){location.reload();})
   .catch(function(){busy.textContent='Ошибка, попробуйте ещё раз';}); }
 strip.addEventListener('click',function(e){
   var b=e.target.closest('.del'); if(!b) return;
   var tile=b.closest('.tile'); if(!confirm('Удалить это фото?')) return;
   var fd=new FormData(); fd.append('name',tile.dataset.name);
   post('{{url_for("admin.delete_photo",slug=slug)}}',fd); });
 var up=document.getElementById('up');
 if(up) up.addEventListener('change',function(){
   if(!up.files.length) return; var fd=new FormData();
   for(var i=0;i<up.files.length;i++) fd.append('photos',up.files[i]);
   post('{{url_for("admin.upload_photos",slug=slug)}}',fd); });
 var drag=null;
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
   post('{{url_for("admin.reorder_photos",slug=slug)}}',fd); });
})();
</script>"""
