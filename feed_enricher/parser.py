"""Парсинг фида ProfitBase в формате CIAN-XML v2.x (CamelCase теги).

Реальная структура одного <object>:
  <Category>newBuildingFlatSale</Category>
  <ExternalId>5069059</ExternalId>
  <TotalArea>24.9</TotalArea>
  <FlatRoomsCount>9</FlatRoomsCount>   <!-- 1..5: 1K..5K; 9: студия; 7: своб.планировка; 10: 5+К -->
  <IsApartments>true</IsApartments>
  <FloorNumber>16</FloorNumber>
  <Decoration>without</Decoration>      <!-- without / rough / finishing / fineWithFurniture -->
  <LayoutPhoto>
    <FullUrl>...png</FullUrl>
    <IsDefault>1</IsDefault>
  </LayoutPhoto>
  <Photos>
    <PhotoSchema>
      <FullUrl>...</FullUrl>
      <IsDefault>0|1</IsDefault>
    </PhotoSchema>
    ...
  </Photos>
  <JKSchema>
    <Id>19487</Id>
    <Name>Зорге 9</Name>
    <House>
      <Id>39584</Id>
      <Name>Корпус 1. Madison</Name>
    </House>
  </JKSchema>
  <Building>
    <FloorsCount>23</FloorsCount>
  </Building>
  <BargainTerms>
    <Price>18890623.2</Price>
    <currency>rur</currency>
  </BargainTerms>
"""
import requests
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Маппинг кодов комнатности из ЦИАН XML на «человеческое» значение
ROOMS_MAP = {
    1: 1, 2: 2, 3: 3, 4: 4, 5: 5,
    7: -1,   # своб. планировка
    9: 0,    # студия
    10: 6,   # 5+ комнат
}

_QUARTER_MAP = {"first": 1, "second": 2, "third": 3, "fourth": 4}

# Маппинг отделки ЦИАН
DECORATION_MAP = {
    "without":          "без отделки",
    "rough":            "черновая",
    "finishing":        "чистовая",
    "fineWithFurniture":"с мебелью",
    "":                 "",
}


@dataclass
class FeedLot:
    """Один лот из XML-фида."""
    internal_id: str
    rooms: int = -1
    area_total: float = 0.0
    floor: int = 0
    floors_total: int = 0
    price: int = 0
    decoration: str = ""
    house_name: str = ""
    jk_name: str = ""
    plan_url: str = ""              # <LayoutPhoto><FullUrl> (приоритет)
    other_photos: list[str] = field(default_factory=list)
    is_apartments: bool = False
    # ─── поля, нужные для конвертации в формат Авито ───
    address: str = ""              # <Address>
    phone: str = ""                # <Phones><PhoneSchema> → +7XXXXXXXXXX
    description: str = ""          # <Description>
    developer: str = ""            # <JKSchema><Developer>
    video_url: str = ""            # <Videos><VideoSchema><Url>
    # ─── срок сдачи (для Яндекс.Недвижимости) ───
    built_year: int = 0
    ready_quarter: int = 0         # 1..4
    building_complete: bool = False
    raw_xml: Optional[ET.Element] = None


def download_feed(url: str, save_to: Optional[Path] = None) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    if save_to:
        save_to.write_bytes(r.content)
    return r.content


def parse_feed(xml_bytes: bytes) -> list[FeedLot]:
    root = ET.fromstring(xml_bytes)
    lots: list[FeedLot] = []
    for obj in root.iter("object"):
        lot = FeedLot(internal_id=(obj.findtext("ExternalId") or "").strip())
        lot.raw_xml = obj
        lot.area_total = _to_float(obj.findtext("TotalArea"))
        lot.floor      = _to_int(obj.findtext("FloorNumber"))
        lot.address     = _clean_address(obj.findtext("Address"))
        lot.description  = (obj.findtext("Description") or "").strip()
        # Телефон: <Phones><PhoneSchema><CountryCode/><Number/></PhoneSchema>
        ph = obj.find("Phones/PhoneSchema")
        if ph is not None:
            cc  = (ph.findtext("CountryCode") or "").strip()
            num = (ph.findtext("Number") or "").strip()
            lot.phone = _normalize_phone(cc, num)
        # Видео (первое)
        vid = obj.find("Videos/VideoSchema")
        if vid is not None:
            lot.video_url = (vid.findtext("Url") or "").strip()
        rooms_code = _to_int(obj.findtext("FlatRoomsCount"))
        lot.rooms = ROOMS_MAP.get(rooms_code, rooms_code)
        lot.is_apartments = (obj.findtext("IsApartments") or "").strip().lower() == "true"
        # Отделка
        dec_code = (obj.findtext("Decoration") or "").strip()
        lot.decoration = DECORATION_MAP.get(dec_code, dec_code)
        # Цена внутри <BargainTerms><Price>
        bt = obj.find("BargainTerms")
        if bt is not None:
            lot.price = _to_int(bt.findtext("Price"))
        # ЖК и корпус
        jk = obj.find("JKSchema")
        if jk is not None:
            lot.jk_name = (jk.findtext("Name") or "").strip()
            lot.developer = (jk.findtext("Developer") or "").strip()
            house = jk.find("House")
            if house is not None:
                lot.house_name = (house.findtext("Name") or "").strip()
        # Этажей всего
        bld = obj.find("Building")
        if bld is not None:
            lot.floors_total = _to_int(bld.findtext("FloorsCount"))
            dl = bld.find("Deadline")
            if dl is not None:
                lot.built_year = _to_int(dl.findtext("Year"))
                lot.ready_quarter = _QUARTER_MAP.get((dl.findtext("Quarter") or "").strip().lower(), 0)
                lot.building_complete = (dl.findtext("IsComplete") or "").strip().lower() == "true"
        # LayoutPhoto — основной план (приоритет)
        lp = obj.find("LayoutPhoto")
        if lp is not None:
            lot.plan_url = (lp.findtext("FullUrl") or "").strip()
        # Photos — остальные
        photos = obj.find("Photos")
        if photos is not None:
            for p in photos.findall("PhotoSchema"):
                url = (p.findtext("FullUrl") or "").strip()
                if url:
                    lot.other_photos.append(url)
        # Fallback: если LayoutPhoto пустой — берём первое фото с IsDefault=0 типа layout
        if not lot.plan_url and lot.other_photos:
            lot.plan_url = lot.other_photos[0]
        lots.append(lot)
    return lots


def _clean_address(s) -> str:
    """Чистит адрес ProfitBase: убирает подряд идущие дубли частей.

    'Москва, Москва, Москва, улица Зорге 9, Корпус 1'
        → 'Москва, улица Зорге 9, Корпус 1'
    """
    if not s:
        return ""
    parts = [p.strip() for p in str(s).split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        if not out or out[-1].lower() != p.lower():
            out.append(p)
    return ", ".join(out)


def _normalize_phone(country_code: str, number: str) -> str:
    """('+7', '4954325291') → '+74954325291'. Любой ввод → формат +7XXXXXXXXXX."""
    digits = "".join(ch for ch in f"{country_code}{number}" if ch.isdigit())
    if not digits:
        return ""
    # 8XXXXXXXXXX → 7XXXXXXXXXX; 10 цифр без кода → префикс 7
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return "+" + digits


def _to_float(s) -> float:
    try:
        return float(str(s).replace(",", ".")) if s else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_int(s) -> int:
    try:
        return int(float(str(s))) if s else 0
    except (TypeError, ValueError):
        return 0
