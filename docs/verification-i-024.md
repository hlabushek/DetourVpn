# I-024 — проверка backend, agent и Linux dataplane

2026-10-23. Область: локальная реализация renewable multiuser peer windows v3.
Реализация и локальная приёмка завершены. Полная backend/infra регрессия:
**719 passed, 5 skipped, 626.20s** на настоящем disposable PostgreSQL.
Серверный rollout и Android v3 не выполнялись. Следующий issue — I-025.

## Реализация

- Миграции `0014_peer_windows`, `0015_window_admission`: immutable lot-pinned
  holds/allocations/receipts, runtime epoch history, owner-bound connections,
  уникальные live slots/devices и защищённый downgrade. 0015 переносит node/runtime/
  slot metadata из существующего JSON без переписывания исходного grant.
- Funding reserve/settle/reconcile: held lots недоступны другим debits; expiry/
  revoke не дублируют reserved/unavailable; measured settlement использует
  исходные lots даже после expiry. Unknown сохраняет hold без guessed debit.
  Restore audit проверяет суммы, порядок allocations, metadata, цепочку поколений,
  связь current connection/device и точные источники измеренных списаний.
- Явно выключенный по умолчанию public admission API; device proof/session/authz,
  idempotency, renewal/revoke. Config без client private key возвращается только
  после active ACK. Hidden agent API с node-scoped credential/nonce/timestamp,
  CAS epoch и recovery перед новой выдачей. Нельзя перейти обратно на v2 после v3.
- Fixed daemon, private SQLite WAL/FULL + lifetime flock, durable intent до gate
  opening, exact receipt ACK, revoke/generation/runtime fencing, закрытие всех
  gates на restart. Зависимости node отделены от backend runtime.
- Fixed root/operator AWG slot adapter и nft gate: общий duplex budget,
  отдельный passed counter после quota, независимый kernel timeout, без удалённых
  shell/path/netns/executable параметров. Node-local/server и client private keys
  не передаются backend. Адрес и native peer не меняются при rotation.
- Обновлены public OpenAPI, пять internal JSON schemas, Flutter parser/label
  источника `peer_window`. Android controller/renewal в этом issue не менялся.

## Полученные результаты и границы доказательств

| Проверка | Фактический результат | Что это доказывает |
|---|---|---|
| Полная backend/infra регрессия | 719 passed, 5 skipped, 626.20s | Настоящий PostgreSQL, финальные API/DB/agent unit tests и прежняя регрессия |
| Targeted funding/service/migrations | 48 passed, 91.63s | Настоящий disposable PostgreSQL, миграции, allocation/settlement, изоляция владельцев/узлов |
| Финальный targeted funding/service/runtime/daemon | 72 passed, 84.83s | PostgreSQL + SQLite + HTTP + настоящий daemon tick, несколько продлений двух пользователей; kernel/peer и Telegram в этих тестах simulated TEST ONLY |
| Ruff format/check | Passed | Форматирование/статический lint |
| Mypy | 146 source files passed; explicit Linux check 6 files passed | API/tests/migrations/scripts/node-agent, включая Linux paths |
| Экспорт контрактов | OpenAPI и internal schemas current | Контракты совпадают с кодом, agent paths исключены из public OpenAPI |
| Real Linux kernel gate/journal | Passed | Concurrent UDP boundary ≤65536 bytes, duplex meter 4112 bytes, timeout, slot isolation, startup closed; permissions/link/flock/persistence проверки root-private journal |
| Real pinned AWG + runtime/SQLite | Passed | Два настоящих изолированных AWG туннеля, stable key/address rotation, revoke одного сохраняет другой, restart и SIGKILL recovery |
| Sustained real AWG | 105s, 4 renewals на каждый из двух туннелей, 10 unique measured receipts, 239864 outer IP bytes | Передача дольше первого 90s grant с настоящими native cores/kernel; grants локальные synthetic, без backend billing/WAN/Android в этом процессе |
| Flutter snapshot | Analyze/format, 51 tests, native unit/lint, debug/release builds passed | Совместимость quota parser с новым history source; новый v3 APK на Pixel не устанавливался |

