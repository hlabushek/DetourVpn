# A5 — Phone checklist for survivability phases 1-6 (ADR-144..149, plus ADR-150..154 fixes)

For the owner, on one Android phone with a build from a **committed** commit of
`claude/connection-reliability` (record `git rev-parse --short HEAD` and the app version).
Nothing here was run by the agent. Every result below is an **expectation from code and ADRs**.

## How to record (same for every row)

- Result: pass / fail / not run, plus the local time.
- Evidence: the event-log part of the diagnostic report (app menu, «Поделиться диагностикой»).
  It contains only ages and codes, for example `-12s window_up`. Copy only the lines named in the
  row. Do not copy IP addresses, node names, screenshots of other apps, or anything else.
- If a row fails: note the screen text (Russian, exact) and the last 20 event-log lines.

## Phase 1 — native state and single owner (ADR-144, ADR-151)

| # | Do | Expect | Record |
|---|---|---|---|
| 1.1 | Fresh start, tap «Подключить», wait. | «Подключено» within about 1 s of the first handshake (ADR-151), not after 15 s. | `window_up`, the first `state connected verified`, the age gap between them. |
| 1.2 | Leave connected and idle for 5 min, screen on, no traffic. | Stays «Подключено» (no flip to «Переподключение» at 60 s or 150 s). | Any `state recovering ...` lines (should be none). |
| 1.3 | Swipe the app away from recents while connected; wait 2 min; reopen. | VPN key icon stays; reopened app shows «Подключено» at once. | `owner native` / `owner ui` changes, no `stopped`. |

## Phase 2 — initial connect hardening (ADR-145, ADR-154)

| # | Do | Expect | Record |
|---|---|---|---|
| 2.1 | Clean install, first «Подключить». | VPN consent first, then (Android 13+) the notification request, then «Работа в фоне» and the battery screen. Denying notification or battery still connects. | Which dialogs appeared, in order. |
| 2.2 | Background the app 1 min, return, tap «Подключить» at once (ADR-154). | The first tap shows busy, then connects; a second tap is not needed. | Number of taps needed. |
| 2.3 | Deny VPN consent. | No connect attempt, a plain message; no API call made (no `window_up`). | Screen text. |

## Phase 3 — Doze overnight (ADR-147, ADR-150 3600 s windows)

| # | Do | Expect | Record |
|---|---|---|---|
| 3.1 | Android 14+: grant «Будильники и напоминания»; connect; screen off overnight on charge or battery. | Still connected in the morning; regular native renewals; no `renew_alarm_fallback`. | Count of `native_renewal` success lines, any `stopped` with reason. |
| 3.2 | Same night test without the exact-alarm permission (second night or second phone). | Still renews (fallback timing); gaps may be longer. | Same as 3.1 plus `renew_alarm_fallback` count. |
| 3.3 | Airplane mode 20 min while connected, then off. | Notification «DetourVPN восстанавливает подключение» while offline; `backstop` entries; VPN back within about a minute; the notification goes away. | `backstop` count, time to `window_up`. |
| 3.4 | Disconnect from the app. | No companion notification, no further `backstop` entries. | Any `backstop` after `user_disconnect`. |

## Phase 4 — network roaming (ADR-146, ADR-153) — includes the A4 item

| # | Do | Expect | Record |
|---|---|---|---|
| 4.1 | Connected on Wi-Fi, turn Wi-Fi off (switch to mobile data). | `recovering network_changed` for a few seconds at most, then «Подключено» on mobile data. | `network_available changed`, `watchdog_restart stale_or_network`, `underlying_set`, next `state connected`. |
| 4.2 | **A4: restart order.** Right after 4.1, open Android Settings → Network → VPN / data usage (or `dumpsys connectivity` if the owner uses adb): the VPN network should report the mobile network as its underlying network, and metering should follow mobile data. | After each `watchdog_restart` the log shows `underlying_set` (not `underlying_skipped` or `underlying_failed`). If `underlying_set` is missing after a restart and appears only after the next tick, note it: it means the tunnel was not "up" yet right after `restartInPlace`. | The lines between `watchdog_restart` and the next `state connected`. Pass only if `underlying_set` follows every restart. |
| 4.3 | Switch back to Wi-Fi, then quickly back to mobile twice within 10 s. | No `watchdog_new_lease` unless two restarts got no handshake; ends «Подключено». | Count of `watchdog_restart`, any `watchdog_new_lease`. |
| 4.4 | Disconnect, reconnect within 10 s (ADR-153). | The new window is not restarted or given up at its first tick. | Any `watchdog_restart` or `watchdog_new_lease` in the first 15 s after the new `window_up` (should be none). |
| 4.5 | Lose network (airplane mode) for 5 s, then back. | Stays «Подключено» during the 8 s loss confirmation; no `network_loss_confirmed`. | `network_lost`, `network_available`, any `network_loss_confirmed`. |

## Phase 5 — dead peer and captive portal (ADR-148, ADR-198)

| # | Do | Expect | Record |
|---|---|---|---|
| 5.1 | Normal shipped build (probe URL `none`). Connect; check the log start. | `probe_config off`. No `probe_start` ever. Stalls handled by the watchdog only. | `probe_config ...` line. |
| 5.2 | **ADR-198 decision input.** Only with a test build whose `DEAD_PEER_PROBE_URL` is a project-owned HTTPS endpoint the owner set up. Create a suspected stall (for example a staging node with an authorised drop rule; needs explicit staging authorisation). | Expected by the Android rules: `probe_start` followed by `probe_unavailable bind_refused`. If instead `probe_pass` / `probe_fail io` appear, the bind works and ADR-148 can be enabled as designed. | The `probe_*` lines only. This decides ADR-198. |
| 5.3 | Join a Wi-Fi with a sign-in page (hotel or café), VPN wanted. | State «Сеть требует входа (страница авторизации Wi-Fi)…»; no restart loop; after sign-in the VPN connects by itself. | `network_captive_portal`, any `watchdog_restart` before sign-in (should be none), time to `window_up` after sign-in. |

## Phase 6 — Lockdown, boot and update (ADR-149, ADR-152)

| # | Do | Expect | Record |
|---|---|---|---|
| 6.1 | Android Settings: Always-on + «Блокировать соединения без VPN» on. Connect from the app; screen off past one renewal. | Native renewal over the protected path, no refusal; `underlying_set` after connect and after a network switch. | `native_renewal` lines, `underlying_set` vs `underlying_skipped no_service`. |
| 6.2 | Same with Lockdown off. | Native renewal works (direct path). | Same lines. |
| 6.3 | Connected, reboot the phone, do not unlock for 2 min, then unlock. | Nothing connects before unlock (with Lockdown: no app has network before unlock); after unlock `restore_boot restore` and «Подключено». | `restore_boot ...`, time from unlock to `window_up`. |
| 6.4 | Connected, install an app update (in-app update or side-load). | VPN comes back without opening the app: `restore_update restore`. If disconnected before the update: `restore_update not_wanted`, VPN stays off. | `restore_update ...`. |
| 6.5 | ADR-152: open the split-tunnelling app list (many apps installed) while connected near a renewal. | Renewal is not ended; at most `native_renewal_wait worker_busy`, then success. | Any `native_renewal` stop with `tunnel_stopped` (should be none). |

## Not covered here

- Windows client, server-side checks (see A7), and any staging silent-drop test without explicit
  staging authorisation.
