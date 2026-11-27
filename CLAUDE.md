# usasales — onboarding и карта проекта для Claude

> Подготовлено 2026-11-25, часовой пояс пользователя — Asia/Tashkent (UTC+5).
> Сверено с GitHub `M-xD333/usasales`, ветка `main`, снимок `72ecad34f793ba825cebb3601a10b8844a56c776`.
> Исходники локального проекта на момент проверки совпадают с этим снимком. Коммиты состояния появляются во время работы бота, поэтому HEAD может быстро измениться.

Этот файл — техническая точка входа для нового разработчика или coding-agent. Он описывает **реальную текущую реализацию**, а не проектируемую архитектуру. При расхождении доверяй коду и тестам; исторические комментарии и ранние разделы README могут быть менее точными.

## 1. Что делает бот и чего хочет владелец

`usasales` собирает предложения американских магазинов и агрегаторов, определяет товар и итоговую цену, оценивает скидку и публикует подходящие предложения в Telegram-канале.

Предметная область:

- Комплектующие: GPU, CPU, платы, RAM, SSD/HDD, БП, корпуса, охлаждение.
- Периферия: мыши, клавиатуры, гарнитуры/наушники, микрофоны и прочее.
- Телефоны Google Pixel, с точным различением поколений и вариантов.
- Музыкальное оборудование: drum machine, sampler, synthesizer, Roland AIRA.
- Ноутбуки и готовые ПК тоже поддерживаются, но имеют низкий приоритет обработки.

Ключевые требования владельца:

1. Не отбрасывать реальную горячую скидку только потому, что цена необычно низкая.
2. Недостаток подтверждений — повод сохранить предложение для проверки, а не объявить его фейком.
3. Ссылка в Telegram ведёт непосредственно на товар магазина; кнопки «В магазин» и ссылки на обсуждение не нужны.
4. По возможности отправлять реальное фото товара через `sendPhoto`.
5. Показывать вес в кг; неизвестный вес не придумывать.
6. Не публиковать одинаковые предложения каждый день. Повтор возможен при новой более низкой цене.
7. Распределять поиск между производителями, чтобы каталог Corsair не занимал все запросы.
8. После сбоев сохранять очередь, историю цен и факты доставки.
9. Amazon и Newegg рассматривать без партнёрских API; обход CAPTCHA/защит не реализован.

## 2. Стек и модель исполнения

| Слой | Реализация |
|---|---|
| Язык | Python 3.12 |
| HTTP | `requests` |
| RSS/Atom | `feedparser` |
| HTML/JSON-LD/XML | Стандартные `html.parser`, `json`, `xml.etree.ElementTree` |
| Тесты | `pytest`, существующие классы `unittest`, mocks |
| Telegram | Прямые HTTP-запросы к Bot API, без отдельного Telegram SDK |
| Хранение | JSON в `data/`; канонический атомарный checkpoint |
| Запуск | GitHub Actions на `ubuntu-latest`, внутренний непрерывный shell-цикл |
| Между запусками | Git-коммиты `data/`, аварийные Actions artifacts |
| Git-управление | `workflow_git.py` + системный `git`; `gh` для artifacts/self-dispatch |
| Время | Unix timestamps в данных; пользовательские метки времени — Asia/Tashkent |

Нет веб-сервера, UI, SQL-базы, ORM, Redis, отдельного брокера очередей, Docker-обвязки или LLM-классификатора. Очередь — структуры в JSON; решения принимают Python-правила.

`requirements.txt`: `requests>=2.31`, `feedparser>=6.0`.
`requirements-dev.txt`: runtime-зависимости плюс `pytest>=8,<10`.

## 3. Структура репозитория

```text
usasales/
├── CLAUDE.md                       # Этот onboarding; подготовлен локально
├── readme.md                       # Эксплуатационное описание, накопленное по мере разработки
├── AUDIT_ACCEPTANCE.md             # Матрица регрессий аудита и изменения требований
├── requirements.txt               # Runtime-зависимости
├── requirements-dev.txt           # Runtime + pytest
├── pytest.ini                     # testpaths = tests
├── .gitignore
│
├── bot.py                         # Главный сценарий, решения, outbox и Telegram
├── sources.py                     # Реестр и сбор RSS/Reddit/прямых каталогов
├── direct_stores.py               # HTML/JSON-LD/нативные данные магазинов
├── market_search.py               # Поиск точных товарных ссылок для сравнения
├── market_prices.py               # Идентичность, котировки, планировщик, сравнение
├── store_links.py                 # Разрешённые URL и раскрытие ссылок агрегаторов
├── merchant_http.py               # Ограниченные безопасные HTTPS-редиректы
├── product_images.py              # Изображения; запрос деталей карточки
├── product_weight.py              # Извлечение веса и перевод в кг
├── offer_queue.py                 # Слияние свежих кандидатов, ревизии, события цены
├── publication_history.py         # Постоянная защита от повторных публикаций
├── checkpoints.py                 # Атомарные поколения state + history
├── state_merge.py                 # Семантическое слияние локальных/удалённых данных
├── workflow_git.py                # Git-сохранение, sync, восстановление artifacts
│
├── .github/workflows/
│   ├── deals.yml                  # Основной continuous workflow и watchdog cron
│   └── photo-check.yml            # Ручная реальная проверка отправки фото
│
├── data/
│   ├── .gitignore
│   ├── checkpoint.json            # Источник истины: state + history в одном поколении
│   ├── checkpoint.backup.json     # Предыдущее корректное поколение
│   ├── state.json                 # Совместимый экспорт state
│   ├── history.json               # Совместимый экспорт history
│   └── seen.json                  # Старый формат; нужен для миграции
│
├── tests/
│   ├── test_bot.py
│   ├── test_direct_stores.py
│   ├── test_market_prices.py
│   ├── test_market_search.py
│   ├── test_store_links.py
│   ├── test_product_images.py
│   ├── test_product_weight.py
│   ├── test_music_search.py
│   ├── test_publication_history.py
│   ├── test_regressions.py
│   ├── test_workflow_git.py
│   ├── test_workflow_control.py
│   └── t.txt                      # Вспомогательный файл, не модуль приложения
│
└── test_bot.py                     # Старый тестовый файл в корне; не основной suite
```

