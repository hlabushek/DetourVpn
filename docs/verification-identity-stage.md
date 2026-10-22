# Локальный этап: Telegram-вход и устройства

Дата: 2026-10-18. I-005 и I-006 выполнялись последовательно.
Результат — backend-сценарий: pairing → код в боте → verify → /me → public-key
registration → refresh → device revoke/logout. Исполняемого Android/Flutter UI
и настоящего VPN в этом этапе нет.

## Фактическая проверка

Windows, Python 3.13.1, PostgreSQL 17.11 (portable runtime предыдущей I-004).
Последний полный прогон: **90 passed, 0 skipped, 44.03 sec**.
Временный настоящий PostgreSQL cluster остановлен и удалён runner; постоянная
БД пользователя не использовалась и не изменялась.

| Проверка | Результат |
|---|---|
| Ruff format | 70 files already formatted (включая итоговый отчёт) |
| Ruff lint | All checks passed |
| mypy strict | No issues in 31 source files |
| uv lock --check | Lock согласован, 73 packages |
| OpenAPI export --check | Контракт актуален |
| pip-audit с frozen export/hashes | No known vulnerabilities found на момент запуска |
| Source credential-pattern scan | 0 candidates для bot token/private PEM/password URL; это ограниченный pattern scan |
| Реальный отдельный HTTP процесс | GET health 200; disabled auth/start 503, stable request ID |
| Остановка smoke | Test process tree завершено, OS TCP table: listener отсутствует |

### Команды из корня

```powershell
uv lock --check
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen python scripts/export_openapi.py --check
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
uv export --frozen --no-emit-project --format requirements-txt --output-file .venv/audit-requirements.txt --quiet
uv run --frozen pip-audit --requirement .venv/audit-requirements.txt --disable-pip --require-hashes
```

Другой собственный disposable PostgreSQL: VPN_TEST_DATABASE_URL и pytest, см.
runbooks/database.md. Без этого PostgreSQL tests пропускаются, это неполная проверка.

### Что проверяют тесты

- One-time/bound/expired code; лимит ошибок действительно коммитится. Cooldown,
  rolling subject limits, immutable Telegram subject и resend без reset budget.
- Webhook shared secret, private human sender, no forwarded/group/business/edited
  identity, fresh timestamp, durable Update dedup, unknown send без автоматического retry.
- Два concurrent verify — одна session; разные pairing одного Telegram ID — один
  user. Если expiry наступил во время ожидания lock, account/session не создаются.
  Последний clock-boundary test явно моделирует сдвиг времени, сохраняя настоящий DB.
- Refresh rotation/reuse/concurrent retry, абсолютный TTL, logout и logout-all,
  disabled account. Старый refresh отзывает всю family и новый access.
- Device owner isolation (чужой/несуществующий ID одинаковый 404), rejection private
  key/owner extra fields, natural idempotency, global key conflict и atomic limit 3.
- Concurrent same-key/different-owner registration; cross-owner session binding
  отвергается composite FK в БД; concurrent refresh/revoke не оставляет живую family.
- Empty schema/repeated upgrade/ORM drift/downgrade, data-preserving downgrade
  guards, structured storage errors, capped/chunked request bodies и secret-free errors.
- TelegramSender request construction/disabled proxies+redirects/one send attempt
  проверены **с явно mocked HTTP opener**, а не настоящим Telegram API.

Во всех auth/device integration tests Telegram delivery — FakeTelegramSender,
который существует только в tests. Public-key bytes синтетические и не доказывают
native keypair/handshake. Никаких mock code paths, dev tokens или example passwords
в runtime не добавлено. Настоящие сообщения не отправлялись.

### Обнаруженные проблемы и исправления

При review исправлена возможность сохранить новый account после expiry во время
ожидания identity lock: окончательная проверка теперь до любых account/session
insert. Отдельный regression test включён в финальные 90.
Ошибка 413 сначала могла иметь разные request IDs в header/body — исправлено и
проверено для обычного/chunked body. Коды resend дополнительно не повторяют
предыдущие восемь цифр даже при случайной CSPRNG collision.

Первый дополнительный HTTP smoke подтвердил ответы, но TCP connect probe сразу
после завершения процесса не подтвердил закрытие порта (assertion failure).
Оставшихся Python-процессов при осмотре не обнаружено. Smoke повторён с явным
Windows process-tree stop и проверкой OS TCP listener table: успешно, listener
отсутствует. Причина поведения первого network probe не установлена; его результат
не выдаётся за успешную проверку cleanup. Автоматический pytest suite не падал.

## Основные файлы изменения

| Группа | Файлы |
|---|---|
| Pairing/session/RBAC/Telegram | services/api/src/vpn_api/auth/{contracts,routes,security,service,telegram}.py |
| Ownership | services/api/src/vpn_api/devices.py |
| API/error/body bound/settings | services/api/src/vpn_api/{main,schemas,body_limit,config}.py |
| Storage | db/models.py; migrations/versions/0002_authentication.py, 0003_devices.py |
| Tests | tests/conftest.py, test_authentication.py, test_devices.py, test_telegram_sender.py; health/migration tests |
| API contract | packages/contracts/openapi.json |
| Local tools | scripts/local_auth_client.py, scripts/auth_cleanup.py |
| Docs/config | .env.example; README/ROADMAP; ADR-001/012/013; issues I-005/I-006; runbooks |

Новых third-party зависимостей нет, pyproject/uv.lock не менялись.
AGENTS.md, исходный CODEX_START_PROMPT.md и chessy_training_game.pgn не редактировались.
Текущая local branch codex/i-006-devices. Репозиторий по-прежнему без baseline
commits/remote; файлы untracked. Это локальный результат, не PR или опубликованный
release, а границы issue пока документированы файлами, не отдельными Git diffs.

## Ручные действия и границы готовности

Ручной запуск: runbooks/authentication.md и scripts/local_auth_client.py.
Токены/коды в клиенте не печатаются и не сохраняются; только public pairing link.
Локально настроить собственную БД/test bot/secrets, выполнить `alembic upgrade head`.
Telegram требует отдельно одобренный HTTPS ingress и ручной setWebhook. Ничего
из этого не настроено автоматически, настоящая доставка/бот не проверены.
Cleanup истёкших auth rows запускается явно; account deletion пока не реализован.

Функциональный пользовательский milestone готов к local review. Полная phase 1
из ROADMAP **остаётся незавершённой**: admin identity/MFA enrollment отсутствует,
административные операции fail-closed; shared limiter реализован на PostgreSQL,
Redis не подключён. До реальных аккаунтов нужны privacy/deletion/recovery,
least-privilege DB roles, secret management/TLS/proxy и backup/retention gates.
Нет remote GitHub CI, Docker runtime, physical Android/native, VPN tunnel/egress,
node provisioning, quota ledger, начислений, платежей или production deployment.
Device revoke отзывает control-plane sessions, не доказывает удаление VPN peer.

Следующий инженерный issue: **I-007 quota ledger** — integer bytes, idempotency,
atomic grants/debits и negative/concurrency tests; затем I-008 usage ingestion.
Native SDK/licence/build gates I-002 остаются перед I-011/012.
