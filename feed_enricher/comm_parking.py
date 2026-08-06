"""Машиноместа Зорге 9 в коммерческих фидах (ЦИАН + Авито).

Источник — отдельный ProfitBase-фид паркинга (машиноместа+кладовки, Яндекс-realty
XML). Берём КОНКРЕТНЫЕ места из LOTS (задача клиента), склеиваем «семейные» пары
в один лот (сумма площади и цены). Отдаём как CIAN-объекты категории `garageSale`
— их дописываем в ЦИАН-фид Зорге (comm_zorge_cian), а Avito-конвертер (comm_avito)
по категории garage* выводит в Avito-категорию «Гаражи и машиноместа».

ВАЖНО: схемы паркинга у площадок строгие и отличаются от коммерции — прогнать
через валидаторы (ЦИАН-кабинет, autoload.avito.ru). Тип места на CIAN — parking.
"""
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import PUBLIC_BASE_URL, file_ver, CACHE_DIR

PARKING_FEED_URL = "https://pb7828.profitbase.ru/export/profitbase_xml/4afc254cede6521b4f96eb1aa9029368?scheme=https"
ADDRESS = "Москва, ул. Зорге, дом 9"
PHONE   = "+74952924193"

# Картинка машиноместа = фото parking.jpg + 2 белые плашки: номер (ММ N) и площадь.
# Динамический текст на плашках делает КАЖДОЕ фото уникальным (Авито не считает дублями).
# Координаты плашек — из SVG-макета клиента (Паркинг_плашки[_длинный].svg → 1200×900).
IMG_PATH  = Path(__file__).parent / "assets" / "parking.jpg"
_FONT     = Path(__file__).parent / "assets" / "fonts" / "TT-Fors-Trial-Medium.ttf"
RENDER_DIR = CACHE_DIR / "parking_img"
_WHITE = (254, 254, 254)
_BLACK = (43, 42, 41)
# np — плашка номера, ap — плашка площади; xxc — центр текста (anchor mm); f — кегль
_LAY_SHORT = {"np": (909, 731, 1146, 791), "npc": (1028, 760),
              "ap": (909, 804, 1146, 864), "apc": (1028, 834), "f": 46}   # одиночное
_LAY_LONG  = {"np": (861, 731, 1177, 791), "npc": (1019, 760),
              "ap": (941, 804, 1177, 864), "apc": (1059, 834), "f": 41}   # семейное (201+201А)


def _ensure_img(ext: str, num_txt: str, area_txt: str, family: bool) -> Path:
    """Отрисовать фото машиноместа с плашками (кэш; перерис. если фото-исходник новее)."""
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    out = RENDER_DIR / f"{ext}.png"
    if out.exists() and out.stat().st_mtime >= IMG_PATH.stat().st_mtime:
        return out
    lay = _LAY_LONG if family else _LAY_SHORT
    im = Image.open(IMG_PATH).convert("RGB")
    d = ImageDraw.Draw(im)
    for rect in (lay["np"], lay["ap"]):
        d.rounded_rectangle(rect, radius=12, fill=_WHITE, outline=_BLACK, width=2)
    font = ImageFont.truetype(str(_FONT), lay["f"])
    d.text(lay["npc"], num_txt,  fill=_BLACK, font=font, anchor="mm")
    d.text(lay["apc"], area_txt, fill=_BLACK, font=font, anchor="mm")
    im.save(out, "PNG", optimize=True)
    return out


def img_url(lot: dict) -> str:
    num = ("+".join(lot["numbers"])).upper()            # 192 / 201+201А
    area = f"{lot['area']:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    out = _ensure_img(lot["ext"], f"ММ {num}", f"{area} м²", lot["family"])
    return f"{PUBLIC_BASE_URL}/parking-img/{lot['ext']}/{file_ver(out)}.png"

# Гаражная категория ЦИАН (по офиц. доке cian.ru/xml_import/doc):
#   <Garage><Type> = box/garage/parkingPlace; <Status> = byProxy/cooperative/ownership.
#   GarageType (builtIn/capital/samostroy/shell) и Material (brick/metal) — только для
#   гараж-БОКСА, машиноместу НЕ нужны.
GARAGE_TYPE   = "parkingPlace"   # <Type>: машиноместо
GARAGE_STATUS = "ownership"      # <Status>: в собственности

# Что выводим: (ExternalId в фиде, [номера мест], семейное?)
# Семейное = смежная пара, продаётся одним лотом (площадь и цена суммируются).
LOTS = [
    ("mm192", ["192"],          False),
    ("mm201", ["201", "201а"],  True),
]


