# ADR-064 — bounded packet observation for I-028 DNS/IPv6 audit

Status update 2026-10-31: ADR-072 removes this broad audit from the owner's
remaining personal I-028 completion checklist. This ADR remains the method for
future product security/release validation; its broad audit is descoped from
that personal checkpoint, not passed.

Статус: direct baseline и синхронизированные included/excluded пробы выполнены;
широкий packet audit по переходам и отказам остаётся незакрытым.

Для owner-scoped IPv4-only staging acceptance действует ADR-071: IPv6 leak-free
поведение не является критерием этой личной staging-приёмки и явно не считается
проверенным. Требования этого ADR остаются отложенным privacy gate для любых
более широких security/release утверждений.

Последний source-bound Egress run прошёл. В included AP-station capture были
121 VPN outer UDP пакет, ноль прямых DNS и ноль фиксированных probe source-port
пакетов; два aggregate IPv6 TCP/443 пакета нельзя приписать телефону или probe
UID. В этой included-пробе IPv6 завершился с `ipv6_failure=dns`, поэтому сам
IPv6 HTTPS probe socket не открылся. В paired excluded capture положительный
контроль увидел 16 прямых IPv4 DNS пакетов, один фиксированный DNS source-port
пакет и 14 direct-baseline IPv6 HTTPS source-port пакетов. Capture summaries:
`../evidence/i-028-egress-packet-audit-20261031T1652Z.json`; physical receipt:
`../evidence/i-028-egress-diagnostic-20261031T165654Z-85a5fc2c.json`.

Эти результаты закрывают controlled baseline/included/excluded сценарий и не
показывают утечку в included окне. Они не являются пакетной проверкой во время
переходов Wi-Fi/mobile или сетевого outage, и не доказывают отсутствие утечки
для IPv6 socket, который в included-пробе не был создан. Это пробел покрытия,
а не наблюдённая утечка. Не повторять уже прошедшие физические Doze, natural
sleep, переходы, revoke, renewal-disconnect или two-device сценарии ради этого
пункта; если широкий packet audit откладывается, записать его отдельным
открытым security gate.

Более ранний run `../evidence/i-028-packet-audit-included-20261031T1302Z.json`
остался неуспешным: Egress test завершился на DNS timeout до hold, а AP capture
увидел 1,083 VPN endpoint пакета, 1 прямой IPv4 DNS пакет и 178 aggregate IPv6
пакетов. Он не подтверждал ни утечку во время VPN, ни её отсутствие.

## Контекст

Фиксированные тестовые пробы уже проверяют IPv4/IPv6 egress и один синтетический
DNS-вопрос. Это подтверждает поведение probe UID на уровне ответов, но не
показывает, что телефон отправил по физической сети мимо туннеля. VPN-нода также
не является достаточной точкой наблюдения: прямой обходящий трафик может пройти
мимо неё.

## Решение

- Наблюдать трафик на контролируемой тестовой Wi-Fi сети до NAT, чтобы различать
  трафик телефона и трафик самого шлюза. VPN-ноду не считать заменой этой точки.
- Использовать только уже заданные фиксированные HTTPS/DNS-пробы и короткие
  bounded интервалы: direct baseline, включённый probe UID, переход/отказ сети и
  excluded UID как отдельный положительный direct-routing контроль.
- Собирать только агрегаты по семейству IP, протоколу и направлению/порту для
  тестового устройства. Не сохранять PCAP, payload, DNS-имена, полные адреса,
  MAC, произвольный logcat или список пользовательских приложений.
- Исключить IPv6 link-local, multicast и соседское обнаружение из категории
  routed Internet egress; оценивать global-unicast назначения отдельно.
- Автоматический режим I-028 выбирает ровно одну станцию на AP через `iw` и
  использует её MAC только в памяти для фильтрации на L2. MAC передаётся
  `tcpdump` через временный файловый дескриптор; он не попадает в аргументы
  процесса, вывод или receipt. Если станций нет или их больше одной, capture
  завершается без результата. В этом режиме staging endpoint берётся из
  фиксированного default, а UDP-порты ограничены слотами I-028 `51901` и `51902`
  (`51900 + slot_id`); порт `51892` — порт node-agent и не является tunnel
  endpoint. Ввод IP и портов не требуется.
- Поле `phase` в receipt — это операторская метка окна, а не автоматическое
  подтверждение того, что весь интервал принадлежал одной фазе. Если bounded
  window перекрывает baseline, включённый/исключённый probe или восстановление,
  его нельзя засчитывать как отдельный фазовый результат.
- Для точного начала included-окна helper может ожидать первый исходящий UDP
  пакет от выбранной AP-станции к фиксированному endpoint и слотам `51901/51902`.
  До trigger строки пакетов разбираются только в памяти и отбрасываются; затем
  начинается отдельный короткий aggregate-интервал. Ожидание ограничено 240
  секундами, отсутствие trigger — ошибка, а не нулевой успешный результат. Этот
  режим сохраняет только исходные агрегаты и не меняет физические критерии.
  Receipt также содержит UTC wall-clock начала/конца capture и времени trigger,
  помеченный как часы capture-хоста. Это помогает сопоставить фазу, но без
  независимой проверки синхронизации часов не доказывает точный порядок событий.
  Локальная проверка режима записана в
  `../evidence/i-028-packet-audit-tooling-20261030T225537Z.json`; она использует
  только синтетический pipe и не является физической приёмкой.
- Если точка наблюдения до NAT или положительный direct IPv6 baseline недоступны,
  соответствующий критерий оставить открытым. Не заменять его handshake,
  успешным DNS-ответом или счётчиком на VPN-узле.

