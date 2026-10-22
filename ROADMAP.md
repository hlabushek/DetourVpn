# Дорожная карта разработки VPN-сервиса через Codex

**Дата:** 2026-10-15  
**Уточнение авторизации:** 2026-10-18, по прямому решению владельца — код из Telegram-бота,
см. TECHNICAL_SPEC v0.2 и ADR-001. Это меняет способ входа, не является выполненной интеграцией.  
**Базовый режим:** один разработчик + Codex, работа спринтами.  
**Оценка:** ориентир 12–20 двухнедельных спринтов до уверенного коммерческого продукта; это не срок, обещанный Codex. Реальный темп зависит от освоения Android VPN API, тестов и внешних юридических ограничений.

## Правила перехода между этапами
- Каждый этап имеет демонстрацию (**demo**), измеримые критерии приёмки (**DoD**) и явное решение **go / no-go**.
- Не начинать продажи, пока не закрыты security-, legal-, payment- и reliability-gates.
- Продуктовые мокапы не считать работающим туннелем, а локальную учётную систему — готовым биллингом.
- Не трогать продуктивные VPS автоматически: все production-операции с ручным подтверждением.

## 0. Foundation: подготовка репозитория (спринт 0)
**Цель:** сформировать управляемый проект, а не хаотичную папку с кодом.

**Задачи:**
- Создать приватный репозиторий, добавить `AGENTS.md`, `TECHNICAL_SPEC.md`, `ROADMAP.md`, `docs/adr/`, `docs/runbooks/`.
- Определить соглашения по именованию, версиям API, PR, issue labels, тестам и миграциям.
- Создать `.gitignore`, `.env.example`, pre-commit и базовый GitHub Actions CI.
- Сделать dev/staging/prod матрицу без выдачи production credentials.
- Приоритизировать ADR-001/002/003 до разработки реального туннеля.

**DoD:** CI выполняется, проект стартует локально одной документированной командой; секретов в репозитории нет.

## 1. Backend skeleton и идентичность (спринты 1–2)
**Поставляемый результат:** API + PostgreSQL + Redis, тестовая авторизация.

**Задачи:**
- FastAPI app factory, Pydantic конфигурация, SQLAlchemy модели, Alembic миграции.
- Таблицы users, auth_identities, devices; вход по одноразовому коду из Telegram-бота согласно ADR-001.
- Минимальный auth bot adapter необходим уже в I-005: доверенные Updates, код для pairing, rate limits и replay protection. Telegram Mini App остаётся отдельной I-017; она для входа не требуется.
- Сессионная модель, refresh token revocation, rate limits, user-scoped access.
- RBAC и отдельный доступ администратора с MFA.
- OpenAPI, requests IDs, минимальные healthchecks, pytest тесты.

**DoD:** пользователь не может прочитать/изменить данные другого; повторный логин/отзыв токена тестируются; миграции применяются с пустой БД.

## 2. Quota ledger — сердце экономики (спринты 3–4)
**Результат:** баланс и списания без кликера и VPN — полностью протестированная бизнес-логика.

**Задачи:**
- `quota_ledger`, credit/debit, idempotency key, уникальные индексы, транзакции.
- Демо-модель usage samples с node/peer/epoch/sequence, защитой от replay.
- Разрешение reset счётчика, упорядочивание отложенных образцов.
- API `/quota`, `/quota/history`, единицы bytes без плавающих точек.
- Property/concurrency testing: несколько параллельных начислений/списаний.

**DoD:** повтор запроса и гонки не изменяют баланс дважды; есть ручная процедура сверки баланса и ledger.

## 3. Проверяемая VPN-песочница (спринты 5–6)
**Результат:** реальный туннель на Android через тестовый узел; отдельная простая test harness утилита допустима.

**Задачи:**
- Провести лицензионный и совместимостный spike: базовая версия движка, SDK/биндинги, IPv6/DNS, требования Android.
- Один тестовый VPS, ограниченные SSH-права, воспроизводимый Ansible playbook.
- Node Agent: выдача/отзыв одного peer, read-only traffic counters, status/health.
- Backend provisioning orchestration, сохранение public keys/peer states.
- Android-native proof-of-concept на `VpnService`, настоящий connect/disconnect.
- Тесты DNS/IP leak, сетевых переключений, перезагрузки VPN узла.

