# I-018: рефералы

## Безопасный старт

Сначала применить `uv run --frozen alembic upgrade head` к **выбранной локальной
или отдельно согласованной staging БД**, затем обновить API и Mini App.
Это руководство не даёт разрешения на production/staging deployment.
Миграция `0011_referrals` не создаёт кампаний и не включает начисления.
Новых env secrets, Telegram permissions и библиотек нет.

Через существующий защищённый операторский доступ создать `referral_campaigns`:
UUID `id`, integer `reward_bytes > 0`, `budget_bytes >= reward_bytes`,
integer `inviter_cap > 0`, UTC `starts_at < ends_at`, `enabled=false`.
Значения должен выбрать владелец после расчёта экономики. Для включения отдельным
решением поменять `enabled=true` внутри транзакции. Campaign row блокируется при
выдаче бонуса: операторский UPDATE дождётся завершающихся начислений.
Уменьшение бюджета/cap не отменяет историю, но может остановить новые награды.
Изменение reward_bytes влияет только на будущие привязки; старые сохраняют snapshot.

Перед реальной кампанией отдельно утвердить условия (кому/за что/до какого срока
бонус, cap и ограниченный общий бюджет), поддержку, правовую оценку и retention.
Production collection запрещена до retention/privacy решения. Для локальной
сверки records/receipts append-only и не удаляются автоматически; browsing
content, URLs, DNS, IP-история и payload туннеля не собираются.

## Пользовательский сценарий

1. Пригласивший входит в Mini App, открывает «Бонусы» и получает свой код.
   Код публичный случайный, не содержит Telegram/user ID и не даёт доступа к аккаунту.
2. Новый пользователь входит, выбирает кампанию и вручную вводит код. Это можно
   сделать в первые 7 суток аккаунта, до создания любого VPN-туннеля. Пригласивший
   должен зарегистрироваться раньше. Привязка одна на аккаунт и не меняется.
3. Приглашённый завершает VPN-сеанс через Android. Backend должен получить
   окончательные счётчики: `observed_bytes > 0`, `charged_bytes > 0`, settled
   reservation и applied `tunnel_lease` debit для того же tunnel/user/amount.
   Таймаут с потерей счётчиков, нулевой трафик, старый туннель и кнопка не подходят.
4. Пригласивший нажимает «Проверить бонус» для записи. Только сервер принимает
   решение. Snapshot бонуса зачисляется пригласившему; приглашённому второй бонус
   не обещан. После окончания/паузы кампании новые награды не выдаются.

Это pull-модель: фонового начисления нет. При закрытой странице бонус не
запрашивается. Пока кампания активна, запрос можно повторить; UI показывает
100 записей за страницу и кнопку «Ещё приглашения». После reload/сомнительного
ответа сверить состояние записи и quota history.

## API и ошибки

Все endpoints authenticated; permission `referrals:manage` доступна обычной и
ограниченной Mini App сессии. Сначала persisted rate limit общей reward-группы
(120/user/min, 3000/global/min), затем повторная auth в бизнес-транзакции.

- `GET /api/v1/referrals[?before_id=UUID]`: свой код или null, bound, invited,
  rewarded, активные условия и максимум 100 собственных записей. Cursor — UUID
  последнего элемента; сортировка по `(created_at,id)`; refresh начинает список заново.
- `POST /api/v1/referrals/code` без полей: создать/повторно получить свой код.
- `POST /api/v1/referrals/resolve`: `{code,campaign_id,idempotency_key}`. Повтор
  того же code/campaign возвращает `bound=true`; другое приглашение — conflict.
- `POST /api/v1/referrals/claim`: `{referral_id,idempotency_key}`. Чужая запись —
  404. Повтор одного referral даже с новым ключом возвращает прежний receipt;
  ключ, уже использованный другим award, — conflict. Ledger также защищает
  общий user/key namespace от конфликтов с другими источниками.

Стабильные 409-коды: `referral_self_invitation`, `referral_binding_conflict`,
`referral_binding_ineligible`, `referral_code_unavailable`,
`referral_campaign_unavailable`, `referral_not_qualified`, `referral_inviter_limit`,
`referral_budget_exhausted`, `referral_idempotency_conflict`,
`quota_idempotency_conflict`, `quota_capacity_exceeded`.
Неверный/чужой cursor: 422 `invalid_referral_cursor`; общие auth/validation/rate
ошибки используют существующий structured error contract. Данные rejected body
не возвращаются. При несогласии — ручной разбор через поддержку, без автоматических банов.

Credit имеет `source_type=reward`, `source_id=referral UUID`; receipt находится в
`referral_awards`, а не daily/clicker `reward_claims`. Это сохраняет совместимость
с текущей Android quota/history. Campaign budget — SUM неизменяемых awards в bytes,
не float и не баланс стороннего кошелька. User lock -> campaign lock; invitee
не блокируется для межаккаунтного начисления. Его активность проверяется по
видимому committed состоянию, а факт traffic settlement сохраняется неизменным.

## Откат и восстановление

Выключить кампанию можно через `enabled=false` без удаления записей.
Downgrade до `0010_mini_app` допустим только пока нет codes/referrals/awards;
при наличии записей миграция откажется. Не отключать append-only triggers ради
отката. Использовать forward fix или штатный encrypted backup/restore drill I-016.
Перед production отдельно требуется утверждённая процедура удаления аккаунта.

## Локальное воспроизведение

```powershell
uv run --frozen python -m ruff check .
uv run --frozen python -m ruff format --check .
uv run --frozen python -m mypy
uv run --frozen python scripts/export_openapi.py --check
npm ci --ignore-scripts --prefix apps/telegram-mini-app
npm test --prefix apps/telegram-mini-app
npm run build --prefix apps/telegram-mini-app
$env:PYTEST_ADDOPTS = '-q --tb=short -k "referrals or migrations or full_admission_lease or rate_limit_failed_claims"'
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
```

Без `-k` — полный regression. Runner создаёт случайный loopback PostgreSQL cluster
и случайные БД, останавливает/удаляет их после проверки; существующие БД не трогает.
В тестах только **TEST SYNTHETIC** Telegram и node evidence, настоящий PostgreSQL/API.
`test_full_admission_lease_revoke_and_reservation` проверяет также путь через
подписанный **synthetic** node ACK до referral award.

Browser harness: build Mini App, выставить `VPN_MINI_APP_BROWSER_QA=1` и
`VPN_I018_BROWSER_QA=1`, `PYTEST_ADDOPTS='-q --tb=short -k test_browser_cabinet'`,
запустить тот же PostgreSQL runner и открыть локальный `/mini-app/` на порту 18767.
Harness живёт до 300 секунд. Для ранней остановки создать `.cache/mini-app-qa.stop`;
после cleanup удалить три env variables. TEST ONLY banner содержит публичный
код тестового пригласившего; он подходит для проверки формы. Никаких реальных
Telegram credentials не нужно вводить. Это не production SDK и не live Telegram acceptance.
