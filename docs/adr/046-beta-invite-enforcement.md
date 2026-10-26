# ADR-046 — явный invite gate для закрытой beta

Статус: реализовано локально в I-030, hosted acceptance не выполнен.

`VPN_BETA_INVITES_ONLY` по умолчанию false. Включение допускается только при
`VPN_ENVIRONMENT=closed` или `test` и `VPN_ENABLE_AUTH=true`. Перед включением
требуется migration `0016_beta_invites`. Feature-off поведение существующего
closed control plane не меняется.

При включённом gate OTP разрешается только после MFA-admin `grant_beta_invite`.
`revoke_beta_invite` запрещает повторный вход/refresh, disables account, revokes
session families, помечает active peer connections `revoke_pending` и enqueue-ит
фиксированные remove-peer commands. HTTP accept от control plane не означает,
что peer уже закрыт: ждать authenticated node ACK либо независимого kernel
deadline до безопасного повторного приглашения. Re-invite до полного database
closure получает отказ.

Telegram numeric subject приходит только в TLS admin request и не сохраняется
в новом invite record: таблица хранит HMAC digest через существующий auth
pepper и домен `beta-invite`. Admin action хранит actor, reason, result UUID и
request digest, включающий только keyed subject digest; API запрещает включать
точный numeric ID в reason, поэтому raw ID не попадает в audit DB/логи. Запросы
требуют существующий admin TOTP/RBAC; только
role `admin` может писать. Идемпотентность — существующий AdminAction key;
concurrent grant одного subject сериализуется теми же auth subject/identity
locks и дополнительным partial unique index на active digest. Отозванные invite
rows сохраняются для аудита.

Применение проверяется в webhook `/start` OTP preparation, Mini App OTP,
direct Mini App session exchange, native `/auth/verify`, bearer authentication
и refresh. Так закрыты прямой Mini App bypass и уже выданные токены. Auth
проверка держит `FOR SHARE` на active invite до завершения транзакции; revoke
берёт row update lock, затем отключает account/session и ставит data-plane
команды в ту же транзакцию.

Migration downgrade разрешён только при пустой таблице; с историей invite он
отказывается удалять данные. Реальный Telegram, удалённый admin и node ACK
локально не доказываются. До I-028 нельзя рекомендовать друзьям чувствительный
трафик и нельзя обещать kill switch.
