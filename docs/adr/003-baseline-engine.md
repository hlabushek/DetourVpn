# ADR-003: baseline engine и совместимые исходные версии

Status: **Proposed**  
Updated: 2026-10-18 / I-002  
Verdict: AWG source pair для build/compatibility spike; **NO-GO для release**.

## Сравнение и рекомендация

| Кандидат | Польза | Ограничение / выбор |
|---|---|---|
| AWG userspace | Совпадает с выбранным Android core, peer/counter модель, AWG transport config | Основной source-кандидат после build/licence gates; не доказана работа в сети |
| WireGuard userspace | Reference implementation и Android embedding | Диагностический reference; не замена AWG-specific config без явного выбора |
| Xray-core + TUN layer | Возможный второй transport | Нужны другой TUN backend, counter attribution, battery/licensing review; пока не включается |

Для исследования выбрать SDK из ADR-002 и одинаковый AWG core с обеих сторон:

| Роль | Точная ревизия |
|---|---|
| Android SDK source | `amnezia-vpn/amneziawg-android@ff15093bf3856fea0947923474870b86efb682f0`, versionName 3.1.4 |
| Android embedded module | `github.com/amnezia-vpn/amneziawg-go/v3@v3.1.20260814` |
| Кандидат server userspace core | `amnezia-vpn/amneziawg-go@1b86b2ae0e493e7ea93f8c1a0f0cb6735b1551f1`, тот же module tag |
| AWG tools / gitlink SDK | `amnezia-vpn/amneziawg-tools@ee0f0a9aa34ff0a0da4b3433b9512781cfe02843` |
| Reference Android | `WireGuard/wireguard-android@e7b3a3c118836e112620b1302a8ba1873ad4daac`, source versionName 1.0.20260315 |
| Reference embedded/server core | `golang.zx2c4.com/wireguard@v0.0.0-20250521234502-f333402bd9cb`, commit `f333402bd9cbe0f3eeb02507bd14e23d7d639280` |
| Xray, только сравнение licence | `XTLS/Xray-core@92ccc939aaf4f8965f369cf0b142e005aeaa11d5`, MPL-2.0 |

Это baseline для spike, не обязательство использовать старую версию при выпуске.
SDK master и core master могут расходиться; не брать server head вместо embedded tag.
Security updates требуют новой пары revisions и повторения матрицы.

## Что подтверждено исходниками

Go proxy Origin и GitHub tag согласуются по commit embedded AWG. Android serializer
и Go UAPI содержат AWG 3.1 config fields, включая HeaderProtectionKey, padding/timings
и DisableCookies. Это SOURCE evidence, не round-trip/runtime interoperability.
VersionName приложения не является номером протокола или доказательством версии AAR.
Hash файла/ZIP проверяет воспроизводимость bytes, не доверенность upstream.

Источник: [SDK go.mod](https://github.com/amnezia-vpn/amneziawg-android/blob/ff15093bf3856fea0947923474870b86efb682f0/tunnel/tools/libwg-go/go.mod),
[core tag](https://github.com/amnezia-vpn/amneziawg-go/tree/1b86b2ae0e493e7ea93f8c1a0f0cb6735b1551f1),
[receipt](../evidence/i-002-sources.json). Описание протокола:
[Amnezia docs](https://docs.amnezia.org/documentation/amnezia-wg/).

## Blockers и следующий эксперимент

- Android Makefile фиксирует Go 1.24.2, module требует 1.25.0. Возможна автоматическая
  загрузка другого toolchain, а runtime patch может оказаться неприменим.
  До build зафиксировать один поддерживаемый Go и проверить patch; auto-fetch
  нельзя выдавать за закреплённую сборку. Современный toolchain ещё не выбран/проверен.
- Supply chain: Gradle wrapper без distributionSha256Sum, нет полученного resolved
  Maven/native inventory и доказанного повторного AAR build. План в матрице.
- GPL/LGPL/native boundary из ADR-002; полный Go module graph не равен linked runtime.
- Release logging и Key lifecycle из ADR-002 нуждаются в проверке.
- OSV version-level query нашёл advisory-записи для 6 declared modules. До выбора
  production baseline нужен govulncheck/reachability для каждого build target,
  обновлённая согласованная core/SDK пара и повторный compatibility test.
  [OSV receipt](../evidence/i-002-go-advisories.json) сохраняет IDs и примеры triage;
  advisory в Windows package не доказывает дефект Android runtime.
- До quota enforcement нужны ADR-004/008: rx+tx accounting, counter_epoch, sequence,
  leases/резервирование на нескольких узлах и предел перерасхода при API outage.
  Возможность продолжать tunnel не означает неограниченную бесплатную сессию.

Нет собственного crypto или новых remote-shell endpoints. Server/data plane отдельно
от FastAPI. Конечный IP может быть недоступен при strict allowlist независимо от
обфускации. Не обещать универсальную достижимость или готовый kill switch.

До Accepted: утверждён source/build/licence plan и закрыты blockers решения;
до рабочего VPN: реальные I-010/011 на явно выбранном стенде и физическом устройстве.
Никакого VPS deploy, provisioning peer, kernel module или traffic test в I-002 нет.
