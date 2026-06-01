"""Сборка нового XML-фида с подменой URL планировок.

Формат фида — CIAN-XML v2.x (CamelCase теги). У каждого <object> есть:
  • <LayoutPhoto><FullUrl>...</FullUrl></LayoutPhoto>          — план квартиры
  • <Photos><PhotoSchema><FullUrl/><IsDefault>1|0</IsDefault></PhotoSchema>...

Стратегия:
1. Подменяем <LayoutPhoto><FullUrl> на наш обогащённый PNG.
2. Дополнительно в <Photos> ищем PhotoSchema с IsDefault=1 — тоже подменяем
   (чтобы классифайды показывали обогащённую карточку как основное превью).
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import PUBLIC_BASE_URL
from .parser import FeedLot


def _public_url_for(slug: str, internal_id: str) -> str:
    return f"{PUBLIC_BASE_URL}/enriched/{slug}/{internal_id}.png"


def assemble_feed(slug: str, original_xml: bytes,
                  lots: list[FeedLot], out_path: Path) -> Path:
    root = ET.fromstring(original_xml)
    by_id = {l.internal_id: l for l in lots}

    for obj in root.iter("object"):
        iid = (obj.findtext("ExternalId") or "").strip()
        if iid not in by_id:
            continue
        new_url = _public_url_for(slug, iid)

        # 1) LayoutPhoto/FullUrl — основной план
        lp = obj.find("LayoutPhoto")
        if lp is not None:
            full = lp.find("FullUrl")
            if full is None:
                full = ET.SubElement(lp, "FullUrl")
            full.text = new_url

        # 2) Photos/PhotoSchema[IsDefault=1]/FullUrl — основное превью карточки
        photos = obj.find("Photos")
        if photos is not None:
            for p in photos.findall("PhotoSchema"):
                if (p.findtext("IsDefault") or "").strip() == "1":
                    full = p.find("FullUrl")
                    if full is None:
                        full = ET.SubElement(p, "FullUrl")
                    full.text = new_url
                    break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