В локальном рабочем каталоге могут быть `__pycache__/`, PNG-доказательства прошлых проверок и другие артефакты. Это не компоненты production-бота. Каталог `data/` в локальной копии может отсутствовать, хотя в GitHub он есть.

## 4. Карта модулей: за что отвечает каждый

| Модуль | Главные точки входа | Ответственность |
|---|---|---|
| `bot.py` | `main`, `make_deal`, `assess`, `process`, `build_message`, `tg_send`, `smoke` | Собрать pipeline, принять решение, сохранить outbox, отправить сообщение |
| `sources.py` | `Source`, `FetchResult`, `active_sources`, `fetch_all`, `fetch_one`, `normalize_entry`, `fetch_market_product` | Выбрать источники, получить и нормализовать записи, прочитать точную карточку |
| `direct_stores.py` | `parse_catalog`, `jsonld_entries`, `corsair_catalog`, `retail_cards`, `offer_meta`, `entry` | Извлечь цены, варианты, наличие, продавца, MPN/GTIN, изображения и вес |
| `market_search.py` | `terms`, `discover`, `read_page`, `links_from_html`, `product_url` | Найти подходящие URL по точному идентификатору; цены выдачи не считать доказательством |
| `market_prices.py` | `product_identity`, `identity_keys`, `purchase_conditions`, `quote`, `MarketPrices` | Проверить совместимость товара/условий, вести кэш, планировать поиск, сравнить цены |
| `store_links.py` | `merchant_url`, `unwrap`, `extract_links`, `follow`, `resolve_store_url` | Получить разрешённую прямую ссылку конкретного товара |
| `merchant_http.py` | `get` | Максимум три HTTPS-перехода только между разрешёнными merchant-доменами |
| `product_images.py` | `from_feed`, `extract_image`, `image_url`, `from_product_details`, `from_product_page` | Фото из ленты/JSON-LD/OG; детали карточки одним запросом |
| `product_weight.py` | `kilograms`, `from_product`, `from_markup`, `display` | O4: раздельный package/net с происхождением и конвертацией единиц |
| `offer_queue.py` | `merge_latest`, `revision`, `price_event`, `history_eligible`, `pkey` | Убрать устаревший pending, отслеживать изменения, защищать baseline |
| `publication_history.py` | `aliases`, `seen`, `remember`, `merge` | Узнать уже отправленный товар и разрешить только новое снижение цены |
| `checkpoints.py` | `Store.load`, `Store.save`, `validate_*`, `atomic_json` | Атомарное состояние, валидация, legacy migration, backup recovery |
| `state_merge.py` | `merge_state`, `merge_history` | Объединить отправки, минимумы публикаций, pending, котировки и историю |
| `workflow_git.py` | `RunnerGit.push`, `sync`, `restore_checkpoint`, `validate_code` | Работа с disposable checkout Actions и сохранение данных поверх свежего кода |

## 5. Путь одного предложения

```text
GitHub schedule / manual dispatch
  → checkout → Python/dependencies → pytest
  → restore-checkpoint → preflight push
  → shell iteration
      → bot.main()
      → checkpoints.Store.load()
      → sources.active_sources() → fetch_all()
      → RSS/Reddit normalization или direct_stores.parse_catalog()
      → bot.make_deal()
      → bot.process()
          → merge_latest(incoming, pending)
          → сохранить ВЕСЬ pending до внешних действий
          → MarketPrices.add() + prepare()
          → уточнить неоднозначную цену, если возможно
          → assess() + история цен
          → проверка купона/экстремальной цены или market.compare()
          → publication_history.seen()
          → раскрыть прямой merchant URL
          → повторить дедупликацию по реальному товару
          → получить фото/вес при необходимости
          → build_message()
          → сохранить sending до Telegram-запроса
          → tg_send(): sendPhoto или sendMessage
          → подтвердить delivered, posted, publication_history
          → сохранить checkpoint
      → workflow_git.py push
      → sleep → workflow_git.py sync → следующая итерация
  → safety save / artifact при сбое / chain next run
```

Не путай этапы: **сбор каталога**, **внешний поиск аналогов** и **публикация** — три разных процесса. Большое количество строк Corsair в отчёте не доказывает, что остальные сайты не запрашивались.

## 6. Источники и их включение

Реестр — `sources.SOURCES`. Типы: `rss`, `reddit`, `store`.
Уровни доверия, реально используемые правилами: `curated`, `editorial`, `unvetted`, `community`, `merchant`.

### RSS/агрегаторы

- Включены по умолчанию: `sd-frontpage`, `sd-popular`, `sd-hot`.
- Поисковые Slickdeals ID создаются как `sd-q-` + запрос в нижнем регистре с дефисами.
- Запросы: `ssd`, `nvme`, `rtx`, `radeon`, `ryzen`, `ddr5`, `graphics card`, `motherboard`, `monitor`, `google pixel`, `gaming mouse`, `mechanical keyboard`, `headset`, `microphone`, `drum machine`, `sampler`, `synthesizer`, `Roland AIRA`.
- Дополнительные, по умолчанию отключённые: `techbargains`, `bensbargains`, `9to5toys-steals`, `pcworld`, `pcworld-deals`, `tomshardware`, `dealnews-computers`.
- `tomshardware` имеет `publish=False`: вспомогательные наблюдения, не публикации.
- `reddit-buildapcsales` требует обе Reddit credentials; без них исключается из активных источников.

