# Woot Developer API: адаптер без активации

[Официальный портал](https://developer.woot.com/) проверен 2026-11-27: GET /feed/{feedname}; Computers/Electronics допустимы; ключ только в x-api-key. Лимиты1rps/burst10/1000 запросов в день. Ключ запрашивает владелец через форум Woot; мы не регистрировались и запросов с ключом не выполняли.

FeedPage содержит Items/TotalPages/MarketingName. FeedItem: OfferId, Title, Url, Photo, SalePrice.Minimum/Maximum, ListPrice.Minimum/Maximum, Condition, IsSoldOut, IsAvailableOnMobileAppOnly, StartDate/EndDate. Исходный пример из портала сохранён в tests/fixtures/woot/feed-official-example.json; он sold-out/2018, не живое предложение.

woot_api.fetch выключен без WOOT_API_KEY; не подставляет ключ в URL/логи. Ключ не предоставлен, live coverage не измерено. Сейчас адаптер не зарегистрирован в Source/publishing loop. После предоставления ключа нужен отдельный интеграционный шаг: секрет workflow → store-type consumer, очередь неоднозначных вариантов, явная атрибуция и полный тест пути публикации. Не считать наличие адаптера доказательством новых публикаций.

Только точная SalePrice при Minimum==Maximum; ListPrice — заявленная база, не независимая рыночная цена. Sold-out/истёкшие предложения не публиковать. Диапазоны и App-only не терять: сохранены candidates с неизвестной ценой для GetOffer/проверки. Не считать доставку бесплатной без подтверждения и не угадывать Condition при null.

Будущий scheduler: минимум15мин между проверками каждого feed;2×96=192 запроса/день, не скачивать весь каталог/все страницы в каждом проходе. При429 соблюдать Retry-After и общий stop/cooldown; текущий адаптер никогда не повторяет запрос автоматически. Никаких антибот-обходов или платных сервисов. API не содержит подтверждённых package/net полей в проверенном FeedItem: вес может дать обычный существующий кеш/оценка, не выдуманный API-атрибут.
