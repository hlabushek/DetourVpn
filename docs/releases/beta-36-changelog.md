# DetourVpn 0.1.0-beta.36 (code 36) — changelog

Status: build-verified only. Not verified on a phone or a live Windows tunnel. The Windows zip is unsigned.

## Client fixes (since beta.35)

- **DNS can be saved while connected** (Android and Windows). The change applies at the next connect, matching split rules (ADR-187). Settings no longer ask the user to turn the VPN off. (A-07, D-06)
- **Windows window limit aligned** with Android and the server maximum: 3600 s for renewable windows. Non-renewable connect stays at 60 s. (W-04)
- **Windows handshake age uses a monotonic clock**, so a wall-clock change no longer skews it. (W-06)
- **Native DNS lookup in background renewal is bounded** by the 25 s call deadline. A hanging lookup is treated as a transient error and retried inside the window. The protected (Lockdown) path is unchanged. (A-05)
- **Token refresh with a malformed 2xx answer** keeps the session and is retryable; it no longer erases the session. Storage failures still end the session. (C-01)
- **Diagnostic reports** accept the `native_timeout` error code on both Android and Dart. (A-08)
- **Build scripts** accept an optional `-ApiFallbackOrigins` parameter (ADR-161). Default is empty, so existing builds are unchanged. (R-03)
- **Version** bumped to `0.1.0-beta.36` / code `36` in `pubspec.yaml` and `build.gradle.kts`.

## Docs

- Launch checklist: Always-on line corrected to match the release manifest. (D-01)
- ADR-165 and ADR-173..188: "uncommitted" statuses corrected to "committed". (D-03)
- HANDOVER: branch state, ADR-190 and next free ADR number (191) corrected. (D-04)

## Known gaps, not in this release

- Windows still routes only the primary API origin direct (W-05). Fallback origins need a contract change between Dart and the native layer.
- No kill switch, sleep/resume or tray mode on Windows (W-01, W-02).
- Dead-peer probe is inert (A-03). Renewal does not recover from a 401 after token rotation (A-01).
- Exact-alarm and foreground-service policy for Play is unchecked (A-06).
- High availability of the API is not built (B-01).
