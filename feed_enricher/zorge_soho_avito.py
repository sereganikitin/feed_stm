"""Отдельный Avito-фид апартаментов Зорге — корпус 3 «Soho», вторичка.

15 конкретных лотов (по «Усл номер квартиры»). Данные (цена/площадь/этаж/потолки/
студия) — из profitbase_xml. Фото по лоту: (1) интерьер из папки ЯД по усл.номеру
(есть не у всех) → (2) сырая планировка ProfitBase (image type=plan, БЕЗ обогащения)
→ (3) общие фото из второй папки ЯД (одинаковые для всех). Описание — пока общее
(текст от 348/4). Шаблон Avito «Продам · Вторичка».

Роут /feed/zorge9-soho-avito.xml ; триггер POST /refresh-soho.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import CACHE_DIR, PUBLIC_BASE_URL, file_ver, PROJECTS
from .parser import download_feed
from . import yadisk

# «Усл номер квартиры» → ExternalId (internal-id в profitbase_xml). Имя папки ЯД —
# усл.номер с заменой «/»→«.» (342/2 → 342.2).
SOHO = {
    "11/1": "17622307", "11/2": "17622308", "17/2": "17468940", "151/2": "17622342",
    "252/3": "17622366", "317/1": "17622400", "328/1": "17622407", "342/2": "17622426",
    "343/2": "17622429", "345/1": "17622433", "346/1": "17622436", "348/4": "17622443",
    "365/4": "17622492", "368/1": "17622498", "371/2": "17622507",
}
INTERIOR_YD = "https://disk.360.yandex.ru/d/GM4wly-ZNxV-xw"   # интерьер (подпапки по усл.номеру)
COMMON_YD   = "https://disk.360.yandex.ru/d/6idk2ATkDGOZWg"   # общие фото (для всех)

OUT       = CACHE_DIR / "soho" / "avito.xml"
PHOTO_DIR = CACHE_DIR / "soho" / "photos"

ADDRESS = "Москва, улица Зорге 9, Корпус 3"
LAT, LNG = "55.783173", "37.509525"

# Описание — пока общее для всех лотов (текст от 348/4, плейсхолдер).
DESCRIPTION = (
    "Готовая студия на Ходынке. 4 станции метро, 5 минут до «Москва-Сити», напротив "
    "«Березовая роща» (44 га). Свой парк 2 га с фонтанами, беллмен и консьерж 24/7, "
    "роскошные лобби, фитнес-центр 3000 м², 5 ресторанов, ВкусВилл, и т.д.\n\n"
    "Ходынка — новый деловой центр Москвы: здесь реализуется «Большой Сити» с "
    "масштабным ростом офисной инфраструктуры (потенциал Ходынского кластера — 2 млн "
    "м² к 2035 году), что формирует устойчивый спрос на аренду жилья со стороны "
    "сотрудников «Сити», IT-компаний и федеральных офисов.\n\n"
    "— м. Полежаевская — 6 мин. пешком\n"
    "— МЦК Зорге — 7 мин. пешком\n"
    "— 5 мин. до ТТК\n"
    "— 9 мин. до Тверской\n"
    "— 10 мин. пешком до Ходынского поля\n"
    "— 20 мин. пешком до ТРЦ «Авиапарк»\n\n"
    "Планировка и характеристики:\n\n"
    "Студия выполнена в формате свободной планировки, что позволяет эргономично "
    "организовать пространство и выделить три функциональные зоны: кухню, спальное "
    "место и рабочую зону. Высокие потолки 3,25 м. Панорамные окна 2,75 м.\n\n"
    "Инфраструктура комплекса полностью готова:\n\n"
    "Фитнес-центр 3 000 м² с бассейном 25 м, спа-зоной, залом для йоги. Комьюнити-центр "
    "с кинотеатром, библиотекой и игровой зоной. Детский плейхаб «Две башни». "
    "Двухуровневый подземный паркинг на 400 м/м с зарядкой для электрокаров. Комплексная "
    "безопасность: более 500 камер видеонаблюдения. На территории — 5 ресторанов, "
    "3 супермаркета, чайная 24/7 «Чай китайской панды», лейбл Гуфа «DOMA», 2 салона "
    "красоты, 3 груминга, студия развития «Береза», пекарня-кондитерская, аптеки, "
    "пункты выдачи.\n\n"
    "Для инвесторов предусмотрена услуга «Доверительное управление» от управляющей "
    "компании «Pure Home Comfort», которая берет на себя все процессы «под ключ» — "
    "от поиска арендаторов до операционного сопровождения."
)


def _folder(usl: str) -> str:
    return usl.replace("/", ".")


def _sync_photos() -> None:
    """Синк общих фото + интерьера по лотам с Яндекс.Диска (зеркало)."""
    try:
        yadisk.sync_public_folder(COMMON_YD, "/", PHOTO_DIR / "common", mirror=True)
    except Exception as e:
        print(f"[soho] common photos sync failed: {e}")
    for usl in SOHO:
        dest = PHOTO_DIR / "int" / _folder(usl)
        try:
            yadisk.sync_public_folder(INTERIOR_YD, "/" + _folder(usl), dest, mirror=True)
        except Exception as e:
            print(f"[soho] interior {usl} sync failed: {e}")


def _images(usl: str, raw_plan: str) -> list:
    """Порядок: интерьер (если есть) → сырая планировка ProfitBase → общие фото."""
    urls = []
    fol = _folder(usl)
    idir = PHOTO_DIR / "int" / fol
    if idir.exists():
        for f in sorted(idir.glob("*.jpg")):
            urls.append(f"{PUBLIC_BASE_URL}/soho-img/int/{fol}/{file_ver(f)}/{f.name}")
    if raw_plan:
        urls.append(raw_plan)                       # сырой план из ProfitBase (без обогащения)
    for f in sorted((PHOTO_DIR / "common").glob("*.jpg")):
        urls.append(f"{PUBLIC_BASE_URL}/soho-img/common/{file_ver(f)}/{f.name}")
    return urls


def _val(el):
    return (el.text or "").strip() if el is not None else ""


def refresh() -> dict:
    _sync_photos()
    raw = download_feed(PROJECTS["zorge9"]["euro_source_url"])
    root = ET.fromstring(raw)
    loc = lambda t: t.split("}")[-1]
    by_id = {}
    for off in root.iter():
        if loc(off.tag) == "offer" and off.get("internal-id") in SOHO.values():
            by_id[off.get("internal-id")] = off

    ads = ET.Element("Ads", {"formatVersion": "3", "target": "Avito.ru"})
    n = 0
    for usl, ext in SOHO.items():
        off = by_id.get(ext)
        if off is None:
            continue
        f = {loc(c.tag): c for c in off}
        area = _val(f["area"].find("{*}value")) if "area" in f else ""
        price = _val(f["price"].find("{*}value")) if "price" in f else ""
        floor = _val(f.get("floor"))
        floors = _val(f["house"].find("{*}floors-total")) if "house" in f else ""
        built = _val(f["house"].find("{*}built-year")) if "house" in f else ""
        studio = _val(f.get("studio")) == "1"
        ceil = ""
        raw_plan = ""
        for c in off.iter():
            t = loc(c.tag)
            if t == "image" and c.get("type") == "plan" and not raw_plan:
                raw_plan = (c.text or "").strip()
            if t in ("custom-field", "custom_field"):
                nm = vl = ""
                for x in c:
                    if loc(x.tag) == "name": nm = (x.text or "").strip()
                    if loc(x.tag) == "value": vl = (x.text or "").strip()
                if nm == "Высота потолка" and vl:
                    ceil = vl.replace(".", ",")
        imgs = _images(usl, raw_plan)
        if not (price and area and imgs):
            continue

        ad = ET.SubElement(ads, "Ad")

        def T(tag, v):
            if v is None or str(v).strip() == "":
                return
            ET.SubElement(ad, tag).text = str(v)

        T("Id", ext)
        T("Category", "Квартиры")
        T("OperationType", "Продам")
        T("MarketType", "Вторичка")
        T("PropertyRights", "Собственник")
        T("ContactMethod", "По телефону и в сообщениях")
        T("ManagerName", "Отдел продаж")
        T("ContactPhone", "+74951267404")
        T("Address", ADDRESS)
        T("Latitude", LAT)
        T("Longitude", LNG)
        T("Description", DESCRIPTION)
        T("Price", price)
        T("Square", area)
        T("Rooms", "Студия" if studio else "1")
        T("Floor", floor)
        T("Floors", floors)
        T("Status", "Апартаменты")
        T("HouseType", "Монолитный")
        T("Decoration", "Без отделки")
        if ceil:
            T("CeilingHeight", ceil)
        if built:
            T("BuiltYear", built)
        T("AdStatus", "Free")
        im = ET.SubElement(ad, "Images")
        for u in imgs[:40]:
            ET.SubElement(im, "Image", {"url": u})
        n += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(ads)
    ET.ElementTree(ads).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"ads": n}
