# ADR-033 — I-019: два staging-сервера одного провайдера

Status: accepted by explicit owner instruction, 2026-10-22.

Владелец разрешил проверять I-019 на двух имеющихся серверах одного
провайдера; сервер другого провайдера появится позже. Адреса восстановлены
из чата «Продолжить работу с VPN-сервером» и текущего staging inventory:
A — 45.10.166.106, B — 85.209.132.205. SSH user — vpn-deploy.
Секреты и production root credentials не запрашиваются.

Это уточняет live gate ADR-031: допустима отдельная приёмка отказа узла
и ручного переключения между этими серверами. Устойчивость к отказу провайдера
остаётся открытой. Разные страны указаны владельцем, независимо не проверены.
Название провайдера не установлено; его не следует угадывать по IP.

Остальные ограничения сохраняются: один тестовый peer на узел, lease до 60s,
явный recovery после deadline+10s, полный debit при потере счётчиков,
physical Android, fresh egress/cutoff/restore probes, ledger и cleanup.
Остановка допускается только для выделенного тестового runtime A;
существующие контейнеры и пользовательские VPN не затрагиваются.

Предварительная read-only проверка подтвердила SSH, TUN и NTP на обоих
хостах. B не имеет /opt/project-vpn/current; установка ещё не выполнена.
На обоих хостах sudo -n true требует интерактивной авторизации.
Android отсутствует в adb. На B обнаружен reboot-required; перезагрузка
не выполнялась и этим решением не назначается.

Старые I-010/011/012 scripts/playbooks закреплены за A. Нельзя подменять
inventory или отключать guards, чтобы запустить их на B. До provisioning B
нужна проверенная процедура с явным approved target, pinned artifacts,
проверкой baseline и scoped local sudo. До live drill нужны рабочий
двухузловой runner и физический телефон. Наличие двух SSH-сессий не является
VPN-приёмкой; текущий статус I-019 остаётся незавершённым.

## Подготовленный bounded runner

Продолжение 2026-10-22: artifacts B установлены и независимо проверены по четырём
SHA-256. Физический Android подключён; live failover пока не запускался.

I-019 использует отдельные `vpn-i019`, `v19host/v19ns/v19awg`, UDP 51893,
`project-vpn-i019{,-agent}.service` и `/run/project-vpn-i019*`. Старый I-011
по умолчанию остаётся A-only; B доступен только через явный I-019 opt-in.
Используются прежние pinned AWG artifacts и kernel adapter, без изменения
криптографии, native-клиента или production API. Новых зависимостей нет.

Root-owned host JSON допускает только A/B. Временное sudoers-разрешение даёт
доступ лишь фиксированным действиям i019_control.py; internal systemd actions
нельзя вызвать через sudo grant. Grant связан с UID, host, boot ID, wall/monotonic
deadline и хешами исходников/units. Срок — 45 минут, без автоматического продления;
expiry останавливает выделенные runtime и удаляет grant/секретные agent files.
Каждый endpoint/agent отдельно ограничен 600s, peer lease — до 60s.

До сетевой мутации выполняется privileged read-only preflight. Forwarding
фиксируется до изменения; ExecStopPost восстанавливает его даже при ошибке
namespace cleanup. Повторный внешний stop проверяет фактическое значение по
private restoration record. На A сохраняются проверки двух прежних контейнеров.
На B отсутствие Docker/containerd/podman проверяется, а не подменяется fake rows.
Поддержан только iptables-nft: если до теста filter/nat отсутствовали, удаляются
лишь созданные тестом пустые стандартные таблицы с policy accept. Любой foreign
rule/set/map/chain/policy блокирует удаление; исходные таблицы не удаляются.

Physical test — отдельный debug integration entrypoint. Он использует реальный
Flutter ConnectionController, Android engine, local API/PostgreSQL и staging agents;
Telegram delivery явно TEST FAKE ONLY. Coordination routes существуют только
в opt-in pytest app на 127.0.0.1, production routes/APK не дополняются.
Сценарий: заморозка агента A + отключение reverse API channel + kernel expiry;
recovery; свежий runtime A + остановка до lease deadline + fresh cutoff;
recovery/replay; B handshake/egress; возврат A с новым runtime/key identity;
отсутствие старого peer; B revoke/observed counters; ledger и cleanup.
Сначала B handshake/egress и revoke, затем полный A→B сценарий.
Текущая тестовая квота — 64 MiB, каждый из четырёх intent — до 16 MiB.
Часы A/B/API проверяются реальными timestamp round trips с верхней границей 1s.

Нет утверждения о seamless/automatic failover, независимых провайдерах или
повторной полной DNS/IPv6/network-switch матрице I-011/I-013. Ни сборка, ни
симуляции OS metadata не заменяют фактический live receipt I-019.

## Одна диагностическая сессия по просьбе владельца

Владелец попросил однократный локальный ввод двух sudo-паролей, после которого
Codex самостоятельно наблюдает, диагностирует и исправляет локальный код.
Опциональный `-DiagnosticSession` сохраняет прежний fixed-action grant после
неудачной попытки, но обязательно останавливает оба runtime, проверяет baseline,
forwarding и отсутствие owned resources; телефон, reverse channels, private
profiles и PostgreSQL очищаются как прежде. Следующая попытка разрешена только
после подтверждённой очистки и рассмотрения причины отказа. Нет автоматического
цикла повторов. Каждая попытка сохраняет отдельный receipt, fixed stages и
безопасные признаки состояния публикуются атомарно по ходу теста.