### Прямые магазины

| ID | Что читает |
|---|---|
| `amazon-components` | Каталог комплектующих Amazon |
| `amazon-peripherals` | Периферия Amazon |
| `amazon-pixel` | Pixel на Amazon |
| `newegg-direct` | Newegg today's deals |
| `corsair-memory` | Память Corsair |
| `corsair-psu` | Блоки питания Corsair |
| `corsair-cooling` | Охлаждение Corsair |
| `corsair-keyboards` | Клавиатуры Corsair |
| `corsair-mice` | Мыши Corsair |
| `corsair-headsets` | Гарнитуры Corsair |
| `msi-direct` | Комплектующие MSI |
| `samsung-storage` | Накопители Samsung |
| `crucial-direct` | Crucial promotions |
| `asus-gpu` | Видеокарты ASUS |
| `google-pixel-direct` | Google Store offers |

`ENABLE_SOURCES` **добавляет** отключённые по умолчанию ID; это не allowlist, выключающая остальные. `DISABLE_SOURCES` имеет приоритет и отключает указанные ID.

В реестре 44 источника с учётом генерируемых поисковых лент. Количество активных зависит от настроек и Reddit credentials; число «43» из старых логов не константа.

Сбор `fetch_all()` использует до трёх дочерних процессов, общий deadline по умолчанию 120 с и ограничение отдельного источника до 40 с. Зависший worker завершается принудительно. Сам `--smoke` перебирает источники последовательно через `fetch_one()`, поэтому его время отличается.

Прямые каталоги опрашиваются раз в 900 с, после ошибок — раз в 1800 с. Локальная пауза показана отдельно от настоящего HTTP 304 RSS/Atom.

## 7. Модели данных

Это словари, не ORM-модели и не Pydantic-схемы. Точные поля смотри в `make_deal()`.

### Нормализованная запись источника

```python
{
    'item_id': 'идентификатор записи или store:hash',
    'title': 'Название и, для RSS, часто цена',
    'url': 'страница сделки или товар магазина',
    'discuss_url': 'обсуждение, если источник его предоставляет',
    'summary': 'текст',
    'published_ts': 1790000000.0,  # либо None
    'expired': False,
    'community_score': None,
    'store_candidates': ['прямые или вложенные ссылки'],
    'image_url': None,
    # Для type=store:
    'store_price': 80.0,
    'store_original': 150.0,
    'store_currency': 'USD',
    'product_meta': {...},
}
```

### Сделка после `make_deal()`

- Идентификаторы: `id`, `source_id`, `source`, `ident`, `asin`.
- Классификация: `title`, `name`, `tag`, `cat`, `cond`.
- Политика: `trust`, `publish`, `flags`, `community_score`, `expired`.
- Цена: `price`, `original`, `original_kind`, `price_kind`, `code`, `issue`, `price_origin`, `price_components`.
- Ссылки: `url`, `discuss`, `store_candidates`, `store`.
- Время: `published_ts`, `fetched_ts`, `undated`.
- Данные товара: `product_meta`, `image_url`, `weight_pkg_kg`, `weight_net_kg`, provenance; legacy `weight_kg` — только net-экспорт.
- Во время обработки: `_queue`, `_rebound_at`.

`product_meta`: `brand`, `mpn`, `gtins`, `currency`, `market`, `condition`, `in_stock`, `shipping`, `seller`, `weight_pkg_kg`, `weight_net_kg` с provenance (legacy `weight_kg` только net), при необходимости `coupon`.

Поля имеют разные смыслы:

- `price_origin='feed'` не равно проверенной магазином цене.
- `original_kind='MSRP'` — не собственная история и не цена независимого магазина.
- `published_ts` и время обхода — разные вещи. Для разрешённых undated-каталогов код ставит текущее время, сохраняя `undated=True`.
- `ident` из нормализованного названия — слабая идентичность; GTIN/MPN и прямая карточка сильнее.
- `shipping=None` означает неизвестную доставку, а не бесплатную.

### Канонический checkpoint

```json
{
  "generation_id": 123,
  "saved_at": 1790000000.0,
  "state": {
    "version": 2,
    "pending": {},
    "posted": {},
    "processed": {},
    "sources": {},
    "review": [],
    "assessment_cache": {},
    "price_events": {},
    "publication_history": {},
    "market_prices": {},
    "last_delivery": {"at": 1790000000.0, "kind": "photo", "message_id": 123}
  },
  "history": {}
}
```

`state.version` — legacy-поле; не подменяет `generation_id`, `RULES_VERSION` и `PARSER_VERSION`. Не все необязательные поля обязаны присутствовать в старых данных.

| Поле | Что означает |
|---|---|
| `pending` | Сделки, которые ещё нужно обработать/отправить; служебное состояние находится в `deal['_queue']` |
| `posted` | Старые ключи `ident|cond|price` → время подтверждённой отправки; больше не очищаются по 24-часовому TTL |
| `processed` | Совместимый журнал подтверждённых отправок по `id@price`, ограниченный 5000; не вечный фильтр отказов |
| `assessment_cache` | Ревизия, версия правил/парсера, причина и статус оценки; не должен блокировать исправленные входные данные |
| `price_events` | Наблюдавшиеся рост/падение; больше не разрешает повтор прежней опубликованной цены |
| `publication_history` | Постоянные aliases товара → минимальная опубликованная цена в центах, время, идентификаторы |
| `sources` | ETag/Last-Modified, `last_attempt`, `last_ok`, число ошибок |
| `market_prices` | `quotes`, `checked`, `products`, `searched`, `brand_searched` |
| `last_delivery` | Последняя подтверждённая доставка, тип photo/text и message_id |
| `review` | Legacy/совместимый список; не очередь ручного согласования владельцем |