## Критерии

1. Direct baseline той же probe UID подтверждает доступность IPv6 и фиксированного
   DNS-запроса через выбранную Wi-Fi сеть.
2. Включённая через VPN probe UID создаёт ожидаемую outer-транспортную активность,
   но не создаёт прямых routed DNS/IPv6 пакетов на наблюдаемом внешнем интерфейсе.
3. Excluded UID отдельно подтверждает, что точка наблюдения видит direct traffic;
   этот ожидаемый результат не засчитывается как утечка включённого UID.
4. В коротких переходах/отказах сохраняются только счётчики и итоговые категории;
   неопределённый или неполный capture не принимается.

### Маркеры фиксированных probe-сокетов

Тестовый `I028Probe` закрепляет source port `42428` для фиксированного UDP DNS
запроса, `42430` для direct-baseline IPv6 HTTPS и `42429` для included IPv6
HTTPS. Packet auditor сохраняет только количество исходящих пакетов с этими
портами; адреса, MAC, имена, заголовки и payload не сохраняются. Это позволяет
отличить именно контролируемые probe-сокеты от фонового трафика той же AP-
станции. Маркер не является захватом Android UID в целом: direct-baseline
capture должен показать положительные source-port counters, а included capture
должен показать их отсутствие для probe-потоков при наличии VPN endpoint
трафика. Остальные DNS/IPv6 агрегаты остаются отдельными сигналами и не должны
приписываться probe UID без такой корреляции.

Положительный capture без внешнего endpoint-пакета не доказывает VPN-фазу:
операторская метка без временной привязки остаётся диагностической. Для
included-запуска используйте `--wait-for-vpn`; в принятом интервале должны быть
видны endpoint-пакеты, а direct DNS/global IPv6 оцениваются только после
trigger.

Физический запуск требует доступной контролируемой Wi-Fi observation point и
синхронизированного окна с тестовым Android. До такого запуска никаких новых
серверов, DNS-зон, аккаунтов или capture-хостов не создаётся.

Последний физический Egress запуск прошёл staging IPv4, но его фиксированный
UDP DNS probe завершился timeout. Отдельный синхронизированный DNS observer
позже получил ответ без потерь. Эти наблюдения не доказывают, что DNS
исправлен или что причиной был именно DNS resolver; проверка IPv6 включённого
UID также осталась неразрешённой (`ipv6_failure=dns`). После локальной правки
harness сохраняет bounded included window при этом одном точном timeout
сценарии, затем всё равно завершает suite неуспешно. Следующий физический
захват должен покрыть полный hold до disconnect.

Оператор также сообщил о потере global IPv6 после тестов до переподключения
Wi-Fi. Пока немедленная post-test Probe отсутствует, это не доказывает ни
отключение IPv6 в VPN, ни утечку, ни DNS-причину. При следующем воспроизведении
сначала снять phone-only direct Probe без переключения Wi-Fi, затем отдельно
проверить восстановление; не смешивать это с included packet capture.

2026-10-31 source-port positive control: a 120-second operator-provided
baseline capture overlapped the physical phone-only Egress Probe. The auditor
counted 28 packets on the direct-baseline IPv6 HTTPS probe port and 2 on the
fixed UDP DNS probe port; the Probe independently reported direct Wi-Fi IPv4,
IPv6, and UDP DNS available and removed its APKs. The capture had no VPN
endpoint, as expected for baseline, and hit no packet cap. This validates
source-port correlation for those fixed probe sockets only. It does not
attribute the 491 aggregate global-IPv6 packets to the Android UID, nor prove
included/excluded leak behavior. Next physical gate: a synchronized included
capture must observe VPN endpoint traffic and zero packets on the fixed direct
DNS and included-IPv6 probe ports. Evidence:
`evidence/i-028-packet-audit-source-port-baseline-20261031T1418Z.json` and
`evidence/i-028-probe-after-startup-fix-20261031T141909906Z.json`.

2026-10-31 included source-port capture: after the VPN outer trigger the
90-second AP-station window saw 5,529 outer UDP packets, 6 aggregate direct
IPv4 DNS packets, 133 aggregate global IPv6 packets, and zero packets on the
fixed DNS or included-IPv6 probe source ports. The associated Egress suite
failed during `egress_hold_refresh_probe`: the fixed IPv6 probe had already
reported DNS failure, and the later IPv4 hold probe did not confirm staging
egress. Thus the zero IPv6 counter is inconclusive because the IPv6 socket
flow was not reached; aggregate DNS/IPv6 packets remain unattributed. The
capture is diagnostic only and does not pass the included or comprehensive
DNS/IPv6 gate. The sanitized receipt records quota headroom falling from 3,004
bytes to zero across the failed hold probe; cleanup and timer disarm passed.
Evidence: `evidence/i-028-packet-audit-included-source-port-20261031T1431Z.json`
and `evidence/i-028-egress-diagnostic-20261031T143401Z-5344375a.json`.

2026-10-31 synchronized included rerun: the AP-station capture observed 7,808
VPN outer UDP packets, zero direct DNS packets, and zero packets on the fixed
DNS and included-IPv6 probe ports. Five aggregate global IPv6 TCP/443 packets
had no fixed source-port match and cannot be attributed to the included test
UID. The associated Egress receipt passed its scoped checks and cleanup, but
remains diagnostic-only. Do not interpret this as a leak or leak-free result
for all phone traffic. Evidence:
`evidence/i-028-packet-audit-included-source-port-20261031T1515Z.json` and
`evidence/i-028-egress-diagnostic-20261031T152004Z-5995667a.json`.
