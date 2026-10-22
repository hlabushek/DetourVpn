# ADR-013: registry устройств и привязка сессий

Status: **Proposed — implemented locally for review**, 2026-10-18 / I-006.
Допущения для локального functional milestone, не тарифные/коммерческие обещания.

## Ключи и API

`POST /api/v1/devices`: platform=android, key_type=x25519, public_key — canonical
base64 32 bytes, nonzero. Принимается **только публичный** ключ; extra fields,
включая private_key/user_id/role/revoked_at, запрещены. Ключ генерирует будущий
native client. Backend не генерирует и не получает private key. Формат совместим
с планируемыми WG/AWG public keys и не выбирает/не подключает tunnel SDK.
Проверка длины/кодирования не доказывает possession, корректность keypair или
пригодность curve point; проверка адаптера и настоящий handshake нужны в I-011/012.
Синтетические bytes тестов явно не являются работающим native ключом/туннелем.

`GET /devices` по умолчанию возвращает свои active devices; include_revoked=true
включает историю. Limit 1..100 (default 100), offset 0..100000; порядок created_at/id
descending. Offset pagination может сдвигаться при concurrent additions; это не
snapshot export. `GET /devices/{id}` читает своё устройство, включая revoked.
Чужой и несуществующий ID дают одинаковый not_found, без owner/key disclosure.

Public key global unique. Повтор той же активной регистрации владельцем возвращает
тот же device_id. Чужой или отозванный ключ даёт generic device_conflict. Смена key
требует нового устройства/нового входа, resurrection и key update endpoints нет.
Registry не является доказательством владения ключом: злоумышленник, заранее
узнавший ещё не зарегистрированный public key, может занять его; production
provisioning требует proof/handshake и политики conflict resolution в I-012.
Не заявлять защиту от configuration sharing из одной регистрации public key.

## Ownership и revocation

В локальном режиме максимум **3 active devices/user**; billing/plan-based limit
ещё нет. User row lock сериализует создание и проверку лимита. Shared key advisory
lock и unique constraint сериализуют collision между разными аккаунтами. Повторная
регистрация одного key естественно идемпотентна; не создаёт начислений или peer.

При регистрации текущая session family привязывается к device. Новые логины создают
unbound family; на новом устройстве требуется регистрация его public key.
Одна family не может переключаться на другой device. Повторный вход с тем же key
может связать несколько family одного владельца. Composite FK (device_id, user_id)
в БД запрещает даже внутреннюю cross-owner привязку. Unbound sessions имеют только
обычные self/device-management права, не право на выдачу tunnel config.

DELETE /devices/{id} атомарно помечает device revoked и отзывает все связанные
family этого пользователя. Refresh/device writes/logout сериализуются через user
lock; concurrent refresh не оставляет живой family отозванного device. Повторный
DELETE идемпотентен при действующей другой сессии. Отзыв текущего device немедленно
закрывает текущую family: повтор тем же bearer закономерно unauthorized.

Это **отзыв control-plane session**, а не удаление peer на узле: узлов/peer ещё нет.
В I-012 device revoke должен дополниться bounded node revocation/lease policy.
Ключи/identity/metadata не пишутся в application logs; device records пока хранятся
на время существования аккаунта, в том числе revoked keys против resurrection.
Deletion/export/backup retention нужны до реальных пользователей, как в ADR-012.
Миграция 0003: upgrade/ORM drift и FK/races проверяются на PostgreSQL. Downgrade
с device rows запрещён под table locks; data-preserving forward changes предпочтительны.

После I-006 функциональный пользовательский путь auth → device → revoke/logout
доступен для локальной проверки. Полная roadmap phase 1 ещё имеет отдельные gates:
admin identity/MFA, production privacy/recovery и operational Redis/proxy decisions.