История: `history[hist_key] = [[timestamp, price, observation_id, source_id], ...]`.
Старые трёхэлементные записи мигрируют с `source_id='legacy'`.
Для прямых магазинов ключ истории — точная карточка + состояние; для остальных — `ident + cond`.
История удерживается `HIST_DAYS`, по умолчанию 90 дней; повтор одинакового наблюдения внутри 12 ч не накапливается бесконтрольно.

## 8. Очередь и доставка

`offer_queue.merge_latest()` выбирает свежее предложение одной карточки/записи. Входные данные побеждают pending при равных timestamps; устаревшие $500 не должны перезаписывать свежие $400.

Служебный объект:

```python
'_queue': {
    'status': 'queued',
    'attempts': 0,
    'retry_at': 0,
    'revision': 'sha256 важных полей/правил/парсера',
    'reason': 'если есть'
}
```

Статусы: `queued → processing → sending → delivered`; также `failed_permanently`, `priority_verification`, `delivery_unknown`.

Важные инварианты:

- Весь хвост очереди сохраняется до первого сетевого действия публикации.
- `sending` сохраняется до Telegram-запроса, `posted`/publication floor — после подтверждения успеха.
- Временные ошибки сохраняют кандидата, attempts и retry_at; HTTP 429 учитывает полный retry_after.
- После сбоя во время `sending` результат неизвестен: `delivery_unknown`, отложенная повторная попытка.
- Нельзя обеспечить exactly-once: Telegram Bot API не даёт клиентского ключа идемпотентности для этих отправок. При потере ответа возможен дубль.
- `other`, `review`, unknown price перехватываются в `process()` и могут ждать уточнения/устаревания; один вызов `assess()` не описывает всю политику очереди.
- `trim_state()` должен менять связанные словари pending/assessment_cache на месте. Замена словаря ломает ссылки обработчика и уже приводила к потере результатов.

## 9. Решение о скидке

### Порог категории: процент и экономия в USD

Оба порога обычно должны выполняться. `RULES_JSON` позволяет переопределить категории.

| Категория | Мин. % | Мин. $ |
|---|---:|---:|
| gpu | 15 | 40 |
| cpu | 20 | 40 |
| mobo | 20 | 30 |
| ram | 25 | 20 |
| ssd | 30 | 25 |
| hdd | 25 | 25 |
| ext_storage | 25 | 20 |
| nas | 15 | 40 |
| monitor | 25 | 50 |
| psu / case | 25 | 20 |
| cooler | 25 | 15 |
| prebuilt / laptop | 15 | 100 |
| minipc | 15 | 50 |
| keyboard / mouse / headset | 30 | 15 |
| microphone | 25 | 20 |
| pixel | 15 | 50 |
| music_gear | 20 | 30 |
| controller / webcam | 30 | 15 |
| networking | 25 | 20 |
| dock | 25 | 15 |
| capture | 25 | 25 |
| printer | 25 | 30 |
| combo | 0 | 0, но есть дополнительные условия доверия/голосов |
| неизвестная категория | 30 | 20; сама категория ещё должна пройти классификацию |

### Реальная ветвящаяся логика

`assess()` проверяет пригодность источника, состояние, возраст, отсутствие товара, условия Pixel. Источники с `publish=False` не публикуются.

Подтверждение может опираться на:

1. Заявленную прежнюю цену, с проверкой порогов и возможного завышения.
2. Собственную историю: минимум три наблюдения, сравнение с медианой.
3. Уровень доверия и реальные голоса сообщества.
4. Подтверждённое сравнение с независимыми магазинами.

**Не утверждай, что любое сообщение обязательно прошло проверку двух магазинов.** Это fallback, когда обычная оценка не подтвердила скидку. У `curated` есть отдельная более разрешительная ветка: без заявленной прежней цены она может разрешить публикацию. `community` зависит от настоящих голосов, а не искусственного score.

Старая цена выше 3× медианы при достаточной истории помечается подозрительной. Скидка **выше 85% сама по себе не фейк**: `priority_verification`, принудительная проверка карточки. Комментарий рядом с `MAX_SANE_DISCOUNT` исторически неточен — смотри исполняемую логику.

`info.status`: `insufficient_data`, `contradictory_data`, `discount_confirmed`. Это состояния оценки, не все они равны статусам outbox.

### Купоны и условия

- `$100 with $20 coupon` → $80.
- `$100 with $20 off coupon` → $80.
- `$80 after coupon` → $80, без повторного вычитания.
- Неоднозначные суммы не выбираются через `min()`; нужен запрос точной карточки.
- Идентичность товара определяется отдельно от `purchase_conditions()`.
- Купонная цена требует подтверждённых `coupon.verified`, `coupon.public`, итоговой суммы.
- Условия тарифа, trade-in, membership, cashback/rebate нельзя выдавать за безусловную цену.
- `no trade-in required` не создаёт положительный флаг trade-in.
- Бесплатный чехол Pixel или софт к синтезатору не должны превращать основной товар в `other`.

## 10. Поиск и сравнение магазинов

Три разных области:

- `market_search.discover()` ищет URL по MPN/GTIN или полному варианту Pixel.
- `MarketPrices.verify()` получает и проверяет карточку.
- `MarketPrices.compare()` строит подтверждённое сравнение.

Для оборудования используются структурированный GTIN либо бренд + MPN, включая короткие/цифровые артикулы. Произвольные числа заголовка не становятся MPN.
Для Pixel важны поколение, Pro/XL/Fold, память, цвет, unlocked и совместимость сильных идентификаторов.

Котировка требует новой вещи, USD, US, явного наличия, магазинного происхождения цены и допустимого продавца. На маркетплейсах недостаточно домена: требуется подтверждённый собственный продавец площадки.

