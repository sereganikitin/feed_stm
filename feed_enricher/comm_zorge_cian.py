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
import types
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from .config import (CACHE_DIR, PUBLIC_BASE_URL, file_ver,
                     COMMERCIAL_TEMPLATE_ZORGE_URL, COMMERCIAL_TEMPLATE_ZORGE_EXT,
                     COMMERCIAL_LAYOUT_ZORGE)
from .enricher import enrich_commercial

PB_API_BASE   = "https://pb7828.profitbase.ru/api/v4/json"
RENT_FIELD    = "pbcf_6218cf5652a5d"   # «Стоимость аренды, руб./мес.»
NAZ_FIELD     = "pbcf_61e947e1daaa1"   # «Назначение помещения»
CEILING_FIELD = "pbcf_6218cf455c37f"   # «Высота потолка», м
POWER_FIELD   = "pbcf_6218cf2f8b6e2"   # «Подводимая мощность, кВт»
ADDRESS     = "Москва, ул. Зорге, дом 9Ак1"
PHONE       = "+74952924193"
# Нативный ЦИАН-экспорт коллеги: у отдельных Зданий там есть описания (в API-поле
# description они пустые), подтягиваем их по ExternalId.
NATIVE_CIAN_URL = "https://pb7828.profitbase.ru/export/cian/8a4445dbd945f674e5982af685553b8b?scheme=https"
# Срок сдачи: дом сдан (3 квартал 2023). Quarter у ЦИАН — словом.
DEADLINE    = {"quarter": "third", "year": "2023", "complete": "true"}
OUT_DIR  = CACHE_DIR / "comm_zorge"
OUT      = OUT_DIR / "cian.xml"
ENR_DIR  = OUT_DIR / "enriched"     # обогащённые планировки (обложка)
TPL_DIR  = OUT_DIR / "templates"
PLANS_DIR = OUT_DIR / "plans"

# Назначение → конкретная категория ЦИАН. Под конкретной категорией ЦИАН подставляет
# назначение в ЗАГОЛОВОК (а не только в теги Speciality). Для прочих назначений точной
# категории нет либо она требует доп. полей (Готовый бизнес — доходность/окупаемость),
# поэтому они остаются «свободным назначением» (freeAppointmentObject) + тег Speciality.
CIAN_CAT = {
    "Офис": "office",
    "Торговая площадь": "shoppingArea",
}

