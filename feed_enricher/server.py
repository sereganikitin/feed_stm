"""HTTP-сервер с мультипроектной маршрутизацией.

Endpoints:
  GET  /feed/<slug>.xml           → обогащенный фид проекта <slug>
  GET  /enriched/<slug>/<id>.png  → конкретная обогащенная планировка
  POST /refresh/<slug>            → пересобрать кэш проекта
  POST /refresh                   → пересобрать все проекты
  GET  /                          → список проектов и ссылок
"""
from pathlib import Path
import os, json, threading, time
import requests
from flask import Flask, send_file, abort, jsonify

from .config import (
    PROJECTS, project_dirs, ADMIN_DIR,
    SERVE_HOST, SERVE_PORT, REFRESH_INTERVAL_HOURS,
    PB_API_TOKEN, PB_UPLOAD_URL,
)
from .parser import download_feed, parse_feed
from .enricher import enrich_lot, installment_values
from .assembler import assemble_feed
from .assembler_avito import assemble_avito_feed, enrich_pb_avito_feed
from .yadisk import sync_public_folder

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-in-env")
app.permanent_session_lifetime = 60 * 60 * 24 * 14  # 2 недели
_lock = threading.Lock()

# Админ-панель (/admin)
from .admin import admin_bp
app.register_blueprint(admin_bp)


def refresh_project(slug: str) -> dict:
    """Скачать → сгенерить планировки → собрать XML → (опц.) залить в ProfitBase."""
    proj = PROJECTS[slug]
    if not proj.get("pb_feed_url"):
        return {"slug": slug, "skipped": "pb_feed_url не задан"}
    dirs = project_dirs(slug)
    with _lock:
        original = download_feed(proj["pb_feed_url"], dirs["feeds"] / "original.xml")
        lots = parse_feed(original)
        ok, fail = 0, 0
        for lot in lots:
            if not (lot.plan_url and lot.price and lot.area_total):
                continue
            try:
                enrich_lot(slug, lot); ok += 1
            except Exception as e:
                fail += 1
                print(f"[{slug}] enrich error {lot.internal_id}: {e}")
        out = dirs["feeds"] / "feed.xml"
        assemble_feed(slug, original, lots, out)
        # Авито-фид. Вариант A: native-выгрузка ProfitBase + подмена обложки.
        #            Вариант B (fallback): конвертация из ЦИАН-фида.
        out_avito = dirs["feeds"] / "avito.xml"
        if proj.get("pb_avito_feed_url"):
            # Наши фото для карточки Авито с Яндекс.Диска (если набор задан)
            extra_cfg = proj.get("avito_extra_photos")
            if extra_cfg:
                try:
                    n = len(sync_public_folder(
                        extra_cfg["yadisk_public_key"], extra_cfg["yadisk_path"], dirs["extra"]))
                    print(f"[{slug}] extra photos synced: {n}")
                except Exception as e:
                    print(f"[{slug}] yadisk sync failed: {e}")
            avito_src = download_feed(proj["pb_avito_feed_url"], dirs["feeds"] / "original_avito.xml")
            enrich_pb_avito_feed(slug, avito_src, out_avito)
        else:
            assemble_avito_feed(slug, lots, out_avito)
        # Опционально пушим копию в ProfitBase
        uploaded = False
        if PB_UPLOAD_URL and PB_API_TOKEN:
            try:
                with open(out, "rb") as f:
                    r = requests.post(
                        f"{PB_UPLOAD_URL}/{slug}",
                        headers={"Authorization": f"Bearer {PB_API_TOKEN}"},
                        files={"feed.xml": f},
                        timeout=60,
                    )
                uploaded = r.ok
            except Exception as e:
                print(f"[{slug}] PB upload failed: {e}")
        # Статус для дашборда админки
        try:
            ADMIN_DIR.mkdir(parents=True, exist_ok=True)
            sp = ADMIN_DIR / "status.json"
            statuses = json.loads(sp.read_text("utf-8")) if sp.exists() else {}
            statuses[slug] = {"ts": time.strftime("%Y-%m-%d %H:%M"),
                              "enriched_ok": ok, "lots_total": len(lots)}
            sp.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), "utf-8")
        except Exception as e:
            print(f"[{slug}] status write failed: {e}")
        return {
            "slug": slug, "lots_total": len(lots),
            "enriched_ok": ok, "enriched_fail": fail,
            "feed_path": str(out), "feed_avito_path": str(out_avito),
            "uploaded_to_pb": uploaded,
        }


def refresh_all() -> dict:
    return {slug: refresh_project(slug) for slug in PROJECTS}


def _refresh_loop():
    while True:
        try:
            print(f"[auto-refresh] {refresh_all()}")
        except Exception as e:
            print(f"[auto-refresh] error: {e}")
        time.sleep(REFRESH_INTERVAL_HOURS * 3600)