**DoD:** на физическом телефоне устанавливается реальный VPN и подтверждённый новый egress IP; конфигурация отзывается; client secrets не лежат в логе. Без этого UI не объявляется готовым.

## 4. Мобильное приложение MVP (спринты 7–8)
**Результат:** Flutter UI поверх реально работающей нативной VPN-интеграции.

**Задачи:**
- Design tokens, light/dark, onboarding, login, home/connect, регионы, квота, ошибки.
- Flutter ↔ Kotlin bridge со state machine.
- Обработка network change, foreground/background, denial `VpnService`, logout/revoke.
- Клиентское кэширование без секретов в plaintext; encrypted storage для чувствительных данных.
- Android integration tests и базовая доступность интерфейса.

**DoD:** полный сценарий пользователь→регистрация→VPN→отключение→выход; нативный статус синхронизирован с UI.

## 5. Геймификация MVP (спринты 9–10)
**Результат:** реальные балансы и награды, без фиктивного локального увеличения мегабайт.

**Задачи:**
- Таблицы reward_rules/claims, server-driven configuration.
- `POST /rewards/claim`: проверка eligible / daily cap / cooldown / idempotency.
- Ежедневный бонус, кликер с максимальным числом начислений; антибот-ограничения.
- История начислений, автоматический тест replay и параллельных запросов.
- Экономический конфиг: reward bytes задаются после реальных замеров расходов.

**DoD:** десять параллельных повторов одного claim дают одну награду; лимиты не превышаются; данные совпадают на телефоне и в БД.

## 6. Ограниченный альфа-тест и поддержка (спринты 11–12)
**Результат:** закрытая группа пользователей и проверяемые наблюдения о качестве.

**Задачи:**
- Приватная выдача тестовой APK, crash reporting без избыточного сбора данных.
- Инструкция для пользователя, доступный канал поддержки, механика отправки диагностического отчёта по согласию.
- Нагрузочные тесты API и VPS, измерение задержек сбора usage, расхода памяти и батареи.
- Мониторинг, backup+restore drill, runbooks, аварийная ротация узлов.
- Корректировка бесплатных квот по реальной себестоимости.

**DoD:** базовые SLO измерены, инциденты воспроизводятся, восстановление проверено, нет критических уязвимостей.

## 7. Telegram Mini App и рефералы (спринты 13–14)
**Результат:** второй интерфейс для наград, единый backend.

**Задачи:**
- React/TypeScript Mini App, валидация Telegram initData на сервере.
- Задания с честной проверкой; `getChatMember` только там, где права бота позволяют.
- Referral codes, qualification event, бюджет кампании, простая аналитика.
- Антифрод: повторное использование ссылок/задач, лимиты, добровольные апелляции.
- Юридическое решение о допустимости стимулированных подписок и рекламы.

**DoD:** отсутствие подделки Telegram payload; повторные награды не начисляются; партнёрская статистика не раскрывает лишние персональные данные.

## 8. Multi-node, failover, second engine (спринты 15–17)
**Результат:** готовность к отказу одного узла.

**Задачи:**
- Второй VPS у независимого провайдера, совместимость версии движка.
- Node registry, health checks, capacity thresholds, signed available-node metadata по необходимости.
- Автоматическое предложение альтернативного узла после ошибки; диагностические причины.
- Абстракция второго транспорта после успешного acceptance spike.
- Анти-DDoS: оценка L3/L4 mitigation провайдера; WAF/rate-limit только для API.
- Регламент переноса пользователей без утери начисленной квоты.

**DoD:** физическое выключение узла А не портит баланс; узел Б продолжает обслуживать новые подключения; происходит ясное информирование пользователя.

## 9. Legal/payment/commercial gate (спринты 18–19; только после подтверждения)
**Результат:** законный путь к коммерческому тесту.

