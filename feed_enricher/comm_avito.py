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
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import CACHE_DIR, ADMIN_DIR
from . import comm_zorge_cian, comm_cian_rent

OUT = CACHE_DIR / "comm_avito" / "avito.xml"
SETTINGS_PATH = ADMIN_DIR / "comm_avito.json"   # редактируемые дефолты (из панели)

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
# (премиальный ЖК, коммерция на 1-х этажах). Правятся из панели (SETTINGS_PATH),
# перекрывая эти значения; DEFAULTS — фолбэк.
DEFAULTS = {
    "PropertyRights": "Собственник",
    "Decoration":     "Без отделки",
    "Security":       "Есть",
    "AccessSchedule": "24/7",
    "CarAccess":      "Есть",
    "Lighting":       "Есть",
    "PowerSockets":   "Есть",
    "Heating":        "Центральное",  # Авито НЕ принимает «Есть» для Heating (только цент./автон./нет)
    "ParkingType":    "В здании",     # обязательный параметр Авито (Парковка)
    "BuildingType":   "Жилой дом",   # для отдельных зданий — «Другой» (см. _add_ad)
    "ContactMethod":  "По телефону и в сообщениях",
}

# Допустимые значения (из справочника Авито) — для выпадающих списков в панели
# и валидации сохраняемого. Порядок ключей = порядок в форме.
CHOICES = {
    "Decoration":     ["Без отделки", "Чистовая", "Офисная"],
    "BuildingType":   ["Жилой дом", "Бизнес-центр", "Торговый центр", "Административное здание", "Другой"],
    "Security":       ["Есть", "Нет"],
    "AccessSchedule": ["24/7", "По графику"],
    "CarAccess":      ["Есть", "Нет"],
    "Lighting":       ["Есть", "Нет"],
    "PowerSockets":   ["Есть", "Нет"],
    "Heating":        ["Центральное", "Автономное", "Нет"],
    "ParkingType":    ["На улице", "В здании", "Нет"],
    "PropertyRights": ["Собственник", "Посредник"],
    "ContactMethod":  ["По телефону и в сообщениях", "По телефону", "В сообщениях"],
}
# Человеческие подписи полей для панели
LABELS = {
    "Decoration":     "Отделка",
    "BuildingType":   "Тип здания (для отдельных зданий — всегда «Другой»)",
    "Security":       "Охрана",
    "AccessSchedule": "Доступ",
    "CarAccess":      "Подъезд для автомобиля",
    "Lighting":       "Освещение",
    "PowerSockets":   "Электрические розетки",
    "Heating":        "Отопление",
    "ParkingType":    "Парковка",
    "PropertyRights": "Права на объект",
    "ContactMethod":  "Способ связи",
}


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text("utf-8"))
    except Exception:
        return {}


def save_settings(d: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")


def settings() -> dict:
    """Итоговые значения атрибутов: сохранённые в панели поверх DEFAULTS."""
    d = dict(DEFAULTS)
    for k, v in load_settings().items():
        if k in CHOICES and v in CHOICES[k]:
            d[k] = v
    return d


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


def _add_garage_ad(root, ext, area, price, imgs, desc, addr, floor, op, phone) -> bool:
    """Машиноместо → Avito-категория «Гаражи и машиноместа» (отдельная от коммерции)."""
    ad = ET.SubElement(root, "Ad")

    def T(tag, val):
        if val is None or str(val).strip() == "":
            return
        ET.SubElement(ad, tag).text = str(val)

    T("Id", ext)
    T("Category", "Гаражи и машиноместа")
    T("OperationType", op)
    T("ObjectType", "Машиноместо")
    T("Title", f"Машиноместо, {area} м²"[:50])
    T("Description", desc)
    T("Address", addr)
    try:
        T("Price", int(round(float(price))))
    except ValueError:
        T("Price", price)
    T("Square", area)
    if floor:
        T("Floor", floor)
    T("ContactPhone", phone)
    T("ManagerName", MANAGER)
    T("ContactMethod", "По телефону и в сообщениях")
    im = ET.SubElement(ad, "Images")
    for u in imgs[:40]:
        ET.SubElement(im, "Image", {"url": u})
    return True


def _add_ad(root, o, cfg) -> bool:
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
    # Машиноместа/гаражи — своя Avito-категория (не «Коммерческая недвижимость»)
    if base == "garage":
        floor = (o.findtext("FloorNumber") or "").strip()
        return _add_garage_ad(root, ext, area, price, imgs, desc, addr, floor, op, _phone(o))
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
    # Атрибуты (из настроек панели поверх дефолтов)
    T("PropertyRights", cfg["PropertyRights"])
    T("Decoration",     cfg["Decoration"])
    T("Security",       cfg["Security"])
    T("AccessSchedule", cfg["AccessSchedule"])
    T("CarAccess",      cfg["CarAccess"])
    T("Lighting",       cfg["Lighting"])
    T("PowerSockets",   cfg["PowerSockets"])
    T("Heating",        cfg["Heating"])
    T("ParkingType",    cfg["ParkingType"])
    T("BuildingType", "Другой" if base == "building" else cfg["BuildingType"])
    # Контакты
    T("ContactPhone", _phone(o))
    T("ManagerName", MANAGER)
    T("ContactMethod", cfg["ContactMethod"])
    # Картинки
    im = ET.SubElement(ad, "Images")
    for u in imgs[:40]:
        ET.SubElement(im, "Image", {"url": u})
    return True


def refresh() -> dict:
    cfg = settings()
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
            if _add_ad(root, o, cfg):
                n += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"ads": n}
