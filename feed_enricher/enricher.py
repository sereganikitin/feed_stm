"""Генерация обогащенной планировки.

Логика:
1. Скачать шаблон (PNG/JPG из Tilda CDN).
2. Скачать оригинал планировки из ProfitBase (LayoutPhoto/FullUrl).
3. Вписать оригинальный план в `plan_box` шаблона (сохраняя пропорции).
4. Подставить ТОЛЬКО значения в существующие поля шаблона:
     • число этажа / площади / комнат
     • для Зорге 9 — ещё ПВ и платёж по формуле из config.installment

Шаблон уже содержит все статичные надписи (метки, бренд, "ОТ 10%" и т.п.) —
ничего дополнительного НЕ рисуем. Дубль текста с титром был убран в этой версии.
"""
import hashlib, requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from .config import PROJECTS, project_dirs, get_project
from .parser import FeedLot


# ──────────────── загрузка картинок с кэшированием ────────────────

def _http_get_image(url: str, cache_path: Path) -> Image.Image:
    if not cache_path.exists():
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        cache_path.write_bytes(r.content)
    return Image.open(cache_path).convert("RGBA")


def get_template(slug: str) -> Image.Image:
    proj = PROJECTS[slug]
    dirs = project_dirs(slug)
    if not proj["figma_template_url"]:
        raise RuntimeError(f"figma_template_url не задан для проекта {slug}")
    ext = proj.get("template_ext", "jpg")
    return _http_get_image(proj["figma_template_url"], dirs["templates"] / f"template.{ext}")


def get_original_plan(slug: str, plan_url: str) -> Image.Image:
    dirs = project_dirs(slug)
    h = hashlib.md5(plan_url.encode("utf-8")).hexdigest()[:16]
    return _http_get_image(plan_url, dirs["plans"] / f"{h}.jpg")


# ──────────────── шрифт и форматирование ────────────────

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_TT_FORS_PATH = _FONT_DIR / "TT-Fors-Trial-Medium.ttf"


