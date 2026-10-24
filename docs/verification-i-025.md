# I-025 — Android, локальная реализация и проверки

2026-10-23. Телефон подключён; native и local API/session physical checks прошли.
Полный v3 dataplane acceptance открыт. Реализация подготовлена в ветке
`codex/i025-android-windows`; локальные проверки завершены, physical acceptance не выполнен. Изменения I-024,
уже находившиеся в checkout, сохранены; backend/DB в I-025 не менялись.

## Реализовано

- `apps/client/android/app/src/main/java/org/example/vpnpoc/`: `SecureVault`
  (AndroidKeyStore AES-GCM + backup-excluded AtomicFiles), persistent local
  X25519 identity, session bridge, `WindowDeadline` monotonic fencing,
  `ClientProfile` и локальные app exclusions; existing native SDK не обновлялся.
- `apps/client/lib/{api,session_store,main}.dart`: origin-bound refresh,
  server validation на startup, durable consume-before-refresh, epoch fencing,
  logout erase и ошибки storage. Access token остаётся в RAM.
- `window_connection.dart`, `native_tunnel.dart`: явно выбранный v3 controller,
  create/renew idempotency, active ACK, stable config/runtime/slot/device binding,
  conservative deadline, native-first Disconnect и restart reconciliation.
- `split_apps.dart`, `connection_screen.dart`, `connection.dart`: локальные
  исключения с direct-network disclosure, выбор узла и сообщения v3, описание
  outer UDP meter/unknown holds. DNS IPv4 идёт через VPN profile; IPv6 внутри
  included apps блокируется по retained parser/SDK policy. Физически не доказано.
- `scripts/build_flutter_client.ps1`: opt-in `-WindowTunnels`, без fallback v2.
  Добавлены Flutter session/controller/split tests и JVM parser/deadline tests;
  в прежнем physical harness добавлен await изменённого async session метода.

## Фактические результаты

| Проверка | Результат и граница |
|---|---|
| Final local snapshot | `9261ecea2e89433a8325699c8db493a6`, повторно синхронизирован; SHA-256 всех 31 исходных файлов совпали с checkout |
| Locked pub + Dart format/analyze | Passed, no issues |
| Final Flutter tests | **70 passed**, fake API/native/storage/platform channel, не physical VPN |
| Android JVM unit tests | **17 passed**: Profile 4, KeyConfirmation 3, TunnelStatus 3, ClientProfile 3, WindowDeadline 4 |
| Android lint | Passed; Gradle `BUILD SUCCESSFUL`, 121 tasks |
| APK debug и unsigned release | Built; release 43.0 MB. Без фактического ApiOrigin — setup screen, не подключаемая сборка сервиса |
| Final analyze | Passed, no issues, финальный синхронизированный snapshot |
| Final native/build повтор | **Passed**, 17 JVM tests, lint, debug и unsigned release; export выполнен, pub lock не изменился |
| APK zip audit | Нет перечисленных secret file extensions и test/integration_test asset paths; это ограниченная проверка состава ZIP, не полный secret scan |
| Ruff check/format | Passed; 282 Python files already formatted |
| OpenAPI/internal schema exports | Current; контракты API не менялись |
| UI preview | Существующий credential-free fake shell/profile визуально просмотрен; это не physical screenshot и не split-routing acceptance |

Первый полный snapshot дал 64 Flutter tests и обе сборки. Дополнения после него
(device binding, storage/expiry/down/split negatives и erasure при failed SDK init)
проверены финальным повтором: 70 tests, Android tests/lint/build — passed.
Из-за OS error 112 (C: full) одна промежуточная попытка была остановлена;
после освобождения временного compiler workspace места стало достаточно.
Использован existing successful snapshot без новой полной копии. Ошибка не
считалась passed и не осталась открытым acceptance gate.

Локальные логи: `.cache/i025-build.log`, `.cache/i025-final-build.log` (неудачная
попытка), `.cache/i025-rebuild.log` (финальные сборки). Финальные Flutter результаты
зафиксированы tool output и receipt `docs/evidence/i-025-local.json`. Первая попытка
direct analyze в OneDrive завершилась ошибкой LSP JSON transport; ASCII local
snapshot прошёл analyze. Тесты и сборки выполнены на одинаковых исходниках.

Полный Python/PostgreSQL regression I-024 не повторялся: Python/backend/DB в этом
issue не менялись; прежние 719 results не выдаются за новый прогон I-025.
Новые библиотеки не добавлены, dependency locks и native versions сохранены.
Остаются прежние Gradle/AGP/Kotlin/NDK/deprecation warnings; их обновление вне scope.

## Открытые проверки и ограничения

Владелец отдельно разрешил очистку ненужных файлов проекта. Аудированы только
reproducible generated builds: три snapshot этой задачи и intermediates/tmp/
test cache workspace, с сохранением актуальных APK/исходников/settings/DB/env.
Автоматическая проверка отклонила удаление и после явного разрешения с причиной
`blocked by policy`; более подробной причины инструмент не сообщил. Удаление
не выполнялось и не обходилось другим механизмом. На финальном read-only check
C: имел 11 185 528 832 свободных bytes; disk blocker для I-025 снят.

Физические native Keystore/GCM persistence/corruption, разные PID, session erasure,
сохранение split settings и отмена системного permission прошли. Настоящие local
Flutter/API/PostgreSQL refresh/logout/lost-response checks прошли отдельно.
Полный отчёт и точные границы: [physical verification](verification-i-025-physical.md),
receipts `i-025-native-android.json` (10 фаз/11 фактов), `i-025-session-android.json`
(1 passed / 68.84s). Real-account process relaunch, VPN egress/sustained renewal,
Doze/background, Wi-Fi/mobile, active permission revoke, DNS/IPv6 и actual split
routing остаются **pending prepared v3 staging**; оба узла без v3 slots.
Flutter/native timers не гарантируют wakeup в deep Doze; node kernel cutoff
остаётся authoritative. 16 MiB могут исчерпаться раньше time-based renewal.
Нет always-on kill switch; исключённые apps и control plane идут напрямую.
X25519 доступен engine в RAM, hardware protection/zeroization не обещаются.

Ручные шаги/матрица: [runbook](runbooks/android-windows-v3.md).
Решения/риски: [ADR-040](adr/040-android-window-lifecycle.md).
I-026/I-027 не начаты; I-028 rollout остаётся отдельным разрешаемым этапом.

Рекомендуемый следующий отдельный issue — I-026; здесь он не начат.
