# ADR-103: Allow screenshots and recents previews on Android (remove FLAG_SECURE)

Status: accepted for the overnight build (owner request, 2026-11-11). Reversible.

## Context
The owner asked to remove "screen blocking" on the phone. Searching `apps/` for window
flags found exactly one screen-blocking behavior: `getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE)`
in:
- `apps/client/android/app/src/main/java/org/example/vpnpoc/ClientActivity.java` (the client app, the main target);
- `apps/android/app/src/main/java/org/example/vpnpoc/MainActivity.java` (the older PoC app).

Effect of FLAG_SECURE: blocks screenshots and screen recording, and hides the app content
in the recents switcher. No other `WindowManager` flags that block the screen were found.
`FLAG_KEEP_SCREEN_ON` appears only in androidTest code and is not a blocking flag.

## Decision
Remove the `addFlags(FLAG_SECURE)` call from both activities. Leave a comment that the
flag is intentionally absent. No other behavior changes.

## Consequences
- This removes a privacy protection. Screenshots and recents thumbnails can now show
  account, device-list, and status content to anyone with access to the unlocked phone or
  to a screenshot tool. The owner accepted this explicitly; it must be listed in the report.
- No code path depended on FLAG_SECURE. No test covered it.
- Android builds are not run here (see OVERNIGHT_REPORT.md); the change is a one-line removal.

## Alternatives considered
- Keep FLAG_SECURE only on the key/config screens: not requested; can be added later.
- Make it a user setting: adds UI scope not requested.
