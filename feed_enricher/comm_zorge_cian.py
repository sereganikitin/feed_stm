"""Зорге 9 — коммерция → ЦИАН-фид (продажа + аренда). Сборка из ProfitBase API.

15 листингов: 6 продажа + 9 аренда (Фитнес идёт И в продажу, И в аренду —
отдельными листингами с разными ExternalId). Нативный экспорт ProfitBase не
годится (помечает всё как продажу, не содержит корпус 3), поэтому собираем
из API по фиксированному списку лотов.

Конфиг ключа — в .env сервера: PB_API_KEY (тот же, что для B37-коммерции).
Фид: /feed/comm/zorge-cian.xml ; ручной триггер POST /refresh-comm-zorge.
"""
import os
import json
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from .config import CACHE_DIR

PB_API_BASE = "https://pb7828.profitbase.ru/api/v4/json"
RENT_FIELD  = "pbcf_6218cf5652a5d"   # «Стоимость аренды, руб./мес.»
NAZ_FIELD   = "pbcf_61e947e1daaa1"   # «Назначение помещения»
ADDRESS     = "Москва, ул. Зорге, дом 9Ак1"
PHONE       = "+74952924193"
# Срок сдачи: дом сдан (3 квартал 2023). Quarter у ЦИАН — словом.
DEADLINE    = {"quarter": "third", "year": "2023", "complete": "true"}
OUT_DIR = CACHE_DIR / "comm_zorge"
OUT = OUT_DIR / "cian.xml"

# (ExternalId в фиде, id помещения в ProfitBase, kind, FloorsCount дома, назначение)
LISTINGS = [
    # ── Продажа: 1-5 ГАБ (готовый арендный бизнес) + Фитнес ──
    ("16890194",  "16890194", "sale", 3, "Готовый арендный бизнес"),   # Здание 1 Велопрокат
    ("16890195",  "16890195", "sale", 4, "Готовый арендный бизнес"),   # Здание 2 Офис продаж
    ("11242968",  "11242968", "sale", 4, "Готовый арендный бизнес"),   # Йога 2 эт
    ("11242974",  "11242974", "sale", 4, "Готовый арендный бизнес"),   # Йога 3 эт
    ("11242998",  "11242998", "sale", 4, "Готовый арендный бизнес"),   # Йога 4 эт
    ("13443309",  "13443309", "sale", 1, "Фитнес"),                    # Фитнес (продажа)
    # ── Аренда: торговые площади + Фитнес (К3П11 пока не выводим) ──
    ("15077881",  "15077881", "rent", 2, "Торговая площадь"),          # К1П6-2
    ("9841263",   "9841263",  "rent", 2, "Торговая площадь"),          # К2П5
    ("11901465",  "11901465", "rent", 3, "Торговая площадь"),          # С6П3
    ("11760880",  "11760880", "rent", 2, "Торговая площадь"),          # К2П12
    ("9849983",   "9849983",  "rent", 2, "Торговая площадь"),          # К3П1
    ("9849988",   "9849988",  "rent", 2, "Торговая площадь"),          # К3П6
    ("9849991",   "9849991",  "rent", 2, "Торговая площадь"),          # К3П9
    ("13443309R", "13443309", "rent", 1, "Фитнес"),                    # Фитнес (аренда)
]


def _api(url, data=None, method="GET", tries=4):
    body = json.dumps(data).encode() if data is not None else None
    h = {"Content-Type": "application/json"} if data is not None else {}
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                last = e; time.sleep(min(2 ** i, 15)); continue
            raise
    raise last


def _auth(key):
    return _api(PB_API_BASE + "/authentication",
                {"type": "api-app", "credentials": {"pb_api_key": key}}, "POST")["access_token"]


def _prop(token, pid):
    p = _api(PB_API_BASE + "/property?full=1&access_token=" + token + "&id=" + str(pid))
    items = p.get("data") if isinstance(p.get("data"), list) else (p.get("data") or p)
    return items[0] if isinstance(items, list) and items else items


def _num(x):
    try:
        return f"{float(x):.1f}".rstrip("0").rstrip(".")
    except Exception:
        return ""


def _cf(it, fid):
    for x in (it.get("custom_fields") or []):
        if x.get("id") == fid:
            return x.get("value")
    return None


def _T(parent, tag, val):
    if val is None or str(val).strip() == "":
        return None
    e = ET.SubElement(parent, tag)
    e.text = str(val)
    return e


def refresh():
    key = os.environ.get("PB_API_KEY", "")
    if not key:
        return {"skipped": "PB_API_KEY не задан"}
    token = _auth(key)
    root = ET.Element("feed")
    ET.SubElement(root, "feed_version").text = "2"
    n_sale = n_rent = 0
    for ext_id, pid, kind, floors, naz in LISTINGS:
        it = _prop(token, pid)
        area = (it.get("area", {}) or {}).get("area_total")
        if not area:
            continue
        o = ET.SubElement(root, "object")
        _T(o, "Category", "freeAppointmentObjectRent" if kind == "rent" else "freeAppointmentObjectSale")
        _T(o, "ExternalId", ext_id)
        _T(o, "Description", it.get("description"))
        _T(o, "Address", ADDRESS)
        ph = ET.SubElement(o, "Phones"); psc = ET.SubElement(ph, "PhoneSchema")
        _T(psc, "CountryCode", "+7"); _T(psc, "Number", PHONE.lstrip("+7"))
        _T(o, "TotalArea", _num(area))
        if it.get("floor") is not None:
            _T(o, "FloorNumber", it.get("floor"))
        bld = ET.SubElement(o, "Building")
        _T(bld, "Name", "Зорге 9")
        _T(bld, "FloorsCount", floors)
        dl = ET.SubElement(bld, "Deadline")
        _T(dl, "Quarter", DEADLINE["quarter"])
        _T(dl, "Year", DEADLINE["year"])
        _T(dl, "IsComplete", DEADLINE["complete"])
        # Цена
        bt = ET.SubElement(o, "BargainTerms")
        if kind == "rent":
            rent = _cf(it, RENT_FIELD)
            try:
                rent = int(round(float(rent)))
            except Exception:
                rent = None
            _T(bt, "Price", rent)
            _T(bt, "currency", "rur")
            _T(bt, "PriceType", "all")
            _T(bt, "PaymentPeriod", "monthly")
            n_rent += 1
        else:
            price = (it.get("price", {}) or {}).get("value")
            _T(bt, "Price", int(price) if price else None)
            _T(bt, "currency", "rur")
            _T(bt, "PriceType", "all")
            n_sale += 1
        # Назначение (задано в LISTINGS по логике клиента)
        sp = ET.SubElement(o, "Speciality"); types = ET.SubElement(sp, "Types")
        for v in str(naz).replace(";", ",").split(","):
            v = v.strip()
            if v:
                ET.SubElement(types, "String").text = v
        # План + фото
        imgs = [im.get("source") for im in (it.get("planImages") or [])
                if im.get("source") and not im.get("technical")]
        cover = it.get("preset") or (imgs[0] if imgs else None)
        if cover:
            lp = ET.SubElement(o, "LayoutPhoto")
            _T(lp, "FullUrl", cover); _T(lp, "IsDefault", "1")
        if imgs:
            photos = ET.SubElement(o, "Photos")
            for i, u in enumerate(imgs):
                ps = ET.SubElement(photos, "PhotoSchema")
                _T(ps, "FullUrl", u); _T(ps, "IsDefault", "1" if i == 0 else "0")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"sale": n_sale, "rent": n_rent, "total": n_sale + n_rent}
