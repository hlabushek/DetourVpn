# I-018: локальная проверка рефералов

Дата: 2026-10-22. Ветка: `codex/i-018-referral-qualification`.
Решение: **Go для локального review I-018; no-go для публичной кампании**.

## Что реализовано

Четыре новые таблицы в миграции `0011_referrals`: кампании с budget/cap/window,
стабильные случайные коды, неизменяемые привязки и append-only award receipts.
Authenticated API создаёт код, закрепляет приглашение, возвращает собственную
статистику/страницы и проверяет бонус. Отдельная permission доступна restricted
Mini App session; VPN capabilities не добавлены.

Бонус выдаётся только пригласившему: active verified invitee, tunnel создан после
привязки, revoked + settled, observed/charged bytes положительные, matching
applied `tunnel_lease` debit. Campaign lock сериализует конкурирующих пригласивших;
receipt и `reward` quota credit атомарны. Self-referral, циклы, старая регистрация,
смена пригласившего, потеря счётчиков, fake qualification fields, повторные
ключи, overflow и превышение budget/cap fail closed. Данные друзей не выдаются.

Mini App показывает условия, свой код, форму привязки, invited/rewarded counts,
записи по 100 с cursor, кнопку «Проверить бонус» и причины отказа. Retry key
сохраняется в RAM при неопределённом ответе; logout очищает кабинет и RAM.
Существующая Android quota/history принимает `source_type=reward`; Android и
туннельный runtime в этой задаче не менялись.

## Фактические результаты

- Финальный полный PostgreSQL regression: **498 passed, 3 skipped**, 262.03 s.
  Команда: `PYTEST_ADDOPTS='-q --tb=short'` и
  `uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin`.
  Skips: I-012 staging, I-013 physical и отдельно opt-in browser harness.
- Проверены миграции upgrade/repeat/check без schema drift, пустой downgrade,
  отказ удаления существующих referral records; concurrency одного award с
  одинаковыми/разными ключами, конкурирующая привязка, budget между двумя
  пригласившими, cap, snapshot, disabled/expired campaign, auth/owner isolation,
  cursor 100+2, отсутствующий debit, rollback при capacity/conflict и encrypted
  backup/restore с referral awards и сверкой обоих аккаунтов.
- Полный synthetic signed lifecycle путь: до final ACK квалификации нет,
  после accepted node counters + реального PostgreSQL debit bonus=17 bytes;
  replay final ACK и referral claim не создают второго начисления.
- `npm test --prefix apps/telegram-mini-app`: **5 passed**.
- `npm run build --prefix apps/telegram-mini-app`: TypeScript strict + Vite passed.
  `npm audit --prefix apps/telegram-mini-app`: **0 vulnerabilities** на момент проверки.
  Новых зависимостей нет.
- `uv run --frozen python -m ruff check .`: passed.
  `uv run --frozen python -m ruff format --check .`: **203 files** formatted.
  `uv run --frozen python -m mypy`: **103 files**, passed.
  `uv run --frozen python scripts/export_openapi.py --check`: current.
- Финальный opt-in browser harness: **1 passed, 500 deselected**, 191.75 s.
  Реальные local API/PostgreSQL; **TEST ONLY** SDK, синтетические Telegram accounts,
  campaigns и tunnel evidence. Через UI: claim 17 bytes,
  `12,400,000,000 -> 12,400,000,017`, invited=1/rewarded=1, binding подтверждён,
  форма исчезла после binding; logout удалил баланс/рефералы из страницы.
  Dark/light и viewport 390/320: `scrollWidth == clientWidth` (375/305 с scrollbar).
- Созданные этими runner-ами PostgreSQL clusters остановлены и удалены;
  final loopback port 50535 и browser API 18767 закрыты. Две остановленные
  cache-папки с 2026-10-21 уже существовали и не удалялись. Временная вкладка
  закрыта, viewport override сброшен.

## Ошибки во время разработки

Первый targeted run выявил отсутствие обязательного Device.created_at в новом
synthetic fixture; затем assertion ожидал другое сообщение append-only trigger.
Оба тестовых дефекта исправлены. Первый полный прогон: 495 passed, 2 failed,
3 skipped. Restore fixture не содержал matching lease debit; это исправлено,
и matching debit теперь явно требуется также в qualification service.
Существующий reward rate test пересёк реальную минутную границу: clock в этом
тесте зафиксирован, серверная rate policy не менялась. После исправлений targeted
проверки прошли (25 passed), затем выполнен полный финальный regression выше,
включая дополнительный negative test отсутствующего debit.

QA fixture первоначально открывал другой synthetic Telegram subject; исправлен
subject, пересобран UI и повторена проверка финальной сборки. Результаты разных
прогонов не складываются. Неисправленных ошибок финальных проверок нет.

## Изменённые файлы

- `services/api/src/vpn_api/referrals/{contracts,routes,service,__init__}.py` — API и logic.
- `services/api/src/vpn_api/db/models.py`, `services/api/migrations/versions/0011_referrals.py` — schema.
- `services/api/src/vpn_api/{main.py,auth/security.py,auth/service.py}` — routing и permission.
- `services/api/tests/{test_referrals,test_migrations,test_tunnel_admission,test_rewards,test_mini_app_browser}.py` — проверки.
- `apps/telegram-mini-app/src/{main.tsx,api.mjs,style.css}`, `tests/api.test.mjs` — UI и retry.
- `packages/contracts/openapi.json`, `TECHNICAL_SPEC.md`, `ROADMAP.md`, API/Mini App README — контракт/статус.
- `docs/adr/030-referral-qualification.md`, `docs/issues/I-018.md`,
  `docs/runbooks/referrals.md`, этот отчёт, receipt и QA screenshots — решения/воспроизведение.

До работы исходное дерево уже показывало файлы как untracked. Ничего массово
не индексировалось и не коммитилось; автоматический PR/публикация не выполнялись.

## Границы и ручные gates

Никаких кампаний вне disposable tests не создавалось. Не было изменений VPS,
бота, внешнего HTTPS/menu button, DNS, production данных или реальных наград.
Тестовые счётчики не подтверждают новый физический VPN/egress/DNS/IPv6 и не
доказывают отсутствие ферм аккаунтов. Только inviter bonus, pull-модель,
ручной ввод кода; deep links, background payout и appeal UI не реализуются здесь.
До публичной кампании нужны экономика, условия, privacy/retention/legal review,
поддержка и live Telegram/staging acceptance. Production deployment не разрешён.

Ручная конфигурация и команды: [runbook](runbooks/referrals.md).
Решения: [ADR-030](adr/030-referral-qualification.md).
Receipt: [i-018-local-acceptance.json](evidence/i-018-local-acceptance.json).
Скриншоты **синтетической проверки**: [результат](assets/i-018/referrals-result.png),
[light](assets/i-018/referrals-light.png),
[полная страница с TEST ONLY](assets/i-018/referrals-test-only.png).

Следующий функциональный issue: **I-019 — Second node/failover**;
новая инфраструктура требует отдельного согласованного staging scope.
