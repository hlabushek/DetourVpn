# ADR-036 — отдельная административная поверхность

2026-10-23, локальная реализация I-023, не разрешение deployment.

Отдельные admin_identities/admin_sessions; Telegram user.role не открывает admin.
RBAC: admin пишет, operator читает; user tokens никогда не принимаются. Пароль
Argon2id через закреплённую cryptography (64 MiB, 3 iterations, 1 lane, salt 16B,
32B output). TOTP RFC 6238 через cryptography.twofactor (HMAC-SHA1, 6 digits,
30s, окно +/-1 step). Собственная OTP/KDF реализация не пишется.
DB last_totp_step под row lock запрещает повтор кода и одновременные логины
одним кодом. TOTP secret зашифрован Fernet отдельным VPN_ADMIN_ENCRYPTION_KEY,
который хранится вне БД и checkout. Compromise API host остаётся существенным
риском; это не phishing-resistant hardware MFA. Независимый security review открыт.

Access token opaque 256 bits, только digest в DB, absolute TTL 10min, без refresh.
Каждый вход требует пароль+MFA; logout отзывает token, disabled_at проверяется
каждым запросом. Секреты/токен браузера только RAM; reload требует повторного входа.
TLS обязателен на одобренном ingress; local tests на loopback не доказывают TLS.

Durable PostgreSQL rate buckets до KDF: 5/identity, 10/network, 60/global в минуту.
Ключи HMAC-псевдонимизированы отдельным admin key, raw IP не хранится. Чужим
forwarded headers не доверяем; за proxy лимит сети общий, безопасный консервативный
режим. Auth cleanup удаляет expires_at; admin_sessions retention пока требует
I-027 supervisor. Audit хранится на срок закрытой эксплуатации с последующим
согласованием удаления персональных данных; публичный запуск не разрешён.

POST /api/admin/v1/actions — строгий allowlist операций, не shell/SQL execution.
Reason/actor/time/key обязательны. Action audit immutable, writes и audit в одной
транзакции; глобальный key и fingerprint включают actor и все входные поля.
Повтор возвращает прежний результат; другой payload/actor даёт conflict.
Credit/debit используют существующий ledger; компенсация — новый credit с новой
причиной/key, исходные проводки не удаляются. Revocation сохраняет durable outbox,
device revoke отзывает связанные сессии. Network I/O в DB transaction отсутствует.

Bootstrap — только локальная интерактивная Linux CLI в защищённом operator env:
нет стандартного логина/пароля, getpass, private enrollment file вне checkout,
подтверждение TOTP до commit. Файл создаётся O_EXCL/0600 в 0700 каталоге и удаляется
в finally. На Windows CLI отказывает: mode bits не подтверждают ACL. Для настоящего
сервера запускать после отдельного разрешения. CLI даёт начальную identity и audit,
но не меняет webhook/DNS и не выдаёт фиктивный успешный payment.

Ограничения: I-024–028 ещё не выполнены. Нельзя обещать прекращение туннеля по
одному HTTP audit event. Current v2 не принимает subscription users (ADR-035).
Безопасное lost-MFA recovery/rotation — локальный оператор, не публичный bypass;
перед реальными пользователями нужен отдельный проверенный recovery runbook.
