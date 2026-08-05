"""Отдельные фиды коммерции Зорге для классифайдов (ЦИАН + Авито).

Тот же набор лотов, что comm_zorge_cian, но с ДРУГИМИ текстами описаний (из Google
Doc, вшиты в comm_zorge_cls_texts.json) и ПОРЯДКОМ (из PDF клиента: 9 аренда →
продажа Фитнес/ГАБ371/Йога4/Йога3/Йога2/ГАБ894). Данные (цена/площадь/фото/
категория/адрес/назначение) берём из готового comm_zorge_cian.OUT — меняем ТОЛЬКО
Description и порядок.

Форматирование (жирный и т.п.) площадками в описании не передаётся: ЦИАН — только
plain text (абзацы через переносы), Авито plain по умолчанию. Значок «●» → «—».
Длина: ЦИАН ≤3000 (усечение по границе предложения), Авито ≤7500 (полный текст).
"""
import copy
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import CACHE_DIR
from . import comm_zorge_cian, comm_avito

OUT_DIR   = CACHE_DIR / "comm_zorge_cls"
OUT_CIAN  = OUT_DIR / "cian.xml"
OUT_AVITO = OUT_DIR / "avito.xml"

_TEXTS_PATH = Path(__file__).parent / "comm_zorge_cls_texts.json"


def _load_texts() -> dict:
    """Тексты читаем при каждой сборке (обновление json не требует рестарта)."""
    return json.loads(_TEXTS_PATH.read_text("utf-8"))


# Порядок лотов: аренда (PDF 1-9), затем продажа (Фитнес, ГАБ371, Йога 4/3/2 эт, ГАБ894).
# Каждый Йога-этаж — свой текст продажи (в Google Doc 6 блоков продажи).
ORDER = ["11901465", "9849983", "13443309R", "11770818", "15077881",
         "9841263", "9849991", "11760880", "9849988",
         "13443309", "16890194", "11242998", "11242974", "11242968", "16890195"]

CIAN_MAX  = 3000
AVITO_MAX = 7500


def _clean(t: str) -> str:
    t = comm_zorge_cian._clean_desc(t)   # &→и, «»→", –→-, №/\ убрать
    t = t.replace("●", "—")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _fit(t: str, limit: int) -> str:
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep in ("\n", ". "):
        i = cut.rfind(sep)
        if i > limit * 0.6:
            return cut[: i + (0 if sep == "\n" else 1)].strip()
    return cut.strip()


def _set_desc(o, text: str) -> None:
    d = o.find("Description")
    if d is None:
        d = ET.SubElement(o, "Description")
    d.text = text


def build() -> dict:
    texts = _load_texts()
    src = ET.parse(comm_zorge_cian.OUT).getroot()
    objs = {o.findtext("ExternalId"): o for o in src.findall("object")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── ЦИАН: переупорядоченные объекты с новыми (усечёнными) описаниями ──
    root = ET.Element("feed")
    ET.SubElement(root, "feed_version").text = "2"
    n_cian = 0
    for eid in ORDER:
        o = objs.get(eid)
        if o is None or eid not in texts:
            continue
        oc = copy.deepcopy(o)
        _set_desc(oc, _fit(_clean(texts[eid]), CIAN_MAX))
        root.append(oc)
        n_cian += 1
    ET.indent(root)
    ET.ElementTree(root).write(OUT_CIAN, encoding="utf-8", xml_declaration=True)

    # ── Авито: конвертация тех же объектов (полный текст) через comm_avito ──
    cfg = comm_avito.settings()
    aroot = ET.Element("Ads", {"formatVersion": "3", "target": "Avito.ru"})
    for eid in ORDER:
        o = objs.get(eid)
        if o is None or eid not in texts:
            continue
        oc = copy.deepcopy(o)
        _set_desc(oc, _fit(_clean(texts[eid]), AVITO_MAX))
        comm_avito._add_ad(aroot, oc, cfg)
    ET.indent(aroot)
    ET.ElementTree(aroot).write(OUT_AVITO, encoding="utf-8", xml_declaration=True)

    return {"cian": n_cian, "avito": len(aroot.findall("Ad"))}
