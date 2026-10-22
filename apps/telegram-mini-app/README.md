# DetourVpn Telegram Mini App — I-017

React/TypeScript кабинет в стиле Soft Orbit: квота, daily/clicker, последние
20 операций и активные устройства. Реальный ledger API, без локального увеличения
баланса. Сервер проверяет исходный initData; frontend никогда не получает bot token.

`npm ci --ignore-scripts`, `npm test`, `npm run build`.
FastAPI может раздавать `dist/` на same-origin `/mini-app/`.
Для dev: `npm run dev` (loopback 5173, proxy API на 18767).
Вне Telegram вход отсутствует; production не включает тестовый SDK или seed.

Настройка/ручные gates: [runbook](../../docs/runbooks/telegram-mini-app.md).
Решения: [ADR-029](../../docs/adr/029-telegram-mini-app.md).
Проверки: [verification](../../docs/verification-i-017.md).

I-018: раздел рефералов в «Бонусах»: собственный код, ручная привязка, условия
кампаний, счётчики, постраничный список и серверная проверка бонуса. Кампании
пустые по умолчанию. [Условия и запуск](../../docs/runbooks/referrals.md),
[ADR-030](../../docs/adr/030-referral-qualification.md). Токены и retry keys только в RAM.