@app.route("/")
def index():
    rows = []
    for slug, p in PROJECTS.items():
        rows.append(
            f'<li><b>{p["name"]}</b> ({slug}) — '
            f'<a href="/gallery/{slug}">галерея</a> · '
            f'<a href="/feed/{slug}.xml">ЦИАН XML</a> · '
            f'<a href="/feed/{slug}-avito.xml">Авито XML</a></li>'
        )
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>feed_enricher</title>
<style>body{{font-family:system-ui;max-width:680px;margin:40px auto;padding:0 20px;color:#222}}</style>
</head><body><h1>feed_enricher</h1><ul>{''.join(rows)}</ul>
<p><a href="/admin">→ Панель управления</a></p></body></html>"""


@app.route("/gallery/<slug>")
def gallery(slug: str):
    if slug not in PROJECTS:
        abort(404)
    proj = PROJECTS[slug]
    dirs = project_dirs(slug)
    feed_path = dirs["feeds"] / "original.xml"
    if not feed_path.exists():
        refresh_project(slug)
    lots = parse_feed(feed_path.read_bytes())
    lots.sort(key=lambda l: (l.rooms, l.area_total))
    installment = proj.get("installment")

    cards = []
    for lot in lots:
        png = dirs["enriched"] / f"{lot.internal_id}.png"
        if not png.exists():
            continue
        pv_html = ""
        if installment and lot.price:
            pv, monthly = installment_values(lot.price, installment)
            pv_html = (f"<div>ПВ: <b>{pv/1e6:.2f} млн</b> · "
                       f"платёж: <b>{round(monthly/1e3)} тыс/мес</b></div>")
        rooms_lbl = "Ст." if lot.rooms == 0 else (f"{lot.rooms}К" if lot.rooms > 0 else "СП")
        cards.append(f"""<div class="card">
  <a href="/enriched/{slug}/{lot.internal_id}.png" target="_blank">
    <img src="/enriched/{slug}/{lot.internal_id}.png" loading="lazy">
  </a>
  <div class="meta">
    <div><b>{lot.internal_id}</b> — {rooms_lbl}, {lot.area_total:.1f} м², этаж {lot.floor}/{lot.floors_total}</div>
    <div>Цена: <b>{lot.price:,} ₽</b></div>
    {pv_html}
    <div class="house">{lot.house_name}</div>
  </div>
</div>""".replace(",", " "))

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>{proj['name']} — галерея</title>
<style>
  body {{ font-family: system-ui; margin: 0; padding: 16px 24px; background: #f5f5f5; color: #222; }}
  h1 {{ margin: 0 0 4px 0; }}
  .summary {{ color: #666; margin-bottom: 20px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 18px; }}
  .card {{ background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .card img {{ width: 100%; display: block; }}
  .meta {{ padding: 10px 12px; font-size: 13px; line-height: 1.4; }}
  .meta b {{ color: #000; }}
  .house {{ color: #888; font-size: 12px; margin-top: 4px; }}
</style></head>
<body>
<h1>{proj['name']}</h1>
<div class="summary">Всего лотов: <b>{len(cards)}</b>. <a href="/">← к списку</a> · <a href="/feed/{slug}.xml">фид XML</a></div>
<div class="grid">{''.join(cards)}</div>
</body></html>"""


@app.route("/feed/<slug>.xml")
def serve_feed(slug: str):
    if slug not in PROJECTS:
        abort(404)
    dirs = project_dirs(slug)
    p = dirs["feeds"] / "feed.xml"
    if not p.exists():
        refresh_project(slug)
    if not p.exists():
        abort(503)
    return send_file(p, mimetype="application/xml")


@app.route("/feed/<slug>-avito.xml")
def serve_feed_avito(slug: str):
    if slug not in PROJECTS:
        abort(404)
    dirs = project_dirs(slug)
    p = dirs["feeds"] / "avito.xml"
    if not p.exists():
        refresh_project(slug)
    if not p.exists():
        abort(503)
    return send_file(p, mimetype="application/xml")


@app.route("/enriched/<slug>/<name>")
def serve_plan(slug: str, name: str):
    if slug not in PROJECTS:
        abort(404)
    p = project_dirs(slug)["enriched"] / name
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="image/png")


@app.route("/extra/<slug>/<name>")
def serve_extra(slug: str, name: str):
    """Наши фото для карточки Авито (скачаны с Яндекс.Диска)."""
    if slug not in PROJECTS or "/" in name or "\\" in name:
        abort(404)
    p = project_dirs(slug)["extra"] / name
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="image/jpeg")


@app.route("/refresh/<slug>", methods=["POST"])
def manual_refresh_slug(slug: str):
    if slug not in PROJECTS:
        abort(404)
    return jsonify(refresh_project(slug))


@app.route("/refresh", methods=["POST"])
def manual_refresh_all():
    return jsonify(refresh_all())


def start_background_refresher():
    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()


def main():
    start_background_refresher()
    app.run(host=SERVE_HOST, port=SERVE_PORT)


if __name__ == "__main__":
    main()
