# I-025 — физический Android, 2026-10-23

Pixel 8 Pro, Android API 37; устройство подключено владельцем с прямым запросом
«тестируй». Два отдельных локальных физических набора успешно завершены.
Это **не полное закрытие I-025**: реальный Android v3 dataplane пока не проверен.

## 1. Настоящий native/Keystore, process lifecycle и permission

`docs/evidence/i-025-native-android.json`: **passed=true**, 10 фаз, 11 фактов.
Runner — `apps/client/android/app/src/androidTest/.../I025DeviceTests.java`,
отдельный instrumentation APK, отсутствующий в production source set.

- Настоящий AndroidKeyStore AES-GCM encrypt/decrypt; одинаковая запись даёт
  разный ciphertext. В проверенных ciphertext нет тестового plaintext marker
  или encoded device private key. Это ограниченная проверка этих файлов,
  не memory-forensics scan и не доказательство hardware/StrongBox backing.
- Backup flag выключен, records в noBackupFilesDir. Ключ identity сохраняется
  между реальными разными PID; native VPN сам не возобновляется.
- Синтетическая **TEST ONLY, не credential** session marker и split settings
  переживают process restart. Удаление session record переживает новый процесс.
- Повреждённый authentication tag session record отклонён. Повреждение device
  ciphertext оставило native unavailable, не заменило identity новым ключом.
  После восстановления исходного ciphertext исходная identity вернулась.
- Настоящий системный VPN dialog показан и отменён через Back. Разрешение
  не выдано, native tunnel не поднят. Это не permission revoke активного VPN.

Destructive corruption выполнялась только в **новой тестовой установке**.
До начала `org.example.vpnclient` и её test package отсутствовали. Runner
отказывается удалять/перезаписывать уже установленный клиент. Существующий
`org.example.vpnpoc` сохранён. После тестов созданная тестовая установка с
искажёнными записями удалена, чистый ordinary APK установлен обратно, test APK
удалён; Private DNS mode остался `opportunistic`.

Сборка runner: `assembleDebugAndroidTest` passed. Первый compile нашёл
недоступные в Android SDK Java `Files.readString/writeString`: заменены на
byte APIs; повторная сборка и весь физический набор прошли. Runtime клиента
не менялся ради теста.

## 2. Flutter Api + настоящий API/PostgreSQL + Android vault

`docs/evidence/i-025-session-android.json`: **passed=true**.
Pytest orchestrator: **1 passed in 68.84s**. Flutter entrypoint:
`apps/client/integration_test/i025_session_test.dart`; orchestration:
`services/api/tests/test_i025_android.py` и штатный disposable PostgreSQL helper.

- Реальный auth start/verify, device registration с настоящим native public key.
  OTP sender — **Telegram TEST FAKE ONLY**, Telegram/webhook не менялись.
- Настоящие refresh credentials сохраняются в Android vault. После закрытия
  Dart Api object новый Api читает сохранённый refresh, сервер его ротирует,
  возвращает рабочий access; новый refresh отличается от предыдущего. Public
  device key не меняется, user-scoped quota API доступен.
- Logout отзывает family в настоящей PostgreSQL и очищает durable session;
  повторное создание Api не восстанавливает вход.
- При втором login middleware **после успешного server refresh transaction**
  заменяет ответ на 503. Клиент остаётся неаутентифицированным, vault session
  пуст; следующая попытка restore не повторяет потерянный refresh. В БД ровно
  две rotation и две family, одна family отозвана logout. Потерянная новая
  credential второй family не считается отозванной: это fail-closed клиента,
  не автоматическое server revocation при сетевой потере.
- В БД нет Tunnel/PeerConnection: ни один набор не заявляет работающий VPN.

Наборы **композиционные**: разные Android PID проверены с synthetic noncredential
native marker; реальный auth refresh проверен с новым Dart Api object в одном
процессе Flutter. Это не единый real-account relaunch acceptance; он остаётся
в полной подготовленной staging матрице вместе с VPN lifecycle.

API слушал только `127.0.0.1:18766`, телефон обращался через временный USB ADB
reverse. Test-only OTP/fact/drop-reply routes существуют только в pytest app,
не в production API. Сырые Flutter/Android outputs с потенциальными credentials
не выводились. В receipts только operational facts, PID и состояния.

## Проверки и cleanup

- Текущий Flutter analyze — passed, включая новый integration entrypoint.
- Ruff format/check всего checkout — passed (286 Python files formatted);
  полный mypy — passed (148 source files), новые runner/test включены.
- Без physical opt-in новый pytest корректно пропущен (1 skipped), не запускает
  телефон/API по умолчанию. Public OpenAPI/internal exports current.
- PowerShell launcher parser и `git diff --check` — passed.
- Новых dependencies, DB migrations или public API changes нет.
- Временный PostgreSQL остановлен и его cluster удалён штатным helper.
- API thread остановлен; независимый check: listener 18766 отсутствует,
  local postgres processes = 0, ADB reverse отсутствует.
- Ordinary APK восстановлен, test APK отсутствует, прежний POC сохранён.
  Это debug APK без настоящего ApiOrigin: он показывает setup, не готовый
  подключаемый сервис. Device/session private records не экспортировались.

Запуск: `scripts/run_i025_android.ps1` (fresh native installation), затем
`scripts/run_i025_android.ps1 -Session` в течение 30 минут после native receipt.
Можно явно задать `-Serial` и `-ClientDirectory`; по умолчанию выбирается готовый
local snapshot и проверяются hashes Dart/native source. `-BuildOnly` не меняет
телефон. Существующее приложение с аккаунтом нельзя очищать ради повтора.

Первый session запуск выполнен эквивалентными отдельными шагами: locked offline
pub, analyze, build test APK; env `VPN_I025_LOCAL_ANDROID=1`, выбранный serial/
client dir и `PYTEST_ADDOPTS=services/api/tests/test_i025_android.py -q`, затем
`scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin`.
Локальный log: `.cache/i025-session-run.log`.

## Что препятствует полному закрытию

Read-only SSH preflight двух существующих staging hosts: дополнительный `ls -ld`
подтвердил **ENOENT** для самих `/run/detourvpn-node` и `/opt/detourvpn-node`
на обоих хостах (не просто недоступность private subdirectory). Поэтому fixed
v3 slots/current release отсутствуют. Запрошенный node unit на B inactive;
API/БД/Caddy B active. Receipt: `evidence/i-025-staging-preflight.json`. Содержимое secrets, env, журналов и
пользовательской БД не читалось/не выводилось. SSH checks не меняли серверы.
Эта ограниченная проверка не заменяет полный rollout baseline.

Для remaining acceptance нужен подготовленный разрешённый v3 staging по
`runbooks/peer-windows-v3.md`: fixed slot/netns/core, закрытый kernel gate,
supervision, route/NAT isolation, authenticated agent ingress, manifest и funding.
Это отдельная граница I-028, не автоматически разрешённый server rollout из
фразы «подключил телефон, тестируй».

Остаются открыты: Android sustained real v3 renewal/egress, real account process
relaunch, background/screen-off/Doze, Wi-Fi/mobile, API/agent outage с kernel cutoff,
Disconnect против реального renewal, активный system permission revoke,
DNS/Private DNS/dual-stack IPv6 и реальная split/shared-UID/lockdown маршрутизация.
Сохранённые split settings не являются доказательством маршрутизации приложений.
V2 runner намеренно не запускался как подмена v3 acceptance.
