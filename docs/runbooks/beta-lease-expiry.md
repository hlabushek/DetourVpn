# «Срок подключения истёк» / VPN сам выключается

Статус на 2026-11-10: исправление в `0.1.0-beta.26`, на телефоне не проверено.

## Как это устроено

Подключение выдаётся окнами до 90 секунд (API `MAX_WINDOW_SECONDS`, узел `window_gate.py`,
приложение `window_connection.dart` и `ClientApplication.java`). Приложение продлевает окно таймером
каждые ~2 с; нативный таймер жёстко отключает VPN за 6 с до конца окна. Если таймер приложения не
сработал (Android усыпил приложение в фоне), туннель гаснет.

## Гипотезы (по убыванию вероятности)

1. Doze / экономия батареи усыпляет таймер продления при выключенном экране.
2. Приложение подписывало «срок истёк» любую остановку VPN системой (другой VPN, ручное
   выключение, перезапуск службы). С beta.25 такая остановка называется «Системный VPN остановлен».
3. Сеть пропала дольше ~50 с (смена Wi-Fi/мобильная сеть), продление не дошло до API.
4. Лимит трафика окна (1 МиБ по умолчанию) исчерпан, продление не успело: даёт паузу, не «срок истёк».

## Что сделано в beta.25

- Автопереподключение после настоящего истечения окна (до 4 раз за 10 минут, не при ручном
  «Отключить»).
- Системный запрос «работать без ограничений батареи» при первом подключении (до 2 раз).
- Различение «окно истекло» и «Android остановил VPN».

## Что проверить дома

1. Поставить beta.25 (`.\scripts\release_beta.ps1 -NoDeploy -Install`), разрешить запрос батареи.
2. Включить VPN, выключить экран на 5–10 минут, включить экран. Какой текст на экране?
   - «Системный VPN остановлен»: причина в системе (другой VPN, Always-on, убийство службы), не в окне.
   - Подключение восстановилось само: помогло автопереподключение.
   - Снова «Связь прервалась...»: окно всё же истекает; снять логи
     `adb logcat -s DetourVpnNativeCall DetourVpnNativeInit` и смотреть настройки батареи
     производителя (Xiaomi/Huawei/Samsung держат свои «автозапуск» и «защиту»).
3. Если всё ещё рвётся: единственное надёжное лечение — увеличить окно (например до 10–15 минут).
   Это изменение протокола: API, все три узла (`window_gate.py`, `window_daemon`), приложение
   (Dart и Java), тесты. Скрипта обновления узлов пока нет: сначала нужен он.

## Update 2026-11-10: long windows (ADR-096)

Code for windows of up to 900 s is in place but opt-in (`window_seconds`, `-WindowSeconds 900`).
Rollout order: all node agents first, then the API, then a client built with `-WindowSeconds 900`.
This alone does not keep the tunnel alive under Doze; that needs native renewal (plan step C2).

## Update 2026-11-11: native renewal and service restore (ADR-097, ADR-098)

- Builds with `-WindowSeconds 900` renew from the native VPN process (ADR-097).
- The VPN service is sticky and restores itself after a process kill. After a reboot it restores
  only if the user left it connected. Always-on is enabled in release (ADR-098).
- Check on a device:
  - screen off for 60 minutes or more;
  - swipe the app away;
  - `adb shell am kill` while the screen is off;
  - reboot with the VPN on, then with it off.

## Update 2026-11-11: network changes and stalls (ADR-099)

- A Wi-Fi/mobile switch restarts the tunnel in place after 3 s. No handshake for 180 s with a
  working network also restarts it.
- The app reconnects with backoff and no hard limit, but never after VPN permission was revoked.
- Check on a device:
  - switch Wi-Fi and mobile 5 times;
  - turn airplane mode on for 1 minute, then off;
  - disable Wi-Fi on the router for 5 minutes while on mobile.
  - each time, traffic must resume without opening the app.
