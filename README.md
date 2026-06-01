# feed_stm — обогащение фидов квартир для классифайдов

Сервис берёт фиды квартир из ProfitBase (ЦИАН/Авито форматы), накладывает на планировки
брендированный шаблон, подменяет фото карточки на свои (с Яндекс.Диска или загруженные
через админку) и отдаёт обогащённые XML-фиды для ЦИАН и Авито. Прод: **feeds.infoseledka.ru**.

Проекты: **zorge9** (Зорге 9) и **b37** (Квартал Серебряный Бор, Берзарина 37).

## Структура

```
Dockerfile, docker-compose.yml   — сборка/запуск контейнера feed-enricher
feeds.conf                       — nginx (проксирование feeds.infoseledka.ru → :8765)
feed_enricher/                   — пакет (Flask + Pillow)
  ├── server.py        — роуты: /feed/<slug>.xml, /feed/<slug>-avito.xml, /enriched, /extra, /admin
  ├── parser.py        — парсинг ЦИАН-XML
  ├── enricher.py      — генерация брендированной планировки (PIL)
  ├── assembler.py     — сборка ЦИАН-фида (подмена картинки)
  ├── assembler_avito.py — Авито-фид: native ProfitBase + подмена обложки/фото/полей
  ├── yadisk.py        — синк фото с публичной папки Яндекс.Диска
  ├── admin.py         — админ-панель (/admin): фото, настройки, refresh
  ├── config.py        — PROJECTS + рантайм-оверрайды (overrides.json)
  └── DEPLOY.md        — инструкция по развёртыванию
```

## Деплой обновления (на сервере)

```bash
cd /opt/feed-enricher
git pull                      # если сервер — клон репозитория
docker compose build feed-enricher && docker compose up -d --force-recreate feed-enricher
```

Подробнее — `feed_enricher/DEPLOY.md`.
