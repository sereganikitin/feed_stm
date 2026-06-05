"""Коммерческие фиды (самообслуживание): рантайм-стор проектов + сборка.

Проект = ссылка ProfitBase (profitbase_xml) + площадки + (опц.) папка-фолбэк на Я.Диске.
Хранится в cache/admin/commercial.json — добавляется/правится из «мастера фидов» в админке.
Отдельная подсистема, не пересекается с жилыми PROJECTS.
"""
import json
import time
import threading

import requests

from .config import (CACHE_DIR, ADMIN_DIR, PUBLIC_BASE_URL, file_ver,
                     COMMERCIAL_TEMPLATE_URL, COMMERCIAL_TEMPLATE_EXT, COMMERCIAL_LAYOUT)
from .parser_pb import parse_pb_feed
from .enricher import enrich_commercial
from .assembler_commercial import build_yandex, build_avito, build_cian
from .yadisk import sync_public_folder

_STORE = ADMIN_DIR / "commercial.json"
_lock = threading.Lock()

_DEFAULT = {
    "b37comm": {
        "name": "Б37 Коммерция (аренда)",
        "source_url": "https://pb7828.profitbase.ru/export/profitbase_xml/f0f1bccd01b8cc5c3303cd4765cb4232?scheme=https",
        "platforms": ["yandex", "avito", "cian"],
        "address": "Москва, улица Берзарина, 37",
        "sales_agent": {"organization": "St MICHAEL", "category": "застройщик",
                        "phone": "+74952924193", "url": "https://stmichael.ru"},
        "yadisk_fallback": "",   # папка ЯД с картинками, если из ProfitBase их нет
    },
}


def load_projects() -> dict:
    if not _STORE.exists():
        save_projects(_DEFAULT)
        return dict(_DEFAULT)
    try:
        return json.loads(_STORE.read_text("utf-8"))
    except Exception:
        return dict(_DEFAULT)


def save_projects(data: dict) -> None:
    ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def comm_dirs(slug: str) -> dict:
    base = CACHE_DIR / "commercial" / slug
    dirs = {k: base / k for k in ("templates", "plans", "enriched", "extra", "feeds")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


_PLATFORM_FILE = {"yandex": "yandex.xml", "avito": "avito.xml", "cian": "feed.xml"}


def refresh_commercial(slug: str) -> dict:
    proj = load_projects().get(slug)
    if not proj or not proj.get("source_url"):
        return {"slug": slug, "skipped": "нет source_url"}
    d = comm_dirs(slug)
    with _lock:
        raw = requests.get(proj["source_url"], timeout=60).content
        (d["feeds"] / "original.xml").write_bytes(raw)
        lots = parse_pb_feed(raw)

        # фолбэк-картинки с Я.Диска (если задано) — общий набор
        fb_urls = []
        if proj.get("yadisk_fallback"):
            try:
                files = sync_public_folder(proj["yadisk_fallback"], "/", d["extra"])
                fb_urls = [f"{PUBLIC_BASE_URL}/commercial/{slug}/extra/{file_ver(f)}/{f.name}" for f in files]
            except Exception as e:
                print(f"[comm {slug}] yadisk fallback failed: {e}")

        ok = 0
        for lot in lots:
            plan = lot.images[0] if lot.images else (fb_urls[0] if fb_urls else "")
            if not plan:
                continue
            try:
                enrich_commercial(lot, plan, COMMERCIAL_TEMPLATE_URL, COMMERCIAL_TEMPLATE_EXT,
                                  COMMERCIAL_LAYOUT, d["templates"], d["plans"],
                                  d["enriched"] / f"{lot.internal_id}.png")
                ok += 1
            except Exception as e:
                print(f"[comm {slug}] enrich {lot.internal_id}: {e}")

        def images_for(lot):
            urls = []
            png = d["enriched"] / f"{lot.internal_id}.png"
            if png.exists():
                urls.append(f"{PUBLIC_BASE_URL}/commercial/{slug}/enriched/{file_ver(png)}/{lot.internal_id}.png")
            # остальные планы/фото из ProfitBase (кроме первого — его заменяет обогащение)
            urls += lot.images[1:] if lot.images else []
            if not lot.images and fb_urls:
                urls += fb_urls
            return urls

        plats = proj.get("platforms", [])
        now = time.strftime("%Y-%m-%dT%H:%M:%S+03:00")
        if "yandex" in plats:
            build_yandex(lots, proj, images_for, d["feeds"] / "yandex.xml", now)
        if "avito" in plats:
            build_avito(lots, proj, images_for, d["feeds"] / "avito.xml")
        if "cian" in plats:
            build_cian(lots, proj, images_for, d["feeds"] / "feed.xml")

        return {"slug": slug, "lots": len(lots), "enriched": ok, "platforms": plats}


def refresh_all_commercial() -> dict:
    return {slug: refresh_commercial(slug) for slug in load_projects()}
