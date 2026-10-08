"""Информационная админ-панель /admin: вкладки по проектам и сайтам.

Вкладки: Зорге 9 · Зорге 9 коммерция · Серебряный бор · Серебряный бор коммерция · Сайты.
В каждой — ссылки на фиды для площадок, превью галереи карточки «как на площадке» и справка,
откуда берутся фото. Ничего не редактируется (кроме кнопки «Обновить фиды» — пересборка).

Доступ — общий пароль (env ADMIN_PASSWORD), сессия в cookie.
"""
import hmac
import os
import threading
from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template_string,
                   request, session, url_for)

from .config import PUBLIC_BASE_URL, PROJECTS
from . import admin_data as ad

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


# ──────────────── авторизация ────────────────

def _check_password(pw: str) -> bool:
    return bool(_ADMIN_PASSWORD) and hmac.compare_digest(pw, _ADMIN_PASSWORD)


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


# ──────────────── страницы ────────────────

@admin_bp.route("/")
@login_required
def dashboard():
    return redirect(url_for("admin.tab_page", tab=ad.tabs()[0]["key"]))


@admin_bp.route("/<tab>")
@login_required
def tab_page(tab: str):
    tabs = ad.tabs()
    cur = next((t for t in tabs if t["key"] == tab), None)
    if cur is None:
        abort(404)
    feeds = ad.tab_feeds(tab)
    infos = {f["key"]: ad.feed_info(f) for f in feeds}
    sel = next((f for f in feeds if f["key"] == request.args.get("feed")), feeds[0] if feeds else None)

    lots, lot, photos, opts = [], {}, [], []
    plat = {}
    if sel:
        plat = ad.PLATFORMS[sel["platform"]]
        lots = ad.load_lots(sel)
        lot = ad.pick_lot(lots, request.args.get("lot", ""))
        opts = ad.lot_options(lots, sel["platform"])
        if lot and all(o["id"] != lot["id"] for o in opts):
            opts = [lot] + opts
        photos = _gallery(lot, plat)

    st = ad.status().get(cur.get("slug", ""), {}) if cur["kind"] == "project" else {}
    health = ad.feed_health(cur["slug"]) if cur["kind"] == "project" else {"ok": True, "missing": []}
    idx = next((i for i, o in enumerate(opts) if lot and o["id"] == lot["id"]), 0)
    return render_template_string(
        _TAB_HTML, tabs=tabs, cur=cur, feeds=feeds, infos=infos, sel=sel, plat=plat,
        lot=lot, photos=photos, opts=opts, lot_label=ad.lot_label,
        prev_id=opts[idx - 1]["id"] if opts and idx > 0 else "",
        next_id=opts[idx + 1]["id"] if opts and idx + 1 < len(opts) else "",
        sources=ad.photo_sources(cur["key"]), health=health, st=st, kinds=ad.KINDS,
        base=PUBLIC_BASE_URL, running=cur["key"] in _running)


def _gallery(lot: dict, plat: dict) -> list:
    """Фото лота для превью: подпись, цвет, «не попадёт из-за лимита», дубль ссылки."""
    if not lot:
        return []
    limit = plat.get("limit")
    urls = [p["u"] for p in lot["photos"]]
    out = []
    for i, p in enumerate(lot["photos"]):
        label, color = ad.KINDS[p["k"]]
        out.append({"u": p["u"], "k": p["k"], "l": label, "c": color,
                    "over": bool(limit and i >= limit), "dup": urls.count(p["u"]) > 1})
    return out


# ──────────────── пересборка (единственное действие) ────────────────

_running: dict = {}


def _rebuild_job(tab: str):
    def run():
        try:
            if tab in PROJECTS:
                from .server import refresh_project
                refresh_project(tab)
                from . import site_feed
                site_feed.refresh(reuse_original=True)
            elif tab == "zorge9-comm":
                from . import comm_zorge_cian, comm_avito, comm_zorge_classified
                comm_zorge_cian.refresh()
                comm_avito.refresh()
                comm_zorge_classified.build()
            elif tab == "b37-comm":
                from . import comm_cian_rent, comm_avito
                comm_cian_rent.refresh()
                comm_avito.refresh()
            elif tab == "sites":
                from . import site_feed
                site_feed.refresh()
        except Exception as e:
            print(f"[admin] rebuild {tab} failed: {e}")
        finally:
            _running.pop(tab, None)
    return run


