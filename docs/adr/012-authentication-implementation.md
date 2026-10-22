# ADR-012: локальная реализация bot-code authentication

Status: **Proposed — implemented locally for review**, 2026-10-18 / I-005.
Основной способ входа уже выбран владельцем. Эта запись уточняет реализацию ADR-001;
не разрешает production collection/deployment и не объявляет принятое техническое ADR.

## Контракт и доверие

Реализованы `/api/v1/auth/start`, `verify`, `refresh`, `logout`, `logout-all`, `/me`.
Start возвращает 128 random bits, представленные UUID (без version-v4 контракта),
и независимый 256-bit verifier. Public ID является только correlation identifier.
Verifier не передаётся в Telegram. В хранилище только SHA-256 verifier/tokens и
HMAC-SHA256 кода с domain/version, UUID, generation и отдельным server pepper.
OTP 8 ASCII digits, 5 минут; pairing 10 минут, раньше обоих expiry. Проверка
constant-time. Пять ошибок с правильным verifier блокируют попытку; resend не
восстанавливает budget и не продлевает pairing. Cooldown 60 сек, rolling 5/15 мин
и 20/24 ч на Telegram subject. Новая issuance заменяет прежний code.

Выбран **webhook**, не long polling. Перед обработкой нужен отдельный случайный
`X-Telegram-Bot-Api-Secret-Token`; secret сравнивается constant-time. Входящий body
ограничен 16 KiB, в том числе без Content-Length. Только свежее message (не старше
300 сек, future skew <=60 сек), private chat, human sender, chat.id == from.id.
Forwarded/via-bot/business/edited/group updates не используются для входа.
Sender subject никогда не берётся из публичного verify JSON или start payload.
Telegram update dedup durable по (configured bot id, update id); bot token должен
соответствовать этому id. Профиль, username, phone и текст переписки не сохраняются.

Отправка через фиксированный официальный HTTPS Bot API, stdlib urllib; redirects,
environment proxy и retries выключены. Token-bearing URL не выводится, provider
exceptions заменяются безопасным DeliveryUnknown. DB claim/issuance коммитится
перед send: crash/timeout может потерять сообщение, повтор Update не отправляет
и не выпускает код снова. Пользователь явно повторяет запрос после cooldown.
Это at-most-once send attempt, **не** гарантия доставки. При неясном результате
код может оставаться валидным: Telegram мог доставить его до timeout.

## Транзакции и сессии

PostgreSQL row locks обеспечивают один successful consume. Advisory identity lock
сериализует создание аккаунта одного subject из разных pairing. User locks
сериализуют refresh/revoke и будущие ownership writes. Время берётся из DB
clock_timestamp после ожидания locks. Error counters и refresh-reuse revocation
коммитятся до формирования ошибки; код не теряет защиту из-за transaction rollback.

Access/refresh — отдельные opaque random 256-bit secrets. Access 10 минут,
family absolute expiry 30 дней без продления при rotation. Rotation инвалидирует
предыдущий access, старый refresh хранится digest tombstone до конца family.
Refresh reuse (включая concurrent retry) отзывает всю family, даже если один запрос
уже получил ответ. Клиент обязан выполнять refresh последовательно; lost response
требует нового входа. Logout отзывает family, logout-all — все family данного user.
Disabled/pending user не может войти/refresh/use access. В I-005 квоты ещё не было.
I-007 добавляет once-only initial credit в transaction успешного code login,
см. ADR-014; выдача кода сама по себе начислением не является.

RBAC — allowlist permissions с default deny, пользователь не выбирает role через
API. Обычная сессия может читать себя, отзывать свои сессии и управлять своими
устройствами. Admin permissions запрещены даже для DB role admin: отдельного
admin MFA enrollment/provider пока нет, поэтому административных операций/API нет.
Это fail-closed gate, **не завершённый административный MFA**. Ручное изменение role
не открывает обход. Полная admin identity/MFA требует отдельной issue перед admin API.

## Общие лимиты и retention до реальных аккаунтов

В local increment shared limiter находится в PostgreSQL, без новой Redis library:
атомарный UPSERT, fixed 60-second windows, отдельные global/network buckets.
На start 10/IP и 100/global, verify 60/1000, refresh и webhook 120/1000 в минуту.
На границе windows возможен двойной burst — это не sliding limiter. Unknown IDs
также расходуют budget. Raw IP не сохраняется: HMAC(scope, IP, window). Forwarded
headers не доверяются. Запуск Uvicorn только с `--no-proxy-headers --no-access-log`.
При будущей установке за proxy нужна явная allowlist trusted proxies и иной budget:
нынешний режим считает адрес proxy общим и может ограничить пользователей.
DB outage fail-closed 503, in-memory fallback отсутствует. Redis остаётся планом
для распределённой инфраструктуры, а не выполненной частью этого increment.

Cleanup (`python scripts/auth_cleanup.py`) запускается явно по локальному расписанию:

| Данные | Хранение |
|---|---|
| Rate buckets, только keyed digest | До 2 минут + интервал cleanup |
| Pairing/code digests и subject | До expiry + 24 ч + интервал cleanup |
| Issuance subject/time | 24 ч + интервал cleanup |
| Bot update id/status, без payload | 7 дней + интервал cleanup; stale messages rejected |
| Session family/credentials/tombstones | До absolute expiry + 24 ч + интервал cleanup |
| Users/Telegram identity | Пока существует аккаунт; deletion/recovery до реальных аккаунтов отдельно |

Без запуска cleanup записи автоматически не удаляются. До production нужны
утверждённые account deletion/privacy policy, backup retention, least-privilege DB
roles, TLS/proxy policy, secret management/rotation и admin MFA. Pepper rotation
сейчас инвалидирует незавершённые коды/лимитеры; runtime keyring/online rotation
не реализованы. Ни один локальный тест не подтверждает доставку в настоящий Telegram.

Новых библиотек нет: CSPRNG/HMAC/hashlib/urllib — Python stdlib, SQLAlchemy/psycopg
уже pinned. Свой crypto primitive/OTP-algorithm не вводится.

Первичные источники проверены 2026-10-18:
[Telegram Update/User](https://core.telegram.org/bots/api#update),
[webhook secret](https://core.telegram.org/bots/api#setwebhook),
[sendMessage](https://core.telegram.org/bots/api#sendmessage),
[deep linking](https://core.telegram.org/bots/features#deep-linking).