def _txt(o, *path, ns=""):
    e = o
    for p in path:
        if e is None:
            return ""
        e = e.find(f"{{{ns}}}{p}" if ns else p)
    return (e.text or "").strip() if e is not None else ""


def _fetch_offers() -> dict:
    """{номер места: {area, price, floor, bti, plan, addr}} из фида паркинга."""
    raw = urllib.request.urlopen(PARKING_FEED_URL, timeout=60).read()
    root = ET.fromstring(raw)
    ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""
    q = lambda t: f"{{{ns}}}{t}" if ns else t
    out = {}
    for o in root.findall(".//" + q("offer")):
        num = _txt(o, "number", ns=ns)
        if not num:
            continue
        plan = ""
        for im in o.findall(q("image")):
            if im.get("type") == "plan":
                plan = (im.text or "").strip()
                break
        bti = ""
        for c in o.findall(q("custom-field")):
            if _txt(c, "name", ns=ns) == "Номер помещения для БТИ":
                bti = _txt(c, "value", ns=ns)
        out[num] = {
            "area":  _to_float(_txt(o, "area", "value", ns=ns)),
            "price": _to_int(_txt(o, "price", "value", ns=ns)),
            "floor": _txt(o, "floor", ns=ns),
            "bti":   bti,
            "plan":  plan,
            "addr":  _txt(o, "object", "location", "address", ns=ns),
        }
    return out


def get_lots() -> list:
    """Список лотов паркинга к выводу: семейные пары склеены (сумма S и цены)."""
    offers = _fetch_offers()
    lots = []
    for ext, nums, family in LOTS:
        parts = [offers[n] for n in nums if n in offers]
        if not parts:
            continue
        lots.append({
            "ext": ext, "numbers": nums, "family": family,
            "area":  round(sum(p["area"] for p in parts), 1),
            "price": sum(p["price"] for p in parts),
            "floor": parts[0]["floor"],
            "plan":  parts[0]["plan"],
        })
    return lots


def _desc(lot) -> str:
    nums = " и ".join(f"№{n}" for n in lot["numbers"])
    area = f"{lot['area']:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    if lot["family"]:
        head = f"Семейное машиноместо (два смежных места {nums})"
    else:
        head = f"Машиноместо {nums}"
    return (f"{head} в подземном паркинге ЖК «Зорге 9». "
            f"Общая площадь {area} м², этаж {lot['floor']}. Дом сдан.")


def _T(parent, tag, val):
    if val is None or str(val).strip() == "":
        return None
    e = ET.SubElement(parent, tag)
    e.text = str(val)
    return e


def append_cian(root) -> int:
    """Дописать объекты машиномест (Category=garageSale) в CIAN-фид root. Возвращает число."""
    n = 0
    for lot in get_lots():
        o = ET.SubElement(root, "object")
        _T(o, "Category", "garageSale")
        _T(o, "ExternalId", lot["ext"])
        _T(o, "Description", _desc(lot))
        _T(o, "Address", ADDRESS)
        ph = ET.SubElement(o, "Phones"); psc = ET.SubElement(ph, "PhoneSchema")
        _T(psc, "CountryCode", "+7"); _T(psc, "Number", PHONE.lstrip("+7"))
        area = f"{lot['area']:.1f}".rstrip("0").rstrip(".")
        _T(o, "TotalArea", area)
        if lot["floor"]:
            _T(o, "FloorNumber", lot["floor"])
        # Блок гаража ЦИАН: машиноместо в собственности
        g = ET.SubElement(o, "Garage")
        _T(g, "Type", GARAGE_TYPE)
        _T(g, "Status", GARAGE_STATUS)
        # Цена (у гаража: Price + Currency + ContractType=sale)
        bt = ET.SubElement(o, "BargainTerms")
        _T(bt, "Price", lot["price"])
        _T(bt, "Currency", "rur")
        _T(bt, "ContractType", "sale")
        # Картинка — фото parking.jpg с плашками номера/площади (уникальна на лот)
        img = img_url(lot)
        lp = ET.SubElement(o, "LayoutPhoto")
        _T(lp, "FullUrl", img); _T(lp, "IsDefault", "1")
        photos = ET.SubElement(o, "Photos")
        ps = ET.SubElement(photos, "PhotoSchema")
        _T(ps, "FullUrl", img); _T(ps, "IsDefault", "1")
        n += 1
    return n


def _to_float(s):
    try:
        return float(str(s).replace(",", ".")) if s else 0.0
    except ValueError:
        return 0.0


def _to_int(s):
    try:
        return int(float(str(s))) if s else 0
    except ValueError:
        return 0
