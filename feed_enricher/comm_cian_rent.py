"""Коммерция (аренда) Берзарина 37 → корректный ЦИАН-фид.

Проблема: нативный ЦИАН-экспорт ProfitBase отдаёт эти помещения как ПРОДАЖУ
за 1 ₽ (основная цена = заглушка). Реальная аренда лежит в кастомном поле
«Стоимость аренды, руб./мес.» (pbcf_6218cf5652a5d) и доступна только через API.

Решение: берём нативный ЦИАН-экспорт (структура верная — адрес/этаж/этажность/
фото/назначение) и ЧИНИМ два поля:
  • Category  → freeAppointmentObjectRent (была ...Sale)
  • BargainTerms.Price → реальная аренда из API + PaymentPeriod=monthly

Конфиг через .env сервера:
  PB_API_KEY        — ключ ProfitBase API (app-...)  [НЕ в репозитории]
  PB_COMM_CIAN_URL  — URL нативного export/cian/<token> с этими лотами
"""
import os
import json
import re
import time
import types
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from .config import (CACHE_DIR, PUBLIC_BASE_URL, file_ver,
                     COMMERCIAL_TEMPLATE_URL, COMMERCIAL_TEMPLATE_EXT, COMMERCIAL_LAYOUT)
from .enricher import enrich_commercial

PB_API_BASE = "https://pb7828.profitbase.ru/api/v4/json"
RENT_FIELD    = "pbcf_6218cf5652a5d"   # «Стоимость аренды, руб./мес.»
CEILING_FIELD = "pbcf_6218cf455c37f"   # «Высота потолка», м
POWER_FIELD   = "pbcf_6218cf2f8b6e2"   # «Подводимая мощность, кВт»
NAZ_FIELD     = "pbcf_61e947e1daaa1"   # «Назначение помещения» (торговая/кафе/…)

# Срок сдачи объекта (одинаков для всех помещений). Quarter у ЦИАН — словом.
DEADLINE = {"quarter": "second", "year": "2027", "complete": "false"}
# Та же дата текстом — дописывается в конец <Description> (редактировать описание
# в ProfitBase через API нельзя, поэтому строку добавляем на стороне фида).
# Это «времянка»: как только в описании ProfitBase появится упоминание срока сдачи
# (DEADLINE_DESC_MARKER), фид перестаёт подставлять свою строку и берёт текст из профита.
DEADLINE_DESC_LINE   = "Срок сдачи в эксплуатацию: 2 квартал 2027 года"
DEADLINE_DESC_MARKER = "срок сдачи"

# Назначение по лотам (ExternalId → текст). Используется, если поле
# «Назначение помещения» в ProfitBase пустое. Заполнят в ProfitBase — оно приоритетнее.
NAZ_MAP = {  # значения = точные названия из справочника ЦИАН (через запятую = несколько)
    "17835265": "Торговля, Услуги",           # П-4
    "17835266": "Торговля, Услуги",           # П-8
    "17835267": "Общепит",                    # П-12
    "17835272": "Общепит",                    # П-14
    "17835273": "Фитнес, Спортивный зал",     # П-17
}
# Точечные правки описания (ExternalId → [(было, стало), …]). Времянка на стороне
# фида: исправляет ошибочный текст, который ввели в само описание ProfitBase.
# Применяется ПОСЛЕ _clean_desc (т.е. '&' уже заменён на 'и'). Когда текст поправят
# в ProfitBase — замена просто перестанет находить подстроку (станет no-op).
DESC_FIX = {
    "17835273": [("без отделки, формат Shell и Core", "с отделкой и авторским дизайном")],  # П-17
}
# Категория ЦИАН по лоту: торговые (Торговля/Услуги) → shoppingArea («Торговая площадь»),
# остальные (кафе, фитнес) — freeAppointmentObject («Свободное назначение»; своей категории нет).
CAT_BY_LOT = {
    "17835265": "shoppingArea",   # П4  Торговля/Услуги
    "17835266": "shoppingArea",   # П8  Торговля/Услуги
}
OUT_DIR  = CACHE_DIR / "comm_rent"
OUT      = OUT_DIR / "b37-cian.xml"
ENR_DIR  = OUT_DIR / "enriched"        # наши обогащённые планировки
TPL_DIR  = OUT_DIR / "templates"
PLANS_DIR = OUT_DIR / "plans"


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
    a = _api(PB_API_BASE + "/authentication",
             {"type": "api-app", "credentials": {"pb_api_key": key}}, "POST")
    return a["access_token"]


