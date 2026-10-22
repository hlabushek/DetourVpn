# Проверки I-005, 2026-10-18

Локально Windows, Python 3.13.1, portable PostgreSQL 17.11. После основной
реализации весь suite: **63 passed, 0 skipped**, 28.17 сек. PostgreSQL настоящий,
кластер disposable; остановлен и удалён runner. Telegram delivery — только
явно обозначенный FakeTelegramSender в tests, реальных сообщений не было.

Команды:

```powershell
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen python scripts/export_openapi.py --check
```

Ruff lint/format и mypy прошли; OpenAPI экспортирован. Финальные проверки общего
сценария после следующей I-006 записываются отдельно в verification-identity-stage.md.
Особенно проверены code replay/binding/expiry, durable dedup, unknown delivery,
five-attempt persistence, resend/cooldown/rolling limits, concurrent consume,
same-identity account race, concurrent refresh/reuse family revoke, logout-all
ownership, disabled user, secret-free public errors, body bound и migration guard.

Два ограничения этапа: admin MFA enrollment отсутствует (admin operations закрыты),
Redis не используется (shared local limiter на PostgreSQL, ADR-012). Настоящий
Telegram webhook/sendMessage и удалённый CI не проверялись. Production, Android,
VPN, quotas/payments не реализованы этой issue.
