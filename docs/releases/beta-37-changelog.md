# DetourVpn 0.1.0-beta.37 (code 37) — changelog

Status: **version bumped, not released.** `pubspec.yaml` is `0.1.0-beta.37+37` and
`build.gradle.kts` is code `37`. Unit-tested and build-verified only. Not verified on a phone or a
real Windows PC. No tunnel handshake was observed. No signed APK was built and no update manifest
was published. The Windows zip is unsigned. The owner's release steps are in
`docs/runbooks/beta-37-release.md`.

Commits since beta.36 (`9f22e54`): `b69de0a`, `79d1c47`, `e745d6e`, `ab66e17`, `df5c0c1`, plus
the version bump.

## Для тестеров (текст на русском)

Что нового в 0.1.0-beta.37:

- **Подключение стабильнее.** Временные ответы сервера (сервер занят, окно ещё не продлено,
  прошлое подключение ещё закрывается) больше не обрывают VPN: приложение ждёт и повторяет.
- **Главный экран понятнее.** Заголовок, круг и главная кнопка всегда показывают одно и то же
  состояние. При переподключении есть кнопка «Подключить сейчас», при ошибке входа — «Войти
  снова», при закончившемся трафике — «Открыть трафик».
- **Обновление внутри приложения (Android 13 и новее).** Баннер о новой версии, загрузка с
  реальным прогрессом, кнопка отмены. Установка начинается только после вашего нажатия.
- **Android: тёмная панель навигации** внизу экрана, в цвет приложения.
- **Windows: автоматическое восстановление.** Если движок VPN упал, завис, сменилась сеть или
  компьютер вышел из сна, приложение само перезапускает туннель.
- **Windows: резервные адреса сервиса** работают и при включённом VPN.
- **Windows: новая настройка «Блокировать трафик при обрыве».** По умолчанию **выключена**.
  Если включить, то при обрыве VPN интернет блокируется, пока VPN не восстановится или пока вы не
  нажмёте «Отключить» (или не выключите настройку). Действует со следующего подключения.

Что проверить, пожалуйста:

1. Обычное подключение и работу в течение часа; переключение Wi-Fi ↔ мобильная сеть.
2. Android 13+: баннер обновления, прогресс загрузки, отмена, установка.
3. Windows: сон/пробуждение ноутбука, смена сети — VPN должен восстановиться сам.
4. Windows, настройка «Блокировать трафик при обрыве» **включена**: завершите процесс
   `detour-engine.exe` в диспетчере задач. Пока VPN не восстановился, сайты **не должны**
   открываться. После «Отключить» интернет должен сразу работать напрямую. Не присылайте свои
   IP-адреса — только «работает / не работает».
5. Windows, настройка **выключена**: всё работает как раньше.

Известные ограничения:

- Блокировка трафика на Windows пока не проверена на реальных компьютерах — поэтому она
  выключена по умолчанию.
- На Windows закрытие окна приложения отключает VPN (режима работы в трее пока нет).
- Сборка для Windows не подписана: Windows может показать предупреждение при запуске.
- Обновление внутри приложения работает только на Android 13 и новее.

## Changes (English)

### Android and shared client
- **Connection not ended by recoverable answers** (ADR-191). `202 revoke_pending`, 503 codes and
  `window_not_renewable` no longer end the user's connection; a pending remote close is waited for
  and retried; Android adopts a running tunnel without native renewal; the renew hand-off is
  retried. (`b69de0a`)
- **Home-screen state mapping** (ADR-192). Title, orb and primary button come from one mapping.
  Reconnecting without a lease offers «Подключить сейчас»; sign-in errors offer «Войти снова»;
  quota errors offer «Открыть трафик». (`b69de0a`)
- **In-app update banner and sheet** (ADR-193). Real byte progress, cancel, explicit install; a
  partial download is promoted only after all ADR-107/188 checks pass; the installer opens only on
  a user tap. Android 13+ only. (`b69de0a`)
- **Android navigation bar colour**: dark system navigation bar that follows the in-app theme.
  (`b69de0a`)

### Windows client
- **Watchdog parity with Android** (ADR-194). Crashed engine restarted in place (at most 2 times
  per 10 min), then a new lease as before. Network change or resume from sleep restarts the tunnel
  unless a newer handshake is seen; after two restarts without a handshake the app reconnects on a
  new lease. No handshake for 180 s restarts the tunnel, backoff 2 to 10 min. Handshake rules run
  only when the engine reports peer stats (VLESS unchanged). Window deadlines count sleep.
  (`79d1c47`)
- **Fallback API origins bypass the tunnel** (ADR-195, W-05). Without fallbacks nothing changes.
  (`e745d6e`)
- **Optional kill switch** (ADR-196, W-01), setting «Блокировать трафик при обрыве», **opt-in,
  OFF by default**. Confirmed in code for this release, behaviour unchanged:
  - Dart `KillSwitchSetting.read()` returns `false` for a missing value, `PlatformException` or
    `MissingPluginException`; the settings row starts with `_enabled = false`.
  - Native `Prefs::kill_switch` defaults to `false`; only the exact prefs line `kill_switch\t1`
    turns it on; with the setting off the WFP backend is never created or called.
  When on, a dynamic-session WFP block keeps non-tunnel traffic blocked while the VPN is wanted
  (engine crash, in-place restart, recovery gaps). If the block cannot be installed, the VPN does
  not start (`kill_switch_failed`). **Not leak-tested.** (`df5c0c1`)

### Docs
- ADR-191, ADR-192, ADR-193, ADR-194, ADR-195, ADR-196; ADR-095 and I-032 limits updated;
  release runbook `docs/runbooks/beta-37-release.md`.

## Not verified
- No physical-phone run of beta.37: connection fixes, home states, update banner/progress and the
  navigation bar colour are unit-tested only; `ClientActivity.java` was not compiled in `b69de0a`.
- No real Windows PC run: watchdog, sleep/resume, network change, fallback bypass and kill switch
  are unit-tested against fakes only. The kill switch has never changed a real firewall.
- No tunnel handshake, egress or leak result was observed for this version.
- No signed APK of beta.37 exists yet; the in-app update path for 36 → 37 is untested.
- The test suite was not re-run for the release step (owner: tests come with the update). The
  Windows build script runs its own built-in format/analyze/test gate; see the runbook.

## Known gaps, not in this release
- **Kill switch is opt-in and not leak-tested** (W-01). Off by default until the owner's leak test
  (ADR-196) passes. With it off, when the engine dies or restarts, traffic can go direct until it
  is up again.
- **No tray mode on Windows** (W-02, tray part). **Deferred to beta.38.** Reason: hiding the window
  to the tray keeps the VPN running with no visible state, and the only protection against a
  direct-traffic gap (the kill switch) is not leak-tested and off by default. Tray mode should land
  after the kill-switch leak test. Closing the window still stops the VPN.
- **No dead-peer probe** (A-03). Inert on Android (`DEAD_PEER_PROBE_URL` = `none` in the manifest)
  and absent on Windows. It needs a **project-owned HTTPS health endpoint from the owner**; no URL
  was invented. On Windows the stall rule is the 180 s stale-handshake restart.
- **Windows zip is unsigned** (UAC and SmartScreen warnings; no auto-update on Windows).
- **In-app update is Android 13+ only.** Older Android versions need a manual APK install.
- Unchanged from beta.36: A-01 (renewal after token rotation 401), A-06 (Play exact-alarm/FGS
  policy), B-01 (API high availability).