def _fields(token, pid):
    """Из одного запроса: аренда (int ₽/мес), высота потолка (float), мощность (str кВт)."""
    p = _api(PB_API_BASE + "/property?full=1&access_token=" + token + "&id=" + str(pid))
    items = p.get("data") if isinstance(p.get("data"), list) else (p.get("data") or p)
    it = items[0] if isinstance(items, list) and items else items
    out = {"rent": None, "ceiling": None, "power": None, "naz": None}
    for x in (it.get("custom_fields") or []):
        fid, val = x.get("id"), x.get("value")
        if val in (None, "", 0, "0"):
            continue
        if fid == RENT_FIELD:
            try: out["rent"] = int(round(float(val)))
            except Exception: pass
        elif fid == CEILING_FIELD:
            try: out["ceiling"] = float(val)
            except Exception: pass
        elif fid == POWER_FIELD:
            out["power"] = str(val)
        elif fid == NAZ_FIELD:
            out["naz"] = str(val)
    return out


def _set(parent, tag, val):
    e = parent.find(tag)
    if e is None:
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
    return s


_QWORD = {"1": "first", "2": "second", "3": "third", "4": "fourth"}


def _parse_deadline(text):
    """Достать «N квартал YYYY» из текста описания → (quarter-слово, year). None если нет."""
    if not text:
        return None
    m = re.search(r"([1-4])\s*кварт\w*\s*(\d{4})", text, re.I)
    return (_QWORD[m.group(1)], m.group(2)) if m else None


