"""Превью-галерея карточек из любого нашего фида (для визуальной проверки).

Главная страница: меню выбора фида + сетка карточек (главная картинка + текст),
как их увидит классифайд. Поддержаны форматы: CIAN (<object>), Avito (<Ad>),
Yandex realty-feed (<offer> в неймспейсе).
"""
import html
import xml.etree.ElementTree as ET

from .config import PROJECTS, project_dirs


def _registry():
    """Список доступных фидов: key, label, путь к XML, формат."""
    feeds = []
    for slug, p in PROJECTS.items():
        d = project_dirs(slug)["feeds"]
        feeds += [
            {"key": f"{slug}-cian",   "label": f'{p["name"]} · ЦИАН',   "path": d / "feed.xml",   "fmt": "cian"},
            {"key": f"{slug}-avito",  "label": f'{p["name"]} · Авито',  "path": d / "avito.xml",  "fmt": "avito"},
            {"key": f"{slug}-yandex", "label": f'{p["name"]} · Яндекс', "path": d / "yandex.xml", "fmt": "yandex"},
        ]
    try:
        from . import comm_zorge_cian
        feeds.append({"key": "comm-zorge", "label": "Зорге коммерция · ЦИАН",
                      "path": comm_zorge_cian.OUT, "fmt": "cian"})
    except Exception:
        pass
    try:
        from . import comm_cian_rent
        feeds.append({"key": "comm-b37rent", "label": "Б37 коммерция аренда · ЦИАН",
                      "path": comm_cian_rent.OUT, "fmt": "cian"})
    except Exception:
        pass
    return feeds


def _ln(tag):
    return tag.split("}")[-1]


def _txt(el, tag):
    """Текст первого под-элемента с локальным именем tag (без учёта неймспейса)."""
    if el is None:
        return ""
    for c in el:
        if _ln(c.tag) == tag:
            return (c.text or "").strip()
    return ""


def _money(s):
    try:
        return f"{int(float(s)):,}".replace(",", " ")
    except Exception:
        return s


# ──────────────── извлечение карточек по форматам ────────────────

def _cards_cian(root):
    cards = []
    for o in root.iter("object"):
        photos = o.find("Photos")
        img = ""
        if photos is not None:
            ps = photos.findall("PhotoSchema")
            default = next((p for p in ps if _txt(p, "IsDefault") == "1"), None)
            img = _txt(default, "FullUrl") if default is not None else (_txt(ps[0], "FullUrl") if ps else "")
        if not img:
            lp = o.find("LayoutPhoto")
            img = _txt(lp, "FullUrl") if lp is not None else ""
        cat = (o.findtext("Category") or "")
        rooms = o.findtext("FlatRoomsCount")
        sp = o.find("Specialty")
        spec = (next((s.text for s in sp.iter("String")), "") if sp is not None else "")
        if rooms:
            rmap = {"9": "Студия", "7": "Своб. планировка", "10": "6+ комнат"}
            title = rmap.get(rooms, f"{rooms}-комн")
        elif spec:
            title = spec
        else:
            title = "Помещение"
        bt = o.find("BargainTerms")
        price = _txt(bt, "Price")
        period = _txt(bt, "PaymentPeriod")
        deal = "аренда" if "Rent" in cat else ("продажа" if "Sale" in cat else "")
        cards.append({
            "img": img, "title": title,
            "area": o.findtext("TotalArea") or "",
            "price": _money(price) + (" ₽/мес" if period == "monthly" else " ₽") if price else "",
            "addr": o.findtext("Address") or "",
            "sub": " · ".join(x for x in [deal, f'эт. {o.findtext("FloorNumber")}' if o.findtext("FloorNumber") else ""] if x),
            "id": o.findtext("ExternalId") or "",
        })
    return cards


def _cards_avito(root):
    cards = []
    for ad in root.iter("Ad"):
        imgs = ad.find("Images")
        img = imgs.find("Image").get("url") if (imgs is not None and imgs.find("Image") is not None) else ""
        title = ad.findtext("Title") or ad.findtext("PropertyType") or ""
        if not title:
            r = ad.findtext("Rooms")
            title = "Студия" if r == "Студия" else (f"{r}-комн" if r else "Объявление")
        price = ad.findtext("Price")
        cards.append({
            "img": img, "title": title,
            "area": ad.findtext("Square") or "",
            "price": (_money(price) + " ₽") if price else "",
            "addr": ad.findtext("Address") or "",
            "sub": " · ".join(x for x in [ad.findtext("OperationType") or "", f'эт. {ad.findtext("Floor")}' if ad.findtext("Floor") else ""] if x),
            "id": ad.findtext("Id") or "",
        })
    return cards