Рыночная база — минимальная подтверждённая цена **двух других независимых магазинов**. Сайт текущего предложения не считается конкурентом. К текущей цене прибавляется известная доставка; неизвестная доставка блокирует именно этот способ подтверждения. Налоги по адресу покупателя не вычисляются.

Поисковые цены, snippets и MSRP не становятся market evidence.

Ограничения по умолчанию на экземпляр `MarketPrices`/проход:

- До 4 поисковых запросов.
- До 12 проверок карточек.
- Общий бюджет начала операций — 60 с; отдельный discovery — до 20 с.
- Кэш quotes — 900 с.
- Неудачные проверки — cooldown до 600 с; новая отличающаяся цена вызывает force refresh.
- Повторный поиск одного товара — не чаще 120 с.
- `products` — до 2000 ссылок и 30 дней; quotes — до 1000.
- Планировщик сначала распределяет слоты между брендами/группами и учитывает `brand_searched`; внутри бренда — никогда не проверенные, давно проверенные, экстремальные скидки при равном остальном.
- Из бюджета списывается `result['requests']`, а не максимально возможный резерв.

Поиск реализован на Amazon, Newegg, B&H, Best Buy, официальном Corsair; для других поддержанных производителей — Bing RSS с ограничением домена. Наличие домена в allowlist не означает наличие отдельного crawler/поискового адаптера.

## 11. Дедупликация: актуальное правило с 2026-11-09

**Не возвращать TTL 24 ч как разрешение ежедневно публиковать то же самое.**

`publication_history` хранит минимальную подтверждённо опубликованную цену в центах. Алиасы строятся по прямому URL, сильным идентификаторам с магазином/состоянием, Amazon ASIN и Newegg Item. Ключи хешируются SHA-256; tracking query-параметры нормализуются.

- Прежняя или более высокая цена — не новая публикация.
- Рост и возврат к старой цене не разрешают повтор.
- Более низкая цена, минимум на один цент, может публиковаться после остальных проверок.
- Новый RSS-ID, заголовок или другая форма ссылки не должны обходить защиту.
- Разные подтверждённые модификации и состояния товара различаются.
- Проверка повторяется после раскрытия прямого URL, чтобы поймать дубли из разных агрегаторов в одном проходе.
- `remember()` вызывается после подтверждённой отправки; не создавать floor при временной ошибке Telegram.
- При Git-слиянии сохраняется минимальный floor, а не только последний записанный словарь.
- Старые `posted` используются при миграции. Не восстанавливать историю, которая уже отсутствует, выдуманными данными.

## 12. Telegram, изображения и вес

`tg_send()` получает готовый HTML, `store_url`, `photo_url`.

Если есть допустимый photo URL: `POST https://api.telegram.org/bot<TOKEN>/sendPhoto`.
Иначе: `sendMessage` с явно выбранным предпросмотром прямой ссылки.

Подпись/сообщение включает товар, цену/условия скидки, вес, прямую ссылку, состояние, магазин/источник и при наличии сравнение. Ссылка на обсуждение не выводится; inline-кнопок нет.

Особенности:

- HTML экранируется; длинный текст собирается заново, а не обрезается внутри тегов.
- Подпись фото ограничена лимитом Telegram; при сокращении сохраняется кликабельная ссылка.
- Явная ошибка Telegram 400, относящаяся к изображению, позволяет fallback на текст.
- 401/403/chat-not-found не скрываются fallback; read timeout считается неопределённой доставкой.
- HTTP 429 и временные server errors обрабатываются retry/очередью.
- Фото приходит из media/enclosure/HTML ленты, JSON-LD, OG/Twitter metadata карточки.
- Для Corsair CDN `f_auto` преобразуется в JPEG rendition.
- `from_product_details()` читает фото и вес одной карточки одним запросом.

Вес (политика владельца O4, 2026-11-27):

- Основной вес для расчёта доставки — **package/shipping**: Shipping Weight, Package Weight, Weight (with packaging), масса после `;` в Amazon Package Dimensions.
- Item/Product/Net Weight и schema Product.weight — отдельно net; поле без явного вида («Weight») и неоднозначные варианты не угадываются.
- `weight_pkg_kg`/`weight_net_kg` имеют отдельные `weight_pkg_provenance`/`weight_net_provenance`: source, field, ts. kg/g/lb/oz обязательны и переводятся в кг.
- Приоритет подписи: `⚖️ Вес: 1 кг (с упаковкой)` → `⚖️ Вес: 0.5 кг (без упаковки)` → `⚖️ Вес: не указан`. Отсутствие веса не ограничивает публикацию.
- У комплектов сумма только полного набора компонентов одного вида веса: `≈ … кг (сумма комплектующих)`; это не измеренный вес посылки. Неполный набор — неизвестно.
- O5: `WEIGHT_ESTIMATES=0` по умолчанию; при явном включении ограниченные оценки категорий подписываются `≈ … кг (оценка)`, не являются package/net и не увеличивают измеренное покрытие.
- В отправляющем потоке нет ожидания веса: `weight_lookup.Lookup` заранее читает карточки в максимум двух дочерних процессах, до 30 запросов за проход, жёсткий deadline 2 с/запрос. Готовый результат/кеш читается без ожидания, иначе неизвестно.
- `weight_knowledge.json.gz` — отдельный необязательный кеш до 500 000 байт; ASIN/MPN/UPC/нормализованная модель + строгий guard ёмкости/цвета/комплекта/состояния; 180 дней. Его сбой не меняет publication_history и не блокирует отправку.
- Поля очереди валидируются в checkpoints, переносятся при совместимой идентичности и проверены restart/merge тестами. Legacy scalar без происхождения не превращается в package.
- Журнал содержит optional `weight_status`; старые строки без него — unmeasured. `tools/weight_coverage.py` показывает покрытие по магазину/категории и учитывает обрезку журнала.
- [Измерение, ограничения, API и позднее редактирование — только дизайн](docs/package-weight-2026-11-27.md).

