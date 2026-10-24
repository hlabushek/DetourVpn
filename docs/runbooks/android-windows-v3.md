# I-025 — локальный Android v3 и отложенная физическая проверка

## Локальная сборка

Из корня checkout, PowerShell:

```powershell
./scripts/build_flutter_client.ps1 -WindowTunnels
```

Скрипт создаёт отдельный local snapshot вне OneDrive, проверяет locked pub,
Dart format/analyze/tests, Android unit/lint, debug и unsigned release. APK:
`apps/client/build/app/outputs/flutter-apk/app-debug.apk` и
`apps/client/build/app/outputs/apk/release/app-release-unsigned.apk`.
Сборка без ApiOrigin показывает экран настройки; это не готовый login/VPN.
Для подготовленного тестового HTTPS API:

```powershell
./scripts/build_flutter_client.ps1 -WindowTunnels -ApiOrigin 'https://staging.example.invalid'
```

Адрес выше — placeholder. Не используйте production или непроверенный origin.
Настоящий HTTPS сертификат проверяется штатным trust store, redirect запрещён.
Loopback HTTP допустим только в debug на 127.0.0.1:18766.
Без `-WindowTunnels` остаётся старый bounded v2 controller; v3 на v2 не откатывается.
Не запускайте прежний v2 staging runner как доказательство v3.

## Перед физической проверкой

Телефон подключён 2026-10-23. Native и local-session наборы прошли;
[фактические результаты](../verification-i-025-physical.md). Для полного VPN прогона:

1. Проверить согласованный read-only baseline API/agent/узлов и отдельное
   разрешение на конкретный staging rollout I-028. Подготовка Linux v3 описана
   в `peer-windows-v3.md`; существующие v2 endpoints не заменять автоматически.
2. Подготовить тестовую session/device/subscription с реальной funded квотой и
   trust-ready v3 slot; Telegram fake delivery помечать TEST ONLY. Настоящий
   Telegram relay принадлежит I-026. Не менять webhook ради этого теста.
3. Собрать APK с фактическим разрешённым origin. Проверить serial через
   `adb devices`, установить только на выбранный телефон. Не очищать app data,
   не удалять прежнее приложение и не перезаписывать чужие сессии автоматически.
4. Не снимать скриншоты кодов, токенов или конфигов. FLAG_SECURE сохраняется.
   Receipts содержат только факты, времена, operational IDs и aggregate counters.

## Обязательная матрица и факты receipt

| Сценарий | Проверяемый факт |
|---|---|
| Вход → остановка процесса → запуск | Тот же public device ID/key, server refresh, ручное подключение; приватный ключ не выводить |
| Logout → процесс restart | Аккаунт не восстанавливается; ciphertext session отсутствует, старые refresh отозваны при доступном API |
| Потеря ответа refresh | Повторный вход; старый refresh не отправляется второй раз |
| Повреждение ciphertext/недоступный Keystore | Отказ без подмены device key, без plaintext fallback; только в отдельной disposable test-installation |
| Передача >180s | Несколько active ACK/renewals, стабильные key/slot/address и свежий handshake/egress; реальные measured receipts/ledger |
| Background/foreground, screen off/Doze | Измерить renewal/expiry, возврат UI согласован с native; не объявлять успех из-за одного значка VPN |
| Wi-Fi → mobile → Wi-Fi | Свежий HTTPS egress/handshake, точный сетевой статус; штатное восстановление или честная остановка |
| Недоступный API/agent | Только исходный bounded доступ, реальное kernel expiry/budget, отсутствие оживления от позднего ответа |
| Disconnect во время renew, permission revoke | Native down, подтверждённое remote closure, поздний ACK не поднимает VPN |
| Default route/DNS/Private DNS | Included app использует VPN egress; DNS leak test с внешним контролируемым resolver; OS Private DNS отдельно |
| IPv6 на dual-stack сети | Included app не получает IPv6 напрямую; успешный IPv4 tunnel. IPv6 support не заявлять |
| Split apps | Один included app — VPN, один исключённый — direct IPv4/IPv6/DNS; настройки переживают restart; remote список приложений отсутствует |
| Android lockdown/shared UID | Замерить фактическое поведение исключений и direct control channel; при блокировке не обходить системную политику |
| Cleanup | Закрытые peers/gates, settled measured windows, отдельно unresolved holds, обычная сеть после Disconnect |

Для DNS нельзя считать внутренний parser unit test тестом отсутствия утечек.
Не записывать DNS/URL/payload трафик в control plane. Измерения выполняются
отдельными добровольными запросами к согласованным test endpoints, не сбором
пользовательской истории. Установленный системный VPN не равен working egress.

## Эксплуатационные ограничения

Device/session records шифруются AES-GCM в noBackupFilesDir. X25519 хранится в
памяти upstream engine во время работы; hardware backing не обещается. После
app-data reset старое устройство требует отдельного revoke. Session vault
привязан к origin: смена адреса требует нового входа. Потерянный refresh ответ
закрывает сессию; это осознанный fail-closed компромисс.

Клиент резервирует 16 MiB за окно и начинает renewal при остатке <=55s.
Быстрый трафик способен исчерпать budget раньше. Flutter и native timers не
дают гарантии работы в deep Doze; доступ ограничен node kernel deadline.
Нет always-on kill switch: после Disconnect/expiry обычная сеть возвращается.
Исключённые приложения и служебный API DetourVpn идут напрямую. DNS/IPv6 policy
касается included apps; внутри VPN IPv6 блокируется, а не туннелируется.
При потере counters hold остаётся для операторской сверки; клиент не списывает
его как угаданный usage и не обещает автоматического возврата.


## Уже выполненные локальные physical наборы

PowerShell 7, из корня checkout:

```powershell
./scripts/run_i025_android.ps1 -Serial '<selected-device-serial>'
./scripts/run_i025_android.ps1 -Session -Serial '<selected-device-serial>'
```

Первый runner требует отсутствия обычного/test package до установки и не удаляет
существующий аккаунт. После него восстанавливается чистый ordinary APK; session
runner требует успешный native receipt не старше 30 минут и отсутствие session
ciphertext. Для повтора при установленном клиенте сначала отдельно проверьте,
что это disposable test installation; не очищайте настоящие данные автоматически.
`-ClientDirectory` задаёт готовый ASCII snapshot; `-BuildOnly` только компилирует.
Native runner проверяет marker/process lifecycle/permission без WAN. Session
runner использует временные real PostgreSQL/API и TEST FAKE Telegram delivery,
не VPN. Test APK после проверки удаляется, API/cluster/ADB reverse очищаются.
