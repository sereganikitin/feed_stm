"""Синхронизация фото из публичной папки Яндекс.Диска в локальный кэш.

Зачем: прямые ссылки на скачивание с ЯД временные (подписанные, истекают) —
в фид их вставлять нельзя. Поэтому скачиваем файлы к себе и раздаём со своего
домена стабильными URL (см. роут /extra/<slug>/<name> в server.py).

Большие исходники (до ~20 МБ) ужимаем до разумного размера под Авито
(длинная сторона ≤ max_side, JPEG) — Авито всё равно пережимает превью,
а нам это экономит трафик и убирает риск отказа по размеру файла.
"""
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

_API = "https://cloud-api.yandex.net/v1/disk/public/resources"


def _open(url_or_req, timeout: int = 60, tries: int = 7):
    """urlopen с ретраями на 429/503 (Яндекс.Диск лимитирует частоту запросов)."""
    last = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(url_or_req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                last = e
                time.sleep(min(2 ** i, 30))   # 1,2,4,8,16,30,30 c
                continue
            raise
    raise last


def save_resized_jpeg(raw: bytes, out: Path, max_side: int = 2560, quality: int = 86) -> Path:
    """Сохранить изображение из байтов как JPEG, ужав длинную сторону до max_side.

    Используется и при синке с Я.Диска, и при ручной загрузке фото в админ-панели —
    чтобы все фото карточки были в едином, дружелюбном к Авито размере.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out


def _api_get(endpoint: str, params: dict) -> dict:
    url = f"{_API}{endpoint}?{urllib.parse.urlencode(params)}"
    with _open(url, timeout=60) as r:
        return json.load(r)


def _list_items(public_key: str, path: str = None) -> list[dict]:
    params = {"public_key": public_key, "limit": "500"}
    if path:
        params["path"] = path
    return _api_get("", params).get("_embedded", {}).get("items", [])


def sync_view_folders(public_key: str, dest_base: Path, resolve,
                      max_side: int = 2560, quality: int = 86) -> dict:
    """Обход публичной папки видов. Для каждой папки вызывает resolve(name, ancestors)
    → ExternalId лота или None. Если id вернулся — качает картинки папки в dest_base/<id>/
    (идемпотентно); иначе спускается глубже. ancestors — список имён родительских папок
    (этаж/секция) для маппинга. Возвращает {id: [имена файлов]}."""
    result: dict = {}

    def walk(path, ancestors):
        for it in _list_items(public_key, path):
            if it.get("type") != "dir":
                continue
            iid = resolve(it["name"], ancestors)
            if iid:
                dest = dest_base / iid
                dest.mkdir(parents=True, exist_ok=True)
                # ЗЕРКАЛИРОВАНИЕ: если набор файлов в ЯД изменился (добавили/удалили) —
                # пере-скачиваем папку лота (чистим старые числовые 01.jpg…). Ручные
                # загрузки (u*.jpg) не трогаем. Манифест _src.json хранит имена из ЯД.
                srcs = sorted((f for f in _list_items(public_key, it["path"])
                               if f.get("type") == "file" and (f.get("mime_type") or "").startswith("image/")),
                              key=lambda f: f["name"])
                yd_names = [f["name"] for f in srcs]
                manifest = dest / "_src.json"
                try:
                    old = json.loads(manifest.read_text("utf-8"))
                except Exception:
                    old = None
                if old != yd_names:
                    for p in dest.glob("*.jpg"):
                        if p.stem.isdigit():
                            p.unlink()
                    for i, f in enumerate(srcs, 1):
                        out = dest / f"{i:02d}.jpg"
                        href = _api_get("/download", {"public_key": public_key, "path": f["path"]})["href"]
                        req = urllib.request.Request(href, headers={"User-Agent": "feed-enricher"})
                        with _open(req, timeout=180) as r:
                            raw = r.read()
                        save_resized_jpeg(raw, out, max_side=max_side, quality=quality)
                        time.sleep(0.4)
                    manifest.write_text(json.dumps(yd_names, ensure_ascii=False), "utf-8")
                names = sorted(p.name for p in dest.glob("*.jpg"))
                if names:
                    result[iid] = names
            else:
                walk(it["path"], ancestors + [it["name"]])

    walk(None, [])
    return result


def list_public_images(public_key: str, path: str) -> list[dict]:
    """Файлы-картинки в публичной папке, отсортированные по имени."""
    data = _api_get("", {"public_key": public_key, "path": path, "limit": "500"})
    items = data.get("_embedded", {}).get("items", [])
    imgs = [it for it in items
            if it.get("type") == "file" and (it.get("mime_type") or "").startswith("image/")]
    imgs.sort(key=lambda it: it["name"])
    return imgs


def sync_public_folder(public_key: str, path: str, dest_dir: Path,
                       max_side: int = 2560, quality: int = 86,
                       mirror: bool = False, exclude=None) -> list[Path]:
    """Скачать (идемпотентно) все картинки публичной папки в dest_dir как NN.jpg.

    Уже скачанные файлы пропускаются по имени. Возвращает отсортированный список путей.
    Если ЯД недоступен — пробрасывает исключение (вызов оборачивать в try в refresh).

    mirror=True — режим зеркала: файлы, которые БЫЛИ скачаны из ЯД, но в ЯД больше
    не существуют, удаляются локально. Манифест _src.json хранит имена из ЯД, поэтому
    файлы, добавленные иначе (ручная загрузка), не трогаются.

    exclude — множество имён файлов, которые НЕ качать из ЯД и удалять локально
    (чёрный список: удалённое вручную в админке, чтобы не возвращалось из ЯД).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    exclude = set(exclude or ())
    saved: list[Path] = []
    yd_names: list[str] = []
    for it in list_public_images(public_key, path):
        out = dest_dir / (Path(it["name"]).stem + ".jpg")
        if out.name in exclude:                    # чёрный список — не качаем, удаляем если есть
            out.unlink(missing_ok=True)
            continue
        yd_names.append(out.name)
        if not out.exists():
            href = _api_get("/download", {"public_key": public_key, "path": it["path"]})["href"]
            req = urllib.request.Request(href, headers={"User-Agent": "feed-enricher"})
            with _open(req, timeout=180) as r:
                raw = r.read()
            save_resized_jpeg(raw, out, max_side=max_side, quality=quality)
            time.sleep(0.4)   # не долбим API Яндекс.Диска — иначе 429
        saved.append(out)
    if mirror:
        manifest = dest_dir / "_src.json"
        try:
            old = set(json.loads(manifest.read_text("utf-8")))
        except Exception:
            old = set()
        cur = set(yd_names)
        for nm in old - cur:                      # были из ЯД, теперь удалены в ЯД
            (dest_dir / nm).unlink(missing_ok=True)
        manifest.write_text(json.dumps(sorted(cur), ensure_ascii=False), "utf-8")
    return sorted(dest_dir.glob("*.jpg"))
