"""Фид для корпоративного сайта: копия profitbase_xml из ProfitBase (та же структура —
цены, special-offers, статусы, кастомные поля не трогаем), в который к лотам Зорге 9 и
Квартала Серебряный Бор дописаны наши фото с Я.Дисков (интерьер → визуализация → вид из
окна) сразу после последней планировки.

Зорге: наши фото отдаём с типом plan — сайт показывает такие картинки в галерее лота (в папках
ЯД не только виды из окон). В ProfitBase часть этих же фото уже лежит в plan → чтобы на сайте
не было дублей, такие plan-картинки ProfitBase (совпадают по содержимому с нашими) убираем.
Б37: в папках только виды → типы interior / visualization / view, plan ProfitBase не трогаем.
Общие фото проекта не добавляем.

Роут /feed/profitbase-site.xml ; триггер POST /refresh-site-feed.
"""
import io
import json
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image

from .config import CACHE_DIR, PROJECTS, lot_view_groups
from .parser import download_feed

SITE_DIR = CACHE_DIR / "site"
ORIG = SITE_DIR / "profitbase_original.xml"
OUT = SITE_DIR / "profitbase.xml"
HASHES = SITE_DIR / "plan_hashes.json"     # кэш: URL plan-картинки ProfitBase → aHash

# Зорге: в папках ЯД не только виды, а сайт показывает plan в галерее → всё отдаём как plan
# (+ дедупликация с plan ProfitBase). Остальные проекты (Б37: в папках только виды) — типы
# по содержимому: interior / visualization / view.
PLAN_TYPED_SLUGS = {"zorge9"}
DUP_MAX_DIST = 8         # aHash 16×16 (256 бит): расстояние ≤ 8 — одна и та же картинка


def _slug_of(project_name: str) -> str:
    n = (project_name or "").lower()
    if "зорге" in n:
        return "zorge9"
    if "серебряный" in n:
        return "b37"
    return ""


def _ahash(im) -> int:
    px = im.convert("L").resize((16, 16)).tobytes()
    m = sum(px) / len(px)
    return int("".join("1" if p > m else "0" for p in px), 2)


def _dist(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _load_hashes() -> dict:
    try:
        return json.loads(HASHES.read_text("utf-8"))
    except Exception:
        return {}


def _fetch_hashes(urls, cache: dict) -> None:
    """Догрузить в cache хэши картинок ProfitBase, которых ещё нет (параллельно)."""
    todo = [u for u in dict.fromkeys(urls) if u not in cache]

    def fetch(u):
        try:
            r = requests.get(u, timeout=30)
            r.raise_for_status()
            return u, _ahash(Image.open(io.BytesIO(r.content)))
        except Exception:
            return u, None

    if todo:
        with ThreadPoolExecutor(8) as ex:
            for u, h in ex.map(fetch, todo):
                if h is not None:
                    cache[u] = h
        HASHES.write_text(json.dumps(cache), "utf-8")


def refresh(reuse_original: bool = False) -> dict:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    if reuse_original and ORIG.exists():
        raw = ORIG.read_bytes()
    else:
        raw = download_feed(PROJECTS["zorge9"]["euro_source_url"], ORIG)
    root = ET.fromstring(raw)
    ns = root.tag.split("}")[0].strip("{") if root.tag.startswith("{") else ""
    ET.register_namespace("", ns)
    tag = lambda t: f"{{{ns}}}{t}" if ns else t
    is_plan = lambda c: c.tag == tag("image") and c.get("type") == "plan"

    # Проход 1: лоты с нашими фото + хэши этих фото и plan-картинок ProfitBase
    work = []
    total = 0
    for off in root.iter(tag("offer")):
        total += 1
        slug = _slug_of(off.findtext("{*}object/{*}name"))
        iid = off.get("internal-id") or ""
        if not slug or not iid:
            continue
        g = lot_view_groups(slug, iid)
        if slug in PLAN_TYPED_SLUGS:
            items = [("plan", u) for u in g["interior"] + g["visualization"] + g["view"]]
        else:
            items = ([("interior", u) for u in g["interior"]]
                     + [("visualization", u) for u in g["visualization"]]
                     + [("view", u) for u in g["view"]])
        if not items:
            continue
        urls = [u for _, u in items]
        own = []
        for u in urls:
            p = CACHE_DIR / slug / "views" / iid / u.rsplit("/", 1)[-1]
            try:
                own.append(_ahash(Image.open(p)))
            except Exception:
                pass
        work.append((off, items, own if slug in PLAN_TYPED_SLUGS else []))

    cache = _load_hashes()
    plan_urls = [(c.text or "").strip() for off, _, own in work if own for c in off if is_plan(c)]
    _fetch_hashes(plan_urls, cache)

    # Проход 2: убираем plan ProfitBase, дублирующие наши фото; дописываем наши после последнего plan
    added = dups = 0
    for off, items, own in work:
        for c in [c for c in off if is_plan(c)] if own else []:
            h = cache.get((c.text or "").strip())
            if h is not None and any(_dist(h, o) <= DUP_MAX_DIST for o in own):
                off.remove(c)
                dups += 1
        children = list(off)
        images = [(i, c) for i, c in enumerate(children) if c.tag == tag("image")]
        plans = [i for i, c in images if c.get("type") == "plan"]
        plan_floors = [i for i, c in images if c.get("type") == "plan floor"]
        if plans:
            anchor_i = plans[-1]
        elif plan_floors:
            anchor_i = plan_floors[-1]
        elif images:
            anchor_i = images[-1][0]
        else:
            anchor_i = len(children) - 1
        anchor = children[anchor_i]
        pos = children.index(anchor) + 1
        for typ, url in items:
            el = ET.Element(tag("image"), {"type": typ})
            el.text = url
            el.tail = anchor.tail
            off.insert(pos, el)
            pos += 1
            added += 1

    ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    return {"offers": total, "enriched": len(work), "images_added": added,
            "duplicate_plans_removed": dups}