**Задачи:**
- Юридическая оценка географии бизнеса/клиентов, возраста владельца, рекламы VPN, договора оферты, политики обработки данных.
- Выбрать одного провайдера платежей с законным KYC/возвратами/отчётностью.
- PaymentAdapter, signing/replay validation, refunds/chargebacks, subscription lifecycle.
- Тесты: дубль webhook, rollback, отмена подписки, провал продления.
- Проверка App Store/Google Play/Telegram правил для конкретных каналов дистрибуции.

**DoD:** положительное решение legal gate, работающая sandbox-интеграция, все критические payment tests зелёные, подготовлена поддержка возвратов.

## 10. Controlled beta → public release (спринт 20+)
**Результат:** контролируемая эксплуатация с бюджетными лимитами.

**Задачи:**
- Security review сторонними глазами, dependency/SBOM audit, ключи ротации, disaster recovery drill.
- Бюджетные лимиты провайдеров VPS и спонсорских кампаний.
- Закрытая платная beta, затем расширение аудитории после стабильных метрик.
- Маркетинговые тесты только разрешёнными форматами в целевых юрисдикциях.
- Мониторинг churn, себестоимости трафика, chargeback, штрафных и правовых рисков.

**DoD:** нет блокирующих security/legal проблем; есть реальная маржа на пилотной когорте; пользователи могут удалить аккаунт и получить поддержку.

## 11. Отдельный R&D поток: качество транспорта и ограниченные сети

Это **исследовательский поток**, не блокирующий основной релиз:
- Составить матрицу сетевых ограничений: распознавание протоколов/DPI, фильтрация IP, strict allowlist, потеря DNS/IPv6.
- Документировать для каждого движка: версия, ОС, тип сети, оператор, исход, причины отказа, оценка стабильности.
- Тестировать только в сетях/на инфраструктуре, на которую есть полномочия.
- Не включать непроверенный experimental transport в глобальную настройку по умолчанию.
- Если строгий allowlist исключает адреса узлов, **не заявлять о работающем универсальном обходе**. Обфускация не исправляет недоступность конечного IP.

## 12. Первые GitHub Issues (порядок исполнения)

| ID | Приоритет | Issue | Зависимость | Критерий готовности |
|---|---|---|---|---|
| I-001 | P0 | Init monorepo + AGENTS/CI | — | Локальный запуск/CI |
| I-002 | P0 | ADR авторизации и VPN SDK | I-001 | ADR accepted, licencing checklist |
| I-003 | P0 | FastAPI skeleton + health | I-001 | `/health` + tests |
| I-004 | P0 | Postgres + Alembic | I-003 | Миграции без ошибок |
| I-005 | P0 | Authentication + RBAC | I-004 | IDOR и auth tests |
| I-006 | P0 | Devices public keys | I-005 | Device ownership tests |
| I-007 | P0 | Quota ledger | I-004 | Replay/concurrency tests |
| I-008 | P0 | Usage sample ingestion | I-007 | Restart/replay tests |
| I-009 | P0 | Node registry + agent contract | I-004 | Idempotent provisioning tests |
| I-010 | P0 | One test node deploy | I-009 | Repeatable provisioning |
| I-011 | P0 | Android native tunnel POC | I-002/I-010 | Реальный egress IP |
| I-012 | P0 | Tunnel API + revoke | I-006/I-009/I-011 | Revocation tests |
| I-013 | P0 | Flutter connect UI | I-011/I-012 | End-to-end Android |
| I-014 | P0 | Quota UI/history | I-007/I-013 | Consistent amounts |
| I-015 | P0 | Clicker + daily reward | I-007/I-013 | No double credit |
| I-016 | P0 | Alerts, encrypted backups | I-010 | Restore drill |
| I-017 | P1 | Telegram Mini App | I-015 | Verified initData |
| I-018 | P1 | Referral qualification | I-017 | No self-referral reward |
| I-019 | P1 | Second node/failover | I-012/I-016 | Node-failure drill |
| I-020 | P1 | Payment provider sandbox | Legal gate | Signed webhooks + refunds |
| I-021 | P1 | Security review | I-019/I-020 | High/critical resolved |
| I-022 | P2 | Windows/Linux clients | Stable mobile core | E2E smoke tests |
| I-023 | P2 | iOS feasibility | Organization/licence gate | Apple-compliant native POC |

