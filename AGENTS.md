# usasales: рабочие инструкции

## Начало
- Основная архитектура: CLAUDE.md; карта регрессий: AUDIT_ACCEPTANCE.md.
- Использовать shallow clone --depth 1: полная история состояния очень большая.
- Отчёты владельцу по-русски, до 15 строк на фазу; rg и чтение по диапазонам.
- Не печатать data/*.json: агрегировать Python/jq, до 40 строк и 200 символов на строку.

## Инварианты
- DRY_RUN=1. Не отправлять реальные Telegram-сообщения для проверок.
- Не удалять/reset posted, publication_history floors или факты доставки.
- Изменения data допустимы только через миграцию с резервной копией.
- CAPTCHA/anti-bot bypass, партнёрские/платные API без решения владельца запрещены.
- Недостаток подтверждений не означает фейк; экстремальная скидка требует проверки.
- Фото и прямой товарный URL. O4: приоритет веса упаковки, net отдельно; источник/поле/время обязательны, неизвестный вес не блокирует публикацию.
- O5: оценки веса только за WEIGHT_ESTIMATES=1, по умолчанию OFF; вес не ожидается в потоке отправки.
- Новые поля состояния: validate/load/merge и restart regression.
- Не заменять живые словари pending/assessment_cache при очистке.
- Никаких force-push. Коммиты состояния могут приходить параллельно.
- Целевая ветка изменений codex/*; один коммит на фазу; Phase 0 отдельный PR.
- Изменение публикаций/парсинга: регрессия и оценка RULES_VERSION/PARSER_VERSION.
- Сначала targeted pytest -q -x -k ..., полный suite один раз на фазу.
- Доказательства раздельно: tests / pushed / runner picked up / Telegram confirmed.
- Запуск нового probe не должен попадать в publishing loop, save/artifact/chain.

## Повторно используемые роли
### usasales-probe
- tools/probe.py: честный UA, один GET на endpoint, максимум 25, без обходов.
- Запуск на GitHub runner; JSON-таблица status/type/bytes/entries; не печатать HTML.
### usasales-funnel
- tools/funnel.py: read-only checkpoint; причины/источники/категории/магазины.
- Различать текущие pending и историческую воронку: отсутствующие события не выдумывать.
### usasales-coverage
- tools/coverage.py: публичный конкурент только для калибровки, не перепубликовывать.
- caught требует свидетельства покрытия, не одного успешного classify/assess.

## Сеть и эксплуатация
- Нет платных сервисов и внешнего Newegg collector без решений O1/O2.
- Не менять 24/7 loop, стратегию веток состояния или частоту коммитов без согласования.
- workflow_git.py предназначен только для disposable Actions checkout, не локального reset.
- Не считать старую ошибку billing/403 нынешним состоянием; проверять текущий run.


Уточнение владельца 2026-11-27, шаг 1: обычные карточки читаются фоном с hard deadline 6 с (прежде 2 с), отправка не ждёт. Никаких альтернатив для Best Buy 403/CamelCamelCamel. Runtime печатает commit/PARSER_VERSION и сохраняет копию строки в optional audit; merge выбирает свежую. `weight_runner_probe` — до 25 обычных URL, один GET/URL, отдельный read-only runner/artifact. PR никогда не содержит data/. O5 одобрен ON; Best Buy API и Keepa вне объёма. Последующие изменения — тесты, отдельный squash PR и проверка публичного поста; три сбойных прохода или сломанный формат требуют revert кода без reset фактов доставки.

Шаг 1, измерение GitHub #45954386923: Corsair 8/8 HTTP200 (max2.133с), но только ambiguous Weight; Amazon/агрегаторы 13 challenge из14, обходов нет. Best Buy403 не перепроверялся альтернативами. Подтверждённый вес0/22; успешный HTTP не считается найденным весом. Артефакт и реальный HTML — docs/evidence/weight-runner-2026-11-27.json и tests/fixtures/weight/corsair-runner-real.html.


Шаг 2 / O5=ON: диапазоны из config/estimate_v1.json, метка «≈ … кг (оценка по категории)», default ON; WEIGHT_ESTIMATES=0 выключает. Только измеренные package-веса уточняют диапазон (>=3 уникальных наблюдений, границы расширяются с запасом, не сужаются). Package/net provenance не содержит оценок; для комплекта полное покрытие всех компонентов обязательно, метка «≈ сумма». Unknown category остаётся неизвестной. PARSER_VERSION=6. Тесты test_estimate_ranges: диапазоны/флаг/комплекты/калибровка/validate+restart.


Шаг 3: optional weight_edits.json.gz <=400КБ/2000 записей, TTL24ч; отдельный validate/load/merge/artifact, без изменения publication_history/posted/delivery. Копируется message_id из реального receipt, сохраняется точный send payload/hash. Одна попытка редактирования только строки веса, preflight публичного превью запрещает перезапись ручной цены/ссылок. text→editMessageText, photo→editMessageCaption. Неопределённый ответ не повторяется. config/late_weight_edit.json=canary (одна попытка); штатный ON запрещён, пока public_preview_verified=false. OFF: LATE_WEIGHT_EDITS=off. После наблюдаемой реальной правки разрешён переход ON; до этого proof4 pending. Регрессии: test_weight_edits.


Шаг 4: Best Buy от curated/community агрегатора может публиковаться с явной пометкой непроверенной цены при недоступной карточке. Только основной URL/явный store+товарный кандидат, не вторичная ссылка roundup. Реальные community votes/пороги и явное противоречие магазина сохраняют силу; unvetted Best Buy не получает fast path. Best Buy403 не обходится, никакого API. RULES_VERSION=8. Замер до изменения: в сохранённых pending и audit0 доказуемо атрибутированных Best Buy; непрозрачные ссылки не считаются Best Buy наугад. Регрессии test_bestbuy_aggregator.


Шаг 5: tools/feed_probe.py — один GET на каждый из6 Tom US/UK/news и Slickdeals RSS endpoint, только отдельный GH read-only job. Никаких обходов, редиректов или выдуманных голосов; фиксируются entry_keys/реальные score-поля, новости не заменяются до доказанного parseable US deals feed. Тесты test_feed_probe.
