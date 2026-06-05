"""Сборка фидов коммерческой аренды (помещения свободного назначения) для площадок.

Источник — PBLot (parser_pb, из profitbase_xml). Площадки:
  • Яндекс.Недвижимость — realty-feed, type=аренда, property-type=коммерческая
  • Авито — Category=Коммерческая недвижимость, OperationType=Сдам
  • ЦИАН — Category=freeAppointmentObjectRent

ВНИМАНИЕ: коммерческие схемы площадок строже и отличаются от жилья — перед боем
прогнать через валидаторы (autoload.avito.ru/format/xmlcheck и т.п.), вероятна итерация.
images_for(lot) → список URL картинок (обложка-обогащение + планы/фото).
"""
import xml.etree.ElementTree as ET

YNS = "http://webmaster.yandex.ru/schemas/feed/realty/2010-06"


def _num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _ceil(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _desc(lot, proj) -> str:
    if lot.description:
        return lot.description
    bits = [lot.property_type or "Помещение свободного назначения",
            f"площадь {_num(lot.area)} м²"]
    if lot.ceiling_m:
        bits.append(f"высота потолка {_ceil(lot.ceiling_m)} м")
    if lot.power_kw:
        bits.append(f"мощность {lot.power_kw} кВт")
    if proj.get("address"):
        bits.append(proj["address"])
    return ", ".join(bits) + "."


# ──────────────── Яндекс.Недвижимость ────────────────

def build_yandex(lots, proj, images_for, out_path, gen_date):
    ET.register_namespace("", YNS)

    def E(p, tag, text=None):
        el = ET.SubElement(p, f"{{{YNS}}}{tag}")
        if text is not None and str(text) != "":
            el.text = str(text)
        return el

    agent = proj.get("sales_agent", {})
    root = ET.Element(f"{{{YNS}}}realty-feed")
    E(root, "generation-date", gen_date)
    for lot in lots:
        imgs = images_for(lot)
        if not (lot.area and lot.rent_month and imgs):
            continue
        o = E(root, "offer"); o.set("internal-id", lot.internal_id)
        E(o, "type", "аренда")
        E(o, "property-type", "коммерческая")
        E(o, "category", "свободного назначения")
        if agent.get("url"):
            E(o, "url", agent["url"])
        E(o, "creation-date", lot.creation_date or gen_date)
        E(o, "last-update-date", gen_date)
        sa = E(o, "sales-agent")
        for k in ("organization", "category", "phone", "url"):
            if agent.get(k):
                E(sa, k, agent[k])
        loc = E(o, "location")
        E(loc, "country", "Россия"); E(loc, "region", "Москва"); E(loc, "locality-name", "Москва")
        if proj.get("address"):
            E(loc, "address", proj["address"])
        pr = E(o, "price"); E(pr, "value", int(lot.rent_month)); E(pr, "currency", "RUB"); E(pr, "period", "month")
        ar = E(o, "area"); E(ar, "value", _num(lot.area)); E(ar, "unit", "кв.м")
        if lot.ceiling_m:
            E(o, "ceiling-height", _ceil(lot.ceiling_m))
        E(o, "description", _desc(lot, proj))
        for u in imgs:
            E(o, "image", u)
    ET.indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


# ──────────────── Авито ────────────────

def build_avito(lots, proj, images_for, out_path):
    agent = proj.get("sales_agent", {})
    root = ET.Element("Ads", {"formatVersion": "3", "target": "Avito.ru"})
    for lot in lots:
        imgs = images_for(lot)
        if not (lot.area and lot.rent_month and imgs):
            continue
        ad = ET.SubElement(root, "Ad")

        def T(tag, val):
            if val is None or str(val).strip() == "":
                return
            ET.SubElement(ad, tag).text = str(val)

        T("Id", lot.internal_id)
        T("Category", "Коммерческая недвижимость")
        T("OperationType", "Сдам")
        T("PropertyType", "Помещение свободного назначения")
        if proj.get("address"):
            T("Address", proj["address"])
        T("Square", _num(lot.area))
        T("Price", int(lot.rent_month))
        if lot.ceiling_m:
            T("CeilingHeight", _ceil(lot.ceiling_m))
        T("Description", _desc(lot, proj))
        if agent.get("phone"):
            T("ContactPhone", agent["phone"])
        if agent.get("organization"):
            T("ManagerName", agent["organization"])
        imel = ET.SubElement(ad, "Images")
        for u in imgs[:40]:
            ET.SubElement(imel, "Image", {"url": u})
    ET.indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


# ──────────────── ЦИАН ────────────────

def build_cian(lots, proj, images_for, out_path):
    agent = proj.get("sales_agent", {})
    root = ET.Element("feed")
    ET.SubElement(root, "feed_version").text = "2"
    for lot in lots:
        imgs = images_for(lot)
        if not (lot.area and lot.rent_month and imgs):
            continue
        o = ET.SubElement(root, "object")

        def T(p, tag, val):
            if val is None or str(val).strip() == "":
                return
            ET.SubElement(p, tag).text = str(val)

        T(o, "Category", "freeAppointmentObjectRent")
        T(o, "ExternalId", lot.internal_id)
        T(o, "Description", _desc(lot, proj))
        T(o, "Address", proj.get("address", ""))
        if agent.get("phone"):
            ph = ET.SubElement(o, "Phones"); psc = ET.SubElement(ph, "PhoneSchema")
            T(psc, "CountryCode", "+7"); T(psc, "Number", agent["phone"].lstrip("+7"))
        T(o, "TotalArea", _num(lot.area))
        if lot.ceiling_m:
            T(o, "CeilingHeight", _ceil(lot.ceiling_m))
        bt = ET.SubElement(o, "BargainTerms")
        T(bt, "Price", int(lot.rent_month))
        T(bt, "currency", "rur")
        T(bt, "PriceType", "all")
        T(bt, "PaymentPeriod", "monthly")
        # картинки
        lp = ET.SubElement(o, "LayoutPhoto"); T(lp, "FullUrl", imgs[0]); T(lp, "IsDefault", "1")
        if len(imgs) > 1:
            photos = ET.SubElement(o, "Photos")
            for i, u in enumerate(imgs):
                ps = ET.SubElement(photos, "PhotoSchema")
                T(ps, "FullUrl", u); T(ps, "IsDefault", "1" if i == 0 else "0")
    ET.indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
