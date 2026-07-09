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

# facing (ProfitBase) → renovation (ДомКлик, строчными как в эталонном фиде)
_RENO = {
    "нет": "без отделки", "без отделки": "без отделки",
    "черновая": "черновая", "предчистовая": "предчистовая",
    "чистовая": "чистовая", "чистовая с мебелью": "чистовая",
    "дизайнерская": "чистовая", "white box": "предчистовая",
}


def _bathroom(comb, sep):
    """combined/separated-bathroom-unit → одно поле bathroom ДомКлик."""
    try:
        c, s = int(comb or 0), int(sep or 0)
    except ValueError:
        c = s = 0
    if c + s >= 2:
        return "Более 2"
    if s >= 1:
        return "Раздельный"
    if c >= 1:
        return "Совмещенный"
    return ""

_SELLABLE = {"AVAILABLE", "BOOKED", "EXECUTION"}

# building-state (ProfitBase) → building_state (ДомКлик)
_BSTATE = {"hand-over": "сдан", "built": "сдан", "unfinished": "строится"}


def _wview(raw: str) -> str:
    """Вид из окна ProfitBase (конкретные места) → словарь ДомКлик (двор/улица)."""
    r = (raw or "").strip().lower()
    if not r:
        return ""
    dvor = "двор" in r
    other = ("," in r) or any(k in r for k in (
        "улиц", "поле", "парк", "роща", "бор", "цска", "река", "набережн",
        "город", "проспект", "шоссе", "сквер"))
    if dvor and other:
        return "Во двор и на улицу"
    if dvor:
        return "Во двор"
    return "На улицу"


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
            _gv(h, "built-year"), _gv(h, "ready-quarter"), _gv(h, "building-state"))


def _addmissing(el, tag, val):
    """Добавить тег в элемент, если его нет и значение непустое."""
    if val and el.find(tag) is None:
        ET.SubElement(el, tag).text = str(val)