### Локальный статус 2026-10-18

- I-003 skeleton/health выполнен в рамках стартового I-001; повторная реализация не нужна.
- I-004 реализован и проверен на изолированном PostgreSQL: connection layer,
  account schema, Alembic и tests. Команды/результаты — docs/verification-i-004.md.
- CI workflow подготовлен, удалённый GitHub run этим статусом не подтверждается.
- I-005 реализован локально: Telegram webhook adapter, pairing/code verification,
  opaque access/refresh, revoke/logout-all, shared PostgreSQL rate limits и RBAC gate.
- I-006 реализован локально: public keys Android devices, owner-only read/list/revoke,
  session binding, concurrent device limit и DB ownership FK. Общий проверяемый путь:
  auth → своё устройство → refresh → revoke/logout. Отчёт: docs/verification-identity-stage.md.
- Это функциональный milestone внутри phase 1, **не полное закрытие phase 1**:
  admin identity/MFA enrollment ещё отсутствует (admin operations закрыты), Redis
  не подключён (локальный limiter PostgreSQL, ADR-012), live Telegram не проверен.
  До реальных пользователей нужны account deletion/recovery/privacy/backup gates.
- I-007 реализован локально: immutable quota ledger/receipts, exact integer bytes,
  credit/debit и durable accepted/rejected replay, owner-only quota/history,
  once-only initial grant и reconciliation. Размер старта не выбран: default 0;
  настройка VPN_INITIAL_QUOTA_BYTES. Результаты: docs/verification-i-007.md.
- I-008 реализован локально: internal usage bindings/epochs/receipts, node/peer/
  epoch/sequence, replay/reset/late reports, rx+tx delta, received/charged watermarks,
  pending и atomic quota debit. Исторические receipts сохраняют epoch boundary;
  отдельная read-only сверка. Отчёт: docs/verification-i-008.md.
- Локальные accounting задачи phase 2 доступны для review/демонстрации на synthetic
  reports. Настоящий collector/доставка и bounded traffic enforcement не доказаны.
- I-009 реализован локально: node registry с одноразовым credential, versioned
  authenticated usage endpoint v1, timestamp/nonce replay protection и атомарная
  транзакция credential nonce + I-008 receipt/debit. Проверка: 224 tests passed на
  disposable PostgreSQL; `docs/verification-i-009.md`. Реального daemon/VPS/TLS
  ingress и credential rotation нет.
- **I-010 завершён 2026-10-19 в границе bounded staging test-node acceptance.**
  На одобренном VPS установлены pinned binaries/licenses; установка и повтор
  проверены. После исправления noexec `/run` заключительный run дал `ok=48
  changed=2 unreachable=0 failed=0`. Два реальных namespace exchanges подтвердили
  handshake, двусторонние пакеты и peer removal; два injected-failure runs —
  cleanup. Existing containers, firewall/routes/links/namespaces сохранены,
  transient units удалены. `docs/evidence/i-010-staging-acceptance.json` проверен.
  Checklist — `docs/issues/I-010.md`, граница — ADR-018. Release остаётся
  установленным, test runtime убран; публичный endpoint/Android/egress не проверены.
  Linux binary scan: 0 symbol/package findings; release/native gates остаются.
- I-011: 2026-10-19 на физическом Pixel 8 Pro подтверждены native UP, свежий
  handshake и HTTPS IPv4 egress через согласованный staging endpoint. Private key
  остаётся в памяти телефона. Проверены Wi-Fi → cellular, возврат Wi-Fi,
  включение/выключение экрана, нормальное отключение и отсечение трафика при
  остановке узла. IPv6-проба блокируется внутри VPN и проходит после отключения;
  выборка из трёх DNS-запросов показала только ожидаемые Cloudflare resolvers.
  Это ограниченные измерения, не доказательство отсутствия всех утечек.
  Исправления sandbox/container routes и snapshot v2 проверены на staging:
  forced-stop и реальный deadline 600s восстановили все семь baseline checks.
  Тестовый узел остановлен, ресурсы очищены, временный sudo grant отозван;
  instrumentation APK удалён, POC остановлен. 174 Python tests passed,
  166 database tests skipped без VPN_TEST_DATABASE_URL; Android build/lint и
  7 unit tests прошли. **Технический I-011 принят (ADR-022):** исправленный direct
  UAPI revoke реально удаляет peer при работающем движке и прекращает трафик
  Pixel, повтор успешен. После потери узла disconnect вернул IPv4/IPv6.
  Forced deep Doze IDLE/resume пройден; естественный hardware suspend и активный
  system permission revoke не измерены. Release licence/SBOM gates остаются.
  Evidence: `docs/verification-i-011.md`,
  `docs/evidence/i-011-revoke-staging.json`; ADR-019/020/021/022.
