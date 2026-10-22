# ADR-002: Android SDK и граница нативного адаптера

Status: **Proposed**  
Updated: 2026-10-18 / I-002  
Verdict: исходный кандидат выбран для build spike; **NO-GO для выпуска/интеграции**.

## Предлагаемое решение и сравнение

Flutter UI + Kotlin bridge + upstream userspace GoBackend через Android VpnService.
Не требовать root, не использовать сторонний непрозрачный Flutter VPN plugin.
Открытая или закрытая модель нашего клиента пока не выбрана владельцем (2026-10-18).
Выбор SDK не является выбором лицензии всего продукта.

| Вариант | Достоинства | Ограничения / результат |
|---|---|---|
| AWG Android source SDK + Kotlin bridge | Контроль key lifecycle и явный adapter; соответствует кандидату ТЗ | Нужен контролируемый build, полный licence inventory и устранение toolchain blockers; основной кандидат |
| WireGuard Android tunnel library | Upstream отдельно описывает embedding | Reference для сравнения; не поддерживает AWG-specific config. Его native bundle тоже требует проверки |
| Форк полного AmneziaVPN | Готовые integration patterns | GPL-3.0 и больший maintenance scope; не выбран |
| Сторонний Flutter plugin | Быстрый UI prototype | Нет проверенного pinned artefact/dependency closure; не выбран |

## Точный кандидат

- Repository: `amnezia-vpn/amneziawg-android`.
- Commit: `ff15093bf3856fea0947923474870b86efb682f0`.
- Upstream versionName: `3.1.4` (метка исходного Android проекта, не подтверждённый AAR).
- Embedded AWG module: `github.com/amnezia-vpn/amneziawg-go/v3 v3.1.20260814`,
  commit `1b86b2ae0e493e7ea93f8c1a0f0cb6735b1551f1`.
- SDK settings: minSdk 24, compileSdk 36, Java 17, AGP 8.13.0, Gradle 8.13,
  NDK 26.1.10909125, build-tools 36.0.0. Это source declarations, не build pass.
- План ABIs: arm64-v8a основной, armeabi-v7a после отдельной проверки,
  x86_64 для emulator smoke; точный AAR/APK inventory ещё не получен.

Полные ссылки, source SHA-256 и module ZIP SHA-256: [source lock](../evidence/i-002-sources.json).
SDK бинарники не собраны и не добавлены в проект.

## Native/license boundary

Root COPYING — Apache-2.0, Go engine LICENSE — MIT. Стандартный tunnel build
также выбирает native executables. `wg-quick/android.c` помечен GPL-2.0;
ряд tools файлов dual-licensed, netlink.h — LGPL-2.1-or-later, есть файлы без SPDX.
Поэтому весь AAR нельзя назвать просто Apache-2.0.
[Checklist](../licensing-checklist.md) отделяет runtime, build tools и transitive modules.
Это не вывод, что GPL автоматически распространяется на весь Flutter UI: linking,
агрегацию и обязательства оценивают по конкретному распространяемому артефакту.

Предпочтительный build spike — GoBackend-only packaging: исключить root backend,
wg/wg-quick executables и неиспользуемые classes/manifest entries, проверить итоговый
AAR/APK и dependency report. Это план контролируемого patch, он **не реализован**.
Альтернатива — сохранить upstream bundle с полностью выполненными licensing/source
obligations после решения владельца. Нельзя удалить COPYING и считать вопрос закрытым.

## Границы приложения и ключей

TunnelProvider v1: connect/disconnect/get_status/network_changed, capabilities/version,
асинхронные native state events. Connected требует фактического native state; для
заявления рабочего VPN дополнительно I-011 egress/DNS tests. Config parse mismatch
завершает подключение с понятной ошибкой, не игнорирует неизвестные параметры.

Генерировать device private key средствами upstream на устройстве. В backend только
public key; Flutter получает key handle/status, без private key в MethodChannel/logs.
Go backend всё равно использует ключ в памяти: Android Keystore не превращает такой
сырой X25519 ключ в гарантированно non-exportable hardware key. Хранить за проверенным
Keystore-backed encryption at rest, исключить secrets из Android backup/diagnostics.
HeaderProtectionKey — отдельный секрет transport config, тоже запрещён в логах.

VpnService permission, foreground lifecycle, IPv6/DNS routing и reconnect реализуются
в native слое; Android always-on/lockdown проверяется отдельно. Нельзя обещать kill
switch без реального теста ОС. Upstream debug/error logging требует review и release
policy до реальных пользователей, особенно имена туннелей/endpoint host metadata.

## Условия Accepted

1. Устранить Go/toolchain discrepancy, закрепить download hashes, Maven resolution
   и native build inputs; получить воспроизводимую сборку в отдельной задаче.
2. Проверить licence/NOTICE/per-file exceptions и фактический список AAR libraries.
3. Владелец утверждает boundary и выбранный способ выполнения obligations.
4. Разобрать advisory findings из [OSV receipt](../evidence/i-002-go-advisories.json)
   для выбранных packages и server build; подтвердить устранение достижимых issues.
   Module-level scan обнаружил записи у 6 из 27 запрошенных модулей (26 dependencies
   плюс core). Это не число независимых vulnerabilities и не runtime verdict.

На текущем Windows host найдена Java 8; Go/Flutter/Gradle в PATH не найдены.
Native build здесь не выполнялся и не объявляется успешным.
План воспроизведения и проверки I-011: [матрица](../compatibility-matrix.md).

Первичные доказательства (immutable revision):
[SDK settings](https://github.com/amnezia-vpn/amneziawg-android/blob/ff15093bf3856fea0947923474870b86efb682f0/settings.gradle.kts),
[Gradle native targets](https://github.com/amnezia-vpn/amneziawg-android/blob/ff15093bf3856fea0947923474870b86efb682f0/tunnel/build.gradle.kts),
[CMake](https://github.com/amnezia-vpn/amneziawg-android/blob/ff15093bf3856fea0947923474870b86efb682f0/tunnel/tools/CMakeLists.txt),
[Android VPN](https://developer.android.com/develop/connectivity/vpn).
