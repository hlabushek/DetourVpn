# ADR-025: Flutter connect client over I-012

Status: accepted for implementation, 2026-10-20. Scope: I-013 only.

Flutter-приложение находится в `apps/client`, отдельно от I-011 instrumentation
harness. Java bridge использует существующие `AwgProviderV1`, `Profile`,
`KeyConfirmation`, `TunnelProviderV1` как исходники, без копии SDK/криптографии.
Идентификатор приложения отдельный; одновременно два тестовых VPN не запускать.
Нативный bridge сохраняет Java-интероп принятого I-011 POC вместо переноса
проверенного adapter на Kotlin в этой issue. Kotlin plugin нужен сборке Flutter;
Kotlin migration не меняет поведение и не является условием технического I-013.

API I-012 пока допускает один явно настроенный staging endpoint и lease до 60s.
Публичного каталога регионов нет: UI честно показывает «Тестовый узел I-012»;
выбор региона, latency, автоматическое продление и failover не имитируются.
История/полный quota UI относятся к I-014. Здесь только available bytes и время
последнего успешного чтения; при ошибке значение явно устаревшее.

Вход использует существующие auth/start и auth/verify (Telegram code), а не
ручной ввод bearer token. Access/refresh/verifier хранятся только в RAM.
После process death требуется новый вход; ключ также ephemeral. Refresh
сериализован, на неоднозначный ответ refresh нет слепого повтора с тем же token.
При новом входе старое device registration требует явного отзыва через
существующие account tools при достижении лимита устройств. Persistent keystore
и recovery/account-management UX не добавляются в эту issue.

Private key создаётся только native runtime и не проходит в Dart/HTTP. Channel v1
разрешает publicKey, prove, permission, connect, disconnect, snapshot и открытие
строго проверенного HTTPS t.me login URL. Profile и proof не логируются/не пишутся
на диск. Release не содержит тестовых credentials, mocks или HTTP bypass.
Debug-only loopback HTTP нужен только для ADB reverse; HTTPS проверяет системную
цепочку сертификатов, redirect запрещён. API origin задаёт сборка, не сервер.

Подключение: объяснение → действие пользователя → системное VPN permission →
registration → key challenge → idempotent tunnel request → applied ACK/config →
native connect. UP без свежего handshake не означает connected. Состояние также
учитывает underlying validated network, API revoke и lease expiry. Connected
означает свежий handshake и active lease, не проверенный egress/DNS. После потери
сети UI показывает reconnecting; новый peer автоматически не создаётся.

Connect сериализован. Disconnect отменяет ожидающее permission/activation и
сначала выключает local VPN, затем запрашивает remote revoke. Неоднозначный
create повторяется с тем же idempotency key; пока исход не установлен, новый
lease не создаётся. Remote revoke_pending явно отображается и повторно
сверяется; HTTP success не приравнивается к node removal/settlement.

Native monotonic lease timer работает независимо от Flutter screen/background;
срок ограничен 60s, считается консервативно от начала create запроса и expiry
config. Timer не служит hard security gate при suspend: удалённый kernel cutoff
I-012 остаётся обязательным. При process death ключ/сессия теряются, remote peer
закрывается своим неизменяемым lease; restart не восстанавливает старый туннель.

Physical acceptance нового Flutter APK проведён отдельно от I-012 на основании
явного bounded разрешения пользователя: `docs/evidence/i-013-staging.json`,
2026-10-21, `passed=true`. Проверены Flutter/native handshake/egress, background,
Disconnect/restore, API revoke, expiry и cleanup; Telegram delivery — TEST FAKE
ONLY. Это три отдельных lease до 60s/1 MiB на одном staging-узле. DNS/IPv6 и
смена Wi-Fi/mobile нового APK не проверены. Новые изменения VPS требуют своего
bounded разрешения. Public release/licence/SBOM gates открыты.

References: [Flutter platform channels](https://docs.flutter.dev/platform-integration/platform-channels),
ADR-019/022/024. Flutter (BSD-3-Clause) — значимая новая runtime dependency;
версия SDK и SHA256 фиксируются в `apps/client/toolchain.json`, Dart зависимости
в pubspec.lock. SDK maintained upstream Flutter; лицензии AWG остаются прежними.

Уточнение 2026-10-21: Flutter engine coordinates `io.flutter:*` исключены из
проверки присутствия Gradle lockfile, потому что Flutter выбирает ABI по телефону
(arm64) либо по сборке (arm64+x86_64). Их версии остаются точными engine-hash
coordinates из закреплённого SDK/archive SHA256. Остальные Android зависимости
сохраняют locking. Перед выдачей staging grant runner собирает именно device/test
target, чтобы ошибка локальной сборки не расходовала привилегированное окно.
