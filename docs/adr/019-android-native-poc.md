# ADR-019: ограниченный Android POC I-011

Status: Accepted for local engineering spike only, 2026-10-19.

I-010 принят в границе ADR-018. Следующий issue I-011 реализует native Android
test harness, без Flutter, публичного API provisioning и production release.
Источник SDK/core сохраняется из ADR-002/003. Go 1.25.14 совпадает с принятым
staging binary; Android использует NDK 26.1.10909125. GOTOOLCHAIN=local.
Существующий runtime patch CLOCK_BOOTTIME должен применяться без rejected hunks.

## Ограничения POC

- Только GoBackend: отдельный Gradle module выбирает Java config/crypto/backend
  classes, без AwgQuickBackend, RootShell, ToolsInstaller и tools executables.
  В APK только libwg-go.so для arm64-v8a и x86_64. Нет custom crypto.
- Private key создаёт upstream KeyPair на телефоне. Ключ только в памяти процесса,
  без файлов, backup, Intent extras, clipboard, сервера и диагностических логов.
  Перезапуск процесса требует нового staging peer. Persistent encrypted key store
  относится к следующему полноценному клиенту, а не имитируется в POC.
- Внешний profile не может содержать PrivateKey, принимает ровно один peer,
  literal IPv4 endpoint, полный IPv4 route и numeric IPv4 DNS. Per-app exclusions
  запрещены. При активном single-default-route SDK не вызывает allowFamily IPv6:
  ОС должна блокировать IPv6. Это требует device leak test, не обещание kill switch.
- После disconnect/crash обычная сеть может восстановиться. Always-on отключён,
  foreground notification сообщает о работающем test service; service START_NOT_STICKY.
- Все JNI операции сериализованы; lifecycle cleanup защищён backend monitor.
  Отключён upstream polling thread, статус читается нашей очередью. Native UP,
  свежий handshake и проверенный egress имеют разные значения в интерфейсе.
- Java logs удалены из подготовленного SDK, Go device logger silent; UAPI socket
  и SIGUSR2 logcat dumps исключены. Ошибки в UI только фиксированными сообщениями.
- Отдельный reviewed patch/receipt и wrapper checksum. Исходный SDK не изменяется.
  Maven verification metadata и зависимости фиксируются перед build acceptance.

## Граница удалённой проверки

Существующее разрешение I-010 не означает автоматическое открытие WAN порта.
Сначала собрать APK и проверить на телефоне permission/packaging/lifecycle.
До staging peer подготовить свежий read-only network preflight и конкретный
bounded endpoint/config/rollback diff. Не менять существующие контейнеры/туннели.
Никаких production credentials или backend private keys.

I-011 завершён только после physical connect/disconnect, измеренного нового
egress IP, DNS/IPv6, отказа permission, network switch и реального peer revoke.
Сборка APK сама по себе не закрывает I-011, licensing/release gate ADR-002 и I-012.

Руководство Android VpnService:
https://developer.android.com/develop/connectivity/vpn
