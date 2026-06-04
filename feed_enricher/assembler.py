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

from .config import PUBLIC_BASE_URL, project_dirs, file_ver, lot_view_urls
from .parser import FeedLot


# Обратный маппинг lot.rooms → код FlatRoomsCount ЦИАН (см. parser.ROOMS_MAP)
_ROOMS_TO_CIAN = {0: 9, -1: 7, 6: 10}


def _public_url_for(slug: str, internal_id: str) -> str:
    # Версия в ПУТИ (не query) — Яндекс.Недвижимость отвергает картинки с ?v=, ждёт .png на конце
    png = project_dirs(slug)["enriched"] / f"{internal_id}.png"
    return f"{PUBLIC_BASE_URL}/enriched/{slug}/{file_ver(png)}/{internal_id}.png"


def assemble_feed(slug: str, original_xml: bytes,
                  lots: list[FeedLot], out_path: Path) -> Path:
    root = ET.fromstring(original_xml)
    by_id = {l.internal_id: l for l in lots}

    for obj in root.iter("object"):
        iid = (obj.findtext("ExternalId") or "").strip()
        if iid not in by_id:
            continue
        new_url = _public_url_for(slug, iid)
        lot = by_id[iid]

        # 0) Комнатность — приводим к числу по описанию (как на картинке и в Авито/Яндекс),
        # чтобы во всех фидах было одинаково. lot.rooms уже учитывает описание.
        fr = obj.find("FlatRoomsCount")
        if fr is not None:
            fr.text = str(_ROOMS_TO_CIAN.get(lot.rooms, lot.rooms))

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

        # 3) Виды из окон лота — добавляем доп. PhotoSchema
        view_urls = lot_view_urls(slug, iid)
        if view_urls:
            if photos is None:
                photos = ET.SubElement(obj, "Photos")
            for u in view_urls:
                ps = ET.SubElement(photos, "PhotoSchema")
                ET.SubElement(ps, "FullUrl").text = u
                ET.SubElement(ps, "IsDefault").text = "0"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
