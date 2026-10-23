# Администрирование закрытого сервиса — I-023

Локально реализованы API и static UI. Реальные администраторы не созданы,
серверы/HTTPS не настроены. Длительный многопользовательский VPN пока закрыт
I-024 gate; issuance в панели не является успешным VPN acceptance.

## Конфигурация и первоначальный администратор

1. Применить миграцию `0013_admin` отдельной migrator ролью по
   closed-control-plane.md. На dev DB: `uv run --frozen alembic upgrade head`.
   Миграция не создаёт admin или стандартный пароль. Downgrade с identity/audit
   запрещён; откат непустой БД — только reviewed forward change/restore.
2. В локальном secret manager создать **отдельный** Fernet key. Можно штатной
   CLI `scripts/operations.py keygen --key-file /private/path/admin-encryption.key`
   в 0700 каталоге, новый файл 0600. Это новый независимый ключ, не backup key.
   Секрет перенести локально в root-only API EnvironmentFile как
   VPN_ADMIN_ENCRYPTION_KEY, VPN_ENABLE_ADMIN=true. Не печатать ключ/URL и не
   добавлять его в argv, Git, чат или скриншот. Копия ключа отдельно от БД/backup.
3. Bootstrap выполнять **на Linux**, под оператором с private directory 0700,
   принадлежащей этому же UID; secrets загрузить из локального secret manager
   в environment. На Windows CLI намеренно отказывает, поскольку POSIX mode не
   доказывает Windows ACL. Это не разрешение запускать CLI на удалённом сервере:
   сначала отдельное согласие на постоянный backend/infra rollout.
4. Из release directory, с теми же DB/key настройками:

```bash
.venv/bin/python scripts/admin_bootstrap.py --username <chosen-name> --enrollment-file /private/0700-directory/enrollment.txt
```

CLI запрашивает новый пароль дважды через getpass (16–256 символов), создаёт
новую случайную TOTP secret и **временный** otpauth URI файл O_EXCL/0600 вне
checkout. Импортировать его локально в свой authenticator; не использовать
внешние QR-генераторы и не присылать файл в чат. Ввести текущий код через
getpass. Только после проверки коммитятся identity и bootstrap audit.
Файл удаляется в finally, в том числе при ошибке. Существующий admin не меняется.
Для read-only поддержки — `--role operator`. После bootstrap дождаться нового
кода: enrollment step уже использован и повторный код вход не откроет.

## Вход и первая выдача

Запустить API обычной командой из authentication.md либо одобренным systemd
unit. Открыть **свой доверенный HTTPS origin** `/admin/` (loopback HTTP допустим
только для dev/test). Войти отдельным логином, паролем и кодом аутентификатора.
Сессия 10 минут, без refresh/remember me. Reload требует нового входа; browser
password-save не включается кодом. Logout отзывает token. Обычный Telegram
пользователь, даже с DB role admin, административную сессию не получает.

1. Нажать «Инициализировать демонстрационный каталог»: Start 9900 копеек/100 GiB,
   Plus 19900/300 GiB, Extra 2900/10 GiB. Цены примеры, payment API выключен.
2. Найти уже вошедшего пользователя по UUID/Telegram numeric ID; выбрать строку.
   Через эту панель не создаются фиктивные Telegram users и не выдаются OTP.
3. Выбрать «Выдать / продлить / сменить тариф», нужную **версию**, указать причину.
   «Применить» создаёт настоящий subscription/admin_grant и credit/funding lot.
   Пользователь видит выдачу через authenticated `/api/v1/subscriptions`.
   Первая начинается сейчас; последующая основная — после последнего периода.
4. Для компенсации — «Добавить трафик / компенсация», **целое число bytes**,
   понятная причина. Для отзыва выбрать subscription/device UUID из карточки.
   Отзыв устройства инвалидирует связанные user sessions и создаёт durable
   revoke через существующий outbox; это не node removal ACK.