## 13. Атомарное сохранение и Git

`Store.save()`:

1. Проверяет структуру state/history.
2. Сохраняет предыдущее корректное поколение как backup.
3. Записывает новый `checkpoint.json` через временный файл, `fsync`, `os.replace`, `fsync` каталога.
4. Обновляет совместимые `state.json` и `history.json`.

Падение на шаге 4 не делает экспорты источником истины. `Store.load()` использует канонический bundle, при повреждении — валидный backup. `null` не означает «начать с пустой базой».

`workflow_git.py` — **только disposable runner GitHub Actions**. CLI проверяет `GITHUB_ACTIONS=true` и совпадение репозитория с `GITHUB_WORKSPACE`.

- `push`: snapshot локального `data/`, fetch актуальной ветки, semantic merge, временный Git index, новый state-коммит поверх текущего remote code tree; rebase не используется.
- `sync`: сохранить data за пределами checkout, завершить незаконченный rebase/merge, fetch, compile Python удалённого дерева, semantic merge, reset disposable checkout, restore данных.
- `restore-checkpoint`: найти recovery artifact данной ветки, сравнить время с коммитом состояния, скачать, проверить и объединить данные до публикаций.
- Git-команды имеют timeout 120 с; push — до шести попыток с паузами 2/4/8/16/32 с.
- `state_merge` объединяет posted/processed по максимуму timestamp, pending по свежести, историю наблюдений без потери строк и publication floors по минимуму цены.

**Не запускать этот helper на пользовательском рабочем дереве, не подделывать guard-переменные.** Его reset предназначен для одноразового runner, а не для исправления локального checkout.

## 14. GitHub Actions и жизненный цикл

Основной файл `.github/workflows/deals.yml`:

- Cron `*/5 * * * *` — watchdog; cron записан в UTC, интервал всё равно каждые 5 минут.
- `workflow_dispatch`: `mode=run|smoke`, `dry_run` default true, `loop` default true, `recovery_attempt=0`.
- `concurrency.group=deals-bot`, `cancel-in-progress=false`: один издатель; ожидающий run не означает зависание текущего.
- Python 3.12, checkout v5, setup-python v6, установка dev dependencies, `python -m pytest -q`.
- Restore до боевого старта; preflight сохранения до цикла.
- Цикл по умолчанию 330 минут, пауза 120 с, job timeout 350 минут.
- Перед проходом compileall. Проход: `timeout --signal=TERM --kill-after=30s 10m python bot.py`.
- Timeout/KILL или три последовательных сбоя завершают run; успешный проход сбрасывает счётчик.
- После прохода push, после sleep — sync кода.
- Safety net сохраняет состояние; при сбое artifact `deals-state-<run_id>` удерживается 7 дней.
- Self-dispatch следующего run через `gh workflow run`; `GITHUB_TOKEN` имеет contents/actions write.
- До двух recovery рестартов подряд; cron остаётся watchdog.
- Оболочка YAML не перечитывается в уже работающем job. Python-модули обновляются через sync, изменения workflow/новых env требуют нового run.

`photo-check.yml` — отдельный ручной workflow с read-only contents и Telegram credentials. Он запускает тесты и **отправляет одно помеченное тестовое фото в настоящий канал**, не меняя историю скидок. Успех подтверждается `result.photo` и строкой `PHOTO_DELIVERY_CONFIRMED message_id=...`. Запускать его только при намерении отправить реальное тестовое сообщение.

`DRY_RUN` гарантирует отсутствие Telegram/сохранений в Python-боте. Не переносить эту гарантию автоматически на каждый служебный шаг workflow: preflight `workflow_git.py push` задан отдельно, без условия dry-run.

## 15. Конфигурация и секреты

### Secrets, значения в документ не помещать

| Имя | Назначение |
|---|---|
| `TG_BOT_TOKEN` | Токен Telegram-бота |
| `TG_CHAT_ID` | Канал/чат назначения; бот должен иметь право публиковать |
| `CHANNEL_LINK` | Необязательная ссылка на канал в сообщении |
| `REDDIT_CLIENT_ID` | Необязательный Reddit OAuth client ID |
| `REDDIT_CLIENT_SECRET` | Необязательный Reddit secret; нужен вместе с ID |
| `GITHUB_TOKEN` | Автоматический Actions token, передаётся как GH_TOKEN для служебных действий |

### Настройки

| Env | Default | Эффект |
|---|---|---|
| `ENABLE_SOURCES` | пусто | Дополнительно включить ID через запятую |
| `DISABLE_SOURCES` | пусто | Отключить ID через запятую |
| `MAX_POSTS_PER_RUN` | 8 | Максимум публикаций за Python-проход, не за весь 330-минутный run |
| `MAX_POSTS` | 8 | Legacy fallback, если MAX_POSTS_PER_RUN не задан |
| `MAX_DEAL_AGE_HOURS` | 12 | Максимальный возраст предложения |
| `MIN_SCORE` | 8 | Порог настоящих голосов community |
| `HIST_DAYS` | 90 | Горизонт собственной истории |
| `RULES_JSON` | встроенные RULES | Например `{"ssd":[30,25]}` |
| `DUP_WINDOW_HOURS` | 24, legacy | Читается конфигом, но больше не разрешает повтор по TTL |
| `ENABLE_REDDIT` | автоматически по credentials | `0/false/no` отключает Reddit |
| `DRY_RUN` | false | `1/true/yes`: бот не отправляет/не сохраняет |
| `LOOP_MINUTES` | 330 | Оболочка Actions |
| `LOOP_INTERVAL_SECONDS` | 120 | Оболочка Actions |
| `TZ` | Asia/Tashkent в job | Пользовательские метки логов |