def _font(size: int) -> ImageFont.FreeTypeFont:
    # Приоритет — кастомный TT Fors Trial Medium (бренд-шрифт)
    if _TT_FORS_PATH.exists():
        try: return ImageFont.truetype(str(_TT_FORS_PATH), size)
        except OSError: pass
    for c in ["arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]:
        try: return ImageFont.truetype(c, size)
        except OSError: continue
    return ImageFont.load_default()


def _rooms_label(lot: FeedLot) -> str:
    """Что писать в поле «Комнаты»."""
    if lot.rooms == 0:  return "Ст."
    if lot.rooms < 0:   return "СП"
    return f"{lot.rooms}К"


def _area_label(area: float) -> str:
    """Площадь — одно дробное (24.9 → '24,9')."""
    return f"{area:.1f}".replace(".", ",")


def _money_short(rub: float, unit: str) -> str:
    """7_500_000 'млн' → '7,5'.  85_000 'тыс' → '85'."""
    if unit == "млн":
        v = rub / 1_000_000
        return f"{v:.2f}".rstrip("0").rstrip(",").rstrip(".").replace(".", ",")
    if unit == "тыс":
        return f"{round(rub / 1_000)}"
    return f"{int(round(rub))}"


# ──────────────── формула рассрочки ────────────────

def installment_values(price_feed: float, params: dict) -> tuple[float, float]:
    """(ПВ, ежемесячный платёж) от цены в фиде.

    Цена в фиде = изн.прайс × feed_to_base_divisor (например 0.8 = «−20%»).
        base    = price_feed / feed_to_base_divisor    (изначальный прайс)
        ПВ      = base × down_payment_pct              (10% от изначальной)
        платёж  = base × monthly_pct_of_base           (0.5% от изначальной)
    """
    base    = price_feed / params["feed_to_base_divisor"]
    pv      = base * params["down_payment_pct"]
    monthly = base * params["monthly_pct_of_base"]
    return pv, monthly


# ──────────────── основной API ────────────────

def _draw_field(draw: ImageDraw.ImageDraw, field: dict, text: str):
    """Универсальное рисование поля по конфигу.

    Поля field:
      pos, size, color     — обязательные
      anchor               — PIL anchor (по умолчанию 'la')
      suffix               — приписать после text: «1,45» + " МЛН ₽" → "1,45 МЛН ₽"
      clear_rect           — (x0,y0,x1,y1) предварительно закрасить эту область
      clear_color          — цвет закрашивания (RGB tuple)
    """
    # Если задан clear_rect — сначала закрашиваем (поверх статики шаблона)
    if field.get("clear_rect"):
        clr = field.get("clear_color", (0, 0, 0))
        draw.rectangle(field["clear_rect"], fill=clr + (255,))

    text_full = text + (f" {field['suffix']}" if field.get("suffix") else "")
    anchor = field.get("anchor", "la")
    draw.text(
        field["pos"], text_full,
        fill=field["color"] + (255,),
        font=_font(field["size"]),
        anchor=anchor,
    )


def enrich_commercial(lot, plan_url: str, template_url: str, template_ext: str,
                      layout: dict, templates_dir: Path, plans_dir: Path, out_path: Path) -> Path:
    """Обогащённая планировка коммерческого помещения: шаблон Б37 + план + подписи
    Площадь/Высота/Мощность (вместо Комнаты/Площадь/Этаж). Идемпотентно по out_path."""
    if out_path.exists():
        return out_path
    canvas = _http_get_image(template_url, templates_dir / f"template.{template_ext}") \
        .convert("RGBA").resize(layout["size"], Image.LANCZOS)
    if plan_url:
        h = hashlib.md5(plan_url.encode("utf-8")).hexdigest()[:16]
        plan = _http_get_image(plan_url, plans_dir / f"{h}.jpg")
        box = layout["plan_box"]
        tw, th = box[2] - box[0], box[3] - box[1]
        pr = plan.copy(); pr.thumbnail((tw, th), Image.LANCZOS)
        canvas.alpha_composite(pr, (box[0] + (tw - pr.width) // 2, box[1] + (th - pr.height) // 2))

    draw = ImageDraw.Draw(canvas)
    # Прямоугольники-заглушки (закрыть ненужные элементы шаблона, напр. плашки рассрочки)
    for cr in layout.get("cover_rects", []):
        draw.rectangle(cr, fill=layout.get("cover_color", (255, 255, 255)) + (255,))
    ov = layout.get("header_overlay")
    if ov:
        draw.rectangle(ov["clear_rect"], fill=ov.get("clear_color", (255, 255, 255)) + (255,))
        for lbl in ov.get("labels", []):
            _draw_field(draw, lbl, lbl["text"])

    area_s = f"{lot.area:.1f}".rstrip("0").rstrip(".").replace(".", ",") if lot.area else ""
    ceil_s = f"{lot.ceiling_m:.2f}".rstrip("0").rstrip(".").replace(".", ",") if lot.ceiling_m else ""
    pow_s = (lot.power_kw or "").replace(".", ",")
    for key, val in [("area_value", area_s), ("ceiling_value", ceil_s), ("power_value", pow_s)]:
        if key in layout and val:
            _draw_field(draw, layout[key], val)

    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def enrich_lot(slug: str, lot: FeedLot) -> Path:
    """Создаёт обогащенную планировку для лота. Идемпотентно по id."""
    proj = get_project(slug)
    dirs = project_dirs(slug)
    layout = proj["layout"]
    out_path = dirs["enriched"] / f"{lot.internal_id}.png"
    if out_path.exists():
        return out_path

    # Фон — шаблон, приведённый к нужному размеру
    canvas = get_template(slug).convert("RGBA").resize(layout["size"], Image.LANCZOS)

    # Планировка → в plan_box, сохраняя пропорции
    if lot.plan_url:
        plan = get_original_plan(slug, lot.plan_url)
        box = layout["plan_box"]
        target_w, target_h = box[2] - box[0], box[3] - box[1]
        plan_resized = plan.copy()
        plan_resized.thumbnail((target_w, target_h), Image.LANCZOS)
        px = box[0] + (target_w - plan_resized.width) // 2
        py = box[1] + (target_h - plan_resized.height) // 2
        canvas.alpha_composite(plan_resized, (px, py))

    draw = ImageDraw.Draw(canvas)

    # Если в layout задана зона "header_overlay" — закрашиваем штатные метки шаблона
    # и рисуем свои значения + свои метки (нужно когда метки шаблона не на тех координатах,
    # которые хочет дизайн — например на Б37 метки на y=190, а нужно на y=235 под «БОР»).
    overlay = layout.get("header_overlay")
    if overlay:
        draw.rectangle(overlay["clear_rect"], fill=overlay.get("clear_color", (255,255,255)) + (255,))
        # подписи (метки)
        for lbl in overlay.get("labels", []):
            _draw_field(draw, lbl, lbl["text"])

    # Три обязательных числовых поля
    if "floor_value" in layout and lot.floor:
        _draw_field(draw, layout["floor_value"], str(lot.floor))
    if "area_value" in layout and lot.area_total:
        _draw_field(draw, layout["area_value"], _area_label(lot.area_total))
    if "rooms_value" in layout:
        _draw_field(draw, layout["rooms_value"], _rooms_label(lot))

    # Рассрочка — только если в проекте есть installment + поля на шаблоне
    if proj.get("installment") and lot.price and "down_payment" in layout:
        pv, monthly = installment_values(lot.price, proj["installment"])
        _draw_field(draw, layout["down_payment"], _money_short(pv, "млн"))
        _draw_field(draw, layout["monthly_pay"],  _money_short(monthly, "тыс"))

    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path
