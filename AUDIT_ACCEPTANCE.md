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
