# Приёмка исправлений аудита usasales

Регрессии проверяются без внешних магазинов и Telegram: сетевые ответы воспроизводятся фикстурами, Git-конфликты — настоящими временными репозиториями. Исходный `results.json` содержит наблюдения старого поведения, а не тестовые утверждения; ниже каждому из его 24 сценариев сопоставлена проверка желаемого результата. Все тесты запускаются через `python -m pytest -q`.

| Сценарий исходного results.json | Требуемый результат | Проверка в tests/ |
|---|---|---|
| coupon_amount | $100 − $20 = $80; уже итоговая сумма не уменьшается повторно | test_regressions.PriceRegression.test_coupon_arithmetic_and_already_final_price |
| 90_percent_discount | Приоритетная проверка; публикация после подтверждения | PriceRegression.test_extreme_discount_prioritised_then_published_after_verification |
| verified_coupon | MPN сохраняется, подтверждённые $80 публикуются | PriceRegression.test_coupon_identity_is_independent_of_eligibility; test_confirmed_coupon_publishes_correct_final_price |
| repeat_after_3_days | По уточнённому правилу от 2026-11-09 прежняя цена блокируется и после 24 часов | QueueRegression.test_same_price_is_blocked_after_legacy_duplicate_window |
| reclassified_same_price | Исправленная категория переоценивается | QueueRegression.test_corrected_category_reassessed_at_same_price |
| no_tradein | Отрицание не создаёт условную цену | PriceRegression.test_negations_and_actual_condition |
| hardware_with_accessory | Главный товар сохраняет категорию | PriceRegression.test_free_accessories_do_not_replace_primary_category |
| transient_delivery_pending | Очередь переживает ошибку и пустую следующую ленту | QueueRegression.test_temporary_delivery_survives_empty_next_feed |
| first_checkpoint_pending_tail | Весь хвост сохранён до отправки | QueueRegression.test_entire_tail_saved_before_first_delivery_and_fatal_error |
| cached_old_price | Новая цена обходит кэш, включая добавление в process | PriceRegression.test_changed_price_forces_refresh; LastMileRegression.test_process_refreshes_changed_price_after_queue_cache_add |
| unverified_msrp | Явное отсутствие товара не преодолевается MSRP | PriceRegression.test_structured_condition_and_stock_take_precedence |
| expired_enters_history | Старое/expired наблюдение не добавляется как свежее | QueueRegression.test_expired_observation_never_contaminates_baseline |
| unknown_shipping | Без доставки рыночное сравнение не подтверждается; запись остаётся pending | AdditionalAuditRegression.test_unknown_shipping_is_pending_not_final_rejection |
| search_starvation | Все 20 товаров получают запрос за пять проходов | PriceRegression.test_twenty_products_all_searched_in_five_cycles |
| music_store_allowlist | Товар Sweetwater разрешён; поиск и корзина запрещены | SafeRedirectRegression.test_music_links_allowed_but_search_and_cart_rejected |
| condition_mismatch | Структурированное Refurbished имеет приоритет | PriceRegression.test_structured_condition_and_stock_take_precedence |
| fetch_deadline | Реальное завершение зависшего дочернего процесса | SafeRedirectRegression.test_hung_source_cannot_hold_whole_cycle |
| null_state | Ошибка вместо обнуления; валидная резервная копия восстанавливается | CheckpointRegression.test_null_legacy_state_raises_instead_of_reset; test_corrupt_primary_recovers_valid_previous_generation |
| remote_state_overwritten | Объединение отправок при конкурентном push | test_workflow_git.RunnerSyncTests.test_concurrent_delivery_records_survive_push_race |
| valid_mpn_rejected | Короткие и числовые структурированные MPN принимаются | PriceRegression.test_short_and_numeric_structured_mpn; AdditionalAuditRegression.test_structured_parser_keeps_short_mpn |
| wrong_direct_candidate | Прямая ссылка основного товара сохраняет приоритет | QueueRegression.test_primary_product_link_wins_over_secondary_link |
| canonical_redirect_rejected | Безопасный 301 проходит; внешний/HTTP запрещён, переходы ограничены | SafeRedirectRegression.test_same_store_canonical_redirect_followed; test_external_or_insecure_redirect_never_fetched; test_redirect_chain_is_bounded |
| conflicting_markup_product_dropped | Каталог сохраняет неоднозначную карточку для перепроверки без выбора цены наугад | AdditionalAuditRegression.test_conflicting_catalog_prices_retained_for_verification |
| stale_pending_overrides_fresh | $400 заменяет $500 и публикуется; старый quote не побеждает | QueueRegression.test_fresh_400_replaces_pending_500_and_is_published; test_old_quote_cannot_replace_new |

