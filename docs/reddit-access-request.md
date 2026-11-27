# Reddit: результаты доступа и честная заявка

2026-11-27: ровно один обычный GET на каждый endpoint, без OAuth, повторов, смены UA и прохождения редиректа:

| URL | Результат | Вывод |
|---|---|---|
| https://www.reddit.com/r/buildapcsales/.rss | HTTP404, application/atom+xml,266байт | Рабочая лента не получена; причину404 не угадываем |
| https://old.reddit.com/r/buildapcsales/.rss | HTTP302, пустой body | Редирект не пройден, второй запрос не выполнялся |

Исходные статусы: docs/evidence/reddit-rss-2026-11-27.json. Runtime источник не включался. Не переходить к обходу блокировок/другим доменам ради доступа. Для API сначала получить approval согласно [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy), [Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki) и [официальному описанию доступа](https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data). Заявка ещё не отправлена.

## Текст для заявки

> I request read-only Data API approval for usasales, a personal, low-volume US PC deal-alert project scoped to r/buildapcsales. The project has an existing public Telegram destination, https://t.me/usasalessearch; I am disclosing this rather than describing the destination as private. Reddit would only signal a product to verify independently at a retailer. I would not republish Reddit posts, comments or usernames, resell Reddit data, train models, vote, post, message users or scrape blocked routes. Proposed access is one bounded recent listing at most every 15 minutes, respecting your approved limits and deletion requirements, with minimal identifiers retained for no more than 24 hours. Please confirm eligibility for free access and whether this public alert destination is permitted. Monetization status: [owner must state the actual current status]. I will seek approval before any commercial change.

Перед отправкой владелец заполняет реальный статус монетизации, контакт и точные app identifiers. Нельзя заявлять «non-commercial/private», если это неправда. Если канал монетизируется/появляются affiliate links или другие коммерческие способы использования, заново проверить актуальные Reddit terms и требуемое разрешение. Платный API вне объёма: если бесплатный доступ не одобрят, остановиться, а не обходить.