@admin_bp.route("/<tab>/refresh", methods=["POST"])
@login_required
def refresh(tab: str):
    if all(t["key"] != tab for t in ad.tabs()):
        abort(404)
    if tab in _running:
        flash("Пересборка уже идёт — подождите несколько минут.")
    else:
        _running[tab] = True
        threading.Thread(target=_rebuild_job(tab), daemon=True).start()
        flash("Пересборка запущена в фоне, обычно занимает несколько минут. Обновите страницу позже.")
    return redirect(url_for("admin.tab_page", tab=tab))


# ──────────────── шаблоны ────────────────

_CSS = """
<style>
 :root{--bg:#f4f6f9;--card:#fff;--line:#e6ebf2;--txt:#1c2430;--mut:#64748b;--acc:#2563eb}
 *{box-sizing:border-box}
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:var(--bg);color:var(--txt)}
 a{color:var(--acc);text-decoration:none} a:hover{text-decoration:underline}
 .top{background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:20}
 .top-in{max-width:1400px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;gap:20px;flex-wrap:wrap}
 .brand{font-weight:800;font-size:17px;white-space:nowrap}
 .tabs{display:flex;gap:6px;flex-wrap:wrap;flex:1}
 .tab{padding:8px 14px;border-radius:9px;color:#33415a;font-size:14px;font-weight:600;white-space:nowrap;background:#eef2f7}
 .tab:hover{background:#e2e8f1;text-decoration:none}
 .tab.on{background:var(--acc);color:#fff}
 .out{font-size:13px;color:var(--mut)}
 main{max-width:1400px;margin:0 auto;padding:22px 24px 60px}
 h1{font-size:26px;margin:0} h2{font-size:19px;margin:30px 0 12px}
 .muted{color:var(--mut);font-size:13.5px}
 .hero{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
 .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
 .chip{background:#eaf1ff;color:#1e40af;border-radius:20px;padding:3px 11px;font-size:12.5px;font-weight:600}
 .btn{display:inline-block;background:var(--acc);color:#fff;border:0;border-radius:8px;padding:9px 16px;cursor:pointer;font-size:14px;font-weight:600}
 .btn.gray{background:#eef2f7;color:#33415a} .btn.sm{padding:6px 11px;font-size:13px}
 .btn:disabled{opacity:.5;cursor:default}
 .flash{background:#fef9c3;border:1px solid #fde047;border-radius:9px;padding:11px 15px;margin:16px 0}
 .warn{background:#fff4e5;border:1px solid #ffd8a8;color:#9a3412;border-radius:9px;padding:11px 15px;margin:14px 0;font-size:14px;line-height:1.5}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 22px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
 .feeds{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
 .feed{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--c);border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;gap:6px}
 .feed.sel{box-shadow:0 0 0 2px var(--c)}
 .feed .t{font-weight:700;font-size:14.5px;min-height:38px}
 .feed .n{font-size:28px;font-weight:800;line-height:1}
 .feed .n small{font-size:13px;color:var(--mut);font-weight:500}
 .feed .note{font-size:12.5px;color:var(--mut);line-height:1.4}
 .feed .acts{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
 .pills{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
 .pill{padding:7px 13px;border-radius:9px;border:1px solid var(--line);background:#fff;color:#33415a;font-size:13.5px;font-weight:600}
 .pill.on{background:var(--pc);border-color:var(--pc);color:#fff;text-decoration:none}
 form.pick{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
 form.pick select{flex:1;min-width:260px;max-width:760px;padding:9px 10px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px}
 table{border-collapse:collapse;width:100%} td,th{padding:9px 10px;border-bottom:1px solid #eef1f5;text-align:left;font-size:14px;vertical-align:top}
 th{color:var(--mut);font-weight:600;font-size:12.5px}
 /* ── галерея ── */
 .gwrap{max-width:860px}
 .stage{position:relative;background:#14181f;border-radius:14px;overflow:hidden;aspect-ratio:16/10}
 .stage img{width:100%;height:100%;object-fit:contain;display:block;background:#14181f}
 .stage .kind{position:absolute;left:12px;top:12px;color:#fff;font-size:12px;font-weight:700;padding:3px 10px;border-radius:14px;background:var(--kc,#475569)}
 .stage .cnt{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);background:rgba(20,24,31,.82);color:#fff;font-weight:700;font-size:15px;padding:8px 16px;border-radius:12px;white-space:nowrap}
 .avito .stage .cnt{left:auto;right:14px;transform:none;font-size:13px;padding:5px 11px;border-radius:8px}
 .stage .nav{position:absolute;top:50%;transform:translateY(-50%);width:42px;height:42px;border-radius:50%;border:0;background:rgba(255,255,255,.88);font-size:22px;cursor:pointer}
 .stage .prev{left:12px}.stage .next{right:12px}
 .stage .over{position:absolute;inset:0;background:rgba(255,255,255,.55);display:none;align-items:center;justify-content:center;font-weight:800;color:#b91c1c;font-size:18px;text-align:center;padding:20px}
 .thumbs{display:flex;gap:8px;overflow-x:auto;padding:10px 2px 6px}
 .th{flex:0 0 auto;width:96px;height:68px;border-radius:9px;overflow:hidden;border:3px solid transparent;cursor:pointer;background:#e5e9ef;position:relative}
 .avito .th{width:76px;height:76px;border-radius:7px}
 .th img{width:100%;height:100%;object-fit:cover;display:block}
 .th.on{border-color:var(--pc)} .th.over{opacity:.35}
 .th .d{position:absolute;right:3px;top:3px;background:#dc2626;color:#fff;font-size:10px;padding:0 5px;border-radius:6px}
 .mosaic{display:grid;grid-template-columns:2fr 1fr 1fr;grid-template-rows:170px 170px;gap:6px;border-radius:14px;overflow:hidden}
 .mosaic .m{position:relative;background:#14181f;cursor:pointer;overflow:hidden}
 .mosaic .m img{width:100%;height:100%;object-fit:cover;display:block}
 .mosaic .m.big{grid-row:span 2}
 .mosaic .m .more{position:absolute;inset:0;background:rgba(20,24,31,.6);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:16px}
 .mosaic .m .k{position:absolute;left:8px;top:8px;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:12px;background:var(--kc)}
 .cap{color:var(--mut);font-size:13.5px;margin:8px 2px}
 .order{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px;margin-top:6px}
 .oi{border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff;font-size:11.5px}
 .oi img{width:100%;height:80px;object-fit:cover;display:block}
 .oi .b{padding:4px 6px;border-top:3px solid var(--kc)} .oi.over{opacity:.4}
 .oi .i{font-weight:800}
 .legend{display:flex;gap:10px;flex-wrap:wrap;font-size:12.5px;color:#475569;margin:8px 0}
 .legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;background:var(--kc)}
</style>
"""

