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


def sync_view_folders(public_key: str, dest_base: Path, wanted_ids=None,
                      max_side: int = 2560, quality: int = 86) -> dict:
    """Обход публичной папки видов: этаж/секция → папка лота с «_id: <ExternalId>» в названии.
    Качает виды лота в dest_base/<id>/ (идемпотентно). Возвращает {id: [имена файлов]}.
    Скачиваются только лоты из wanted_ids (если задан) — чтобы не тянуть лишнее."""
    result: dict = {}

    def walk(path):
        for it in _list_items(public_key, path):
            if it.get("type") != "dir":
                continue
            m = re.search(r"id[:\s]*([0-9]{4,})", it["name"])
            if not m:
                walk(it["path"])           # это этаж/секция — спускаемся глубже
                continue
            iid = m.group(1)
            if wanted_ids is not None and iid not in wanted_ids:
                continue
            dest = dest_base / iid
            names = []
            for f in _list_items(public_key, it["path"]):
                if f.get("type") != "file" or not (f.get("mime_type") or "").startswith("image/"):
                    continue
                out = dest / (Path(f["name"]).stem + ".jpg")
                if not out.exists():
                    href = _api_get("/download", {"public_key": public_key, "path": f["path"]})["href"]
                    req = urllib.request.Request(href, headers={"User-Agent": "feed-enricher"})
                    with _open(req, timeout=180) as r:
                        raw = r.read()
                    save_resized_jpeg(raw, out, max_side=max_side, quality=quality)
                    time.sleep(0.4)
                names.append(out.name)
            if names:
                result[iid] = sorted(names)

    walk(None)
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
                       max_side: int = 2560, quality: int = 86) -> list[Path]:
    """Скачать (идемпотентно) все картинки публичной папки в dest_dir как NN.jpg.

    Уже скачанные файлы пропускаются по имени. Возвращает отсортированный список путей.
    Если ЯД недоступен — пробрасывает исключение (вызов оборачивать в try в refresh).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for it in list_public_images(public_key, path):
        out = dest_dir / (Path(it["name"]).stem + ".jpg")
        if out.exists():
            saved.append(out)
            continue
        href = _api_get("/download", {"public_key": public_key, "path": it["path"]})["href"]
        req = urllib.request.Request(href, headers={"User-Agent": "feed-enricher"})
        with _open(req, timeout=180) as r:
            raw = r.read()
        save_resized_jpeg(raw, out, max_side=max_side, quality=quality)
        saved.append(out)
        time.sleep(0.4)   # не долбим API Яндекс.Диска — иначе 429
    return sorted(saved)
