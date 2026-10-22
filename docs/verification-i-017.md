# I-017 — локальная проверка Telegram Mini App

Дата: 2026-10-21. Ветка: `codex/i-017-telegram-mini-app`. Решение: ADR-029.

Реализованы React/TypeScript кабинет Soft Orbit и настоящий API initData exchange.
Баланс/бонусы/история используют существующий PostgreSQL ledger, а не локальное
число в UI. Native Android/tunnel/VPS не менялись. Весь исходный checkout уже был
untracked до этапа; массовый initial commit/staging проекта не выполнялся.

## Результаты

- Полный backend regression: **469 passed, 2 skipped**, 272.31s, реальный
  disposable PostgreSQL. Skip: явно не запрошенные I-012 staging/I-013 physical.
- После финальных изменений schema/static settings/capabilities отдельно выполнен
  targeted Mini App + migrations regression: **42 passed, 1 skipped, 434 deselected**,
  18.01s. Skip — opt-in browser QA, выполненный отдельно.
  Сюда входят upgrade/repeat/schema drift/downgrade guards, malformed/forged/old/
  future initData, duplicate query keys, wrong bot, ten concurrent exchanges,
  reordered replay, native/Mini App identity/grant, owner isolation, refresh,
  запрет VPN/key capabilities, rewards replay/history, expiry и cleanup.
- `npm test`: **4 passed**. Проверки lossless int64 JSON, неопределённого claim
  retry с тем же ключом, нового ключа после успеха/определённого отказа,
  single-flight refresh и удаления локальных credentials при offline logout.
- `npm run build`: TypeScript strict + Vite production build passed.
  `npm audit`: **0 vulnerabilities** на момент запуска. Exact npm versions/lockfile.
- Python Ruff check, Ruff format check и mypy всего проекта passed; mypy **97 files**.
  OpenAPI export/check current. Прямой mypy launcher один раз не смог canonicalize
  Windows path; `uv run --frozen python -m mypy` успешно проверил исходники.
- Opt-in browser harness: **1 passed, 471 deselected**, 134.38s. Реальные API/БД,
  **TEST ONLY** SDK, ephemeral synthetic Telegram signing token и reward rules.
  В браузере выполнены daily 300 MB: 12,400,000,000 → 12,700,000,000 bytes;
  clicker 17 bytes → 12,700,000,017, обе операции появились в истории.
  Проверены light/dark, 390 px, 320 px (document scrollWidth == clientWidth), logout.
  Успешный выход удалил баланс/историю из страницы. UI screenshots содержат TEST ONLY.
- По окончании все созданные disposable PostgreSQL кластера остановлены/удалены;
  browser harness API 18767 и Vite 5173 остановлены, TCP LISTEN отсутствует.

Полная suite была выполнена до последних усилений CheckConstraint/expiry-after-lock
и static-path validation; targeted regression покрывает эти изменения. Числа
разных запусков не складываются: это пересекающиеся наборы.

Сводный receipt и SHA256 сборки: [i-017-local-acceptance.json](evidence/i-017-local-acceptance.json).

Визуальная проверка: [тёмный кабинет](assets/i-017/home-dark.png),
[светлая история/профиль](assets/i-017/profile-light.png).
Числа на screenshots синтетические; это не эксплуатационный остаток пользователя.

## Воспроизведение

```powershell
npm ci --ignore-scripts --prefix apps/telegram-mini-app
npm test --prefix apps/telegram-mini-app
npm run build --prefix apps/telegram-mini-app
npm audit --prefix apps/telegram-mini-app
uv run --frozen python -m ruff check .
uv run --frozen python -m ruff format --check .
uv run --frozen python -m mypy
uv run --frozen python scripts/export_openapi.py --check
$env:PYTEST_ADDOPTS = '-q --tb=short -k "mini_app or migrations"'
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
```

Полный regression: та же команда PostgreSQL runner без `-k`.
Интерактивный browser harness и ручная настройка: runbooks/telegram-mini-app.md.

## Ограничения и решение go/no-go

**Go: локальный review I-017. No-go: публичный запуск/реальный Telegram acceptance.**
Не проверены Telegram-issued initData и клиентский webview, HTTPS ingress/menu
button, off-host deployment и эксплуатационный cleanup scheduler. Серверная
валидация проверена криптографически подписанными синтетическими тестами.
Production rules по умолчанию не включались. Нет App Store/платёжных изменений,
рефералов/проверяемых партнёрских заданий, новых измерений VPN egress/DNS/IPv6.
Неопределённый reward key живёт только до закрытия страницы; после reload нужно
сверить историю. XSS/SDK supply-chain и кража payload до первого обмена остаются
рисками. История UI ограничена 20 записями, список устройств — 100 с явным `100+`.
Полный SBOM/security review/legal gates перед public release не закрыты.

Во время разработки исправлены замечания TypeScript к CSS type declaration,
mypy к optional User и formatter; все финальные проверки повторены без обходов.
Следующий issue — I-018: referral qualification; здесь не начинался.
