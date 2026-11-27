# Discord: только дизайн, OFF

Сборщик не реализован, Discord-запросов нет. Только официальный Follow announcement-канала в собственный сервер владельца и чтение владельческим bot account. User token/self-bot, чтение чужого сервера напрямую, обход отсутствующего Follow/прав доступа запрещены.

## Ручные шаги владельца

1. Создать собственный Discord-сервер и отдельный destination-канал для сигналов. У владельца на destination требуется Manage Webhooks.
2. В выбранном исходном **Announcement**-канале нажать Follow и выбрать свой сервер/destination. Если Follow нет или владелец источника не разрешил доступ, остановиться. Не заменять Follow скрейпером. В свой сервер попадут только опубликованные источником announcements, не вся история обсуждений.
3. В Discord Developer Portal создать собственное application и Bot, сохранить BOT_TOKEN как секрет, никогда не передавать пользовательский токен/пароль/cookies. Запросить и включить Message Content Intent только если нужны content/embeds; условия privileged intent должны быть выполнены.
4. Пригласить этого бота только в свой сервер официальным OAuth2 с scope bot. Минимальные права destination: View Channel и Read Message History; Send Messages и Administrator для чтения сигналов не нужны. Не давать боту доступ ко всем остальным каналам.
5. Зафиксировать server/channel IDs и проверить одним ручным примером, что Follow действительно доставляет опубликованные announcements. Владелец выполняет эти действия сам; мы не создавали application, сервер, webhook и не подключались к Discord.
6. Перед будущей реализацией проверить разрешения источника, Discord Developer Policy и необходимость Message Content Intent/approval. Ключ и IDs передать только через GitHub Secrets/Variables, не коммитить. До этого `DISCORD_SIGNALS_ENABLED=0`.
7. Будущий read-only consumer: не чаще15мин, небольшой latest batch только из destination своего сервера, Bot-token авторизация, уважать429 и rate headers. Хранить только cursor/message ID и короткоживущий сигнал, не полный текст/архив пользователей. Новый cursor пойдёт через validate/load/merge и отдельные тесты.
8. Сигнал → точный merchant URL/MPN/model → независимая проверка магазина → собственная карточка предложения. Не копировать Discord-текст, вложения, оформление, фото или обсуждения в Telegram. Follow в своём сервере не является разрешением на внешнюю коммерческую перепубликацию.
9. Включение только отдельным будущим PR после owner setup/проверки прав. Сейчас нет runtime collector, новых env-secret bindings, каналов, токенов и публикаций Discord.

Официальные источники проверены 2026-11-27: [Following FAQ](https://support.discord.com/hc/en-us/articles/360028384531-Channel-Following-FAQ), [Announcement FAQ](https://support.discord.com/hc/en-us/articles/360032008192-Announcement-Channel-FAQ), [Message Content / Privileged Intents](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents). Для небольшого проекта отдельный постоянный сервер не обязателен: возможен ограниченный scheduled read; это проектирование, а не уже включённая интеграция.
