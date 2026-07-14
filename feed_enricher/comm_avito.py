"""Общий Avito-фид коммерции (Зорге + Б37, продажа + аренда) — конвертация из
готовых CIAN-коммерц-фидов (comm_zorge_cian.OUT + comm_cian_rent.OUT).

Как в CIAN: продажа/аренда в одном фиде, признак — суффикс Category (…Rent/…Sale);
здесь он превращается в OperationType (Сдам/Продам). Цена уже посчитана в CIAN-фиде
(аренда — ₽/мес, продажа — полная), берём готовую.

Формат — Авито Автозагрузка, категория «Коммерческая недвижимость» (шаблон Авито
«Коммерческая недвижимость», см. поля ниже). Много обязательных полей-атрибутов
которых нет в ProfitBase (отделка/охрана/парковка/свет…) — ставим РАЗУМНЫЕ ДЕФОЛТЫ
(константы DEFAULTS), потом можно уточнять.

ВАЖНО: коммерческая схема Авито строгая и часть требований условна (зависит от
ObjectType). После сборки прогнать через валидатор Авито (autoload.avito.ru или
загрузку в кабинете) и доправить по факту. Габариты (Width/Length), Layout — не
заполняем (нет данных / условно-обязательны), уточняем по валидатору.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import CACHE_DIR
from . import comm_zorge_cian, comm_cian_rent

OUT = CACHE_DIR / "comm_avito" / "avito.xml"

# Источники — готовые CIAN-фиды коммерции
SOURCES = [comm_zorge_cian.OUT, comm_cian_rent.OUT]

# Контакт (обе площадки — St MICHAEL). Телефон берём из самого CIAN-объекта (Phones).
MANAGER = "St MICHAEL"

# CIAN-назначение (Specialty id) → Avito ObjectType (приоритетнее категории)
SPEC_OBJ = {
    "publicCatering":     "Помещение общественного питания",
    "office":             "Офисное помещение",
    "shoppingFloorSpace": "Торговое помещение",
    "trading":            "Торговое помещение",
}
# CIAN-категория (без суффикса Rent/Sale) → Avito ObjectType
CAT_OBJ = {
    "office":                "Офисное помещение",
    "shoppingArea":          "Торговое помещение",
    "freeAppointmentObject": "Помещение свободного назначения",
    "building":              "Здание",
}

# Разумные дефолты для обязательных полей-атрибутов, которых нет в ProfitBase
# (премиальный ЖК, коммерция на 1-х этажах). Меняются здесь.
DEFAULTS = {
    "PropertyRights": "Собственник",
    "Decoration":     "Без отделки",
    "Security":       "Есть",
    "AccessSchedule": "24/7",
    "CarAccess":      "Есть",
    "Lighting":       "Есть",
    "PowerSockets":   "Есть",
    "Heating":        "Есть",
    "BuildingType":   "Жилой дом",   # для отдельных зданий — «Другой» (см. _obj)
    "ContactMethod":  "По телефону и в сообщениях",
}


def _op_and_base(category: str):
    """('shoppingAreaRent') → ('Сдам', 'shoppingArea')."""
    for suf, op in (("Rent", "Сдам"), ("Sale", "Продам")):
        if category.endswith(suf):
            return op, category[: -len(suf)]
    return "Продам", category


def _obj_type(base: str, specialties: list) -> str:
    for s in specialties:
        if s in SPEC_OBJ:
            return SPEC_OBJ[s]
    return CAT_OBJ.get(base, "Помещение свободного назначения")


def _images(o) -> list:
    urls = []
    lp = (o.findtext("LayoutPhoto/FullUrl") or "").strip()
    if lp:
        urls.append(lp)
    for ps in o.findall("Photos/PhotoSchema"):
        u = (ps.findtext("FullUrl") or "").strip()
        if u and u not in urls:
            urls.append(u)
    return urls


def _phone(o) -> str:
    cc = (o.findtext("Phones/PhoneSchema/CountryCode") or "").strip()
    num = (o.findtext("Phones/PhoneSchema/Number") or "").strip()
    digits = "".join(ch for ch in f"{cc}{num}" if ch.isdigit())
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return "+" + digits if digits else ""


def _add_ad(root, o) -> bool:
    cat = (o.findtext("Category") or "").strip()
    ext = (o.findtext("ExternalId") or "").strip()
    area = (o.findtext("TotalArea") or "").strip()
    price = (o.findtext("BargainTerms/Price") or "").strip()
    imgs = _images(o)
    desc = (o.findtext("Description") or "").strip()
    addr = (o.findtext("Address") or "").strip()
    if not (ext and area and price and imgs and desc and addr):
        return False

    op, base = _op_and_base(cat)
    specs = [s.text.strip() for s in o.findall("Specialty/Types/String") if s.text]
    obj = _obj_type(base, specs)

    ad = ET.SubElement(root, "Ad")

    def T(tag, val):
        if val is None or str(val).strip() == "":
            return
        ET.SubElement(ad, tag).text = str(val)

    T("Id", ext)
    T("Category", "Коммерческая недвижимость")
    T("OperationType", op)
    T("ObjectType", obj)
    T("Title", f"{obj}, {area} м²"[:50])
    T("Description", desc)
    T("Address", addr)
    try:
        T("Price", int(round(float(price))))
    except ValueError:
        T("Price", price)
    T("PriceType", "за всё")
    T("Square", area)
    floor = (o.findtext("FloorNumber") or "").strip()
    if floor:
        T("Floor", floor)
    # Готовность — из Building/Deadline/IsComplete (Зорге сдан, Б37 строится)
    complete = (o.findtext("Building/Deadline/IsComplete") or "").strip().lower() == "true"
    T("ReadinessStatus", "В эксплуатации" if complete else "Строится")
    # Продажа: тип сделки
    if op == "Продам":
        T("TransactionType", "Продажа")
    # Атрибуты-дефолты
    T("PropertyRights", DEFAULTS["PropertyRights"])
    T("Decoration",     DEFAULTS["Decoration"])
    T("Security",       DEFAULTS["Security"])
    T("AccessSchedule", DEFAULTS["AccessSchedule"])
    T("CarAccess",      DEFAULTS["CarAccess"])
    T("Lighting",       DEFAULTS["Lighting"])
    T("PowerSockets",   DEFAULTS["PowerSockets"])
    T("Heating",        DEFAULTS["Heating"])
    T("BuildingType", "Другой" if base == "building" else DEFAULTS["BuildingType"])
    # Контакты
    T("ContactPhone", _phone(o))
    T("ManagerName", MANAGER)
    T("ContactMethod", DEFAULTS["ContactMethod"])
    # Картинки
    im = ET.SubElement(ad, "Images")
    for u in imgs[:40]:
        ET.SubElement(im, "Image", {"url": u})
    return True


def refresh() -> dict:
    root = ET.Element("Ads", {"formatVersion": "3", "target": "Avito.ru"})
    n = 0
    for src in SOURCES:
        if not Path(src).exists():
            continue
        try:
            croot = ET.parse(src).getroot()
        except Exception as e:
            print(f"[comm-avito] parse {src} failed: {e}")
            continue
        for o in croot.findall("object"):
            if _add_ad(root, o):
                n += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"ads": n}