def _cards_yandex(root):
    cards = []
    for o in root.iter():
        if _ln(o.tag) != "offer":
            continue
        img = next((c.text for c in o if _ln(c.tag) == "image" and c.text), "")
        rooms = _txt(o, "rooms")
        studio = _txt(o, "studio")
        title = "Студия" if studio == "да" else (f"{rooms}-комн" if rooms else _txt(o, "category") or "Объект")
        price_el = next((c for c in o if _ln(c.tag) == "price"), None)
        val = _txt(price_el, "value") if price_el is not None else ""
        period = _txt(price_el, "period") if price_el is not None else ""
        area_el = next((c for c in o if _ln(c.tag) == "area"), None)
        loc = next((c for c in o if _ln(c.tag) == "location"), None)
        cards.append({
            "img": img, "title": title,
            "area": _txt(area_el, "value") if area_el is not None else "",
            "price": (_money(val) + (" ₽/мес" if period == "month" else " ₽")) if val else "",
            "addr": _txt(loc, "address") if loc is not None else "",
            "sub": _txt(o, "type"),
            "id": o.get("internal-id") or "",
        })
    return cards


_EXTRACT = {"cian": _cards_cian, "avito": _cards_avito, "yandex": _cards_yandex}


# ──────────────── рендер ────────────────

_CSS = """<style>
:root{color-scheme:light}
body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#f4f6f9;color:#1c2430}
header{background:#fff;border-bottom:1px solid #e6eaef;padding:14px 22px;position:sticky;top:0;z-index:5}
h1{font-size:18px;margin:0 0 12px}
.tabs{display:flex;flex-wrap:wrap;gap:8px}
.tab{padding:7px 13px;border-radius:8px;background:#eef2f7;color:#33415a;text-decoration:none;font-size:13px;white-space:nowrap}
.tab:hover{background:#e2e8f1}
.tab.active{background:#2563eb;color:#fff}
.wrap{max-width:1400px;margin:0 auto;padding:20px 22px}
.count{color:#94a3b8;font-size:13px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px}
.card{background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);display:flex;flex-direction:column}
.card .ph{aspect-ratio:4/3;background:#0e1116;display:flex;align-items:center;justify-content:center;overflow:hidden}
.card .ph img{width:100%;height:100%;object-fit:contain}
.card .b{padding:12px 14px}
.card .t{font-weight:700;font-size:15px;margin-bottom:2px}
.card .price{color:#16a34a;font-weight:700;font-size:15px;margin:6px 0}
.card .m{color:#64748b;font-size:13px;line-height:1.45}
.card .id{color:#b6c0cc;font-size:11px;margin-top:6px}
.empty{color:#94a3b8;padding:40px 0;text-align:center}
a.adm{color:#2563eb;text-decoration:none;font-size:13px}
</style>"""


def render(active_key=None):
    feeds = _registry()
    if not feeds:
        return "<p>Нет фидов</p>"
    active = next((f for f in feeds if f["key"] == active_key), feeds[0])
    tabs = "".join(
        f'<a class="tab{" active" if f["key"]==active["key"] else ""}" href="/?feed={f["key"]}">{html.escape(f["label"])}</a>'
        for f in feeds)

    cards_html, n = "", 0
    if active["path"].exists():
        try:
            root = ET.fromstring(active["path"].read_bytes())
            cards = _EXTRACT[active["fmt"]](root)
            n = len(cards)
            for c in cards:
                img = (f'<img src="{html.escape(c["img"])}" loading="lazy" alt="">'
                       if c["img"] else '<span style="color:#445">нет фото</span>')
                area = f'{c["area"]} м²' if c["area"] else ""
                meta = " · ".join(x for x in [area, c["sub"]] if x)
                cards_html += f"""<div class="card">
  <a class="ph" href="{html.escape(c["img"])}" target="_blank">{img}</a>
  <div class="b">
    <div class="t">{html.escape(c["title"])}</div>
    <div class="price">{html.escape(c["price"])}</div>
    <div class="m">{html.escape(meta)}</div>
    <div class="m">{html.escape(c["addr"])}</div>
    <div class="id">{html.escape(c["id"])}</div>
  </div>
</div>"""
        except Exception as e:
            cards_html = f'<div class="empty">Ошибка чтения фида: {html.escape(str(e))}</div>'
    else:
        cards_html = '<div class="empty">Фид ещё не собран</div>'

    body = cards_html if n else (cards_html or '<div class="empty">Пусто</div>')
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Превью карточек — {html.escape(active['label'])}</title>{_CSS}</head><body>
<header><h1>Превью карточек из фидов · <a class=adm href="/admin">панель</a></h1>
<div class=tabs>{tabs}</div></header>
<div class=wrap><div class=count>Фид: <b>{html.escape(active['label'])}</b> — карточек: {n}</div>
<div class=grid>{body}</div></div></body></html>"""