- I-012: **технически принят 2026-10-20 в bounded staging scope ADR-024**: migration 0007/0008, owner-only
  requests, key confirmation, quota reservations, native daemon add/remove,
  config после ACK, kernel UDP budget/expiry и inventory/restart reconciliation.
  Локальный настоящий AWG test подтвердил handshake/packets/remove/replay и оба
  cutoff. Без trusted profile admission закрыт. Финальный live gate пройден:
  реальный API → node ACK → Pixel handshake/staging egress; API revoke/cutoff;
  actual agent restart/reconciliation; transport loss → expiry/cutoff → recovery.
  Во всех трёх сценариях settlement и обычная сеть после disconnect подтверждены.
  Семь restored checks true, grant/timer/test APK/private files удалены, endpoint,
  agent, forwarding, local API и disposable PostgreSQL остановлены.
  Evidence: `docs/evidence/i-012-staging-lifecycle.json` (passed=true).
  Код и запуск — ADR-024, `docs/verification-i-012-admission.md`,
  `docs/runbooks/tunnel-lifecycle-staging.md`. Следующий issue — I-013 (отдельно
  назначен пользователем 2026-10-20, статус ниже). Production/release gates сохраняются.
- I-013: назначен 2026-10-20, технически принят 2026-10-21 в bounded staging.
  Локальные 22 Flutter tests, 10 native unit tests и физические native/API checks
  на Pixel 8 Pro прошли (Telegram delivery TEST FAKE ONLY).
  Lint и debug/unsigned release builds прошли. Новый Flutter client в `apps/client`
  использует existing AWG adapter и API I-012; scope/ограничения —
  ADR-025. Собственный Flutter/Android receipt `docs/evidence/i-013-staging.json`
  подтверждает handshake/egress, background/foreground, Disconnect/restore,
  remote revoke, expiry, settlement и семь cleanup checks. Ограничения:
  один узел, три отдельных lease до 60s/1 MiB; DNS/IPv6 и смена Wi-Fi/mobile
  для нового APK не проверены; production/release gates открыты.
  Рекомендуемый следующий issue — I-014; в I-013 он не реализовывался.
- I-014: реализован 2026-10-21 — экран «Трафик и история» с точными integer bytes,
  granted/used/reserved/available, стабильной revision/cursor пагинацией,
  loading/empty/error/stale/retry и очисткой RAM при выходе. ADR-026.
  На физическом Pixel 8 Pro через локальные реальные API/PostgreSQL проверены
  сумма больше 2^53, 20+5 записей истории, refresh и повтор без двойного credit.
  Ledger events синтетические, Telegram delivery TEST FAKE ONLY; VPS не менялся.
  Receipt `docs/evidence/i-014-local-android.json` (`passed=true`),
  технически принят в локальном scope ADR-026: 36 Flutter, 69 PostgreSQL,
  10 Android unit tests, lint/debug/unsigned release и APK audit прошли.
  результаты сборок и тестов — `docs/verification-i-014.md`.
  Это quota/history acceptance, не новая проверка реального VPN usage/egress.
  Следующий issue — I-015 (clicker + daily reward); в I-014 не реализовывался.
