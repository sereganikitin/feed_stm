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
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from .config import CACHE_DIR

PB_API_BASE = "https://pb7828.profitbase.ru/api/v4/json"
RENT_FIELD = "pbcf_6218cf5652a5d"          # «Стоимость аренды, руб./мес.»
OUT_DIR = CACHE_DIR / "comm_rent"
OUT = OUT_DIR / "b37-cian.xml"


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


def _rent(token, pid):
    """Реальная арендная ставка (руб./мес, int) помещения или None."""
    p = _api(PB_API_BASE + "/property?full=1&access_token=" + token + "&id=" + str(pid))
    items = p.get("data") if isinstance(p.get("data"), list) else (p.get("data") or p)
    it = items[0] if isinstance(items, list) and items else items
    for x in (it.get("custom_fields") or []):
        if x.get("id") == RENT_FIELD and x.get("value") not in (None, "", 0, "0"):
            try:
                return int(round(float(x["value"])))
            except Exception:
                return None
    return None


def _set(parent, tag, val):
    e = parent.find(tag)
    if e is None:
        e = ET.SubElement(parent, tag)
    e.text = str(val)
    return e


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
    lots = priced = 0
    for obj in root.iter("object"):
        lots += 1
        eid = (obj.findtext("ExternalId") or "").strip()
        _set(obj, "Category", "freeAppointmentObjectRent")
        bt = obj.find("BargainTerms")
        if bt is None:
            bt = ET.SubElement(obj, "BargainTerms")
        rent = _rent(token, eid) if eid else None
        if rent:
            _set(bt, "Price", rent); priced += 1
        _set(bt, "currency", "rur")
        _set(bt, "PriceType", "all")
        _set(bt, "PaymentPeriod", "monthly")
    if priced == 0:
        return {"skipped": "ни одной ставки из API — кэш не трогаем", "lots": lots}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"lots": lots, "priced": priced}