def _enriched_url(obj, eid, area, ceiling, power):
    """Отрисовать брендовую планировку (площадь/высота/мощность) и вернуть её URL.
    План берём из LayoutPhoto оригинала. None при отсутствии данных/ошибке."""
    lp = obj.find("LayoutPhoto")
    plan_url = (lp.findtext("FullUrl") if lp is not None else "") or ""
    if not (plan_url and area):
        return None
    for d in (ENR_DIR, TPL_DIR, PLANS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    out = ENR_DIR / f"{eid}.png"
    out.unlink(missing_ok=True)   # всегда перерисовываем под актуальные данные
    lot = types.SimpleNamespace(area=float(area), ceiling_m=ceiling,
                                power_kw=str(power) if power else None)
    enrich_commercial(lot, plan_url, COMMERCIAL_TEMPLATE_URL, COMMERCIAL_TEMPLATE_EXT,
                      COMMERCIAL_LAYOUT, TPL_DIR, PLANS_DIR, out)
    return f"{PUBLIC_BASE_URL}/comm-rent-img/{file_ver(out)}/{eid}.png"


def refresh():
    """Собрать корректный ЦИАН-фид аренды из нативного экспорта + цен из API.

    Пишет фид ТОЛЬКО если получили реальные ставки (иначе оставляем прошлый
    хороший кэш — не подменяем его сломанным «1 ₽»). Возвращает сводку.
    """
    key = os.environ.get("PB_API_KEY", "")
    url = os.environ.get("PB_COMM_CIAN_URL", "")
    if not (key and url):
        return {"skipped": "PB_API_KEY / PB_COMM_CIAN_URL не заданы в .env"}
    raw = urllib.request.urlopen(url, timeout=90).read()
    root = ET.fromstring(raw)
    token = _auth(key)
    lots = priced = enriched = 0
    for obj in root.iter("object"):
        lots += 1
        eid = (obj.findtext("ExternalId") or "").strip()
        _set(obj, "Category", CAT_BY_LOT.get(eid, "freeAppointmentObject") + "Rent")
        # Описание: чистим запрещённые символы (& и т.п.); срок сдачи — АВТОРИТЕТНО из
        # конфига (зафиксирован): убираем любую существующую строку «Срок сдачи…»
        # (в т.ч. просочившуюся из ProfitBase) и ставим актуальную.
        de = obj.find("Description")
        if de is not None and de.text:
            de.text = _clean_desc(de.text)
            for old, new in DESC_FIX.get(eid, []):
                de.text = de.text.replace(old, new)
            de.text = re.sub(r"\n*\s*Срок сдачи в эксплуатацию:[^\n]*", "", de.text).rstrip()
            de.text = de.text + "\n\n" + DEADLINE_DESC_LINE
        bt = obj.find("BargainTerms")
        if bt is None:
            bt = ET.SubElement(obj, "BargainTerms")
        f = _fields(token, eid) if eid else {"rent": None, "ceiling": None, "power": None, "naz": None}
        if f["rent"]:
            _set(bt, "Price", f["rent"]); priced += 1
        _set(bt, "currency", "rur")
        _set(bt, "PriceType", "all")
        _set(bt, "PaymentPeriod", "monthly")

        # Срок сдачи → Building/Deadline — АВТОРИТЕТНО из конфига DEADLINE (зафиксирован).
        bld = obj.find("Building")
        if bld is None:
            bld = ET.SubElement(obj, "Building")
        dl = bld.find("Deadline")
        if dl is None:
            dl = ET.SubElement(bld, "Deadline")
        _set(dl, "Quarter", DEADLINE["quarter"])
        _set(dl, "Year", DEADLINE["year"])
        _set(dl, "IsComplete", DEADLINE["complete"])

        # Назначение помещения → Specialty. Приоритет: поле ProfitBase, иначе мэппинг.
        # Значения через запятую/точку с запятой = несколько назначений.
        # ВАЖНО: правильный тег ЦИАН — Specialty (амер. написание). Нативный экспорт
        # ProfitBase отдаёт его как Speciality (с лишней «i») — ЦИАН такой игнорирует,
        # поэтому старый тег удаляем и пишем корректный.
        for _old in obj.findall("Speciality"):
            obj.remove(_old)
        naz = f.get("naz") or NAZ_MAP.get(eid)
        if naz:
            sp = obj.find("Specialty")
            if sp is None:
                sp = ET.SubElement(obj, "Specialty")
            types = sp.find("Types")
            if types is None:
                types = ET.SubElement(sp, "Types")
            for s in list(types):           # очищаем старое (в т.ч. «Свободное назначение»)
                types.remove(s)
            for val in re.split(r"[;,]", naz):
                val = val.strip()
                if val:
                    ET.SubElement(types, "String").text = val

        # Обогащённая планировка — ПЕРВОЙ картинкой (обложка)
        area = (obj.findtext("TotalArea") or "").strip()
        try:
            enr = _enriched_url(obj, eid, area, f["ceiling"], f["power"])
        except Exception as e:
            enr = None
            print(f"[comm-rent] enrich {eid} failed: {e}")
        if enr:
            enriched += 1
            lp = obj.find("LayoutPhoto")
            if lp is None:
                lp = ET.SubElement(obj, "LayoutPhoto")
            _set(lp, "FullUrl", enr)
            photos = obj.find("Photos")
            if photos is None:
                photos = ET.SubElement(obj, "Photos")
            for p in photos.findall("PhotoSchema"):   # все прочие — не обложка
                _set(p, "IsDefault", "0")
            ps = ET.Element("PhotoSchema")
            ET.SubElement(ps, "FullUrl").text = enr
            ET.SubElement(ps, "IsDefault").text = "1"
            photos.insert(0, ps)                        # наша планировка — первая

    if priced == 0:
        return {"skipped": "ни одной ставки из API — кэш не трогаем", "lots": lots}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"lots": lots, "priced": priced, "enriched": enriched}
