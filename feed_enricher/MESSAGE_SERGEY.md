## Сообщение Сергею

Привет!

Нужно развернуть на сервере небольшой Python-сервис `feed_enricher`. Он генерирует обогащённые XML-фиды квартир для классифайдов (ЦИАН/Авито/Яндекс): берёт исходный фид из ProfitBase, накладывает на каждую планировку наш брендированный шаблон с указанием площади/этажа/цены/рассрочки, и отдаёт всё это по HTTP.

Архитектурно — это Flask-приложение (~150 строк) + Pillow для отрисовки + кэш на диске.

### Что нужно

- Linux-сервер (Ubuntu 22.04+) или Windows Server
- Python 3.10+ (3.14 тоже ок)
- Публичный домен (например `feeds.stmichael.ru`) с TLS
- Открытый порт 443 (HTTPS) → reverse-proxy nginx → :8765 локально
- Исходящий HTTPS до `pb7828.profitbase.ru` и `static.tildacdn.com`
- ~500 МБ диска

### Что отдаю

Архив проекта `price_agent.zip`. Внутри папка `feed_enricher/` — там код, шрифты, инструкция:

- `feed_enricher/DEPLOY.md` — пошаговая инструкция (Ubuntu + systemd + nginx + certbot)
- `feed_enricher/requirements.txt` — pip-зависимости
- `feed_enricher/.env.example` — переменные окружения
- `feed_enricher/wsgi.py` — WSGI entry-point для waitress/gunicorn

### Что попрошу настроить

1. Поставить под пользователем `feedenricher`, путь `/opt/price_agent`
2. Создать systemd-сервис `feed-enricher.service` (шаблон в DEPLOY.md)
3. Поднять nginx с reverse-proxy на 127.0.0.1:8765
4. TLS через `certbot --nginx`
5. Прописать `PUBLIC_BASE_URL=https://feeds.stmichael.ru` в `.env`
6. Проверить что `https://feeds.stmichael.ru/feed/zorge9.xml` отдаёт XML
7. Прислать публичный URL — мы передадим фидологу

### Размер задачи

Стандартный python-deploy. По шагам в DEPLOY.md — 30-60 минут вместе с certbot. Дальше сервис сам забирает фид из ProfitBase каждые 4 часа и обновляет картинки.

### По обновлениям

Если в будущем понадобится поправить шаблон планировки или формулу рассрочки — я обновляю код в проекте, ты делаешь `git pull && systemctl restart feed-enricher`. Если на сервере нет git — буду присылать обновлённый zip.

Спасибо!
