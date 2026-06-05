"""Парсер ProfitBase XML (формат Яндекс-realty, type=profitbase_xml).

Используется для коммерческих помещений (аренда) — Б37 «Коммерция» и подобных.
Структура offer:
  <offer internal-id="...">
    <property_type>Помещение свободного назначения</property_type>
    <object><name>ЖК</name><location>...</location></object>
    <house><name>...</name></house>
    <area><value>155.71</value><unit>кв. м</unit></area>
    <floor>...</floor>                         (у коммерции часто нет)
    <image type="plan">URL</image> ...
    <custom-field><name>Стоимость аренды, руб./мес.</name><value>1009000,8</value></custom-field>
    ... (Высота потолка, Подводимая мощность, Стоимость аренды за кв.м./год)
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

NS = "{http://webmaster.yandex.ru/schemas/feed/realty/2010-06}"


@dataclass
class PBLot:
    internal_id: str
    jk_name: str = ""
    house_name: str = ""
    property_type: str = ""           # назначение (Помещение свободного назначения)
    area: float = 0.0
    floor: int = 0
    rent_month: float = 0.0           # аренда руб./мес
    rent_m2_year: float = 0.0         # аренда за м²/год
    ceiling_m: float = 0.0            # высота потолка, м
    power_kw: str = ""                # подводимая мощность, кВт
    address: str = ""
    description: str = ""
    images: list = field(default_factory=list)   # планы/фото (URL)
    creation_date: str = ""
    last_update: str = ""


def _f(s) -> float:
    try:
        return float(str(s).replace(" ", "").replace(",", ".")) if s else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_pb_feed(xml_bytes: bytes) -> list:
    root = ET.fromstring(xml_bytes)
    lots = []
    for o in root.iter(f"{NS}offer"):
        lot = PBLot(internal_id=(o.get("internal-id") or "").strip())
        lot.property_type = (o.findtext(f"{NS}property_type") or "").strip()
        lot.creation_date = (o.findtext(f"{NS}creation-date") or "").strip()
        lot.last_update = (o.findtext(f"{NS}last-update-date") or "").strip()
        lot.floor = int(_f(o.findtext(f"{NS}floor"))) if o.find(f"{NS}floor") is not None else 0

        obj = o.find(f"{NS}object")
        if obj is not None:
            lot.jk_name = (obj.findtext(f"{NS}name") or "").strip()
            locel = obj.find(f"{NS}location")
            if locel is not None:
                parts = [locel.findtext(f"{NS}locality-name"), locel.findtext(f"{NS}address")]
                lot.address = ", ".join(p.strip() for p in parts if p and p.strip())
        house = o.find(f"{NS}house")
        if house is not None:
            lot.house_name = (house.findtext(f"{NS}name") or "").strip()

        area = o.find(f"{NS}area")
        if area is not None:
            lot.area = _f(area.findtext(f"{NS}value"))

        # картинки (планы/фото)
        for im in o.iter(f"{NS}image"):
            if im.text and im.text.strip():
                lot.images.append(im.text.strip())

        # описание
        lot.description = (o.findtext(f"{NS}description") or "").strip()

        # custom-fields
        for cf in o.iter(f"{NS}custom-field"):
            nm = (cf.findtext(f"{NS}name") or "").strip()
            val = (cf.findtext(f"{NS}value") or "").strip()
            if not val:
                continue
            if nm == "Стоимость аренды, руб./мес.":
                lot.rent_month = _f(val)
            elif nm == "Стоимость аренды за кв.м., руб./год":
                lot.rent_m2_year = _f(val)
            elif nm == "Высота потолка":
                # значение в мм (3500) → метры
                v = _f(val)
                lot.ceiling_m = v / 1000 if v > 100 else v
            elif nm == "Подводимая мощность, кВт":
                lot.power_kw = val.replace(",", ".")
        lots.append(lot)
    return lots