Наборы независимы: in-process HTTP/PostgreSQL composition использует simulated
kernel, а native Linux AWG lab использует synthetic local grants. Они не объявлены
единым remote/physical end-to-end тестом. JSON с фактами: `evidence/i-024-local.json`.

В kernel lab shared quota не пропустила байты сверх budget: финальный passed
counter 64764 при budget 65536. Это конкретный размер synthetic UDP пакетов,
не обещание использовать ровно весь budget при любом размере пакета. Последняя
слишком большая датаграмма может оставить небольшой неиспользованный остаток.
Проверены отсутствие передачи и `is_live=false` после kernel timeout.

В native lab принудительно завершался отдельный процесс WindowRuntime через
SIGKILL. При сохранённой active записи kernel deadline закрыл туннель без агента,
новый runtime сохранил measured positive receipt. SIGKILL после kernel opening,
но до active journal commit дал unknown с `observed_bytes=null`, без выдуманного
нуля. Все четыре AWG cores остановлены; Linux namespaces и private tmpfs удалены
с изолированным процессом. PostgreSQL helpers подтвердили остановку/удаление
собственных кластеров после targeted и полного runs. Пять пропусков — opt-in
admin browser, Mini App browser, прежние I-012/I-019 remote и I-013 physical;
они не представлены как выполненная приёмка этого issue. CI timeout увеличен
до 20 минут с учётом выросшего PostgreSQL набора. `uv lock --check` и
`git diff --check` прошли.

Android сборка использует закреплённый toolchain и выдаёт прежние предупреждения
о будущей поддержке Gradle/AGP/Kotlin, experimental path check и различии NDK
у integration_test. Они не подавлялись и версии не обновлялись в этом issue;
обновление toolchain требует отдельной проверки native SDK.
Финальный `scripts/build_flutter_client.ps1` завершился с exit 0: snapshot
`%LOCALAPPDATA%/ProjectVpn/i013-builds/7249eb264c874ee1ad0705afbc0dbb90`,
release APK 42.9 MB. Установка/проверка v3 на Pixel не выполнялась.
Финальная проверка процессов: PostgreSQL 0, локальных AWG cores 0.

## Найденные ошибки и исправления

Миграция 0015 сначала обращалась к reserved SQL имени `grant` без кавычек:
исправлено, проверены empty upgrade/drift/downgrade и backfill существующего hold.
Delayed revoke уже рассчитанной генерации раньше только ставил fence: теперь
закрывает также новую active генерацию того же connection; есть отдельный тест.
Усилены restore audit и отказ dispatch при несовпадении persisted slot metadata.
Лабораторный handshake учитывает bounded retransmission после намеренно закрытого
gate; это не бесконечные повторные запуски и не смягчение budget/deadline.

## Ограничения и ручные действия

I-024 не включает production/VPS migration, host NAT/routing/bootstrap/supervision,
Android Keystore/session/renewal, DNS/IPv6, Wi-Fi/mobile, реальный Telegram auth,
off-host backup schedule, credential fencing после реального restore или daily
physical acceptance. Эти проверки назначены I-025–I-028. На постоянных узлах
новые migrations/daemon/UDP endpoints не включались; v2 subscription gate сохранён.
Native dependency licensing/provenance прежние; новый значимый dependency не добавлен.
Node Pydantic closure — существующие packages с pinned hashes для Linux
x86_64 CPython 3.13: MIT, кроме typing-extensions (PSF-2.0), по фактическим
distribution metadata. Публичный release/supply-chain acceptance не заявляется.

Unknown holds нельзя стирать или превращать в measured usage. Нужна отдельная
аудируемая операторская компенсация; автоматического разрешения неизвестного
расхода здесь нет. Restore старой БД требует fencing credentials/sessions до
reconnection. Outer meter включает handshake/headers и принятый UDP до проверки
native peer, а не только authenticated user payload. До ordinary use это должно
быть раскрыто в клиенте. Root compromise, DDoS protection, provider independence
и allowlist bypass не проверены и не обещаются.

Команды воспроизведения, необходимые Linux paths/files/permissions и порядок
ручной подготовки: [runbook](runbooks/peer-windows-v3.md).