def enrich_domclick_feed(slug: str, pbdc_bytes: bytes, out_path: Path, cfg: dict = None) -> Path:
    """Взять ГОТОВЫЙ DomClick-экспорт ProfitBase (правильные id ЖК/корпусов, контент)
    и: подменить планировки → наши брендовые (/enriched по flat_id) и фото ЖК → наш
    набор с ЯД; ДОБИТЬ то, что режет валидатор ДомКлик: building-поля (floors_ready,
    building_phase, address, координаты, лифты), маппинг window_view в словарь,
    непустой description (описание ЖК из конфига + удаление акций с пустым описанием)."""
    cfg = cfg or {}
    dc = cfg.get("domclick", {}) or {}
    root = ET.fromstring(pbdc_bytes)
    edir = project_dirs(slug)["enriched"]
    pdir = project_dirs(slug)["extra_yandex"]
    our_imgs = [f"{PUBLIC_BASE_URL}/extra_yandex/{slug}/{file_ver(f)}/{f.name}"
                for f in sorted(pdir.glob("*.jpg"))[:20]]
    for cx in root.findall("complex"):
        clat, clon, caddr = cx.findtext("latitude"), cx.findtext("longitude"), cx.findtext("address")
        imgs = cx.find("images")           # фото ЖК → наш набор с ЯД
        if imgs is not None and our_imgs:
            for im in list(imgs):
                imgs.remove(im)
            for u in our_imgs:
                ET.SubElement(imgs, "image").text = u
        # описание ЖК: если в экспорте пусто/нет — из конфига
        fb = (dc.get("description_main") or {}).get("text")
        if fb:
            dm = cx.find("description_main")
            if dm is None:
                dm = ET.SubElement(cx, "description_main")
            dmt = dm.find("text")
            if dmt is None:
                dmt = ET.SubElement(dm, "text")
            if not (dmt.text or "").strip():
                dmt.text = fb
        # акции с пустым <description> валидатор режет — убираем такие акции
        disc = cx.find("discounts")
        if disc is not None:
            for d in list(disc):
                dd = d.find("description")
                if dd is not None and not (dd.text or "").strip():
                    disc.remove(d)
        # building: добить обязательные для валидатора поля
        for b in cx.findall("buildings/building"):
            floors = b.findtext("floors")
            _addmissing(b, "floors_ready", floors)
            _addmissing(b, "building_phase", "1")
            _addmissing(b, "address", caddr)
            _addmissing(b, "latitude", clat)
            _addmissing(b, "longitude", clon)
            _addmissing(b, "passenger_lifts_count", "1")
            _addmissing(b, "cargo_lifts_count", "1")
        for fl in cx.findall(".//flat"):
            fid = (fl.findtext("flat_id") or "").strip()
            png = edir / f"{fid}.png"       # планировки → наши брендовые
            if png.exists():
                plans = fl.find("plans")
                if plans is None:
                    plans = ET.SubElement(fl, "plans")
                for p in list(plans):
                    plans.remove(p)
                ET.SubElement(plans, "plan").text = \
                    f"{PUBLIC_BASE_URL}/enriched/{slug}/{file_ver(png)}/{fid}.png"
            wv = fl.find("window_view")     # сырой вид → словарь ДомКлик
            if wv is not None:
                m = _wview(wv.text)
                if m:
                    wv.text = m
                else:
                    fl.remove(wv)
            ren = fl.find("renovation")     # пустая отделка → без отделки
            if ren is not None and not (ren.text or "").strip():
                ren.text = "без отделки"
            liv = fl.find("living_area")    # пустая жилая площадь → убрать тег
            if liv is not None and not (liv.text or "").strip():
                fl.remove(liv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


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

    bmap = dc.get("buildings", {}) or {}   # id корпуса ProfitBase → id корпуса ДомКлик
    first_img = None
    for f in sorted(project_dirs(slug)["extra_yandex"].glob("*.jpg"))[:1]:
        first_img = f"{PUBLIC_BASE_URL}/extra_yandex/{slug}/{file_ver(f)}/{f.name}"
    blds = ET.SubElement(cx, "buildings")
    for (hid, hname, floors, byear, bq, bstate), lots in sorted(houses.items()):
        b = ET.SubElement(blds, "building")
        _e(b, "id", bmap.get(hid) or bmap.get(str(hid)) or hid)
        _e(b, "fz_214", "1")
        _e(b, "name", hname)
        # координаты корпуса — по первому лоту
        blat = blon = None
        for o in lots:
            c = coords.get(o.get("internal-id"))
            if c: blat, blon = c; break
        _e(b, "floors", floors)
        _e(b, "floors_ready", floors)
        _e(b, "building_state", bstate or "unfinished")   # сырое значение, как в эталоне
        _e(b, "building_phase", "1")
        _e(b, "built_year", byear)
        _e(b, "ready_quarter", bq)
        _e(b, "building_type", dc.get("building_type") or "монолитный")
        _e(b, "address", dc.get("address"))
        _e(b, "latitude", blat or clat); _e(b, "longitude", blon or clon)
        _e(b, "passenger_lifts_count", dc.get("passenger_lifts_count") or "1")
        _e(b, "cargo_lifts_count", dc.get("cargo_lifts_count") or "1")
        if first_img:
            _e(b, "image", first_img)
        flats = ET.SubElement(b, "flats")
        for o in lots:
            iid = o.get("internal-id")
            f = ET.SubElement(flats, "flat")
            _e(f, "flat_id", iid)
            _e(f, "apartment", _cf(o, "Усл номер квартиры") or _gv(o, "number").rsplit("-", 1)[-1])
            _e(f, "floor", _gv(o, "floor"))
            _e(f, "room", "0" if _gv(o, "studio") == "1" else (_gv(o, "rooms") or "0"))
            plan = enriched_dir / f"{iid}.png"
            if plan.exists():
                plans = ET.SubElement(f, "plans")
                _e(plans, "plan", f"{PUBLIC_BASE_URL}/enriched/{slug}/{file_ver(plan)}/{iid}.png")
            _e(f, "ceiling_height", _cf(o, "Высота потолка"))
            _e(f, "balcony", "0")
            _e(f, "loggia", "0")
            fac = (_gv(o, "facing") or "").lower()
            _e(f, "renovation", _RENO.get(fac, "без отделки"))
            _e(f, "price", _gv(_sub(o, "price"), "value"))
            _e(f, "area", _gv(_sub(o, "area"), "value"))
            _e(f, "kitchen_area", _cf(o, "Сайт.Кухня-гостиная") or _cf(o, "Сайт.Площадь кухни, м2"))
            la = _cf(o, "Сайт.Жилая площадь") or _cf(o, "Жилая площадь")
            if la:
                _e(f, "living_area", la)
            wv = _wview(_gv(o, "window-view"))   # словарь ДомКлик (сырой валидатор режет)
            if wv:
                _e(f, "window_view", wv)
            ba = _bathroom(_gv(o, "combined-bathroom-unit"), _gv(o, "separated-bathroom-unit"))
            if ba:
                _e(f, "bathroom", ba)
            _e(f, "housing_type", "1" if _gv(o, "property_type").startswith("Апарт") else "0")

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
        _e(si, "sales_address", s.get("address"))
        _e(si, "sales_latitude", s.get("lat")); _e(si, "sales_longitude", s.get("lon"))
        _e(si, "timezone", s.get("timezone") or "+3")
        if s.get("work_days"):
            wds = ET.SubElement(si, "work_days")
            for wd in s["work_days"]:
                w = ET.SubElement(wds, "work_day")
                _e(w, "day", wd[0]); _e(w, "open_at", wd[1]); _e(w, "close_at", wd[2])
    # застройщик
    dev = dc.get("developer") or cfg.get("sales_agent") or {}
    d = ET.SubElement(cx, "developer")
    _e(d, "id", dev.get("id") or "1")
    _e(d, "name", dev.get("name") or dev.get("organization"))
    _e(d, "site", dev.get("site") or dev.get("url"))
    if dev.get("logo"):
        _e(d, "logo", dev["logo"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(out)
    ET.ElementTree(out).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
