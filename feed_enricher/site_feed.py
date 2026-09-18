"""Фид для корпоративного сайта: копия profitbase_xml из ProfitBase (та же структура —
цены, special-offers, статусы, кастомные поля не трогаем), в который к лотам Зорге 9 и
Квартала Серебряный Бор дописаны наши фото с Я.Дисков сразу после последней планировки:
интерьер → визуализация → вид из окна → общие фото проекта.

Роут /feed/profitbase-site.xml ; триггер POST /refresh-site-feed.
"""
import xml.etree.ElementTree as ET

from .config import CACHE_DIR, PROJECTS, lot_view_groups, cian_extra_urls
from .parser import download_feed

SITE_DIR = CACHE_DIR / "site"
ORIG = SITE_DIR / "profitbase_original.xml"
OUT = SITE_DIR / "profitbase.xml"

# Типы <image> для наших фото (типы ProfitBase — plan, plan floor, house, facade, building — не трогаем)
TYPE_INTERIOR = "interior"
TYPE_VISUAL = "visualization"
TYPE_VIEW = "view"
TYPE_PHOTO = "photo"


def _slug_of(project_name: str) -> str:
    n = (project_name or "").lower()
    if "зорге" in n:
        return "zorge9"
    if "серебряный" in n:
        return "b37"
    return ""


def refresh(reuse_original: bool = False) -> dict:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    if reuse_original and ORIG.exists():
        raw = ORIG.read_bytes()
    else:
        raw = download_feed(PROJECTS["zorge9"]["euro_source_url"], ORIG)
    root = ET.fromstring(raw)
    ns = root.tag.split("}")[0].strip("{") if root.tag.startswith("{") else ""
    ET.register_namespace("", ns)
    tag = lambda t: f"{{{ns}}}{t}" if ns else t
    common = {slug: cian_extra_urls(slug) for slug in ("zorge9", "b37")}

    total = enriched = added = 0
    for off in root.iter(tag("offer")):
        total += 1
        slug = _slug_of(off.findtext("{*}object/{*}name"))
        iid = off.get("internal-id") or ""
        if not slug or not iid:
            continue
        g = lot_view_groups(slug, iid)
        photos = ([(TYPE_INTERIOR, u) for u in g["interior"]]
                  + [(TYPE_VISUAL, u) for u in g["visualization"]]
                  + [(TYPE_VIEW, u) for u in g["view"]]
                  + [(TYPE_PHOTO, u) for u in common[slug]])
        if not photos:
            continue
        children = list(off)
        images = [(i, c) for i, c in enumerate(children) if c.tag == tag("image")]
        plans = [i for i, c in images if c.get("type") == "plan"]
        plan_floors = [i for i, c in images if c.get("type") == "plan floor"]
        if plans:
            anchor_i = plans[-1]
        elif plan_floors:
            anchor_i = plan_floors[-1]
        elif images:
            anchor_i = images[-1][0]
        else:
            anchor_i = len(children) - 1
        anchor = children[anchor_i]
        pos = list(off).index(anchor) + 1
        for typ, url in photos:
            el = ET.Element(tag("image"), {"type": typ})
            el.text = url
            el.tail = anchor.tail
            off.insert(pos, el)
            pos += 1
            added += 1
        enriched += 1

    ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"offers": total, "enriched": enriched, "images_added": added}
