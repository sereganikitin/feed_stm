# feed_enricher — прокси для классифайдов (мультипроектный)

Сервис: **2 проекта** — Зорге 9 и Б37 (Квартал Серебряный Бор). Каждый проект получает свой обогащенный фид по уникальному URL.

## Архитектура

```
                         ┌──────────────────────────────────┐
[ProfitBase Зорге 9]────→│                                  │
                         │   feed_enricher (наш сервер)     │──→ /feed/zorge9.xml
[ProfitBase Б37]────────→│   • парсит XML                   │──→ /feed/b37.xml
                         │   • генерит планировки (PIL)     │
[Figma Зорге шаблон]────→│   • кэширует PNG                 │
[Figma Б37 шаблон]──────→│   • собирает новый XML           │──→ /enriched/<slug>/<id>.png
                         └──────────────────────────────────┘
                                       ↑
                                Опционально: POST копии XML обратно в ProfitBase
                                (если у них есть upload API)
```

## Конфигурация (config.py → PROJECTS)

```python
PROJECTS = {
    "zorge9": {
        "name": "Зорге 9",
        "pb_feed_url": "https://pb7828.profitbase.ru/export/cian/52f269...",  # есть
        "figma_template_url": "https://static.tildacdn.com/.../plan-z9.jpg",  # есть
        "layout": {...},
    },
    "b37": {
        "name": "Квартал Серебряный Бор",
        "pb_feed_url": "",          # НУЖНА ССЫЛКА
        "figma_template_url": "",   # НУЖНА ССЫЛКА
        "layout": {...},
    },
}
```

## Команды

```bash
# Просто посмотреть что в фиде (без генерации)
python -m feed_enricher.cli inspect zorge9
python -m feed_enricher.cli inspect b37
python -m feed_enricher.cli inspect          # оба проекта

# Пересобрать кэш
python -m feed_enricher.cli refresh zorge9
python -m feed_enricher.cli refresh          # оба проекта

# HTTP сервер с авто-рефрешем
python -m feed_enricher.cli serve
```

## URL для классифайдов

После запуска сервиса в личных кабинетах подключаем:

| Платформа | Зорге 9 | Б37 |
|-----------|---------|-----|
| ЦИАН      | `https://<наш-домен>/feed/zorge9.xml` | `https://<наш-домен>/feed/b37.xml` |
| Авито     | `https://<наш-домен>/feed/zorge9-avito.xml` | `https://<наш-домен>/feed/b37-avito.xml` |
| Яндекс    | (формат ЦИАН) `…/feed/zorge9.xml` | `…/feed/b37.xml` |

> Авито использует собственную схему фида (Avito Автозагрузка, formatVersion=3) —
> поэтому у него отдельный URL, не тот же что у ЦИАН. Источник данных — ProfitBase
> (native Avito-выгрузка, `pb_avito_feed_url`), обложка — наша брендированная планировка.

## Как получается «копия в ProfitBase»

Возможны два сценария (выбрать тот что поддерживает ProfitBase):

### Вариант A — внешний фид (наиболее простой)
Сервис отдаёт обогащенный XML по своему URL. В кабинете ProfitBase создаём «копию выгрузки» с типом «внешний фид» и указываем `https://<наш-домен>/feed/zorge9.xml`. ProfitBase прокачивает его и отдаёт классифайдам как свой.

### Вариант B — upload в ProfitBase через API
Если у ProfitBase есть HTTP-эндпоинт для загрузки готового XML — настраиваем `PB_UPLOAD_URL` и `PB_API_TOKEN` в env. Тогда после каждого refresh сервис делает POST с feed.xml в ProfitBase, и копия живёт уже внутри их системы.

Если ни A, ни B не подходят — классифайды настраиваются **напрямую** на наш URL минуя ProfitBase.

## Что нужно от тебя (минимум, чтобы запустить Б37)

1. **URL фида Б37 из ProfitBase** — формат `https://pb<NNNN>.profitbase.ru/export/cian/<token>?scheme=https`
2. **URL шаблона Figma для Б37** — публичный PNG/JPG (через Tilda CDN или подобный)
3. (Опционально) **API ProfitBase** для загрузки копии XML — endpoint + токен

После этого: вписать значения в `config.py` (или env-переменные `PB_FEED_URL_B37`, `FIGMA_TEMPLATE_URL_B37`) и запустить `python -m feed_enricher.cli refresh b37`.

## Структура файлов

```
feed_enricher/
├── config.py        — PROJECTS, пути, ProfitBase upload API
├── parser.py        — парсинг CIAN-XML, dataclass FeedLot
├── enricher.py      — композиция плана + шаблон + УТП (PIL), параметризован slug
├── assembler.py     — сборка нового XML с подменой URL на /enriched/<slug>/<id>.png
├── server.py        — Flask: /feed/<slug>.xml, /enriched/<slug>/<id>.png, /refresh/<slug>
├── cli.py           — inspect/refresh/serve, аргумент slug
└── cache/
    ├── zorge9/{templates,plans,enriched,feeds}/
    └── b37/{templates,plans,enriched,feeds}/
```

## Env-переменные

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `PB_FEED_URL_B37` | URL фида Б37 в ProfitBase | пусто (надо задать) |
| `FIGMA_TEMPLATE_URL_B37` | URL шаблона Figma для Б37 | пусто |
| `PUBLIC_BASE_URL` | Базовый URL под которым доступны наши PNG | `https://your-domain.example.com` |
| `PB_API_TOKEN` | Токен ProfitBase для upload (если используется вариант B) | пусто |
| `PB_UPLOAD_URL` | Эндпоинт ProfitBase upload (вариант B) | пусто |
| `SERVE_HOST` / `SERVE_PORT` | bind | `0.0.0.0:8765` |
| `REFRESH_INTERVAL_HOURS` | Период авто-обновления | `4` |