Дополнительно проверяются: отсутствие повтора при росте/возврате к прежней цене, версия правил, 429 с длинным retry_after, сохранение sending до запроса, неопределённая доставка, корректность HTML, атомарность при падении экспорта, канонический artifact без совместимых экспортов, конкурентное изменение только history, нулевой расход поисковой квоты, разрешённые купонные виджеты, сохранение pending вопреки старому processed.

## Границы проверки

Тесты подтверждают логику на воспроизводимых входах. Они не гарантируют доступность Amazon/Newegg с IP GitHub Actions, актуальность купонов вне проверенной карточки или изображение в предпросмотре Telegram. Неизвестные данные остаются поводом для повторной проверки. Строгое совпадение версии товара и требование двух независимых магазинов для рыночного сравнения сохранены; MSRP не превращается в рыночную котировку. Категории `pixel` и `music_gear` сохранены для совместимости правил и истории.


## Уточнение 2026-11-09: ежедневные повторы

Прежнее требование разрешать повтор одинаковой акции после TTL заменено: время само по себе не делает предложение новым. `tests/test_publication_history.py` проверяет дни 1/2/3/7/30, более низкую цену, запрет более высокой и возврата к прежней, смену ленты/ID/заголовка/меток URL, два одинаковых товара в одном проходе, старые записи `posted`, разные варианты/состояние, сбой доставки, перезапуск и конкурентное Git-слияние постоянной истории.


## O4: вес упаковки, 2026-11-27

Shipping Weight теперь разрешён и приоритетен; прежняя политика исключения упаковки отменена владельцем. Package/net не смешиваются, каждое значение имеет единицу и происхождение. Ожидание карточки не задерживает отправку; неизвестный вес сохраняет публикацию.

| Сценарий | Требование | Регрессия |
|---|---|---|
| package vs net | Package приоритетен; net явно «без упаковки» | test_package_weight.test_explicit_field_names; test_dry_run_five_cases_and_photo_fallback |
| Amazon Package Dimensions | Только масса после `;`, не размеры/диапазоны | test_amazon_package_dimensions_not_dimensions_and_bullets_both_kinds |
| ambiguity/challenge | Не выдумывать вес, не обходить защиту | test_ambiguous_unitless_conflicting_and_variants_stay_unknown; test_real_html_fragments_and_compound_mass_are_not_mislabelled |
| bundle | Только полный набор, подпись «сумма комплектующих» | test_dry_run_five_cases_and_photo_fallback |
| O5 | Оценки OFF, явная маркировка при включении | test_estimates_off_by_default_and_explicitly_labelled |
| deadline | Hung lookup не задерживает mock-доставку | test_lookup_hard_deadline_and_publication_never_waits |
| knowledge | <=500 КБ, restart, package/net раздельно, строгие варианты | test_knowledge_size_cap_and_merge; test_knowledge_restart_cross_store_separate_kinds_and_variant_safety |
| state | validate/load/merge, статус аудита, старые строки unmeasured | test_state_validate_restart_merge_and_weight_audit |
| Git | Два писателя и повреждённый необязательный кеш не трогают posted | test_workflow_git.RunnerSyncTests.test_weight_knowledge_concurrent_merge_and_corrupt_optional_snapshot |

API и редактирование отправленных постов — дизайн, без запросов/изменений Telegram. Обычные business-пороги, posted, delivery и publication_history floors не меняются. PARSER_VERSION=5, RULES_VERSION=7.


