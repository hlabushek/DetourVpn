# ADR-067 — manual diagnostic sharing with explicit consent

- Status: accepted for local implementation; physical beta acceptance is open.
- Scope: ROADMAP phase 6, user-initiated diagnostic report.
- Platform reference: [Android Sharesheet](https://developer.android.com/develop/ui/compose/sharing/send).

## Context

The client showed only a friendly failure message in release builds and had no
bounded way for a tester to provide a useful error category. Automatic crash
reporting and backend collection would add a separate data-retention and consent
surface that is not defined for the closed beta.

## Decision

- On an error, offer a report preview containing only a fixed report title, a
  locally allowlisted error code, and a locally allowlisted connection stage.
  Unknown values become `unknown_error` and `unknown`.
- Do not include account/device identifiers, keys, URLs, IP or DNS addresses,
  installed applications, usage history, logs, or tunnel data.
- Do not invoke Android sharing until the user confirms the exact preview. Then
  use the system `ACTION_SEND` chooser and let the user select the destination.
  The app does not send the report to its own server and does not record the
  selected destination.
- If the user cancels either the preview or system chooser, no report is sent by
  the app. The user remains responsible for the external app they choose.
- This manual flow is not automatic crash reporting and does not establish an
  available support channel. Physical-device acceptance and a designated support
  recipient remain separate beta operations gates.

## Consequences

- A tester can send a narrow diagnostic category without granting the VPN
  service background telemetry access.
- The report may still leave the device after the user chooses another app; the
  preview and confirmation make those exact fields visible before that choice.
- This implementation does not prove delivery to support, recipient retention,
  or behavior on every Android/OEM sharesheet.

## Verification

- Flutter unit/widget tests verify allowlisting, preview contents, cancel
  behavior, and the exact method-channel arguments after confirmation.
- Flutter and Android each enforce the fixed error/stage allowlists; the native
  boundary rejects arbitrary strings even when they match the identifier shape.
  A Flutter source-sync test checks that both allowlists and the generic fallback
  labels remain synchronized. Keep it passing when adding or changing values.
- Android JVM tests verify accepted allowlisted values and rejection of
  unlisted identifiers, newlines, URLs, nulls, and oversized values.
- No report is automatically sent and no physical installation is part of this
  local implementation change.
