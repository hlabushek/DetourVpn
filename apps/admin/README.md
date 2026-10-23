# Admin — local implementation, remote rollout open

`static/` обслуживается API на `/admin/` только при VPN_ENABLE_ADMIN=true.
Отдельный password + TOTP login, admin/operator RBAC, users/quota/subscriptions/
devices/plans/nodes/audit. Изменения — allowlisted `/api/admin/v1/actions`, с
причиной/key, транзакционным ledger и immutable audit. Реальные платежи выключены.

Bootstrap и использование: `docs/runbooks/administration.md`, ADR-035/036.
Секреты отсутствуют в source/static; токен только RAM. JS сохраняет int64 amounts
без округления. Нет Node dependencies/build/CDN. Полноценный daily dataplane и
production/remote acceptance этим интерфейсом не подтверждаются.