- I-015: **принята и закрыта 2026-10-21**. Server-only daily/clicker rules,
  UTC cap/count/cooldown/window, idempotency, atomic claim/ledger и reward history.
  Backend: 417 passed, 2 skipped (отдельные I-012 staging/I-013 physical).
  Flutter: 38 passed, format/analyze; Android: 10 unit tests, lint clean,
  debug/release APK — passed. Финальный I-015 physical runner в обычном
  PowerShell: **1 passed, 418 deselected, 42.31s**; реальный Pixel/API/PostgreSQL,
  reward history/replay и DB reconciliation подтверждены. Временный API/БД
  остановлены, ADB reverse снят, обычный APK восстановлен. Receipts проверены.
  Production rules выключены; Mini App rewards UI остаётся I-017.
  Следующий по плану — I-016 (актуальный статус ниже). Удаление временных копий SDK/cache
  остаётся заблокировано платформой и не является открытым gate I-015.
  Подробности: docs/verification-i-015.md, docs/evidence/i-015-acceptance.json,
  docs/runbooks/rewards.md.
- I-016: **реализован и технически принят локально 2026-10-21** (ADR-028).
  Зашифрованный Fernet/pg_dump backup без plaintext файла, единый snapshot
  dump/manifest, restore в новую случайную БД, сверка всех public tables и
  ledger/rewards, cleanup success/failure и параллельные drills. Read-only
  alert codes: DB/schema, node heartbeat, pending peer ACK, backup age/source/
  integrity и локальный диск. Лимит custom dump 32 MiB, local retention 7 дней.
  **443 passed, 2 skipped**, включая 26 I-016 tests на настоящем PostgreSQL 17.11;
  Ruff format/check, mypy (91 files), OpenAPI current. Disposable cluster удалён,
  закрытие TCP listener проверено. Receipt: docs/evidence/i-016-local-acceptance.json.
  VPS/Android/VPN не менялись. **Deployment gate открыт:** off-host storage,
  scheduler, независимая доставка/supervisor, эксплуатационные RPO/RTO и privacy
  policy не подтверждены. Статус не означает работающие внешние alerts или
  production disaster recovery. Команды/ручные шаги: docs/runbooks/operations.md;
  полный отчёт: docs/verification-i-016.md. Следующий функциональный issue — I-017,
  в этой задаче не начат.

- I-017: **реализован локально 2026-10-21** (ADR-029). React/TypeScript Mini App
  в стиле Soft Orbit: точный баланс bytes, daily/clicker через I-015 API,
  устройства read-only, последние 20 операций, light/dark и logout.
  Серверный HMAC initData exchange, пяти-минутное окно, durable replay protection,
  общий аккаунт/initial grant с Android и ограниченная сессия без VPN capabilities.
  Локальные API/PostgreSQL и browser QA подтверждены; настоящий Telegram SDK,
  HTTPS и menu button остаются отдельным ручным gate. Никакой публикации/изменения
  бота или VPS не выполнялось. Production rewards выключены. Партнёрские задания
  не предлагаются без проверяющего провайдера/прав бота; рефералы — I-018.
  Команды и фактические результаты: docs/verification-i-017.md,
  docs/runbooks/telegram-mini-app.md. Следующий функциональный issue — I-018.

- I-018: **реализован и технически принят локально 2026-10-22** (ADR-030).
  Кампании/codes, неизменяемая привязка, server-side qualification по final
  observed counters и matching applied lease debit. Бонус только пригласившему,
  один раз, в пределах campaign budget/cap/window; default кампаний нет.
  Mini App: код/условия, binding, counts, cursor list и явная кнопка проверки.
  **498 passed, 3 skipped** в полном PostgreSQL regression; 5 client tests,
  TypeScript/Vite build, Ruff, mypy (103 files), OpenAPI current. Отдельный
  browser harness: **1 passed**, реальные local API/PostgreSQL и TEST ONLY
  Telegram/node data; binding, +17 bytes, counts, themes/390/320 и logout.
  Encrypted backup/restore с referral awards подтверждён. Созданные disposable
  clusters остановлены/удалены, порты 50535/18767 закрыты. VPS/Android не менялись.
  **Публичная кампания — no-go:** нужны экономика/условия, retention/privacy/legal,
  поддержка и live Telegram/staging acceptance. Фермы разных аккаунтов полностью
  не решены; начисление pull-based, коды вводятся вручную, автоматических банов нет.
  Команды/ручные gates: docs/runbooks/referrals.md; фактические результаты:
  docs/verification-i-018.md, docs/evidence/i-018-local-acceptance.json.
  Следующий функциональный issue — I-019 (second node/failover); актуальный статус ниже.

