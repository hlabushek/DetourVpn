# Архитектурный review I-001

Дата: 2026-10-15. Прочитаны пять документов комплекта. Это стартовый анализ,
замечания не меняют требования автоматически. P0 блокирует соответствующую
реализацию/публикацию, но не локальный health.

| ID / приоритет | Пробел и риск | Предложение / точка решения |
|---|---|---|
| R01 / P1 | ROADMAP I-003 выделяет health отдельно, стартовый промпт включает его в I-001; ТЗ §7 называет `/health/public` | В I-001 выполнить заданный промптом `/api/v1/health` как liveness. `/health/public` не добавлять; I-003 перепланировать после review |
| R02 / P0 | Исходный AUTH-01 оставлял способ входа и recovery неопределёнными; возможны захват аккаунта и двойная квота | Владелец выбрал код из Telegram-бота (ТЗ v0.2). ADR-001: trusted Updates/from.id, pairing и атомарный consume; не связывать аккаунты по username. Независимый recovery пока открыт |
| R03 / P0 | AWG 3.1 упомянут без фиксированных SDK/server commits и совместимости | ADR-002/003: кандидат Proposed; выбрать воспроизводимые artefacts и матрицу до интеграции |
| R04 / P0 | Лицензию полного клиента можно ошибочно перенести на отдельный SDK и наоборот | Проверять конкретную сборку, Go/native deps, submodules и linking; checklist I-002 |
| R05 / P0 | Продолжение сессий при отказе API конфликтует с ограниченным балансом на нескольких узлах | ADR-004/008 до enforcement: bounded leases по байтам/времени, атомарное резервирование, предел офлайн-перерасхода и политика expiry |
| R06 / P0 | Перестановка usage samples/epochs и перенос peers могут потерять или дважды списать байты | I-008: уникальный `(node, peer, epoch, seq)`, high-watermark, авторитетная смена epoch и правила старых samples; property/concurrency tests |
| R07 / P0 | Entitlements требуются P-03, но отсутствуют в §6; нет refund/chargeback accounting | До I-020 добавить модель переходов, валютные инварианты, integer minor units и reversal вместо удаления операций |
| R08 / P1 | Не заданы materialized balance, отрицательный остаток, overflow и тарифный overhead | I-007: ledger первичен; определить rx+tx, GB/GiB, checked BIGINT bounds и сверку. Overhead измеряется для выбранного engine |
| R09 / P1 | Не заданы часовой пояс суток, стартовая квота, verified user и событие реферала | ADR-005/006: предложить UTC, versioned rules, уникальность наград и бюджеты; экономику не фиксировать без замеров |
| R10 / P0 | Retention/удаление против аудита; request/agent логи могут раскрывать IP и peer metadata | ADR-009 до реальных данных: категории/сроки/доступы, основания исключений; запрет payload/URL/DNS. Access log выключен командой локального запуска |
| R11 / P0 | Не определены lifecycle device keys, восстановление телефона, node identity и подтверждение revoke | ADR-008, I-006/009: local keys, authenticated agent, bounded commands и expiry; проверять компрометацию/восстановление |
| R12 / P1 | Общий error envelope не задаёт 401/403/422/500; статус может раскрыть топологию | Сейчас реальные 404/405 и request_id; перед расширением API определить sanitization/коды и отдельную readiness |
| R13 / P1 | SLO без точек измерения/окна; CI нельзя считать пройденным до GitHub run | До alpha определить измерения. Windows unit tests, Linux Mint, контейнеры, Android и удалённый CI учитывать отдельно |
| R14 / P0 | Коммерческие каналы, география и правовые условия не утверждены | Сохранить gates ТЗ; актуальные правила и применимость проверить до распространения/платежей. I-001 не является юридической проверкой |

## Проверенные внешние факты

Документация описывает AWG 3.1; это не доказывает совместимость конкретного SDK:
[AmneziaWG](https://docs.amnezia.org/documentation/amnezia-wg/).
У полного клиента GPL-3.0, у отдельного Android AWG репозитория Apache-2.0:
[client LICENSE](https://github.com/amnezia-vpn/amnezia-client/blob/dev/LICENSE),
[Android COPYING](https://github.com/amnezia-vpn/amneziawg-android/blob/master/COPYING).
Применимость к будущей сборке ещё не определена.

Исторически исследовались OIDC и Mini App initData; после уточнения владельца
они не являются выбранным login flow. Для bot-code нужны Bot API Updates и
доверенный bot adapter, см. ADR-001. Ранее проверенные альтернативы:
[Login](https://core.telegram.org/bots/telegram-login),
[Mini Apps](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).

## Допущения только для каркаса

Python 3.13, API без storage/интеграций, local/test, loopback-only запуск,
без access/body logs и production режима. Тесты и экспорт OpenAPI не читают `.env`.
Compose подготовлен отдельно; health не проверяет PostgreSQL/Redis/VPN.
Корневой README заменён инструкцией запуска, оригинал сохранён в docs/source.
TECHNICAL_SPEC/ROADMAP/AGENTS/CODEX_START_PROMPT не изменены.

Уточнение после выбора владельца 2026-10-18: утверждение выше относится к исходному
каркасу I-001. TECHNICAL_SPEC v0.2 и пункт авторизации ROADMAP теперь намеренно
изменены по прямому требованию о входе кодом из бота; AGENTS и стартовый prompt сохранены.

## Обновление I-002 — 2026-10-18

R02–R04/R11 уточнены в ADR-001/002/003 и проверяемых receipts; R05 оставлен
prerequisite ADR-004/008. Найдены mixed-license native bundle, Go bootstrap
discrepancy и advisory findings declared dependencies. Это не исправления
upstream: baseline source зафиксирован для исследования, не для release.
См. [licensing checklist](licensing-checklist.md), [матрицу](compatibility-matrix.md),
[отчёт](verification-i-002.md). R01: предлагается зачесть существующий health в I-003
после review его проверок/CI; канонический ROADMAP не переписан автоматически.
