# feed_enricher — деплой на сервер

Сервис принимает CIAN-XML фид из ProfitBase, генерит для каждого лота обогащённую планировку (PIL: шаблон Figma + план + цифры + блок рассрочки), и отдаёт по HTTP:
- обогащённый XML (тот же фид с подменой URL у `LayoutPhoto` и default-фото)
- PNG обогащённых планировок

URLs которые увидит классифайд:
```
GET /feed/zorge9.xml          → обогащённый фид Зорге 9 (ЦИАН)
GET /feed/b37.xml             → обогащённый фид Б37 (ЦИАН)
GET /feed/zorge9-avito.xml    → обогащённый фид Зорге 9 (Авито)
GET /feed/b37-avito.xml       → обогащённый фид Б37 (Авито)
GET /enriched/<slug>/<id>.png → конкретная картинка лота
GET /gallery/<slug>           → HTML-галерея для визуальной проверки
POST /refresh                 → ручной перезапуск сборки
POST /refresh/<slug>          → перезапуск одного проекта
```

Авито-фид (вариант A): сервис берёт native-выгрузку ProfitBase в формате Avito
(`PB_AVITO_FEED_URL_<SLUG>` в .env) и подменяет обложку каждой карточки на нашу
брендированную планировку. За валидность Авито-схемы отвечает ProfitBase.
Перед боевым подключением прогнать фид через https://autoload.avito.ru/format/xmlcheck/

Автообновление: каждые 4 часа (env `REFRESH_INTERVAL_HOURS`).

---

## Требования

- Python ≥ 3.10 (тестировалось на 3.14)
- pip
- доступ исходящий по HTTPS:
  - `https://pb7828.profitbase.ru` — забор фидов
  - `https://static.tildacdn.com` — забор шаблонов Figma
- доступ входящий: тот порт под которым повесим сервис (по умолчанию 8765); либо через reverse-proxy nginx 80/443
- ~500 МБ дискового пространства (картинки кэшируются)

---

## Установка

### Linux (Ubuntu 22.04+)

```bash
sudo apt-get install -y python3-pip python3-venv
cd /opt
sudo git clone <git_url_проекта> price_agent
sudo chown -R $USER:$USER price_agent
cd price_agent/feed_enricher

python3 -m venv ../venv
source ../venv/bin/activate
pip install -r requirements.txt

# скопировать env-шаблон и заполнить
cp .env.example .env
nano .env       # минимально: PUBLIC_BASE_URL

# тест что фиды забираются
python -m feed_enricher.cli inspect

# тест полной сборки
set -a; source .env; set +a
python -m feed_enricher.cli refresh
```

### Windows Server

Аналогично, но `venv\Scripts\activate.bat` вместо `source ../venv/bin/activate`.

---

## Запуск как сервис (Linux, systemd)

`/etc/systemd/system/feed-enricher.service`:

```ini
[Unit]
Description=feed_enricher — обогащение XML-фидов для классифайдов
After=network-online.target

[Service]
Type=simple
User=feedenricher
WorkingDirectory=/opt/price_agent
EnvironmentFile=/opt/price_agent/feed_enricher/.env
ExecStart=/opt/price_agent/venv/bin/waitress-serve \
          --host=0.0.0.0 --port=8765 \
          feed_enricher.wsgi:application
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin feedenricher
sudo chown -R feedenricher:feedenricher /opt/price_agent
sudo systemctl daemon-reload
sudo systemctl enable --now feed-enricher
sudo systemctl status feed-enricher        # должно быть active (running)
sudo journalctl -u feed-enricher -f        # просмотр логов
```

---

## Nginx (рекомендуется, +TLS через Let's Encrypt)

`/etc/nginx/sites-available/feed-enricher.conf`:

```nginx
server {
    listen 80;
    server_name feeds.example.ru;

    # включить TLS — отдельно через certbot

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    # картинки можно отдавать прямо с диска для скорости — опционально
    # location /enriched/ {
    #     alias /opt/price_agent/feed_enricher/cache/;
    #     expires 1h;
    # }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/feed-enricher.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d feeds.example.ru   # HTTPS
```

После этого:
- В `.env` поставить `PUBLIC_BASE_URL=https://feeds.example.ru`
- Перезапустить сервис: `sudo systemctl restart feed-enricher`
- Проверить: `curl -I https://feeds.example.ru/feed/zorge9.xml` → 200 + `Content-Type: application/xml`

---

## Проверка после деплоя

```bash
# обогащённый фид Зорге 9
curl https://feeds.example.ru/feed/zorge9.xml | head -50

# главная — список проектов
curl https://feeds.example.ru/

# галерея — визуальная проверка всех планировок
открыть https://feeds.example.ru/gallery/zorge9 в браузере
```

URL внутри XML должны быть вида `https://feeds.example.ru/enriched/...`, не `localhost`.

---

## Что отдавать фидологу

После того как `https://feeds.example.ru/feed/zorge9.xml` стабильно открывается из интернета:

```
Привет! Передаю фиды:

ЦИАН:
  https://feeds.infoseledka.ru/feed/zorge9.xml  (Зорге 9, 67 квартир)
  https://feeds.infoseledka.ru/feed/b37.xml     (Берзарина 37, 84 квартиры)

Авито:
  https://feeds.infoseledka.ru/feed/zorge9-avito.xml  (Зорге 9)
  https://feeds.infoseledka.ru/feed/b37-avito.xml     (Берзарина 37)

Формат ЦИАН: CIAN-XML 2.x. Формат Авито: Avito Автозагрузка (formatVersion=3).
Авто-обновление каждые 4 часа.
Основное превью карточки — обогащённая планировка с маркой ЖК.
Остальные фото — оригинальные с ProfitBase.
```

---

## Конфигурация и тонкая настройка

| Файл | Что внутри |
|---|---|
| `feed_enricher/config.py` → `PROJECTS` | URL фидов ProfitBase, URL шаблонов Figma, layout-координаты, формула рассрочки |
| `feed_enricher/.env` | env-переменные runtime |
| `feed_enricher/assets/fonts/` | TT Fors Trial Medium — бренд-шрифт |
| `feed_enricher/cache/<slug>/` | автокэш (templates, plans, enriched, feeds) |

При изменении шаблона/формулы/координат — рестарт сервиса (`systemctl restart`) и `POST /refresh`.

---

## Поддержка

- Логи: `journalctl -u feed-enricher -f`
- При ошибках парсинга — проверить что ProfitBase отдаёт CIAN-XML v2.x (см. `feed_enricher/parser.py`).
- При промахе координат на шаблоне — поправить в `config.py` → `PROJECTS[<slug>]["layout"]` и сделать `POST /refresh/<slug>`.