_LOGIN_HTML = _CSS + """<title>Вход — фиды</title>
<main style="max-width:380px;padding-top:80px"><h1>Панель управления фидами</h1>
{% with m=get_flashed_messages() %}{% for x in m %}<div class=flash>{{x}}</div>{% endfor %}{% endwith %}
<div class=card style="margin-top:18px"><form method=post>
 <label class=muted>Пароль</label><br>
 <input type=password name=password autofocus style="width:100%;padding:9px;margin:6px 0 14px;border:1px solid #cbd5e1;border-radius:8px">
 <button class=btn>Войти</button></form></div></main>"""

_TAB_HTML = _CSS + """<title>{{cur.title}} — фиды</title>
<div class=top><div class=top-in>
 <div class=brand>Фиды · St Michael</div>
 <nav class=tabs>{% for t in tabs %}<a class="tab{{' on' if t.key==cur.key else ''}}" href="{{url_for('admin.tab_page',tab=t.key)}}">{{t.title}}</a>{% endfor %}</nav>
 <a class=out href="{{url_for('admin.logout')}}">Выйти</a>
</div></div>
<main>
{% with m=get_flashed_messages() %}{% for x in m %}<div class=flash>{{x}}</div>{% endfor %}{% endwith %}

<div class=hero>
 <div>
  <h1>{{cur.title}}</h1>
  <div class=muted>{{cur.sub}}</div>
  <div class=chips>
   {% if st.get('ts') %}<span class=chip>обновлён {{st.ts}}</span>{% endif %}
   {% if st.get('lots_total') %}<span class=chip>лотов в выгрузке: {{st.lots_total}}</span>{% endif %}
   {% if st.get('enriched_ok') %}<span class=chip>планировок нарисовано: {{st.enriched_ok}}</span>{% endif %}
   <span class=chip style="background:#eef2f7;color:#475569">только просмотр — правки делаются на Яндекс.Диске и в ProfitBase</span>
  </div>
 </div>
 <form method=post action="{{url_for('admin.refresh',tab=cur.key)}}"><button class=btn{{' disabled' if running else ''}}>{{'⏳ идёт пересборка…' if running else '↻ Обновить фиды'}}</button></form>
</div>

{% if not health.ok %}
<div class=warn>⚠️ <b>Требует настройки.</b> Новый корпус без привязки к Яндексу:
 {% for m in health.missing %}<b>{{m.house}}</b> ({{m.lots}} лот.){% if not loop.last %}, {% endif %}{% endfor %}.
 Нужно добавить id корпуса в <code>yandex_house_ids</code> — иначе Яндекс Поиск отклонит эти лоты.</div>
{% endif %}

<h2>Фиды для площадок</h2>
<div class=feeds>
{% for f in feeds %}{% set i=infos[f.key] %}
 <div class="feed{{' sel' if sel and sel.key==f.key else ''}}" style="--c:{{ {'cian':'#2563eb','avito':'#16a34a','yandex':'#fc3f1d','yandex_realty':'#f59e0b','domclick':'#0d9488','site':'#7c3aed'}[f.platform] }}">
  <div class=t>{{f.title}}</div>
  <div class=n>{{i.count or '—'}} <small>лотов{% if i.photos %} · {{i.photos}} фото{% endif %}</small></div>
  {% if i.updated %}<div class=muted>обновлён {{i.updated}}</div>{% endif %}
  {% if f.note %}<div class=note>{{f.note}}</div>{% endif %}
  <div class=acts>
   {% if i.public %}<a class="btn sm" href="{{i.public}}" target=_blank>XML ↗</a>
   <button class="btn gray sm" type=button onclick="navigator.clipboard.writeText('{{i.public}}');this.textContent='скопировано ✓'">копировать ссылку</button>{% endif %}
   <a class="btn gray sm" href="{{url_for('admin.tab_page',tab=cur.key,feed=f.key)}}#preview">👁 превью</a>
  </div>
  {% if f.source_url %}<div class=note>Источник: <a href="{{f.source_url}}" target=_blank>ProfitBase ↗</a></div>{% endif %}
 </div>
{% endfor %}
</div>

{% if sel %}
<h2 id=preview>Превью галереи «как на площадке»</h2>
<div class=card style="--pc:{{plat.color}}">
 <div class=pills>
  {% for f in feeds %}<a class="pill{{' on' if f.key==sel.key else ''}}" style="--pc:{{ {'cian':'#2563eb','avito':'#16a34a','yandex':'#fc3f1d','yandex_realty':'#f59e0b','domclick':'#0d9488','site':'#7c3aed'}[f.platform] }}" href="{{url_for('admin.tab_page',tab=cur.key,feed=f.key)}}#preview">{{f.title}}</a>{% endfor %}
 </div>
 {% if not opts %}<div class=muted>В этом фиде пока нет лотов.</div>{% else %}
 <form class=pick method=get action="#preview">
  <input type=hidden name=feed value="{{sel.key}}">
  <a class="btn gray sm" href="{{url_for('admin.tab_page',tab=cur.key,feed=sel.key,lot=prev_id)}}#preview" {{'style=visibility:hidden' if not prev_id}}>←</a>
  <select name=lot onchange="this.form.submit()">
   {% for o in opts %}<option value="{{o.id}}"{{' selected' if o.id==lot.id else ''}}>{{lot_label(o)}}</option>{% endfor %}
  </select>
  <a class="btn gray sm" href="{{url_for('admin.tab_page',tab=cur.key,feed=sel.key,lot=next_id)}}#preview" {{'style=visibility:hidden' if not next_id}}>→</a>
 </form>
 <div class=muted style="margin-bottom:10px">{{sel.title}} · лот <b>{{lot.id}}</b> · {{lot.title}}{% if lot.meta %} · {{lot.meta|join(' · ')}}{% endif %}</div>

 {% if plat.limit and photos|length > plat.limit %}
 <div class=warn>Всего фото у лота: {{photos|length}}. {{plat.name}} берёт первые <b>{{plat.limit}}</b> — остальные ({{photos|length - plat.limit}}) в карточку не попадут (на превью бледные).</div>
 {% endif %}
 {% if photos|selectattr('dup')|list %}<div class=warn>В галерее есть повторяющиеся ссылки на одно и то же фото (помечены «дубль»).</div>{% endif %}
 {% if not photos %}<div class=muted>У этого лота нет фото в фиде.</div>{% else %}

 <div class="gwrap {{plat.layout if plat.layout=='avito' else ''}}" id=gal>
  {% if plat.layout=='mosaic' %}
   <div class=mosaic id=mosaic></div>
   <div class=cap>Так Яндекс показывает начало галереи: одно крупное фото и мозаика из следующих. Остальные — по кнопке «Все {{photos|length}} фото».</div>
   <button class="btn gray sm" type=button onclick="document.getElementById('full').style.display='block';this.style.display='none'">Все {{photos|length}} фото</button>
   <div id=full style="display:none;margin-top:10px">
  {% endif %}
  <div class=stage id=stage>
   <img id=main alt="">
   <span class=kind id=kind></span>
   <span class=cnt id=cnt></span>
   <button class="nav prev" type=button onclick="go(-1)">‹</button><button class="nav next" type=button onclick="go(1)">›</button>
   <div class=over id=overmsg>Не попадёт в карточку:<br>лимит {{plat.name}} — {{plat.limit}} фото</div>
  </div>
  <div class=thumbs id=thumbs></div>
  {% if plat.layout=='mosaic' %}</div>{% endif %}
 </div>
 <div class=cap id=cap></div>

 <div class=legend>{% for k,(lbl,col) in kinds.items() %}{% if photos|selectattr('k','equalto',k)|list %}<span style="--kc:{{col}}"><i></i>{{lbl}} × {{photos|selectattr('k','equalto',k)|list|length}}</span>{% endif %}{% endfor %}</div>
 <h3 style="margin:18px 0 6px;font-size:15px">Порядок фото в фиде</h3>
 <div class=order>
  {% for p in photos %}<a class="oi{{' over' if p.over else ''}}" style="--kc:{{p.c}}" href="{{p.u}}" target=_blank title="{{p.u}}">
   <img src="{{p.u}}" loading=lazy alt=""><div class=b><span class=i>{{loop.index}}</span> {{p.l}}{{' · дубль' if p.dup else ''}}</div></a>{% endfor %}
 </div>
 <script>
  const P={{photos|tojson}}, LAYOUT={{plat.layout|tojson}}, NAME={{plat.name|tojson}}, LIMIT={{(plat.limit or 0)|tojson}};
  let i=0;
  const $=id=>document.getElementById(id);
  function show(n){
    i=(n+P.length)%P.length; const p=P[i];
    $('main').src=p.u; $('kind').textContent=p.l; $('kind').style.setProperty('--kc',p.c);
    $('cnt').textContent=(LAYOUT==='avito')?(i+1)+' / '+P.length:P.length+' фото';
    $('overmsg').style.display=p.over?'flex':'none';
    $('cap').textContent='Фото '+(i+1)+' из '+P.length+' · '+p.l;
    document.querySelectorAll('#thumbs .th').forEach((t,k)=>t.classList.toggle('on',k===i));
    const t=document.querySelector('#thumbs .th.on'); if(t) t.scrollIntoView({block:'nearest',inline:'center'});
  }
  function go(d){show(i+d)}
  P.forEach((p,k)=>{
    const d=document.createElement('div'); d.className='th'+(p.over?' over':'');
    d.innerHTML='<img loading=lazy src="'+p.u+'">'+(p.dup?'<span class=d>дубль</span>':'');
    d.onclick=()=>show(k); $('thumbs').appendChild(d);
  });
  if(LAYOUT==='mosaic'){
    const m=$('mosaic'), n=Math.min(5,P.length);
    for(let k=0;k<n;k++){
      const c=document.createElement('div'); c.className='m'+(k===0?' big':'');
      c.innerHTML='<img src="'+P[k].u+'"><span class=k style="--kc:'+P[k].c+'">'+P[k].l+'</span>'+
        ((k===n-1&&P.length>n)?'<div class=more>+'+(P.length-n)+' фото</div>':'');
      c.onclick=()=>{$('full').style.display='block';show(k)}; m.appendChild(c);
    }
  }
  show(0);
  document.addEventListener('keydown',e=>{if(e.key==='ArrowLeft')go(-1);if(e.key==='ArrowRight')go(1)});
 </script>
 {% endif %}{% endif %}
</div>
{% endif %}

{% if sources %}
<h2>Откуда берутся фото</h2>
<div class=card>
 <table><tr><th>Что</th><th>Ссылка на Яндекс.Диск</th><th>Папка</th><th>Куда идёт</th></tr>
 {% for what,url,folder,where in sources %}<tr><td>{{what}}</td><td><a href="{{url}}" target=_blank>{{url.replace('https://','')}}</a></td><td>{{folder or '—'}}</td><td>{{where}}</td></tr>{% endfor %}
 </table>
 <p class=muted style="margin:12px 0 0">Фото берутся только с Яндекс.Диска: раз в час синк зеркалит папки (добавили или удалили файл на диске — изменится в фидах). Планировки и планы этажей приходят из ProfitBase.</p>
</div>
{% endif %}
</main>"""
