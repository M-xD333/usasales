Стандартные GitHub-hosted runners публичных репозиториев обычно бесплатны; larger runners и хранение артефактов имеют отдельные правила [billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
Непрерывный бот расходует runner-время; бесплатность не означает разрешение любой нагрузки.
[Условия Actions](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features) ограничивают непропорциональную нагрузку, serverless-приложения и несвязанную с проектом работу.
Круглосуточный цикл требует оценки владельцем по этим условиям; этот PR не меняет его и не утверждает соответствие.
Для проверки PR используйте review-check.yml: только офлайн-тесты, DRY_RUN=1, contents:read, без Telegram secrets, записи состояния и цепочки запусков.