Уточнение владельца 2026-11-27, шаг 1: обычные карточки читаются фоном с hard deadline 6 с (прежде 2 с), отправка не ждёт. Никаких альтернатив для Best Buy 403/CamelCamelCamel. Runtime печатает commit/PARSER_VERSION и сохраняет копию строки в optional audit; merge выбирает свежую. `weight_runner_probe` — до 25 обычных URL, один GET/URL, отдельный read-only runner/artifact. PR никогда не содержит data/. O5 одобрен ON; Best Buy API и Keepa вне объёма. Последующие изменения — тесты, отдельный squash PR и проверка публичного поста; три сбойных прохода или сломанный формат требуют revert кода без reset фактов доставки.

Шаг 1, измерение GitHub #45954386923: Corsair 8/8 HTTP200 (max2.133с), но только ambiguous Weight; Amazon/агрегаторы 13 challenge из14, обходов нет. Best Buy403 не перепроверялся альтернативами. Подтверждённый вес0/22; успешный HTTP не считается найденным весом. Артефакт и реальный HTML — docs/evidence/weight-runner-2026-11-27.json и tests/fixtures/weight/corsair-runner-real.html.


Шаг 2 / O5=ON: диапазоны из config/estimate_v1.json, метка «≈ … кг (оценка по категории)», default ON; WEIGHT_ESTIMATES=0 выключает. Только измеренные package-веса уточняют диапазон (>=3 уникальных наблюдений, границы расширяются с запасом, не сужаются). Package/net provenance не содержит оценок; для комплекта полное покрытие всех компонентов обязательно, метка «≈ сумма». Unknown category остаётся неизвестной. PARSER_VERSION=6. Тесты test_estimate_ranges: диапазоны/флаг/комплекты/калибровка/validate+restart.


Шаг 3: optional weight_edits.json.gz <=400КБ/2000 записей, TTL24ч; отдельный validate/load/merge/artifact, без изменения publication_history/posted/delivery. Копируется message_id из реального receipt, сохраняется точный send payload/hash. Одна попытка редактирования только строки веса, preflight публичного превью запрещает перезапись ручной цены/ссылок. text→editMessageText, photo→editMessageCaption. Неопределённый ответ не повторяется. config/late_weight_edit.json=canary (одна попытка); штатный ON запрещён, пока public_preview_verified=false. OFF: LATE_WEIGHT_EDITS=off. После наблюдаемой реальной правки разрешён переход ON; до этого proof4 pending. Регрессии: test_weight_edits.


Шаг 4: Best Buy от curated/community агрегатора может публиковаться с явной пометкой непроверенной цены при недоступной карточке. Только основной URL/явный store+товарный кандидат, не вторичная ссылка roundup. Реальные community votes/пороги и явное противоречие магазина сохраняют силу; unvetted Best Buy не получает fast path. Best Buy403 не обходится, никакого API. RULES_VERSION=8. Замер до изменения: в сохранённых pending и audit0 доказуемо атрибутированных Best Buy; непрозрачные ссылки не считаются Best Buy наугад. Регрессии test_bestbuy_aggregator.


Шаг 5: tools/feed_probe.py — один GET на каждый из6 Tom US/UK/news и Slickdeals RSS endpoint, только отдельный GH read-only job. Никаких обходов, редиректов или выдуманных голосов; фиксируются entry_keys/реальные score-поля, новости не заменяются до доказанного parseable US deals feed. Тесты test_feed_probe.

Результат шага5, GH #45955886357: Tom US deals RSS200/50, UK200/50, news200/50. Источник tomshardware заменён на US deals (editorial, publish=True), существующий ENABLE_SOURCES сохраняется. SD computer parts / pc components / frontpage200/25; добавлены первые2 unvetted поисковые ленты. Реальный headlinefixture и was/now (synthetic) проверяют цены; «cents per GB» не превращаются в общую цену. media_thumbnail не является thumb score.


Шаг6 остановлен по результату измерения: Slickdeals RSS0/75 entries с реальным score/thumb/vote. Ничего не маппировать и не менять пороги; media_thumbnail — не голоса. Подробности docs/slickdeals-score-2026-11-27.md, regression test_feed_probe.test_thumbnails_are_not_real_community_votes.