- I-019: **локальная реализация 2026-10-22; live node-failure gate открыт**.
  ADR-031: до двух private operator profiles, каталог heartbeat/runtime/capacity,
  explicit node selection и отдельные конфиги/agent credentials; один peer/узел.
  Flutter показывает выбор, причины и время проверки, не переключает живой lease.
  Явное owner recovery только после deadline+10s и stale heartbeat: полный
  reservation debit с counters_lost, observed_bytes=NULL и version fencing.
  Это не измеренный usage/ACK и не referral qualification. Старый intent
  завершён прежде нового; stalled alert учитывает только текущие commands.
  PostgreSQL полный regression: 506 passed, 3 opt-in skipped; Flutter 44,
  native Android 10, analyze/lint/debug/unsigned release и OpenAPI — passed.
  После финальной правки grace затронутый набор: 63 passed, 446 deselected.
  Все это локальные проверки;
  фактические результаты: docs/verification-i-019.md и
  docs/evidence/i-019-local-acceptance.json. VPS/телефон не изменялись.
  **Следующий шаг: одобренный drill с двумя staging hosts**, физическим клиентом,
  egress/cutoff/expiry, ledger и cleanup по docs/runbooks/node-failover.md.
  Это не готовая production fleet, seamless failover или завершённая приёмка I-019.
  I-020 payment sandbox не начат; требует отдельного legal gate.

## 13. Как работать с Codex по каждому issue

1. Передать контекст (`TECHNICAL_SPEC.md`, ADR, `AGENTS.md`) и **одну** issue.
2. Попросить 3–8 конкретных шагов, затрагиваемые файлы, риски, тесты; без немедленного огромного изменения.
3. Поручить реализацию исключительно этой issue в отдельной ветке.
4. Запустить formatter, linter, mypy/pyright и тесты; на Android — Gradle/Flutter tests.
5. Codex должен вернуть: что изменено, какие команды запускались, результаты, нерешённые вопросы и список ручных проверок.
6. Самостоятельно проверить diff (особенно IAM, ключи, SQL, платежи), затем merge.
7. После каждого этапа пройти DoD и обновить ROADMAP и ADR.

## 14. Матрица рисков

| Риск | Вероятность / ущерб | Тактика |
|---|---|---|
| Нативная интеграция VPN сложнее Flutter UI | Высокая / высокая | Прототип туннеля до большого UI |
| Жёсткие блокировки узлов | Высокая / высокая | Диверсификация, честные статусы, R&D без гарантии |
| Неопределённость закона о рекламе | Высокая / высокая | Legal gate до рекламных закупок |
| Блокировка продавца платежным провайдером | Средняя / высокая | Due diligence, не скрывать бизнес-модель от провайдера |
| Накрутка кликера | Высокая / средняя | Server ledger, daily cap, idempotency, antiabuse |
| Себестоимость бесплатного трафика | Высокая / высокая | Квоты, бюджетные лимиты, unit economics |
| License incompatibility | Средняя / высокая | SBOM, проверка лицензии по библиотекам |
| DDoS узла/панели | Средняя / высокая | L3/L4 hosting mitigation, API WAF, резервирование |
| Утечка пользовательских данных | Средняя / очень высокая | Data minimization, secret lifecycle, external audit |
| AI-generated false sense of completeness | Высокая / высокая | Acceptance по измеренным тестам, no mock in release |

## 15. Ближайшая точка принятия решения

Первые 3 технические неизвестные:
1. Какую нативную Android VPN-библиотеку можно безопасно и лицензионно корректно использовать с Flutter?
2. Как организовать счётчики и enforcement квоты на конкретном движке без двойного списания при рестарте?
3. Какой способ входа обеспечивает надёжную идентичность без избыточных персональных данных и соответствует правилам платформ?

Не начинать красивую геймификацию до завершения базового backend ledger и рабочего туннеля.
