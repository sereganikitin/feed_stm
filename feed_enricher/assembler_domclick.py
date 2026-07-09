"""Сборка фида для ДомКлик (родной формат «Домклик Новостройки»).

ДомКлик для новостроек НЕ принимает YRL/Яндекс — нужен свой формат:
  <complexes><complex>…<buildings><building>…<flats><flat>…

Источник — Яндекс-выгрузка ProfitBase (profitbase_xml, в ней оба проекта + богатые
поля: евро, санузлы, вид из окна, секция, номер квартиры, кухня-гостиная, 3D-тур).
Планировка-обложка — наша (/enriched). Уровень ЖК/офиса продаж/описания — из конфига
(cfg["domclick"]); пустые поля пропускаем.

NB: чего нет в ProfitBase и нужно от клиента — domrf_id (наш.дом.рф), офис продаж,
описание ЖК. Заполняются через конфиг/по мере получения.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import PUBLIC_BASE_URL, project_dirs, file_ver

_LN = lambda t: t.split("}")[-1]

# facing (ProfitBase) → renovation (ДомКлик)
_RENO = {
    "нет": "Без отделки", "без отделки": "Без отделки",
    "черновая": "Черновая", "предчистовая": "Предчистовая",
    "чистовая": "Чистовая", "чистовая с мебелью": "Чистовая",
    "дизайнерская": "Чистовая", "white box": "Предчистовая",
}

_SELLABLE = {"AVAILABLE", "BOOKED", "EXECUTION"}


def _gv(o, t):
    for c in o:
        if _LN(c.tag) == t:
            return (c.text or "").strip()
    return ""


def _sub(o, t):
    for c in o:
        if _LN(c.tag) == t:
            return c
    return None


def _cf(o, name):
    """Значение custom-field по имени."""
    for c in o:
        if _LN(c.tag) != "custom-field":
            continue
        nm = vl = ""
        for x in c:
            if _LN(x.tag) == "name": nm = (x.text or "").strip()
            elif _LN(x.tag) == "value": vl = (x.text or "").strip()
        if nm == name:
            return vl
    return ""


def _e(parent, tag, text=None):
    el = ET.SubElement(parent, tag)
    if text is not None and str(text) != "":
        el.text = str(text)
    return el


def _oname(o):
    ob = _sub(o, "object")
    return _gv(ob, "name") if ob is not None else ""


def _house(o):
    """(id, name, floors-total, built-year, ready-quarter) корпуса лота."""
    h = _sub(o, "house")
    if h is None:
        return ("", "", "", "", "")
    return (_gv(h, "id"), _gv(h, "name"), _gv(h, "floors-total"),
            _gv(h, "built-year"), _gv(h, "ready-quarter"))


def assemble_domclick_feed(slug: str, pbxml_bytes: bytes, coords: dict,
                           cfg: dict, out_path: Path) -> Path:
    """cfg = проект (get_project) с блоком cfg['domclick']."""
    dc = cfg.get("domclick", {}) or {}
    proj_name = dc.get("source_name") or cfg.get("name")
    enriched_dir = project_dirs(slug)["enriched"]

    root = ET.fromstring(pbxml_bytes)
    # отобрать продаваемые квартиры проекта, сгруппировать по корпусу
    houses: dict = {}
    for o in root.iter():
        if _LN(o.tag) != "offer" or _oname(o) != proj_name:
            continue
        if _gv(o, "status") not in _SELLABLE:
            continue
        houses.setdefault(_house(o), []).append(o)

    out = ET.Element("complexes")
    cx = ET.SubElement(out, "complex")
    _e(cx, "id", dc.get("complex_id") or cfg.get("yandex_building_id"))
    _e(cx, "name", dc.get("name") or cfg.get("name"))
    # координаты ЖК — из конфига или из первого лота с координатами
    clat, clon = dc.get("lat"), dc.get("lon")
    if not (clat and clon):
        for lots in houses.values():
            for o in lots:
                c = coords.get(o.get("internal-id"))
                if c:
                    clat, clon = c; break
            if clat: break
    _e(cx, "latitude", clat); _e(cx, "longitude", clon)
    _e(cx, "address", dc.get("address"))
    # фото ЖК — наш набор (extra_yandex)
    imgs = _e(cx, "images")
    pdir = project_dirs(slug)["extra_yandex"]
    for f in sorted(pdir.glob("*.jpg"))[:20]:
        _e(imgs, "image", f"{PUBLIC_BASE_URL}/extra_yandex/{slug}/{file_ver(f)}/{f.name}")

    blds = ET.SubElement(cx, "buildings")
    for (hid, hname, floors, byear, bq), lots in sorted(houses.items()):
        b = ET.SubElement(blds, "building")
        _e(b, "id", hid)
        _e(b, "fz_214", "1")
        _e(b, "name", hname)
        # координаты корпуса — по первому лоту
        blat = blon = None
        for o in lots:
            c = coords.get(o.get("internal-id"))
            if c: blat, blon = c; break
        _e(b, "latitude", blat or clat); _e(b, "longitude", blon or clon)
        _e(b, "address", dc.get("address"))
        _e(b, "floors", floors)
        flats = ET.SubElement(b, "flats")
        for o in lots:
            iid = o.get("internal-id")
            f = ET.SubElement(flats, "flat")
            _e(f, "flat_id", iid)
            _e(f, "apartment", _cf(o, "Усл номер квартиры") or _gv(o, "number").rsplit("-", 1)[-1])
            _e(f, "entrance", _gv(o, "building-section"))
            _e(f, "booking", "1" if _gv(o, "status") == "BOOKED" else "0")
            _e(f, "euro_plan", "1" if _gv(o, "euro-layout") == "1" else "0")
            _e(f, "connected_bathroom", _gv(o, "combined-bathroom-unit") or "0")
            _e(f, "separated_bathroom", _gv(o, "separated-bathroom-unit") or "0")
            _e(f, "floor", _gv(o, "floor"))
            _e(f, "room", "0" if _gv(o, "studio") == "1" else (_gv(o, "rooms") or "0"))
            plan = enriched_dir / f"{iid}.png"
            if plan.exists():
                _e(f, "plan", f"{PUBLIC_BASE_URL}/enriched/{slug}/{file_ver(plan)}/{iid}.png")
            fac = (_gv(o, "facing") or "").lower()
            _e(f, "renovation", _RENO.get(fac, "Без отделки"))
            price = _gv(_sub(o, "price"), "value")
            _e(f, "price", price)
            pc = ET.SubElement(f, "price_conditions")
            conds = ET.SubElement(pc, "conditions")
            cond = ET.SubElement(conds, "condition")
            _e(cond, "kind", "cash"); _e(cond, "price", price)
            _e(f, "area", _gv(_sub(o, "area"), "value"))
            _e(f, "kitchen_area", _cf(o, "Сайт.Кухня-гостиная"))
            _e(f, "window_view", _gv(o, "window-view"))
            tour = _cf(o, "3D планировка")
            if tour.startswith("http"):
                vts = ET.SubElement(f, "virtual_tours")
                vt = ET.SubElement(vts, "virtual_tour")
                _e(vt, "name", "3D тур"); _e(vt, "model_url", tour); _e(vt, "provider", "iframe")

    # описание ЖК
    dm = dc.get("description_main") or {}
    if dm.get("text"):
        d = ET.SubElement(cx, "description_main")
        _e(d, "title", dm.get("title")); _e(d, "text", dm.get("text"))
    # офис продаж
    s = dc.get("sales") or {}
    if s.get("phone"):
        si = ET.SubElement(cx, "sales_info")
        _e(si, "sales_phone", s.get("phone"))
        _e(si, "responsible_officer_phone", s.get("officer_phone") or s.get("phone"))
        _e(si, "sales_address", s.get("address"))
        _e(si, "sales_latitude", s.get("lat")); _e(si, "sales_longitude", s.get("lon"))
        _e(si, "timezone", s.get("timezone") or "+3")
    # застройщик
    dev = dc.get("developer") or cfg.get("sales_agent") or {}
    d = ET.SubElement(cx, "developer")
    _e(d, "id", dev.get("id") or "1")
    _e(d, "name", dev.get("name") or dev.get("organization"))
    _e(d, "site", dev.get("site") or dev.get("url"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(out)
    ET.ElementTree(out).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
