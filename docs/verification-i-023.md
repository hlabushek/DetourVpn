# I-023 — локальная проверка 2026-10-23

Настоящий temporary PostgreSQL 17, данные/пароли/OTP только synthetic TEST ONLY.
Admin/migrations/closed/subscriptions/DB URL: **68 passed / 83.37s**.
Дополненный admin/quota набор: **64 passed / 99.22s**, включая настоящий user
token, который проходит /me и отвергается admin API; disabled identity/session
expiry, owner-scoped device/family/tunnel revoke и disabled user history.

Полная backend/infra регрессия после 0013: **596 passed, 5 skipped / 541.09s**.
Пропуски: admin browser opt-in, прежние I-012/I-013/I-019 remote/physical opt-in,
Mini App browser opt-in. Browser admin затем отдельно выполнен: **1 passed**,
218.58s вместе с ручной UI-проверкой и cleanup. Все temporary clusters остановлены
и удалены. Финальные дополнительные правки small-host KDF serialization/node
observability проверяются targeted, полный набор после них не повторяется.

Браузер IAB работал с настоящим localhost API/PostgreSQL. Проверены password+MFA,
reload требует нового входа, явный demo catalog 99/199/29 RUB minor units,
Start admin grant 107374182400 bytes, credit 9007199254740993 без округления,
повтор той же формы не изменяет баланс/историю, revoke делает остаток subscription
недоступным без fake usage. UI audit содержит actor/reason/time/key.
Синтетический пароль вводился только в localhost тестовую форму; настоящие
credentials/Telegram/VPS не использовались. QA server остановлен, listener/secret
files и временная БД очищены штатным fixture.

Найдено и исправлено: Windows MIME text/plain для .mjs (модуль переименован в .js),
picker сбрасывал тариф после refresh (сохраняется выбор, Start по умолчанию),
flaky fixed-window rate test на границе минуты (фиксированное synthetic время).

Ruff/format, mypy 124 files, OpenAPI current и Node syntax checks passed.
Linux интерактивный bootstrap с настоящим владельцем не запускался. Windows
bootstrap fail closed, поскольку mode bits не удостоверяют ACL. Это остаётся
отдельной ручной проверкой, не скрывается под API fixture enrollment.
Remote deploy/HTTPS/backup scheduler/длительный multiuser VPN/Android egress
не проверены. Отдельный security review и phishing-resistant MFA не заявлены.
