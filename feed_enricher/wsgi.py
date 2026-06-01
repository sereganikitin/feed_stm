"""WSGI-точка входа для production-сервера (waitress, gunicorn).

Запуск с waitress (Windows / Linux):
    waitress-serve --host=0.0.0.0 --port=8765 feed_enricher.wsgi:application

Запуск с gunicorn (Linux):
    gunicorn -b 0.0.0.0:8765 -w 2 feed_enricher.wsgi:application
"""
from .server import app, start_background_refresher

# Автообновитель фида запускается один раз при импорте модуля
start_background_refresher()

application = app
