"""Конфигурация сервиса.

Архитектура: несколько проектов (ЖК), каждый со своим:
  • URL фида из ProfitBase
  • URL шаблона из Figma
  • TEMPLATE_LAYOUT — координаты планировки и текстовых полей
  • installment — формула рассрочки (ПВ, срок, ставка). Только там, где есть рассрочка.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ═══ Проекты (ЖК) ═══
# Ключ slug используется в URL: /feed/<slug>.xml, /enriched/<slug>/<id>.png
PROJECTS = {
    "zorge9": {
        "name": "Зорге 9",
        "pb_feed_url": "https://pb7828.profitbase.ru/export/cian/52f269befad84358fb0c88a64dc2770c?scheme=https",
        "figma_template_url": "https://static.tildacdn.com/tild3130-3231-4832-a464-623331636437/plan-z9.jpg",
        "template_ext": "jpg",
        # ─── Параметры выгрузки в формате Авито ───
        # Вариант A (приоритетный): native Avito-экспорт из ProfitBase.
        #   Если задан — берём готовый Авито-фид ProfitBase и только подменяем обложку.
        #   Получить: кабинет ProfitBase → выгрузки → формат Avito → URL export/avito/<token>.
        "pb_avito_feed_url":        os.environ.get("PB_AVITO_FEED_URL_ZORGE9", ""),
        # Скидка к цене из фида, % (0 = не трогать). Включать, когда ProfitBase
        # начнёт отдавать ПОЛНУЮ цену. Редактируется в админке.
        "price_discount_pct":       0,
        # Авито требует непустую «Отделку». ProfitBase отдаёт её не у всех лотов —
        # где пусто, подставляем дефолт проекта (Зорге 9 сдаётся без отделки).
        "avito_default_decoration": "Без отделки",
        # Подмена фото в карточке Авито: убрать building_image из ProfitBase
        # и поставить наши фото из публичной папки Яндекс.Диска.
        # Остаются: наша планировка (обложка) + house + facade + эти фото.
        "avito_extra_photos": {
            "yadisk_public_key": "https://disk.360.yandex.ru/d/6nA7DAS6HGlR1g",
            "yadisk_path":       "/Для карточки проекта Авито",
            # Убираем ВСЕ фото ProfitBase (дом/фасад/building_image) — в карточке остаётся
            # только наша обогащённая планировка + наши фото. (Планы убирает cover-логика.)
            "replace_markers":   ["/uploads/house/", "/uploads/facade/", "/uploads/building_image/"],
        },
        # Фото для карточки ЦИАН (общий набор с Я.Диска). Карточка ЦИАН пересобирается
        # целиком: обложка = наша планировка, затем эти фото, затем виды из окон лота.
        # Фото ProfitBase в ЦИАН-фид не попадают. Зеркалирование (добавление/удаление в ЯД
        # подхватывается), синк раз в час + кнопка в админке.
        "cian_extra_photos": {
            "yadisk_public_key": "https://disk.360.yandex.ru/d/6nA7DAS6HGlR1g",
            "yadisk_path":       "/Для карточки лота Циан",
        },
        # Вариант B (fallback): если pb_avito_feed_url пуст — конвертируем из ЦИАН-фида сами.
        # Status (Квартира/Апартаменты) берётся из IsApartments автоматически.
        "avito_market_type":        "Новостройка",  # Новостройка | Вторичка
        "avito_house_type":         os.environ.get("AVITO_HOUSE_TYPE_ZORGE9", ""),   # материал: Монолитный/Кирпичный/... (опц.)
        "avito_new_development_id":  os.environ.get("AVITO_ND_ID_ZORGE9", ""),        # id ЖК в Авито (опц., если есть)
        # ─── Яндекс.Недвижимость ───
        "yandex_building_id": "238280",
        "yandex_house_ids":   {1: "2120663", 2: "348510", 3: "348507"},  # № корпуса → yandex-house-id
        "yandex_extra_photos": {
            "yadisk_public_key": "https://disk.360.yandex.ru/d/Fb7SU0zG0kbJUQ",
            "yadisk_path":       "/",
            "replace_markers":   ["/uploads/house/", "/uploads/facade/", "/uploads/building_image/"],
        },
        "sales_agent": {"organization": "St MICHAEL", "category": "застройщик", "url": "https://stmichael.ru"},
        # ─── Виды из окон по лотам (несколько источников на Я.Диске) ───
        # mode "id"        — папка лота содержит «_id: <ExternalId>» в названии;
        # mode "flatnumber"— этаж → секция → «… - <apt>.<sub>», маппинг по FlatNumber
        #                    (ЗГ<korpus>-<этаж>-…-<apt>/<sub>), korpus задаётся в источнике.
        "views_sources": [
            {"public_key": "https://disk.360.yandex.ru/d/csRx3vArvfTcPA", "mode": "id"},
            {"public_key": "https://disk.360.yandex.ru/d/m0i7Gq5M2G2n7Q", "mode": "flatnumber", "korpus": "3"},
        ],
        # ─── Раскладка шаблона Зорге 9 (1200×900) ───
        # Шаблон уже содержит брендинг/фото справа + статичные метки слева.
        # Заполняем только пустые «значения»:
        #   • цифра под меткой «Этаж», «Площадь, м²», «Комнаты»
        #   • план в большом пустом боксе слева
        #   • цифры в плашках «ПЕРВЫЙ ВЗНОС: X МЛН ₽» и «ПЛАТЁЖ В МЕСЯЦ: X ТЫС ₽»
        "layout": {
            "size":          (1200, 900),
            # бокс под планировку — левая колонка шириной от x=60 до x=630 (после x=640 начинается фото дома)
            "plan_box":      (60, 290, 630, 680),
            # ИЗМЕРЕННЫЕ центры меток шаблона на y=215: Этаж=167, Площадь=346, Комнаты=526.
            # Значения над метками строго по центру каждой метки.
            "floor_value":   {"pos": (167, 155), "size": 54, "color": (20, 30, 50), "anchor": "mm"},
            "area_value":    {"pos": (346, 155), "size": 54, "color": (20, 30, 50), "anchor": "mm"},
            "rooms_value":   {"pos": (526, 155), "size": 54, "color": (20, 30, 50), "anchor": "mm"},
            # плашки рассрочки: закрашиваем статику «МЛН ₽» / «ТЫСЯЧ ₽» и пишем полную строку.
            # Левая плашка границы: x=144..374, центр 259; правая: x=374..606, центр 490.
            # Цвет плашки RGB(142, 106, 89). Закрашиваем нижнюю половину плашки и рисуем
            # «{value} МЛН ₽» / «{value} ТЫС ₽» по центру со штатным anchor='mm'.
            "down_payment":  {
                "pos": (259, 778), "size": 34, "color": (255, 255, 255), "anchor": "mm",
                "suffix": "МЛН ₽",
                "clear_rect": (146, 755, 372, 803),
                "clear_color": (142, 106, 89),
            },
            "monthly_pay":   {
                "pos": (490, 778), "size": 34, "color": (255, 255, 255), "anchor": "mm",
                "suffix": "ТЫС ₽",
                "clear_rect": (376, 755, 604, 803),
                "clear_color": (142, 106, 89),
            },
        },
        # ─── Формула рассрочки Зорге 9 (от пользователя) ───
        # Цена в фиде = изн.прайс × 0.8 (цена «при 100% оплате», −20%).
        # ПВ = изн.прайс × 0.8 × 0.10  = price_feed × 0.10
        # Платёж = изн.прайс × 0.005   = (price_feed / 0.8) × 0.005
        "installment": {
            "feed_to_base_divisor": 0.8,  # изн.прайс = price_feed / 0.8
            "down_payment_pct":     0.10, # 10% от feed (= 8% от изн.прайс)
            "monthly_pct_of_base":  0.005,# 0.5% от изн.прайс в месяц
        },
    },
    "b37": {
        "name": "Квартал Серебряный Бор (Берзарина 37)",
        "pb_feed_url": os.environ.get(
            "PB_FEED_URL_B37",
            "https://pb7828.profitbase.ru/export/cian/2c8842a29267697d479e01d8808ed479?scheme=https",
        ),
        "figma_template_url": os.environ.get(
            "FIGMA_TEMPLATE_URL_B37",
            "https://static.tildacdn.com/tild3735-3765-4137-a130-376363353730/plan-ksb.png",
        ),
        "template_ext": "png",
        # ─── Параметры выгрузки в формате Авито ───
        "pb_avito_feed_url":        os.environ.get("PB_AVITO_FEED_URL_B37", ""),
        "price_discount_pct":       0,
        # Свои фото b37 с Яндекс.Диска вместо фото ProfitBase (дом/фасад/building_image).
        "avito_extra_photos": {
            "yadisk_public_key": "https://disk.360.yandex.ru/d/VUz9nj7AoKdu9g",
            "yadisk_path":       "/",
            "replace_markers":   ["/uploads/house/", "/uploads/facade/", "/uploads/building_image/"],
        },
        "avito_default_decoration": "Без отделки",
        "avito_market_type":        "Новостройка",
        "avito_house_type":         os.environ.get("AVITO_HOUSE_TYPE_B37", ""),
        "avito_new_development_id":  os.environ.get("AVITO_ND_ID_B37", ""),
        # ─── Яндекс.Недвижимость ───  (Корпус 1=Золотая, 2=Серебряная, 3=Платиновая)
        "yandex_building_id": "3894226",
        "yandex_house_ids":   {1: "3947831", 2: "3894528", 3: "3947848"},
        "yandex_extra_photos": {
            "yadisk_public_key": "https://disk.360.yandex.ru/d/VUz9nj7AoKdu9g",
            "yadisk_path":       "/",
            "replace_markers":   ["/uploads/house/", "/uploads/facade/", "/uploads/building_image/"],
        },
        # Фото для карточки ЦИАН (см. zorge9): карточка собирается заново —
        # обложка (наша планировка) → эти фото → виды. Фото ProfitBase не попадают.
        "cian_extra_photos": {
            "yadisk_public_key": "https://disk.360.yandex.ru/d/VUz9nj7AoKdu9g",
            "yadisk_path":       "/",
        },
        "sales_agent": {"organization": "St MICHAEL", "category": "застройщик", "url": "https://stmichael.ru"},
        # ─── Раскладка шаблона Б37 (1150×1040) ───
        # Слева — брендинг + фото дома, справа — большая белая зона:
        #   • сверху 3 метки «КОМНАТЫ / ПЛОЩАДЬ / ЭТАЖ»
        #   • под ними — большая пустая зона под планировку
        # Цены/ПВ/Платёж — НЕТ (шаблон без коммерческой части).
        "layout": {
            "size":          (1150, 1040),
            # Белое поле x=565..1085 (ширина 520, центр 825), y=275..930.
            # Уменьшение плана на 15%: исходный бокс 520×655, новый 442×557 (×0.85), центр сохраняем.
            # Новый бокс: (825-221, 602-278, 825+221, 602+278) = (604, 324, 1046, 880)
            "plan_box":      (604, 324, 1046, 880),
            # ─── overlay: центры посчитаны так чтобы ЗАЗОРЫ МЕЖДУ СЛОВАМИ были одинаковыми.
            # Ширины (TT Fors 20): Комнаты=88, Площадь=91, Этаж=53.
            # Зазоры по 114px: центры 640 / 843 / 1029.
            "header_overlay": {
                "clear_rect": (560, 165, 1085, 248),
                "clear_color": (255, 255, 255),
                "labels": [
                    {"pos": (640,  235), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Комнаты"},
                    {"pos": (843,  235), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Площадь"},
                    {"pos": (1029, 235), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Этаж"},
                ],
            },
            "rooms_value":   {"pos": (640,  178), "size": 64, "color": (20, 30, 50), "anchor": "mm"},
            "area_value":    {"pos": (843,  178), "size": 64, "color": (20, 30, 50), "anchor": "mm"},
            "floor_value":   {"pos": (1029, 178), "size": 64, "color": (20, 30, 50), "anchor": "mm"},
        },
        # У Б37 рассрочки на шаблоне нет — installment не задаём.
        "installment": None,
    },
}

# ═══ Хранилище ═══
CACHE_DIR = ROOT / "feed_enricher" / "cache"

def project_dirs(slug: str) -> dict:
    base = CACHE_DIR / slug
    dirs = {
        "templates": base / "templates",
        "plans":     base / "plans",
        "enriched":  base / "enriched",
        "feeds":     base / "feeds",
        "extra":         base / "extra",          # фото для карточки Авито
        "extra_yandex":  base / "extra_yandex",   # фото для карточки Яндекс.Недвижимости
        "extra_cian":    base / "extra_cian",     # фото для карточки ЦИАН (зеркало папки ЯД)
        "views":         base / "views",          # виды из окон по лотам: views/<id>/*.jpg
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs

for _slug in PROJECTS:
    project_dirs(_slug)

# ═══ Рантайм-оверрайды (правятся из админ-панели) ═══
# Хранятся в volume, переживают пересборку контейнера. Перекрывают значения из PROJECTS.
import json as _json

ADMIN_DIR = CACHE_DIR / "admin"
_OVERRIDES_PATH = ADMIN_DIR / "overrides.json"

# Какие ключи проекта разрешено менять из панели (всё остальное — только в коде)
EDITABLE_KEYS = {
    "avito_default_decoration",     # str
    "avito_house_type",             # str
    "avito_market_type",            # str
    "avito_replace_building_image", # bool — заменять ли building_image нашими фото
    "extra_photo_order",            # list[str] — порядок файлов в /extra (Авито)
    "extra_photo_order_yandex",     # list[str] — порядок файлов в /extra_yandex
    "extra_photo_order_cian",       # list[str] — порядок файлов в /extra_cian (ЦИАН)
    "description_suffix",           # str — приписка к каждому описанию
    "installment",                  # dict — формула рассрочки (где есть)
    "price_discount_pct",           # float — скидка к цене из фида, % (0 = не трогать)
}


def load_overrides() -> dict:
    try:
        return _json.loads(_OVERRIDES_PATH.read_text("utf-8"))
    except Exception:
        return {}


def save_overrides(data: dict) -> None:
    ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    _OVERRIDES_PATH.write_text(_json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def set_override(slug: str, key: str, value) -> None:
    """Точечно записать один разрешённый оверрайд проекта."""
    if key not in EDITABLE_KEYS:
        raise KeyError(f"ключ {key} не редактируется")
    data = load_overrides()
    data.setdefault(slug, {})[key] = value
    save_overrides(data)


# ═══ Обогащение коммерции (переиспользуем шаблон Б37, подписи Площадь/Высота/Мощность) ═══
COMMERCIAL_TEMPLATE_URL = "https://static.tildacdn.com/tild3735-3765-4137-a130-376363353730/plan-ksb.png"
COMMERCIAL_TEMPLATE_EXT = "png"
COMMERCIAL_LAYOUT = {
    "size":       (1150, 1040),
    "plan_box":   (604, 324, 1046, 880),
    "header_overlay": {
        "clear_rect": (560, 165, 1085, 248),
        "clear_color": (255, 255, 255),
        "labels": [
            {"pos": (640,  235), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Площадь"},
            {"pos": (843,  235), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Высота"},
            {"pos": (1029, 235), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Мощность"},
        ],
    },
    "area_value":    {"pos": (640,  178), "size": 60, "color": (20, 30, 50), "anchor": "mm"},
    "ceiling_value": {"pos": (843,  178), "size": 60, "color": (20, 30, 50), "anchor": "mm"},
    "power_value":   {"pos": (1029, 178), "size": 50, "color": (20, 30, 50), "anchor": "mm"},
}

# Обогащение коммерции Зорге 9 — на ЖИЛОМ шаблоне Зорге (там фото+брендинг «ЗОРГЕ №9»).
# Жилой шаблон: план слева, метки Этаж/Площадь/Комнаты сверху, плашки рассрочки снизу.
# Под коммерцию: закрываем метки и плашки, рисуем Высота/Площадь/Мощность.
COMMERCIAL_TEMPLATE_ZORGE_URL = "https://static.tildacdn.com/tild3130-3231-4832-a464-623331636437/plan-z9.jpg"
COMMERCIAL_TEMPLATE_ZORGE_EXT = "jpg"
_Z_CREAM = (248, 240, 226)   # тёплый фон левой части шаблона (замер)
# Левое поле: x 0..660, центр ~330. Числа и план центруем по 330 (3 колонки шаг 179).
COMMERCIAL_LAYOUT_ZORGE = {
    "size":       (1200, 900),
    "plan_box":   (45, 330, 615, 695),   # центр x=330, чуть ниже (на месте плашек)
    "header_overlay": {
        "clear_rect": (60, 185, 600, 245),
        "clear_color": _Z_CREAM,
        "labels": [
            {"pos": (151, 215), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Высота"},
            {"pos": (330, 215), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Площадь"},
            {"pos": (509, 215), "size": 20, "color": (20, 30, 50), "anchor": "mm", "text": "Мощность"},
        ],
    },
    "cover_rects": [
        {"rect": (40, 695, 640, 862), "color": _Z_CREAM},    # плашки рассрочки слева → в цвет фона (невидимо)
        {"rect": (700, 722, 1135, 802), "color": (0, 0, 0)}, # кремовая плашка «рассрочка» на чёрном фоне → заливаем чёрным (Дом готов выше y722 — остаётся)
    ],
    "ceiling_value": {"pos": (151, 155), "size": 46, "color": (20, 30, 50), "anchor": "mm"},
    "area_value":    {"pos": (330, 155), "size": 46, "color": (20, 30, 50), "anchor": "mm"},
    "power_value":   {"pos": (509, 155), "size": 46, "color": (20, 30, 50), "anchor": "mm"},
}


def file_ver(path) -> str:
    """Версия файла для cache-busting ссылок (?v=...). Меняется при перезаписи файла —
    тогда классифайд (Авито/Яндекс) видит новый URL и перезабирает картинку."""
    try:
        return str(int(Path(path).stat().st_mtime))
    except Exception:
        return "0"


def lot_view_urls(slug: str, internal_id: str) -> list:
    """URL видов из окон лота (cache/<slug>/views/<id>/*.jpg) с версией в пути."""
    vdir = CACHE_DIR / slug / "views" / internal_id
    if not vdir.exists():
        return []
    return [f"{PUBLIC_BASE_URL}/views/{slug}/{internal_id}/{file_ver(f)}/{f.name}"
            for f in sorted(vdir.glob("*.jpg"))]


def excluded_photos(slug: str, kind: str) -> set:
    """Имена фото, исключённых вручную в админке — не возвращать из ЯД и не показывать."""
    return set(load_overrides().get(slug, {}).get("photos_excluded", {}).get(kind, []))


def add_excluded_photo(slug: str, kind: str, name: str) -> None:
    """Добавить фото в чёрный список набора (чтобы синк с ЯД его больше не возвращал)."""
    data = load_overrides()
    lst = data.setdefault(slug, {}).setdefault("photos_excluded", {}).setdefault(kind, [])
    if name not in lst:
        lst.append(name)
    save_overrides(data)


def cian_extra_urls(slug: str) -> list:
    """URL общего набора фото карточки ЦИАН (cache/<slug>/extra_cian/*.jpg)
    в порядке из настройки extra_photo_order_cian (новые — в конец), с версией в пути."""
    d = CACHE_DIR / slug / "extra_cian"
    if not d.exists():
        return []
    files = {p.name for p in d.glob("*.jpg")} - excluded_photos(slug, "cian")
    order = [n for n in (get_project(slug).get("extra_photo_order_cian") or []) if n in files]
    names = order + sorted(files - set(order))
    return [f"{PUBLIC_BASE_URL}/extra_cian/{slug}/{file_ver(d / n)}/{n}" for n in names]


def get_project(slug: str) -> dict:
    """Эффективный конфиг проекта = PROJECTS[slug] + разрешённые оверрайды из админки."""
    base = dict(PROJECTS[slug])
    for k, v in load_overrides().get(slug, {}).items():
        if k in EDITABLE_KEYS:
            base[k] = v
    return base

# ═══ Публикация ═══
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://your-domain.example.com")

# ═══ HTTP-сервер ═══
SERVE_HOST = os.environ.get("SERVE_HOST", "0.0.0.0")
SERVE_PORT = int(os.environ.get("SERVE_PORT", "8765"))
REFRESH_INTERVAL_HOURS = int(os.environ.get("REFRESH_INTERVAL_HOURS", "4"))

# ═══ Опционально: ProfitBase API для загрузки копии ═══
PB_API_TOKEN  = os.environ.get("PB_API_TOKEN", "")
PB_UPLOAD_URL = os.environ.get("PB_UPLOAD_URL", "")


# ═══ Совместимость со старой версией (один проект) ═══
PB_FEED_URL         = PROJECTS["zorge9"]["pb_feed_url"]
FIGMA_TEMPLATE_URL  = PROJECTS["zorge9"]["figma_template_url"]
_d = project_dirs("zorge9")
TEMPLATES_DIR = _d["templates"]
PLANS_DIR     = _d["plans"]
ENRICHED_DIR  = _d["enriched"]
FEEDS_DIR     = _d["feeds"]