Не любая GitHub Variable автоматически становится env. В текущем YAML напрямую прокинуты ENABLE/DISABLE_SOURCES, MAX_POSTS_PER_RUN, MAX_DEAL_AGE_HOURS, MIN_SCORE, LOOP_MINUTES, LOOP_INTERVAL_SECONDS. `RULES_JSON`, `HIST_DAYS`, `ENABLE_REDDIT` и legacy-параметры доступны Python-коду, но для управления ими через Variables необходимо явно добавить нужный mapping в workflow.

Variables/Secrets уже запущенный job заново не читает.

## 16. Локальный запуск

Из обычного полноценного клона репозитория:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Проверить один компонент:

```bash
python -m pytest -q tests/test_publication_history.py
python -m pytest -q tests/test_product_images.py tests/test_product_weight.py
python -m pytest -q tests/test_market_prices.py tests/test_market_search.py
python -m pytest -q tests/test_workflow_git.py tests/test_workflow_control.py
```

Dry run без токена и Telegram:

```bash
DRY_RUN=1 python bot.py
```

Это всё ещё реальные запросы к источникам. Ограничить источники можно через DISABLE_SOURCES; ENABLE_SOURCES сам по себе остальные не отключает.

Smoke всех источников:

```bash
python bot.py --smoke
```

Smoke не публикует и не сохраняет состояние, но делает реальные сетевые запросы, включая отключённые источники; Reddit без credentials пропускается.

Боевой локальный `python bot.py` использует TG_BOT_TOKEN/TG_CHAT_ID и может сразу публиковать. Не применять его для безопасного smoke. Runtime-state читается из `data/` рядом с `bot.py`; не тестировать изменения на production checkpoint без копии.

`pytest.ini` обязателен: в репозитории два `test_bot.py`, и сбор pytest из всего дерева раньше падал с import-file mismatch. Основная suite — `tests/`.

## 17. Тестовая карта

Последний полный прогон в рамках исправления дедупликации: **219 тестов + 33 subtests**, 2026-11-09. Для новой задачи перепроверь результат, а не объявляй этот исторический прогон текущим.

| Тестовый файл | Что защищает |
|---|---|
| `test_bot.py` | Парсинг, категории, оценка, нормализация, Telegram errors |
| `test_direct_stores.py` | Структурированные цены, наличие, варианты, каталоги |
| `test_market_prices.py` | Независимые магазины, shipping, matching, seller/stock |
| `test_market_search.py` | Точный discovery, бюджеты, реальные ссылки вместо snippet prices |
| `test_store_links.py` | Allowlist, раскрытие, редиректы, отказ от неподходящих URL |
| `test_product_images.py` | Извлечение фото, sendPhoto, fallback, captions, retries |
| `test_product_weight.py` | Единицы, JSON-LD/таблицы, package/net, обязательные единицы, происхождение, подпись |
| `test_music_search.py` | Музыкальные поисковые запросы и категории |
| `test_publication_history.py` | Дни 1/2/7/30, новые цены, aliases, restart и Git merge |
| `test_regressions.py` | Аудит очереди/кэша/checkpoint, extreme/coupon, fairness и deadline |
| `test_workflow_git.py` | Настоящие временные Git remote/editor/runner; races, recovery, sync |
| `test_workflow_control.py` | Shell chain из реального YAML с локальным gh stub |

Это преимущественно offline-тесты с фикстурами/mocks. Они не доказывают текущую доступность магазинов или доставки Telegram. Для реального фото есть отдельный ручной workflow.

## 18. Диагностика по симптомам

| Симптом | Где смотреть / как трактовать |
|---|---|
| `0/2` магазинов | Недостаточно подтверждённых независимых цен, не crash процесса; market_search/market_prices |
| HTTP 403/503/CAPTCHA | Магазин не отдал данные. Парсер существует, но ответ недоступен; не считать это нулевой ценой |
| Повсюду Corsair | Проверить `brand_searched`, фактические 4 запроса, source backoff и примеры отчёта; не судить по 15 строкам |
| Один pending run | Нормальное следствие concurrency; сначала проверить прогресс текущего run |
| Повторы одного товара | publication_history/posted, aliases после прямой ссылки, цена в центах, сохранение checkpoint |
| Фото не пришло | `sendPhoto`/fallback лог, result.photo, last_delivery, допустимость image URL; preview текста не равно фото |
| Вес не указан | Нет явной единицы/характеристики или карточка недоступна; не подставлять средний вес категории |
| Telegram read timeout | Доставка неизвестна; проверить delivery_unknown, не публиковать немедленно вручную |
| JSON `null`/ошибка | Store.load/backup; остановить сброс базы, не заменять `{}` |
| Git push rejected | Проверить state merge и причины 6 попыток; не начинать rebase бесконечного цикла |
| Python SyntaxError | Не принимать сломанный remote tree; validate_code/compileall |
| Неполный artifact | restore-checkpoint должен остановиться до отправок, а не начать без истории |
| Новый job вообще не стартует | Проверить сообщение самого GitHub, квоты/billing/очередь; это не баг Python-парсера |

Пользовательские логи показывают Ташкент; служебные временные метки GitHub могут оставаться UTC.

## 19. Подтверждённые ограничения и первоочередные проверки

### 19.1. Размер checkpoint уже превысил лимит восстановления artifact

По Git tree снимка на 2026-11-25:

| Файл | Размер, bytes |
|---|---:|
| data/checkpoint.json | 27 647 198 |
| data/checkpoint.backup.json | 27 647 198 |
| data/state.json | 26 136 880 |
| data/history.json | 1 510 238 |

