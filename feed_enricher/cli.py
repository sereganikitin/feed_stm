"""CLI:
  python -m feed_enricher.cli inspect [<slug>]
  python -m feed_enricher.cli refresh [<slug>]   # без slug — все проекты
  python -m feed_enricher.cli serve
"""
import sys, json, io
# UTF-8 на Windows console (иначе UnicodeEncodeError на ═, ─ и т.п.)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
from . import server
from .config import PROJECTS, project_dirs
from .parser import download_feed, parse_feed


def cmd_inspect(slug: str | None = None):
    slugs = [slug] if slug else list(PROJECTS.keys())
    for s in slugs:
        proj = PROJECTS[s]
        print(f"\n══ {proj['name']} ({s}) ══")
        if not proj.get("pb_feed_url"):
            print("  pb_feed_url не задан — пропускаем")
            continue
        dirs = project_dirs(s)
        xml_bytes = download_feed(proj["pb_feed_url"], dirs["feeds"] / "original.xml")
        lots = parse_feed(xml_bytes)
        print(f"  Лотов: {len(lots)}")
        if lots:
            by_rooms = {}; by_dec = {}
            for l in lots:
                by_rooms[l.rooms] = by_rooms.get(l.rooms, 0) + 1
                by_dec[l.decoration] = by_dec.get(l.decoration, 0) + 1
            print(f"  По комнатности: {by_rooms}")
            print(f"  По отделке:     {by_dec}")
            print(f"  Пример: {lots[0].internal_id} {lots[0].rooms}к {lots[0].area_total}м² "
                  f"{lots[0].price:,}₽ plan={lots[0].plan_url[:60]}")


def cmd_refresh(slug: str | None = None):
    r = server.refresh_project(slug) if slug else server.refresh_all()
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_serve():
    server.main()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    {"inspect": cmd_inspect, "refresh": cmd_refresh, "serve": lambda *_: cmd_serve()}[cmd](arg)
