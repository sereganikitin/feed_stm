"""Конвертация лотов из ЦИАН-фида ProfitBase в формат Авито (автозагрузка).

Авито использует СВОЮ схему, не совместимую с ЦИАН:
  <Ads formatVersion="3" target="Avito.ru">
    <Ad>
      <Id/> <Category>Квартиры</Category> <OperationType>Продажа</OperationType>
      <MarketType/> <Address/> <Rooms/> <Square/> <Floor/> <Floors/>
      <Status/> <Price/> <Description/> <ContactPhone/>
      <Images><Image url="..."/></Images>
      <VideoURL/>
    </Ad>
  </Ads>

Главная картинка карточки = наша обогащённая планировка (тот же PNG, что и для ЦИАН).
Остальные фото — оригиналы ProfitBase (дом/фасад), но БЕЗ исходных планов
(их заменяет обогащённая планировка).

ВНИМАНИЕ по модерации: на обогащённой картинке Зорге 9 есть плашки рассрочки/цены.
Авито может отклонять фото с рекламным текстом — перед боевым подключением прогнать
готовый XML через официальный валидатор: https://autoload.avito.ru/format/xmlcheck/
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import PUBLIC_BASE_URL, PROJECTS, project_dirs, get_project
from .parser import FeedLot, rooms_from_description


# Какие фото из ProfitBase НЕ берём как доп.снимки (это планы — их заменяет наша картинка)
_PLAN_URL_MARKERS = ("/uploads/layout/", "/uploads/preset/", "/uploads/plan")

# ProfitBase иногда отдаёт «Тип дома» в укороченной форме, которой нет в справочнике
# Авито (напр. «Монолит»). Нормализуем к допустимым значениям Авито.
_HOUSE_TYPE_FIX = {
    "Монолит":            "Монолитный",
    "Кирпич":             "Кирпичный",
    "Панель":             "Панельный",
    "Блок":               "Блочный",
    "Монолит-кирпич":     "Монолитно-кирпичный",
    "Монолитно кирпичный": "Монолитно-кирпичный",
    "Дерево":             "Деревянный",
}


def _enriched_url(slug: str, internal_id: str) -> str:
    return f"{PUBLIC_BASE_URL}/enriched/{slug}/{internal_id}.png"


def _avito_rooms_str(code: int) -> str:
    """Код комнатности (как lot.rooms) → значение Авито."""
    if code == 0:
        return "Студия"
    if code < 0:
        return "Своб. планировка"
    if code >= 10:
        return "10 и более"
    return str(code)


def _rooms_avito(lot: FeedLot) -> str:
    return _avito_rooms_str(lot.rooms)


def _cover_and_photos(slug: str, lot: FeedLot, enriched_dir: Path) -> list[str]:
    """Список URL картинок: [обогащённая планировка (обложка), реальные фото...].

    Если обогащённой нет (лот не прошёл генерацию) — обложкой берём первое НЕ-план фото.
    """
    real_photos = [u for u in lot.other_photos
                   if not any(m in u for m in _PLAN_URL_MARKERS)]
    urls: list[str] = []
    if (enriched_dir / f"{lot.internal_id}.png").exists():
        urls.append(_enriched_url(slug, lot.internal_id))
        urls.extend(real_photos)
    elif real_photos:
        urls.extend(real_photos)
    elif lot.plan_url:
        urls.append(lot.plan_url)
    # Авито: максимум 40 изображений
    return urls[:40]


def _text(parent: ET.Element, tag: str, value) -> None:
    """Добавить дочерний тег с текстом (пропустить если значение пустое)."""
    if value is None or str(value).strip() == "":
        return
    el = ET.SubElement(parent, tag)
    el.text = str(value)


def assemble_avito_feed(slug: str, lots: list[FeedLot], out_path: Path) -> Path:
    proj = get_project(slug)
    enriched_dir = project_dirs(slug)["enriched"]

    market_type = proj.get("avito_market_type", "Новостройка")
    house_type  = proj.get("avito_house_type", "")          # опционально: материал дома
    nd_id       = proj.get("avito_new_development_id", "")   # опционально: id ЖК в Авито

    root = ET.Element("Ads", {"formatVersion": "3", "target": "Avito.ru"})

    for lot in lots:
        images = _cover_and_photos(slug, lot, enriched_dir)
        # Авито требует минимум 1 изображение и цену — иначе лот невалиден, пропускаем
        if not images or not lot.price:
            continue

        ad = ET.SubElement(root, "Ad")
        _text(ad, "Id", lot.internal_id)
        _text(ad, "Category", "Квартиры")
        _text(ad, "OperationType", "Продажа")
        _text(ad, "MarketType", market_type)
        if nd_id:
            _text(ad, "NewDevelopmentId", nd_id)
        _text(ad, "Address", lot.address)
        _text(ad, "Status", "Апартаменты" if lot.is_apartments else "Квартира")
        _text(ad, "Rooms", _rooms_avito(lot))
        _text(ad, "Square", f"{lot.area_total:.1f}".rstrip("0").rstrip("."))
        if lot.floor:
            _text(ad, "Floor", lot.floor)
        if lot.floors_total:
            _text(ad, "Floors", lot.floors_total)
        if house_type:
            _text(ad, "HouseType", house_type)
        _text(ad, "Price", int(lot.price))
        _text(ad, "Description", lot.description or _fallback_description(lot))
        _text(ad, "ContactPhone", lot.phone)

        imgs = ET.SubElement(ad, "Images")
        for u in images:
            ET.SubElement(imgs, "Image", {"url": u})

        if lot.video_url:
            _text(ad, "VideoURL", lot.video_url)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def enrich_pb_avito_feed(slug: str, avito_xml: bytes, out_path: Path) -> Path:
    """Вариант A: берём ГОТОВЫЙ Авито-фид из ProfitBase и подменяем только обложку.

    Для каждого <Ad>:
      • заполняем «Отделку», если ProfitBase её не отдал (Авито требует непустую);
      • если задан avito_extra_photos — убираем фото ProfitBase по replace_markers
        (напр. building_image) и дописываем наши фото из кэша /extra (залиты с Я.Диска);
      • по <Id> ищем нашу планировку (cache/<slug>/enriched/<Id>.png); если есть —
        удаляем исходные «голые» планы и ставим её ПЕРВЫМ <Image> (обложка карточки).
    Прочие поля фида ProfitBase не трогаем — за валидность Авито-схемы отвечает ProfitBase.

    NB: предполагается, что <Id> в Авито-фиде ProfitBase == ExternalId из ЦИАН-фида
    (этим ключом названы PNG). Если ProfitBase нумерует иначе — поправить здесь маппинг.
    """
    proj = get_project(slug)
    dirs = project_dirs(slug)
    enriched_dir = dirs["enriched"]
    default_decoration = proj.get("avito_default_decoration", "")
    description_suffix = (proj.get("description_suffix") or "").strip()
    house_override     = (proj.get("avito_house_type") or "").strip()
    try:
        discount_pct = float(proj.get("price_discount_pct") or 0)
    except (TypeError, ValueError):
        discount_pct = 0.0

    # Наши фото для карточки Авито (залиты в /extra: с Я.Диска и/или загружены в панели).
    # Тоггл avito_replace_building_image (из админки) включает/выключает замену.
    extra_cfg   = proj.get("avito_extra_photos") or {}
    replace_markers = extra_cfg.get("replace_markers", [])
    do_replace  = proj.get("avito_replace_building_image", True)
    extra_urls = []
    if extra_cfg and do_replace:
        files = {f.name: f for f in dirs["extra"].glob("*.jpg")}
        order = [n for n in (proj.get("extra_photo_order") or []) if n in files]
        # файлы не из списка порядка — в конец по имени
        rest  = sorted(n for n in files if n not in order)
        extra_urls = [f"{PUBLIC_BASE_URL}/extra/{slug}/{n}" for n in (order + rest)]

    root = ET.fromstring(avito_xml)

    for ad in root.iter("Ad"):
        iid = (ad.findtext("Id") or "").strip()

        # Авито требует непустую «Отделку». Где ProfitBase её не отдал — дефолт проекта.
        if default_decoration:
            dec = ad.find("Decoration")
            if dec is None or not (dec.text or "").strip():
                if dec is None:
                    dec = ET.SubElement(ad, "Decoration")
                dec.text = default_decoration

        # Скидка к цене (если включена в админке). Применяется к цене из фида.
        if discount_pct:
            pe = ad.find("Price")
            if pe is not None and (pe.text or "").strip():
                try:
                    pe.text = str(int(round(float(pe.text) * (1 - discount_pct / 100))))
                except ValueError:
                    pass

        # «Тип дома»: форс из настроек панели, иначе нормализуем значение ProfitBase
        # к справочнику Авито (напр. «Монолит» → «Монолитный»).
        ht = ad.find("HouseType")
        if house_override:
            if ht is None:
                ht = ET.SubElement(ad, "HouseType")
            ht.text = house_override
        elif ht is not None and (ht.text or "").strip() in _HOUSE_TYPE_FIX:
            ht.text = _HOUSE_TYPE_FIX[(ht.text or "").strip()]

        # «Количество комнат»: Авито отклоняет, когда поле (евро-счёт ProfitBase)
        # расходится с описанием. Берём число из описания (по спальням) и проставляем.
        rv = rooms_from_description(ad.findtext("Description"))
        if rv is not None:
            rm = ad.find("Rooms")
            if rm is None:
                rm = ET.SubElement(ad, "Rooms")
            rm.text = _avito_rooms_str(rv)

        # Приписка к описанию (из админки) — добавляем в конец, если её ещё нет.
        if description_suffix:
            d = ad.find("Description")
            if d is None:
                d = ET.SubElement(ad, "Description")
                d.text = description_suffix
            elif description_suffix not in (d.text or ""):
                d.text = (d.text or "").rstrip() + "\n\n" + description_suffix

        imgs = ad.find("Images")

        # Замена фото ProfitBase (building_image) на наши с Я.Диска.
        # Делаем только если наши фото реально есть — иначе не оставляем карточку без фото.
        if extra_urls and imgs is not None:
            for img in list(imgs):
                if any(m in (img.get("url") or "") for m in replace_markers):
                    imgs.remove(img)
            for u in extra_urls:
                ET.SubElement(imgs, "Image", {"url": u})

        # Обложка — наша брендированная планировка (если сгенерирована для лота).
        if iid and (enriched_dir / f"{iid}.png").exists():
            if imgs is None:
                imgs = ET.SubElement(ad, "Images")
            for img in list(imgs):
                if any(m in (img.get("url") or "") for m in _PLAN_URL_MARKERS):
                    imgs.remove(img)
            imgs.insert(0, ET.Element("Image", {"url": _enriched_url(slug, iid)}))

        # Авито: не более 40 изображений
        if imgs is not None:
            while len(imgs) > 40:
                imgs.remove(imgs[-1])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def _fallback_description(lot: FeedLot) -> str:
    """Минимальное описание, если в фиде его не было (Авито требует непустой текст)."""
    rooms = _rooms_avito(lot)
    bits = [rooms if rooms in ("Студия", "Своб. планировка") else f"{rooms}-комн."]
    bits.append(f"площадь {lot.area_total:.1f} м²")
    if lot.floor and lot.floors_total:
        bits.append(f"этаж {lot.floor} из {lot.floors_total}")
    if lot.jk_name:
        bits.append(f"ЖК «{lot.jk_name}»")
    return ", ".join(bits) + "."
