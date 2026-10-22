# ADR-029 — Telegram Mini App: ограниченная сессия кабинета

Дата: 2026-10-21. Issue: I-017. Решение принято для локальной реализации.

## Контекст и границы

Android управляет VPN; Mini App показывает кабинет и обращается к тому же ledger.
I-017 реализует React/TypeScript кабинет, баланс, последние 20 операций,
ежедневный бонус/кликер и список устройств. Реферальная квалификация — I-018.
Партнёрские задания не предлагаются: провайдер проверки/права бота и юридическое
разрешение кампании не настроены. Непроверенные действия не дают наград.
Платежи, управление VPN, отзыв устройств и deep link в Android не входят в issue.
Универсальный Android deep link пока не зарегистрирован, поэтому ложной кнопки нет.

## Решение

- Использовать исходный `Telegram.WebApp.initData`, а не `initDataUnsafe`.
  `POST /api/v1/auth/mini-app` доступен только при `VPN_ENABLE_MINI_APP=true`
  и включённой существующей авторизации. По умолчанию выключен.
- Проверять Telegram HMAC-SHA256 через стандартный `hmac`, constant-time compare,
  все поля кроме `hash` участвуют в подписи (включая optional `signature`).
  Основание: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app.
  Это bot-token validation, не сторонняя Ed25519 validation.
- Окно входа 300 секунд по серверному PostgreSQL clock; будущие timestamps
  запрещены. Невалидный UTF-8/percent encoding, duplicate query keys, неверные
  user ID/боты отвергаются. Лимит payload 16 KiB.
- Подписанный `hash` служит PK одноразового обмена в `mini_app_exchanges`.
  Повтор с другой URL-кодировкой/порядком полей не выдаёт новую сессию.
  Advisory identity lock совпадает с OTP-входом. Создание пользователя,
  initial grant, replay receipt и сессия находятся в одной транзакции.
- Использовать прежнюю Telegram identity и initial-grant idempotency. Нет
  второго аккаунта/баланса при переходе Android ↔ Mini App.
- Семейство сессий `client_kind=mini_app` живёт один час, access — до десяти
  минут; refresh использует прежнюю ротацию и revoke. Разрешены только чтение
  себя/квоты/устройств, rewards и revoke session. Регистрация ключей/туннель
  запрещены даже при прямом API-запросе. Native sessions сохраняют свои права.
- Токены и неопределённый reward idempotency key живут только в RAM страницы.
  Нет localStorage, cookies, service worker, аналитики, загрузки аватаров.
  После потери ответа claim повторяется с тем же ключом; после reload требуется
  новый вход. Неопределённый вход не повторяется автоматически.
- JSON integer tokens преобразуются в строки до JSON.parse; byte arithmetic
  выполняется BigInt. Короткое обозначение GB десятичное, точный остаток также виден.
- Клиент обращается только к same-origin `/api/v1/`; redirects запрещены.
  FastAPI опционально раздаёт собранный `dist` под `/mini-app/`, без CORS.
  Telegram SDK — единственный внешний runtime script. CSP разрешает его origin,
  same-origin соединения и Telegram web frames; `no-store`, no-referrer,
  nosniff, запрет camera/microphone/geolocation. TLS остаётся deployment gate.

## Данные и миграция

Миграция 0010 добавляет `client_kind` с default native/check constraint и replay
таблицу с индексом срока хранения. Сохраняется только HMAC digest и expiry,
не исходный payload/имя/фото. Replay запись живёт шесть минут после обмена,
существующий `AuthService.cleanup()` удаляет истёкшие записи. Требуется локальный
scheduler согласно auth runbook; до production retention должен быть согласован.
Downgrade отказывается удалять Mini App sessions/replay records; он не
превращает ограниченную сессию в native. Сначала выключить вход, отозвать сессии,
выждать expiry/retention и выполнить штатную очистку. Не удалять активные записи
для обхода этого gate.

## Зависимости и ограничения

React/ReactDOM 19.3.1 и Vite 8.3.5 — MIT; TypeScript 7.0.2 — Apache-2.0;
@types/react и @types/react-dom 19.3.0 — MIT. Установлены exact versions и npm
lockfile; installation scripts выключены при установке. Это поддерживаемые
upstream проекты, новых Python runtime dependencies нет. npm audit не заменяет
полный supply-chain review/SBOM перед публичным выпуском.

Украденный initData может быть обменён первым в течение пяти минут: Telegram
payload является bearer proof, одноразовость не устраняет кражу до обмена.
После потери ответа на вход нужно заново открыть Mini App. XSS/скомпрометированный
Telegram SDK остаются рисками; CSP снижает поверхность, но не доказывает отсутствие
XSS. Клиентские лимиты не являются антибот-доказательством: серверные cap/cooldown
и rate limits обязательны. Реальный Telegram запуск не подтверждён локальным SDK fake.
