FROM python:3.12-slim

# Системные либы для Pillow (jpeg/png/freetype) — на случай если wheel потянет их рантайм
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo libpng16-16 libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости — кэшируется отдельным слоем
COPY feed_enricher/requirements.txt /app/feed_enricher/requirements.txt
RUN pip install --no-cache-dir -r /app/feed_enricher/requirements.txt

# Затем код пакета
COPY feed_enricher/ /app/feed_enricher/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8765

# waitress как WSGI-сервер; wsgi.py поднимает фоновый авто-рефрешер при импорте
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8765", "--threads=4", "feed_enricher.wsgi:application"]
