# ADR-192 — One mapping for the home-screen status, orbit and button

**Status:** accepted, **not committed** (owner review). Code in `apps/client/lib/primary_action.dart`
(`HomeState`, `HomeView`, `resolveHomeView`) and `apps/client/lib/home_screen.dart`; tests in
`apps/client/test/primary_action_test.dart` and `apps/client/test/home_state_test.dart`.
**Not built into an APK, not checked on a phone.**

## Context
The title came from the phase alone, the orbit from a separate rule, and the button from
`resolvePrimaryAction`, which did not see the phase. The three disagreed after errors:
- a running disconnect showed «Отменить подключение» under «Подключено» (the tap was ignored);
- a tap waiting for a poll (ADR-154) showed «Отключено» with «Отменить подключение»;
- a failed attempt showed «Повторить» while every error text says «нажмите «Подключить»»;
  the tap was the same plain connect;
- a pending server close showed «Отключено» or «Восстановление связи» with «Повторить», which
  actually retries the server close;
- restoring without a lease (resume or native restore pending) showed «Подключить»;
- a connected panel session (Windows, no lease) showed «Подключить» under «Подключено»;
- after a failure the subtitle could still show the last step («Ожидается handshake»).

## Decision
`resolveHomeView` maps (phase, busy, disconnecting, lease, pending close, error) to one state,
and each state has one title, orbit and action. Every action has one label everywhere:

| State | Title | Button | Tap |
|---|---|---|---|
| disconnected | Отключено | Подключить | connect |
| failed (nothing held) | Ошибка подключения | Подключить | connect |
| failed/off, `unauthorized` or `session_device_mismatch` (nothing held) | unchanged | Войти снова | sign out (existing `onSignOut`, to the login screen) |
| failed/off, `insufficient_quota` (nothing held) | unchanged | Открыть трафик | Traffic tab (`onOpenTraffic`) |
| connecting (running, or waiting for the first handshake) | Подключение | Отменить подключение | disconnect |
| connected | Подключено | Отключить VPN | disconnect |
| recovering, lease or close held, or a connect running | Восстановление связи | Отключить VPN | disconnect |
| recovering, nothing held (resume or native restore pending) | Восстановление связи | Подключить сейчас | connect |
| disconnecting | Отключение | Отключение… (disabled) | none |
| closing (VPN off, server close unconfirmed) | Закрытие подключения | Повторить закрытие | retryRevoke |
| stop needed (lease held or a failed native stop) | Требуется действие | Отключить VPN | disconnect |

Connect is still never offered while a lease is held or a close is unconfirmed. The callbacks
are the controller's existing `connect`, `disconnect` and `retryRevoke`; only which one a state
offers changed for two states (recovering without a lease and connected panel sessions now
disconnect instead of connect). `ConnectionController.disconnecting` is a new display-only getter.
Two texts now name the real button (`device_clock_skew`, native `no_handshake`).

## Security considerations
No change to tunnel, lease, renewal, revoke or error-code behaviour. A disconnect from
«Восстановление связи» ends the user's wish (ADR-124), as the same button already did while a
lease was held.

## Decided (owner's delegate)
- A: a restore with no lease and no unconfirmed close offers «Подключить сейчас», the existing
  `connect` (a user tap resets the automatic-retry budget). With a lease, a pending close, or a
  connect already running (`connect` would ignore the tap) it stays «Отключить VPN».
- B: `unauthorized` / `session_device_mismatch` offer «Войти снова»; `insufficient_quota` offers
  «Открыть трафик» and no connect. A held lease or pending close still takes precedence. The
  subtitle names the step. Other errors keep «Подключить». `insufficient_window_funding` (same
  notice text as quota) was not in the decision and still offers «Подключить».
- A pending close keeps «Повторить закрытие» (confirmed).
