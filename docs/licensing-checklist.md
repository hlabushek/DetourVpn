# I-002: лицензионный checklist

Проверено: 2026-10-18. Ответственный за инвентаризацию — Codex; за модель
распространения и принятие обязательств — владелец проекта; спорные условия
перед выпуском проверяются специалистом по лицензированию.
Владелец пока не выбрал открытый/закрытый клиент. Лицензия нашего кода не менялась.

Это инженерный checklist, а не разрешение на распространение. Source hashes:
[i-002-sources.json](evidence/i-002-sources.json). Здесь нет vendored библиотек.

## Компоненты выбранной source-цепочки

| Компонент / revision | SPDX / доказательство | Роль и linking | Обязательства / результат |
|---|---|---|---|
| AWG Android `ff15093bf3856fea0947923474870b86efb682f0` | Apache-2.0, COPYING и file headers | Java/JNI/Go bridge в AAR; выбран исходный кандидат | Сохранить license/copyright/применимые NOTICE и отметить изменения; root license недостаточна для native bundle |
| AWG Go `1b86b2ae0e493e7ea93f8c1a0f0cb6735b1551f1`, tag `v3.1.20260814` | MIT, LICENSE | CGO shared library; тот же core предлагается серверу | Copyright/license должны сопровождать распространение; per-file и linked transitive inventory ещё не закрыты |
| AWG tools `ee0f0a9aa34ff0a0da4b3433b9512781cfe02843` | COPYING GPL v2; file headers разнородны | Стандартный CMake SDK включает executables | Нельзя выдать bundle за полностью permissive. Требуется либо проверенный Go-only artefact, либо исполнение всех obligations |
| `src/wg-quick/android.c` этого tools commit | Upstream `GPL-2.0` | Источник native `wg-quick` executable | Отметить precise grant/исходники/инструкции распространения; не объявлять автоматически GPL всего UI |
| Tools `src/*.c/*.h` | Часто `GPL-2.0 OR MIT`; `netlink.h` — `LGPL-2.1+`; часть файлов без SPDX | Source files/preprocessor paths зависят от платформы | Проверены headers 33 файлов, это не завершённый per-file clearance; включённые Linux/UAPI headers отдельно проверить |
| `termux-elf-cleaner@7efc05090675ec6161b7def862728086a26c3b1f` | COPYING GPL v3; точный per-file grant не закрыт | Host build tool; не равен runtime-библиотеке APK | Не переносить copyleft автоматически на очищенный output. При распространении самого build tool/source соблюдать его условия |
| AndroidX annotation 1.7.1, collection 1.4.0 | License metadata будущих resolved POM/AAR ещё не проверено | Прямые implementation deps tunnel module | Получить Gradle dependency report, артефакты/hashes/NOTICE и полный transitive graph до сборки для выпуска |
| jsr305 3.0.2, JUnit 4.13.2 | Пока только declarations version catalog | compileOnly/test соответственно | Не считать runtime без inventory; проверить licenses в build/release tooling scope |
| Android SDK/NDK, JDK, Gradle/AGP, Kotlin, Go runtime | Каждый имеет отдельные upstream условия | Toolchain; runtime portions могут попадать в APK | Утвердить версии/источники/checksums и bundled runtime notices; Android SDK terms требуют отдельного принятия владельцем |

Upstream SPDX `GPL-2.0`, `GPL-3.0`, `LGPL-2.1+` здесь сохранены как написаны;
нормализацию only/or-later нельзя делать по одной корневой COPYING.
API/header включения, ABI и compile flags могут менять реальный состав.

## Go transitive inventory

[i-002-go-dependencies.json](evidence/i-002-go-dependencies.json) содержит все **26**
модулей из require-блоков pinned AWG go.mod: точные версии, immutable proxy URL,
SHA-256 ZIP, hashes root LICENSE/NOTICE и кандидатный SPDX.
Root license text проверен, исключения каждого файла и фактический package graph
ещё не проверены; поле `status` специально не означает clearance.

