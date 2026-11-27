# Slickdeals RSS: реальных score-полей нет

GitHub probe #45955886357: computer parts, pc components и frontpage отдали по25 записей, всего75. Ни одного отдельного score/votes/thumbs-up/down поля. Проверены все entry keys, не только заголовки; исходное наблюдение — docs/evidence/feed-runner-2026-11-27.json.

`media_thumbnail` у Tom's Hardware — изображение, не голоса. Probe теперь исключает thumbnail из кандидатов vote fields; регрессия test_feed_probe.test_thumbnails_are_not_real_community_votes. Comments, позиции ленты, просмотры, бейджи и размер скидки не превращаются в community_score.

До/после:0/75 доступных реальных RSS vote fields. Маппинг и новые пороги НЕ добавлялись, как потребовал владелец при отсутствии поля. Существующие настоящие голоса Reddit JSON и их пороги не менялись. Если RSS начнёт отдавать явные голоса, нужен новый сохранённый пример с семантикой поля, unit test и версия правил перед включением.