# (ExternalId в фиде, id помещения в ProfitBase, kind, FloorsCount дома, назначение)
LISTINGS = [
    # ── Продажа: 1-5 ГАБ (готовый арендный бизнес) + Фитнес ──
    ("16890194",  "16890194", "sale", 3, "Арендный бизнес"),           # Здание 1 Велопрокат (ГАБ)
    ("16890195",  "16890195", "sale", 4, "Арендный бизнес"),   # Здание 2 Офис продаж
    ("11242968",  "11242968", "sale", 4, "Арендный бизнес"),   # Йога 2 эт
    ("11242974",  "11242974", "sale", 4, "Арендный бизнес"),   # Йога 3 эт
    ("11242998",  "11242998", "sale", 4, "Арендный бизнес"),   # Йога 4 эт
    ("13443309",  "13443309", "sale", 1, "Фитнес"),                    # Фитнес (продажа)
    # ── Аренда: торговые площади + Фитнес (К3П11 пока не выводим) ──
    ("15077881",  "15077881", "rent", 2, "Торговая площадь"),          # К1П6-2
    ("9841263",   "9841263",  "rent", 2, "Торговая площадь"),          # К2П5
    ("11901465",  "11901465", "rent", 3, "Офис"),                      # С6П3 (офис 3 эт Велопрокат)
    ("11760880",  "11760880", "rent", 2, "Торговая площадь"),          # К2П12
    ("9849983",   "9849983",  "rent", 2, "Торговая площадь"),          # К3П1
    ("9849988",   "9849988",  "rent", 2, "Торговая площадь"),          # К3П6
    ("9849991",   "9849991",  "rent", 2, "Торговая площадь"),          # К3П9
    ("11770818",  "11770818", "rent", 2, "Торговая площадь"),          # К3П11 (сдан в аренду)
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


def _clean_desc(s: str) -> str:
    """Чистка описания под требования ЦИАН: '&' запрещён, '№ \\' удаляются, '«» –' заменяются."""
    if not s:
        return s
    s = s.replace("&amp;", "и").replace(" & ", " и ").replace("&", "и")
    s = s.replace("«", '"').replace("»", '"').replace("–", "-")
    s = s.replace("№", "").replace("\\", "")
    return s.strip()


def _build_desc(it, naz):
    """Описание из данных помещения (если в ProfitBase пусто) — ЦИАН требует 15–3000 симв."""
    area = (it.get("area", {}) or {}).get("area_total")
    ceiling = _cf(it, CEILING_FIELD)
    power = _cf(it, POWER_FIELD)
    floor = it.get("floor")
    if naz == "Офис":
        head = "Помещение под офис"
    elif naz:
        head = f"Помещение свободного назначения ({naz})"
    else:
        head = "Помещение свободного назначения"
    spec = [f"площадь {area} м²"] if area else []
    if ceiling:
        spec.append(f"высота потолка {ceiling} м")
    if power:
        spec.append(f"подводимая мощность {power} кВт")
    if floor not in (None, ""):
        spec.append(f"этаж {floor}")
    parts = [f"{head} в ЖК «Зорге 9»."]
    if spec:
        parts.append((", ".join(spec)).capitalize() + ".")
    parts.append(f"Адрес: {ADDRESS}. Дом сдан (3 квартал 2023 года).")
    return " ".join(parts)


def _enriched(it, ext_id):
    """Брендовая планировка (жилой шаблон Зорге + подписи Площадь/Высота/Мощность).
    План берём из preset помещения. Возвращает URL или None."""
    plan_url = it.get("preset")
    area = (it.get("area", {}) or {}).get("area_total")
    if not (plan_url and area):
        return None
    ceiling = _cf(it, CEILING_FIELD)
    try:
        ceiling = float(ceiling)
    except Exception:
        ceiling = None
    power = _cf(it, POWER_FIELD)
    for d in (ENR_DIR, TPL_DIR, PLANS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    out = ENR_DIR / f"{ext_id}.png"
    out.unlink(missing_ok=True)
    lot = types.SimpleNamespace(area=float(area), ceiling_m=ceiling,
                                power_kw=str(power) if power else None)
    enrich_commercial(lot, plan_url, COMMERCIAL_TEMPLATE_ZORGE_URL,
                      COMMERCIAL_TEMPLATE_ZORGE_EXT, COMMERCIAL_LAYOUT_ZORGE,
                      TPL_DIR, PLANS_DIR, out)
    return f"{PUBLIC_BASE_URL}/comm-zorge-img/{file_ver(out)}/{ext_id}.png"


def refresh():
    key = os.environ.get("PB_API_KEY", "")
    if not key:
        return {"skipped": "PB_API_KEY не задан"}
    token = _auth(key)
    # Описания Зданий из нативного экспорта (по ProfitBase-id) — в API-поле они пустые
    native_desc = {}
    try:
        nroot = ET.fromstring(urllib.request.urlopen(NATIVE_CIAN_URL, timeout=90).read())
        for no in nroot.iter("object"):
            nid = (no.findtext("ExternalId") or "").strip()
            nd = (no.findtext("Description") or "").strip()
            if nid and nd:
                native_desc[nid] = nd
    except Exception as e:
        print(f"[comm-zorge] native desc fetch failed: {e}")
    root = ET.Element("feed")
    ET.SubElement(root, "feed_version").text = "2"
    n_sale = n_rent = 0
    for ext_id, pid, kind, floors, naz in LISTINGS:
        it = _prop(token, pid)
        area = (it.get("area", {}) or {}).get("area_total")
        if not area:
            continue
        o = ET.SubElement(root, "object")
        base = CIAN_CAT.get(naz.strip(), "freeAppointmentObject")
        _T(o, "Category", base + ("Rent" if kind == "rent" else "Sale"))
        _T(o, "ExternalId", ext_id)
        # Описание: приоритет — из нативного экспорта (Здания), затем API, затем генерим
        desc = _clean_desc(native_desc.get(pid) or it.get("description") or "")
        if len(desc) < 15:                          # ЦИАН требует 15–3000 симв.
            desc = _clean_desc(_build_desc(it, naz))
        _T(o, "Description", desc)
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
        # Обогащённая планировка — обложка; затем фото из planImages
        imgs = [im.get("source") for im in (it.get("planImages") or [])
                if im.get("source") and not im.get("technical")]
        try:
            enr = _enriched(it, ext_id)
        except Exception as e:
            enr = None
            print(f"[comm-zorge] enrich {ext_id} failed: {e}")
        cover = enr or (imgs[0] if imgs else None)
        if cover:
            lp = ET.SubElement(o, "LayoutPhoto")
            _T(lp, "FullUrl", cover); _T(lp, "IsDefault", "1")
        photos = ET.SubElement(o, "Photos")
        if cover:
            ps = ET.SubElement(photos, "PhotoSchema")
            _T(ps, "FullUrl", cover); _T(ps, "IsDefault", "1")
        for u in imgs:
            if u == cover:
                continue
            ps = ET.SubElement(photos, "PhotoSchema")
            _T(ps, "FullUrl", u); _T(ps, "IsDefault", "0")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"sale": n_sale, "rent": n_rent, "total": n_sale + n_rent}
