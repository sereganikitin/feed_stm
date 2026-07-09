"""Сборка фида для НОВОГО «Яндекс Поиск Недвижимости» (пилот, namespace
metarealty/2024-12) — отдельно от старого Яндекс.Недвижимость фида (2010-06).

Формат: <realty-feed xmlns="http://webmaster.yandex.ru/schemas/feed/metarealty/2024-12">
с элементами <offer>. Отличия от старого фида:
  • другой namespace;
  • deal-status = «продажа от застройщика» (в старом было «прямая продажа» —
    новый формат такого значения НЕ принимает);
  • апартаменты передаются тегом <apartment> да/нет (в старом — <apartments>);
  • студии: <studio>да</studio> без <rooms> (спека: «Не указывайте для студий»);
  • добавлены опциональные поля из исходника ProfitBase: living-space,
    kitchen-space, ceiling-height, decoration-type, bathroom-unit.

namespace задаётся атрибутом xmlns на корне (а не через ET.register_namespace),
чтобы не перетирать глобальную регистрацию default-namespace старого ассемблера.

Отдельный «фид рекламной кампании» (campaign-feed: newbuilding-id, cost-per-call,
phone, work-schedule) — НЕ здесь; это другой файл, нужна ставка за звонок.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import PUBLIC_BASE_URL, project_dirs, get_project, file_ver, lot_view_urls
from .parser import FeedLot, _to_float, _to_int

NS = "http://webmaster.yandex.ru/schemas/feed/metarealty/2024-12"

# Отделка ProfitBase (уже по-русски из DECORATION_MAP) → допустимые значения decoration-type
_DEC = {
    "без отделки": "без отделки",
    "черновая":    "черновая",
    "чистовая":    "чистовая",
    "с мебелью":   "под ключ",
}


def _e(parent, tag, text=None):
    el = ET.SubElement(parent, tag)
    if text is not None and str(text) != "":
        el.text = str(text)
    return el


def _korpus_no(house_name: str) -> int:
    m = re.search(r"(\d+)", house_name or "")
    return int(m.group(1)) if m else 0


def _clean_desc(d: str) -> str:
    d = re.sub(r"<\s*br\s*/?>", "\n", d or "", flags=re.I)
    d = re.sub(r"</\s*p\s*>", "\n", d, flags=re.I)
    d = re.sub(r"<[^>]+>", "", d)
    return d.strip()[:10000]


def _num(v: float, prec: int = 2) -> str:
    return f"{v:.{prec}f}".rstrip("0").rstrip(".")


def _area(parent, tag, value, unit="кв.м"):
    a = _e(parent, tag)
    _e(a, "value", value)
    _e(a, "unit", unit)


def _bathroom(comb: int, sep: int) -> str:
    total = comb + sep
    if total >= 2:
        return str(total)
    if sep > 0:
        return "раздельный"
    if comb > 0:
        return "совмещенный"
    return ""


def assemble_yandex_realty_feed(slug: str, lots: list[FeedLot], coords: dict,
                                out_path: Path, generation_date: str) -> Path:
    proj = get_project(slug)
    dirs = project_dirs(slug)
    enriched_dir = dirs["enriched"]

    building_id = proj.get("yandex_building_id", "")
    house_ids   = proj.get("yandex_house_ids", {}) or {}
    agent       = proj.get("sales_agent", {}) or {}
    seller_cat  = agent.get("category") or "застройщик"

    pfiles = {f.name: f for f in dirs["extra_yandex"].glob("*.jpg")}
    order = [n for n in (proj.get("extra_photo_order_yandex") or []) if n in pfiles]
    photo_names = order + sorted(set(pfiles) - set(order))
    photo_urls  = [f"{PUBLIC_BASE_URL}/extra_yandex/{slug}/{file_ver(pfiles[n])}/{n}" for n in photo_names]

    root = ET.Element("realty-feed")
    root.set("xmlns", NS)
    _e(root, "generation-date", generation_date)

    for lot in lots:
        if not (lot.price and lot.area_total):
            continue
        raw = lot.raw_xml

        def rt(path):
            return (raw.findtext(path) or "").strip() if raw is not None else ""

        o = _e(root, "offer")
        o.set("internal-id", lot.internal_id)
        _e(o, "type", "продажа")
        _e(o, "category", "квартира")
        if agent.get("url"):
            _e(o, "url", agent["url"])

        # Продавец
        sa = _e(o, "sales-agent")
        _e(sa, "category", seller_cat)

        # Расположение
        loc = _e(o, "location")
        if lot.address:
            _e(loc, "address", lot.address)
        c = coords.get(lot.internal_id)
        if c:
            _e(loc, "latitude", c[0])
            _e(loc, "longitude", c[1])

        # Цена / условия сделки
        pr = _e(o, "price")
        _e(pr, "value", int(lot.price))
        _e(pr, "currency", "RUB")
        _e(o, "deal-status", "продажа от застройщика")

        # Площади
        _area(o, "area", _num(lot.area_total, 1))
        living = _to_float(rt("LivingArea"))
        if living:
            _area(o, "living-space", _num(living))
        kitchen = _to_float(rt("KitchenArea"))
        if kitchen:
            _area(o, "kitchen-space", _num(kitchen))

        # Фото: планировка первой → фото ЯД → виды (всего не более 30)
        MAX_IMG = 30
        plan = [f"{PUBLIC_BASE_URL}/enriched/{slug}/{file_ver(enriched_dir / f'{lot.internal_id}.png')}/{lot.internal_id}.png"] \
            if (enriched_dir / f"{lot.internal_id}.png").exists() else []
        views = lot_view_urls(slug, lot.internal_id)
        budget = max(0, MAX_IMG - len(plan) - len(views))
        for u in plan + photo_urls[:budget] + views:
            _e(o, "image", u)

        if lot.description:
            _e(o, "description", _clean_desc(lot.description))

        # Комнатность / студия (для студий rooms НЕ передаём)
        if lot.rooms == 0:
            _e(o, "studio", "да")
        elif lot.rooms < 0:
            _e(o, "rooms", "1")
        else:
            _e(o, "rooms", lot.rooms)

        if lot.floor:
            _e(o, "floor", lot.floor)
        _e(o, "apartment", "да" if lot.is_apartments else "нет")

        dec = _DEC.get(lot.decoration)
        if dec:
            _e(o, "decoration-type", dec)
        bath = _bathroom(_to_int(rt("CombinedWcsCount")), _to_int(rt("SeparateWcsCount")))
        if bath:
            _e(o, "bathroom-unit", bath)

        # Здание
        if lot.floors_total:
            _e(o, "floors-total", lot.floors_total)
        if lot.jk_name:
            _e(o, "building-name", lot.jk_name)
        if building_id:
            _e(o, "yandex-building-id", building_id)
        hid = house_ids.get(_korpus_no(lot.house_name))
        if hid:
            _e(o, "yandex-house-id", hid)
        if lot.built_year:
            _e(o, "built-year", lot.built_year)
        if lot.ready_quarter:
            _e(o, "ready-quarter", lot.ready_quarter)
        ch = rt("Building/CeilingHeight")
        if ch:
            _e(o, "ceiling-height", ch)
        _e(o, "building-state", "hand-over" if lot.building_complete else "unfinished")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
