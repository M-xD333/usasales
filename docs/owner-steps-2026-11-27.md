# Выполнение шагов владельца, 2026-11-27

Это отчёт с раздельными доказательствами. Ни тесты, ни squash merge не означают доставку/редактирование в Telegram. Публикующий run: https://github.com/M-xD333/usasales/actions/runs/45927448674 . На момент проверки реальный последний пост в https://t.me/s/usasalessearch — usasalessearch/144 от 27ноября07:06:09 Asia/Tashkent, **до всех этих merge**; checkpoint last_delivery также144. Значит proof4 нового формата и реальной правки пока pending, а не «успешно».

| Шаг | Изменение/измерение | Push/merge | Тесты/runner | Telegram |
|---|---|---|---|---|
| 0 | Package/net и кеш, PARSER_VERSION5 | #11 main | CI45952304812 зелёный; новые weight_status в production audit | нового поста ещё нет |
| 1 | Фоновый deadline2→6с, без ожидания отправки. Corsair timeout8/8→0/8, HTTP200 8/8,max2.133с, только ambiguous Weight; Amazon/агрегаторы13 challenge из14; package/net0/22 | #12 main | CI45954563510; измерениеGH45954386923 | pending |
| 2 | O5 ON, estimate_v1, диапазоны; измеренные package уточняют>=3 наблюдений, net/оценки не смешиваются | #13 main | CI45954814746, Parser6 подхвачен | pending |
| 3 | One-attempt canary, 24ч, only-weight-line, preflight публичного оригинала, exact price/links, ambiguous outcome no retry | #14 main | CI45955352030, DRY/mock tests | **реальной правки нет; обычный ON заблокирован** |
| 4 | Доверенный Best Buy aggregate fallback с явной непроверенной ценой, без обхода403 | #15 main | CI45955652471; воспроизводимый тест исправлен. В исходном pending/audit0 доказуемых BB, исторические неизвестные не угадываются | pending |
| 5 | Tom US deals200/50 вместо news, editorial; SD parts/components200/25 | #16 main | GH45955886357, финальный CI45956153559, реальный XMLfixture/was-now tests | pending |
| 6 | SD реальных score0/75: stop, пороги не менялись, thumbnail не голоса | #17 main | CI45956267488 | публикационное поведение не меняется |
| 7 | Woot адаптер+официальный fixture+дизайн, OFF без ключа/consumer | #18 main | CI45956551853,2 adapter tests; live API не вызывали | OFF, не заявляем публикации |
| 8 | UPC free20/20,12/20 weight+unit,0 подтверждённых pkg/net,14 requests200; не активирован как typed runtime fallback | #19 main | GH45956781056; финальный CI45957315155; rate/stop tests | публикационное поведение не меняется |
| 9 | Reddit ровно1 GET/endpoint:404/302, no follow. Честная заявка, не подана | #20 main | CI45957474186; read-only снимок | API OFF |
| 10 | Discord Follow→own server→own bot→retailer signal; ручные шаги; без user token, копирования и runtime | отдельный PR | docs-only; полный offline CI перед merge | OFF |

## Runtime proof3

Дословная копия stdout из optional audit (также печатается публикующим bot.main):

`[runtime] commit=b300607a6cb19b09b72b6e93c3e393d207211fda PARSER_VERSION=6 RULES_VERSION=8`

Снимок receipt: docs/evidence/runtime-receipt-2026-11-27.json. SHA может быть runtime state-коммитом, содержащим новый код, а не самим squash merge. Доказательство не является вычитанным полным логом ещё работающего job; это сохранённая самим процессом копия той же строки. Промежуточные версии не выдаются за отдельно наблюдавшийся пост.

## Что остаётся условным

Нужен следующий **естественный** пост для проверки нового веса/ссылок в public preview и затем подходящий пост+измеренный вес для одной реальной canary-правки. Только после её наблюдения разрешён public_preview_verified/штатный ON. Искусственные посты, выдуманные package/net веса и подмена fixture реальной доставкой запрещены. Если3 последовательных прохода упадут или формат сломается — revert соответствующего merge кода, без force-push и reset сохранённых фактов. На момент проверки publishing run in_progress, подтверждённых3 сбоев нет; отсутствие нового подходящего предложения не равно сбою.

Нет data/ изменений в PR; runtime state-коммиты продолжаются обычным процессом. Нет paid API, Best Buy API/Keepa/CamelCamelCamel обходов, Reddit заявки без владельца, Discord доступа/токенов или внешнего collector. Следующие ручные зависимости: действительный Woot key/consumer integration, Reddit approval/честный статус монетизации, Discord server/Follow/bot setup. Не считать эти источники включёнными.