Повтор неизменной формы в пределах текущего входа использует прежний key даже
после успешного ответа; транспортная ошибка не создаёт новый key. Чтобы выполнить
ещё одну действительно новую выдачу, проверить audit и нажать «Начать новую
операцию». Reload теряет RAM ключи форм: после неясного ответа сначала сверить
audit, не отправлять новую выдачу вслепую. API глобально отвергает тот же key
с другим actor/payload. Ключи в UI генерируются crypto.randomUUID, не серверным
счётчиком. Amounts проходят текстовый decimal/BigInt путь без округления JS Number.

## Тарифы, история и состояние

«Создать версию» сохраняет новый immutable revision по тому же code. Существующие
подписки остаются на прежних условиях. Отмена будущей выдачи/отзыв текущей —
immutable событие, не удаление. Addon требует активную основную подписку и
истекает с текущим периодом. Полная политика — ADR-035.

Карточка показывает квоту, последние 20 ledger entries, до 100 подписок и устройств.
Список users — до 50, точный поиск UUID/Telegram ID. API users/audit поддерживает
offset; UI показывает последние 100 audit events. Узлы: status/heartbeat,
занятые назначения, известная v2 capacity=1, текущие unacknowledged и stalled>90s
commands. Superseded commands не считаются текущими. Это не VPN egress test,
нагрузочная оценка ёмкости или DDoS защита.

## Восстановление и ограничения

Backup включает identities/ciphertext/audit, ключ Fernet хранить отдельно. Потеря
ключа лишает возможности расшифровать MFA. Не менять key в env без re-encryption:
простой restart с новым ключом не является ротацией. Safe MFA/key recovery,
отдельная подтверждённая процедура ротации и supervisor/retention — I-027.
После DB restore нельзя включать старые sessions/commands без fencing.
Admin sessions истекают за 10min, но сам restore не доказывает отзыв всех прав.

Аудит успешных изменений append-only; техническая ошибка откатывает и действие,
и audit. Отдельный журнал неуспешных попыток/внешние алерты пока не реализованы;
limiter durable. Нет универсального shell, SQL editor, payment/dev bypass или
удаления проводок. Audit reasons должны содержать только операционную причину,
без credentials, DNS/URL и browsing history.

Реальные секреты bootstrap, trusted HTTPS и весь постоянный deployment требуют
локального участия владельца и разрешения на конкретные remote changes.
# B setup without Telegram credentials (ADR-037)

`scripts/start_closed_admin_setup.ps1` installs explicit closed admin-only config
after approved infrastructure deployment. It opens a private interactive SSH
terminal. Owner enters staging sudo password and a new admin password twice;
the 0600 enrollment URI is displayed only on that terminal for authenticator
import. No terminal recording/screenshots or pasting secrets into chat.
With `--resume`, an existing unconfirmed enrollment is accepted only after the
database confirms that the account is absent, and after checking private file
ownership/mode, no symlink/hardlink, exact issuer/account/algorithm/period/digits
and canonical 20-byte secret. The URI is displayed synchronously by the CLI
with explicit `--show-enrollment-in-terminal`; there is no background viewer.
An already existing account returns `admin_already_exists` without changing it.
Shell failures report only a fixed line number; bootstrap failures fixed stages.
After current-code confirmation, CLI commits `owner` and its audit event, then
removes the URI file. Wait for the next TOTP code and open
`https://85.209.132.205/admin/`. No standard password, bot token or CLI login.
If SSH is interrupted, inspect the exact private enrollment file locally;
do not blindly recreate accounts or rotate the encryption key.

Until real auth integration is provisioned, `VPN_CLOSED_ADMIN_ONLY=true`,
`VPN_ENABLE_AUTH=false`: user endpoints return 503. Readiness checks the DB and
schema, not user-service completeness. To switch, provision actual auth secrets,
disable the setup flag and enable auth atomically; contradictory flags fail.