| Root license | Declared modules |
|---|---|
| MIT | goccy/go-yaml; uber-go/atomic; wintun; slim-sprig; ginkgo/v2; qpack; quic-go; testify |
| BSD-2-Clause | gorilla/websocket |
| BSD-3-Clause | golang.org/x/crypto, net, sys, exp, mobile, mod, sync, text, time, tools |
| Apache-2.0 | outline sdk и sdk/x; gvisor; google/btree; google/pprof; go-shadowsocks2; uber-go/mock |

Это declared module graph, **не SBOM готового APK**. Например Windows wintun
и test modules могут не попасть в Android binary; доказать отсутствие может
только build-selected package/dependency inventory. Go module sum h1 и ZIP SHA-256
имеют разные форматы; h1 из SDK go.sum записан, но независимая sumdb verification
в I-002 не выполнялась. Одних совпавших hashes недостаточно для доверия upstream.

## Альтернативы и авторизация

| Кандидат | Source / license | Решение |
|---|---|---|
| WireGuard Android | `e7b3a3c118836e112620b1302a8ba1873ad4daac`, COPYING Apache-2.0 | Reference; похожая упаковка native tools тоже требует полного inventory |
| Embedded WireGuard Go | `f333402bd9cbe0f3eeb02507bd14e23d7d639280`, LICENSE MIT | Reference exact core; нельзя подменять его текущим master |
| Xray-core | `92ccc939aaf4f8965f369cf0b142e005aeaa11d5`, MPL-2.0 | Не выбирается; будущие TUN/native deps не исследованы |
| Полный AmneziaVPN клиент | GPL-3.0 из исходного ТЗ, в I-002 не встраивается | Не выбирается и не выдаётся за лицензию отдельного SDK |
| Authlib 1.9.1 | Исторический PyPI snapshot и sdist hash сохранены в receipt | Снятый с выбора OIDC-кандидат после решения владельца о bot-code login; не установлен и для текущего flow не нужен |
| Bot-code login / opaque sessions | Python stdlib CSPRNG/HMAC/digest и будущая DB; Telegram adapter выбирается позднее | Новая auth/crypto зависимость не установлена; библиотеку бота отдельно закрепить и проверить перед I-005 |

## Release gates

- [x] Immutable revisions, native gitlinks и source checksum receipt записаны.
- [x] Выявлены mixed-license native части и declared Go graph.
- [x] Root license texts 26 Go modules проверены; неизвестные границы явно отмечены.
- [ ] Владелец выбрал модель распространения или утвердил конкретный Go-only boundary.
- [ ] Получены resolved Maven graph, per-file exceptions и build-selected Go packages.
- [ ] Составлен SBOM именно выпускаемого AAR/APK/server binary.
- [ ] Подготовлены соответствующие LICENSE/NOTICE/source availability для выбранной сборки.
- [ ] Проверены обязанности связанного/отдельного native code и публикационные каналы.
- [ ] Владелец принял обязанности; release review закрыт.

**Вердикт: NO-GO для выпуска.** Исследование и backend foundation могут продолжаться
без распространения tunnel binary. Технические вопросы сборки — в матрице;
GPL не удаляется сменой имени executable или корневого LICENSE.

## Отдельный security gate кандидата

По [OSV receipt](evidence/i-002-go-advisories.json) опрошены 27 versioned modules:
26 dependencies и AWG core. Advisory IDs получены у x/crypto, x/net, x/sys,
quic-go, x/mod, x/text. Root license clearance не устраняет эти риски.
Reachability и build-specific scope пока не измерены; часть IDs — aliases одного
сообщения. Например, [GO-2025-4116](https://vuln.go.dev/ID/GO-2025-4116)
относится к SSH agent, [GO-2026-5024](https://vuln.go.dev/ID/GO-2026-5024) —
к Windows API. Они требуют triage, а не автоматического вывода об Android.
Для server candidate отдельно проверить [GO-2024-3302](https://vuln.go.dev/ID/GO-2024-3302)
в quic-go. Автоматическое обновление 26 modules в этом issue не выполнялось.
