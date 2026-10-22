# I-016 — проверка backup, restore и operational alerts

Дата: 2026-10-21. **Локальная техническая приёмка завершена.**
Граница реализации — локальный control-plane по ADR-028.
Ветка `codex/i-016-backups-alerts`. Исходный checkout целиком untracked ещё до
начала I-016; commit/staging всего проекта не выполнялись.

## Реализация

- `services/api/src/vpn_api/operations.py`: bounded pg_dump в RAM, Fernet backup,
  единый exported snapshot для дампа/manifest, isolated restore в template0,
  сверка всех public tables и существующего ledger, cleanup; read-only alerts.
- `scripts/operations.py`: keygen/backup/drill/check, URL только из локального
  окружения, проверка loopback и запрет libpq option injection через dbname,
  sanitized JSON/exit codes. Команды не создают внешних сервисов или endpoints.
- `services/api/tests/test_operations.py`: real PostgreSQL restore, concurrent
  writer и параллельные drills, wrong key/corruption/manifest/dump/size failures,
  source preservation, integer >2^53, rewards replay/restore, CLI, alert recovery.
- `.gitignore`: ciphertext `*.fernet` исключён из Git наряду с keys/backups.
- ADR-028, issue, runbook, ROADMAP и sanitized test receipt.

Нет новых зависимостей, миграций, изменений API/OpenAPI, Android или VPN engine.

## Проверки

Использован существующий bundled PostgreSQL **17.11**, реальный disposable
cluster на случайном loopback port. Никакой SQLite/mock DB или VPS не было.
Данные аккаунтов/узлов/наград — синтетические, не traffic/Telegram evidence.

- Ruff format: `python -m ruff format --check services/api scripts` —
  **90 files already formatted**.
- Ruff check: `python -m ruff check services/api scripts` — **passed**.
- Mypy: `python -m mypy` — **91 source files, no issues**.
- `python scripts/export_openapi.py --check` — **current**.
- Первый real PostgreSQL набор I-016 — **21 passed, 19.08s**.
- Расширенный CLI/rewards/peer-command набор — **24 passed, 30.38s**.
- После добавления двух запретов libpq dbname полный backend/infra набор —
  **443 passed, 2 skipped, 224.87s**; в нём **26/26 I-016 tests passed**.
  Skips — только opt-in I-012 staging и I-013 physical. Настоящий temporary
  PostgreSQL остановлен, cluster удалён, закрытие его TCP listener проверено
  отдельно socket probe. Receipt — `evidence/i-016-local-acceptance.json`.
- Тест mutation ciphertext сделан детерминированным (всегда меняет байт);
  его 4 негативных случая дополнительно прошли: **4 passed, 22 deselected, 0.65s**.

Команда полной регрессии:

```powershell
$env:PYTEST_ADDOPTS = '-q --tb=short --junitxml=.cache/i016-full-tests.xml'
.\.venv\Scripts\python.exe scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
```

Автоматические тесты подтверждают:

1. Encrypted archive восстанавливается в новую БД, все public tables
   сверяются по rows/digest, ledger reconcile проходит для аккаунта с >2^53 bytes.
2. Две параллельные restore операции успешны, временных БД после них нет;
   оригинальные данные не меняются. CLI drill тоже проверен.
3. Credit, записанный после pg_dump, не попадает в manifest того же backup;
   восстановление согласованного snapshot проходит, новый credit остаётся в источнике.
4. Authenticated manifest mismatch и broken pg archive дают failure и cleanup;
   wrong key отклоняется до создания target DB. Size-limit failure не оставляет архив.
5. Реальное начисление reward с повтором ключа восстанавливается одной строкой
   claim/ledger, reconciliation проходит; UI/телефон не затрагиваются.
6. NULL/stale/future heartbeats, empty registry и pending ACK вызывают codes;
   disabled node не вызывает stale alert, свежий receipt/ACK убирает alert.
   Время узлов синтетическое; реальный outage и SLO detection не измерялись.
7. Просроченный/missing/wrong-source backup, low disk threshold и отсутствующая
   schema version дают failure codes. Sanitized CLI не печатает libpq credentials.

## Безопасность, ограничения и ручные действия

Fernet — существующая библиотека, custom crypto primitives нет. Предел custom
дампа 32 MiB, RAM-only plaintext; swap/crash dumps ОС не покрываются. Dump source
должен быть доверенным, поскольку восстановление исполняет SQL. URL ограничен
loopback и безопасным dbname. Windows ACL, secret manager/отдельное хранение
ключа и последняя рабочая backup copy — ответственность оператора по runbook.

Проверен **локальный** backup/restore/alert evaluator. Staging deployment,
off-host copy, scheduler, доставка человеку и независимый supervisor ещё не
установлены. RPO ≤24h/RTO ≤2h, production disaster recovery и рабочий VPN этим
не доказаны. Для них нужны отдельные одобрение и проверка инфраструктуры.
Local retention 7 дней документирован до сбора данных; production collection
остаётся закрыта до общей privacy/deletion policy.

Следующий функциональный issue — I-017 (Telegram Mini App), не начат.