45-минутный wall/monotonic deadline исходного grant не меняется; endpoint/agent
по-прежнему ограничены 600s, peer lease 60s. Успешный полный drill удаляет grants.
При отказе от дальнейшей диагностики Codex вызывает `-FinishSession`: оба хоста
очищаются независимо от ошибки первого. Если Codex прервётся, прежний root timer
останавливает runtime и отзывает grants по исходному сроку.

Remote source/unit hashes остаются закреплёнными. Можно менять локальный клиент,
тесты и test-only API, после проверок пересобирать APK и повторять нужный тест
в оставшемся окне. Изменение привилегированного remote кода, истечение grant или
reboot потребует новой owner-run подготовки. Не выдаётся root shell, не хранится
sudo-пароль и не появляется remote code-upload/execution endpoint.

HTTPS egress acceptance требует два последовательных ответа с IP выбранного
узла через новые HttpClient (force-close после каждой пробы). Допускаются не
более трёх проб, между ними 500ms; перед пробой требуются свежий handshake и
>=15s оставшегося lease. Ответ с IP другого узла/обычной сети сразу завершает
проверку ошибкой. Null/timeout не считается успехом. Эта ограниченная проверка
отделяет восстановление пути после смены Android VPN от готовности peer;
она не утверждает, что handshake доказывает egress. Сохраняются только fixed
phase/result, HTTP status и bounded OS error number, без URL/текста исключений
или адреса обычной сети.

В диагностическом прогоне 25617153e6194d68a83071b1c7854a43 после первого
успешного B egress два следующих запроса получили socket error при свежем
handshake. Реальный remove_peer ACK дал observed/charged=995864 при lease
budget=1048576. Kernel budget учитывает encrypted UDP, counters — данные AWG;
результат согласуется с исчерпанием outer budget из-за общего трафика телефона.
Промежуточный physical entrypoint запрашивал 4 MiB, обычный клиент по умолчанию
сохраняет 1 MiB. Constructor
допускает лишь прежний API диапазон 64 KiB–16 MiB, значение закрепляется в intent
и replay. Общий баланс теста остаётся 16 MiB: четыре reserve по 4 MiB не расширяют
общий bound. Lease/deadline/kernel enforcement не увеличиваются; lost-counter
recovery по-прежнему списывает полный резерв, теперь 4 MiB для physical test.

Последующая попытка 86d65a3943524a0a9286aafb3148c32a остановилась на повторном
A egress после одного успешного HTTPS ответа при свежем handshake. Полного
node-failure acceptance нет; одна предыдущая попытка также выявила неподтверждённый
повторный add_peer на B. Для отделения byte-limit от node-failure следующий
physical build использует прежний максимум адаптера 16 MiB на lease, общий
тестовый баланс 64 MiB на четыре intent. Это новый явно ограниченный тестовый
бюджет, не изменение диапазона API, default клиента, deadline или kernel gates.
Эта версия ещё требует physical acceptance; counters/ACK не подменяются.

Режим `VPN_I019_B_REUSE_DIAGNOSTIC=true` проверяет два настоящих последовательных
B lease в одном runtime, реальные revoke/counters/ledger и возврат обычной сети.
A не запускается; в API существует лишь тестовая DB-запись A для прежнего формата
coordination. Receipt имеет mode=b_reuse_diagnostic_only и отдельное имя;
его passed не является приёмкой полного I-019. При diagnostic session даже
успешная короткая проверка сохраняет исходные grants для дальнейшего полного
drill, без продления срока. Итоговый полный success или FinishSession удаляет их.

Инкрементальная сборка допускает изменение лишь integration entrypoint: прежний
source stamp пересчитывается с cached target и сверяется с metadata полного
прошедшего build. Любое изменение остальных tracked inputs запрещает fast path.
После копирования нового target выполняются format/analyze, все Flutter tests
и physical APK build, затем повторная проверка исходников перед новым stamp.
Обычные APK/native inputs не меняются; source guard не отключается.

## Проверка возврата обычной Android-сети, 2026-10-23

Первый B-only diagnostic 4e6a9ba5f6684a0283fe2c40be03882e подтвердил B egress
двумя запросами и реальный remove_peer ACK. После disconnect native down,
hasLease=false, HTTPS возвращал корректный другой IP, но он не совпадал с
IP до подключения. Независимый readonly Android check показал current default
network NOT_VPN + VALIDATED, transport VPN отсутствует. Причина смены public IP
не устанавливается; предположение о неизменности внешнего IP больше не используется.

Restore acceptance: native down; текущая default network Android однозначно
идентифицирована, transport не VPN, capabilities NOT_VPN и VALIDATED; свежий
HTTPS возвращает синтаксически корректный IP, отличный от A/B. Неизвестный /
неоднозначный dump или отсутствие любого признака — fail closed. Базовая сеть
также проверяется перед первым connect. Current capabilities читаются только
test-only loopback route через фиксированный adb dumpsys connectivity; raw dump,
SSID, адреса, proxy/DNS и история в receipt не сохраняются, только пять boolean.
Это замена нестабильного условия public-IP equality, не имитация восстановления.
