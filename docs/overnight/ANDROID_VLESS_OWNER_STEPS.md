# Android VLESS (panel servers) — steps for you

Plain English. Nothing in this run is on a phone yet. Nothing is committed or deployed.

## What was prepared
- A decision record: `docs/adr/114-android-vless-core.md`. It picks the Amnezia Box engine
  (the same one Windows uses) and a rebuild with the `with_utls` tag.
- A pure code switch for the VLESS engine, off by default. Panel servers on Android still show
  the "not supported on Android" message. Nothing changes for AmneziaWG users.

## Decisions for you to confirm (no reply needed unless you disagree)
1. **Core:** Amnezia Box (the engine already in the project). Alternative: Xray-core (MPL-2.0). Read
   ADR-114 if you want the reasons.
2. **Licence:** the engine is GPL-3.0-or-later. The app already distributes it for the AWG path.
   Confirm you accept this for the APK. Check the upstream `LICENSE` file at the Amnezia Box repo
   yourself; this run did not read it.
3. **uTLS risk:** newer sing-box notes say uTLS is not recommended for censorship circumvention.
   Do not promise that panel servers work on every network.

## Owner decision needed before any device test
4. Add `with_utls` to the engine build tags. This affects the Windows engine too
   (`third_party/amnezia-box/source-lock.json`). It changes the binary that AWG beta users run,
   so it needs its own build and an AWG regression test first. Do not do this on the beta
   without a new build record.

## Check the APK size (after a real engine exists, not now)
5. When a build with the VLESS engine exists, compare the release APK size before and after.
   Record both numbers in the issue. Nothing has been measured yet.

## Physical device test (required before any claim that it works)
6. Use one Android phone (arm64). Install a test build only, never the production build.
7. Connect to a test panel server only (not a live customer server). Note the time and the
   result: connected or not, and whether the browser shows the expected exit address.
8. Do not share server addresses, subscription links or logs in chat. Save only pass/fail notes.

## Things that are NOT done yet
- No VLESS engine is compiled or run.
- The AAR is not rebuilt with `with_utls`.
- Panel servers are not delivered to Android clients.
- The Gradle unit test for the gate passed (11 tests), but only the gate, not a tunnel.
- Gradle needs the prepared AWG tree whose receipt matches the pinned patch hash. Use
  `-PawgPreparedDir=<repo>/.cache/i-011/sdk-go-only-c4-20261111` as the matching one. The default
  `sdk-go-only` receipt is stale. Owner decision whether to refresh the default.