В `RunnerGit.restore_checkpoint()` есть отказ при `path.stat().st_size > 20_000_000`. Таким образом текущие checkpoint/state уже больше разрешённого там размера. **Сопоставление размера с условием подтверждено; аварийное восстановление на этих файлах в рамках подготовки документа не запускалось.** Это приоритетная инженерная задача: установить источник роста, измерить расход, согласовать безопасную компактизацию/формат и размерные лимиты. Не лечить удалением истории отправок или обнулением publication_history.

### 19.2. Сеть и реальные данные

- Наличие адаптера не гарантирует, что Amazon/Newegg/MSI/Google Store отдают HTML с GitHub-hosted IP.
- Строгое сопоставление намеренно оставляет часть неизвестных предложений без рыночного подтверждения.
- Weight/stock/seller/coupon могут отсутствовать в статическом HTML.
- GET timeouts и бюджет начала операций не являются математической гарантией общего wall time каждого произвольного HTTP-тела; сбор источников отдельно защищён worker deadline и общий проход — Actions timeout.

### 19.3. Эксплуатационные оговорки

- Сохранение «sending» локально не равно немедленному сохранению того же поколения в удалённый Git. Remote push выполняется после прохода, плюс safety/artifact. Не обещать распределённую транзакцию Telegram + Git.
- Нет серверной идемпотентности Telegram; неизвестная доставка остаётся отдельным риском дубля.
- `assessment_cache` сохраняет результат, но текущий pipeline не превращает его в универсальный skip-cache; нельзя считать наличие поля доказательством отсутствия повторной оценки.
- Исторические заметки об оплате Actions/403 не доказывают нынешнее состояние аккаунта или сайта; проверять свежие runs.

## 20. Как вносить изменения без повторения прежних ошибок

1. Сверить актуальные main, локальные изменения и настройки; код может обновляться одновременно с state-коммитами.
2. Выбрать минимальный модуль по карте ответственности, воспроизвести дефект на фиксированных входах.
3. Добавить регрессию на пользовательское поведение, особенно если меняются цена, очередь, доставка или дедупликация.
4. Запустить относящиеся тесты и затем полный suite для существенного изменения pipeline.
5. Не изменять `data/` и не отправлять тестовые Telegram-сообщения случайным запуском.
6. Для изменения правил/классификатора/парсинга оценить обновление RULES_VERSION/PARSER_VERSION и совместимость уже сохранённых pending.
7. Новые state-поля включить в validate/load/merge/migration и проверить restart.
8. Не очищать постоянные publication floors ради уменьшения файла.
9. При публикации в Git сохранять параллельные state-коммиты; не использовать force push для обхода race.
10. Проверять, что загруженные исходники побайтно совпадают с локальными: раньше tool-output truncation попала внутрь Python-строки и сломала sources.py. Не строить push из обрезанного вывода инструментов.
11. Для YAML/env/dependencies учесть необходимость нового workflow run; только Python hot sync недостаточно для новой оболочки.
12. Различать «локальные тесты прошли», «код запушен», «runner подхватил», «Telegram подтвердил доставку» — это четыре разных доказательства.

## 21. Рекомендуемый порядок чтения для Claude

1. Этот файл и `AUDIT_ACCEPTANCE.md` — требования и история регрессий.
2. `bot.get_cfg`, `make_deal`, `assess`, `process`, `main` — вся предметная цепочка.
3. `checkpoints.py`, `publication_history.py`, `state_merge.py` — состояние и недопущение потерь/дублей.
4. `sources.py`, `direct_stores.py` — откуда фактически берутся данные.
5. `market_prices.py`, `market_search.py`, `store_links.py` — доказательства цены и прямые ссылки.
6. `product_images.py`, `product_weight.py`, `tg_send` — конечное сообщение.
7. `workflow_git.py`, оба workflow и их тесты — эксплуатация.

Короткое резюме для следующего агента: **это stateful publisher на одноразовом Actions runner; качество данных, сохранение подтверждённых доставок и недопущение пропуска горячей скидки важнее косметического упрощения кода.**


Уточнение владельца 2026-11-27, шаг 1: обычные карточки читаются фоном с hard deadline 6 с (прежде 2 с), отправка не ждёт. Никаких альтернатив для Best Buy 403/CamelCamelCamel. Runtime печатает commit/PARSER_VERSION и сохраняет копию строки в optional audit; merge выбирает свежую. `weight_runner_probe` — до 25 обычных URL, один GET/URL, отдельный read-only runner/artifact. PR никогда не содержит data/. O5 одобрен ON; Best Buy API и Keepa вне объёма. Последующие изменения — тесты, отдельный squash PR и проверка публичного поста; три сбойных прохода или сломанный формат требуют revert кода без reset фактов доставки.

Шаг 1, измерение GitHub #45954386923: Corsair 8/8 HTTP200 (max2.133с), но только ambiguous Weight; Amazon/агрегаторы 13 challenge из14, обходов нет. Best Buy403 не перепроверялся альтернативами. Подтверждённый вес0/22; успешный HTTP не считается найденным весом. Артефакт и реальный HTML — docs/evidence/weight-runner-2026-11-27.json и tests/fixtures/weight/corsair-runner-real.html.


Шаг 2 / O5=ON: диапазоны из config/estimate_v1.json, метка «≈ … кг (оценка по категории)», default ON; WEIGHT_ESTIMATES=0 выключает. Только измеренные package-веса уточняют диапазон (>=3 уникальных наблюдений, границы расширяются с запасом, не сужаются). Package/net provenance не содержит оценок; для комплекта полное покрытие всех компонентов обязательно, метка «≈ сумма». Unknown category остаётся неизвестной. PARSER_VERSION=6. Тесты test_estimate_ranges: диапазоны/флаг/комплекты/калибровка/validate+restart.
