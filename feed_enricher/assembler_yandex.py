"""Сборка фида для Яндекс.Недвижимости (YML realty-feed) из лотов ЦИАН-фида.

Формат: <realty-feed xmlns="http://webmaster.yandex.ru/schemas/feed/realty/2010-06">
с элементами <offer>. Картинки — текстовые <image>URL</image> (обложка = наша
планировка, далее наши фото из /extra_yandex). Привязка к новостройке Яндекса —
<yandex-building-id> + <yandex-house-id> (из xlsx, по номеру корпуса). Контакт —
<sales-agent>. Координаты берём из Авито-выгрузки ProfitBase (передаются картой).

Цена идёт со скидкой −20% как есть из ЦИАН-фида (price у лота).
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import PUBLIC_BASE_URL, project_dirs, get_project
from .parser import FeedLot

NS = "http://webmaster.yandex.ru/schemas/feed/realty/2010-06"
ET.register_namespace("", NS)

_PLAN_MARKERS = ("/uploads/preset/", "/uploads/layout/")


def _e(parent, tag, text=None):
    el = ET.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None and str(text) != "":
        el.text = str(text)
    return el


def _korpus_no(house_name: str) -> int:
    m = re.search(r"(\d+)", house_name or "")
    return int(m.group(1)) if m else 0


def _clean_desc(d: str) -> str:
    """Описание для Яндекса — простой текст (теги ProfitBase убираем)."""
    d = re.sub(r"<\s*br\s*/?>", "\n", d or "", flags=re.I)
    d = re.sub(r"</\s*p\s*>", "\n", d, flags=re.I)
    d = re.sub(r"<[^>]+>", "", d)
    return d.strip()


def assemble_yandex_feed(slug: str, lots: list[FeedLot], coords: dict,
                         out_path: Path, generation_date: str) -> Path:
    proj = get_project(slug)
    dirs = project_dirs(slug)
    enriched_dir = dirs["enriched"]

    building_id = proj.get("yandex_building_id", "")
    house_ids   = proj.get("yandex_house_ids", {}) or {}
    agent       = proj.get("sales_agent", {}) or {}

    files = {f.name for f in dirs["extra_yandex"].glob("*.jpg")}
    order = [n for n in (proj.get("extra_photo_order_yandex") or []) if n in files]
    photo_names = order + sorted(files - set(order))
    photo_urls  = [f"{PUBLIC_BASE_URL}/extra_yandex/{slug}/{n}" for n in photo_names]

    root = ET.Element(f"{{{NS}}}realty-feed")
    _e(root, "generation-date", generation_date)

    for lot in lots:
        if not (lot.price and lot.area_total):
            continue

        o = _e(root, "offer")
        o.set("internal-id", lot.internal_id)
        _e(o, "type", "продажа")
        _e(o, "property-type", "жилая")
        _e(o, "category", "квартира")
        if lot.is_apartments:
            _e(o, "apartments", "да")
        _e(o, "new-flat", "да")
        if agent.get("url"):
            _e(o, "url", agent["url"])
        _e(o, "creation-date", generation_date)
        _e(o, "last-update-date", generation_date)

        sa = _e(o, "sales-agent")
        if agent.get("organization"):
            _e(sa, "organization", agent["organization"])
        if agent.get("category"):
            _e(sa, "category", agent["category"])
        if lot.phone:
            _e(sa, "phone", lot.phone)
        if agent.get("url"):
            _e(sa, "url", agent["url"])

        loc = _e(o, "location")
        _e(loc, "country", "Россия")
        _e(loc, "region", "Москва")
        _e(loc, "locality-name", "Москва")
        if lot.address:
            _e(loc, "address", lot.address)
        c = coords.get(lot.internal_id)
        if c:
            _e(loc, "latitude", c[0])
            _e(loc, "longitude", c[1])

        pr = _e(o, "price")
        _e(pr, "value", int(lot.price))
        _e(pr, "currency", "RUB")

        ar = _e(o, "area")
        _e(ar, "value", f"{lot.area_total:.1f}".rstrip("0").rstrip("."))
        _e(ar, "unit", "кв.м")

        # Комнатность
        if lot.rooms == 0:
            _e(o, "rooms", "1"); _e(o, "studio", "да")
        elif lot.rooms < 0:
            _e(o, "rooms", "1"); _e(o, "open-plan", "да")
        else:
            _e(o, "rooms", lot.rooms)

        if lot.floor:
            _e(o, "floor", lot.floor)
        if lot.floors_total:
            _e(o, "floors-total", lot.floors_total)

        # Привязка к новостройке Яндекса
        if building_id:
            _e(o, "yandex-building-id", building_id)
        hid = house_ids.get(_korpus_no(lot.house_name))
        if hid:
            _e(o, "yandex-house-id", hid)
        if lot.house_name:
            _e(o, "building-name", lot.house_name)
        if lot.built_year:
            _e(o, "built-year", lot.built_year)
        if lot.ready_quarter:
            _e(o, "ready-quarter", lot.ready_quarter)
        _e(o, "building-state", "hand-over" if lot.building_complete else "unfinished")
        _e(o, "deal-status", "прямая продажа")

        if lot.description:
            _e(o, "description", _clean_desc(lot.description))

        # Картинки: обложка (наша планировка) + наши фото
        if (enriched_dir / f"{lot.internal_id}.png").exists():
            _e(o, "image", f"{PUBLIC_BASE_URL}/enriched/{slug}/{lot.internal_id}.png")
        for u in photo_urls:
            _e(o, "image", u)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def coords_from_avito(avito_xml: bytes) -> dict:
    """Карта {internal_id: (lat, lng)} из Авито-выгрузки ProfitBase."""
    out = {}
    try:
        root = ET.fromstring(avito_xml)
    except Exception:
        return out
    for ad in root.iter("Ad"):
        iid = (ad.findtext("Id") or "").strip()
        lat = (ad.findtext("Latitude") or "").strip()
        lng = (ad.findtext("Longitude") or "").strip()
        if iid and lat and lng:
            out[iid] = (lat, lng)
    return out
