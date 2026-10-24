# VPN Flutter client — I-013

I-014 adds the “Трафик и история” screen: exact integer bytes, outstanding
reservations, stable revision/cursor history, loading/error/retry and logout
cleanup. See [verification](../../docs/verification-i-014.md) and
[ADR-026](../../docs/adr/026-quota-history-client.md).
Local physical quota check: `scripts/run_i014_android.ps1` (synthetic ledger,
real loopback API/PostgreSQL, TEST FAKE ONLY Telegram; no staging endpoint).

Android Flutter UI over existing native AWG adapter and I-012 lifecycle API.
Real code; single-peer bounded staging only (60s / 1 MiB), no public release.
I-013 technically accepted on 2026-10-21: actual physical Flutter handshake/egress,
Disconnect, remote revoke, expiry and cleanup passed. Telegram delivery in the
acceptance harness is TEST FAKE ONLY; DNS/IPv6 and radio transitions for this APK
remain unverified. See [verification](../../docs/verification-i-013.md).

Build, physical checks and staging approval procedure:
[runbook](../../docs/runbooks/flutter-client.md).
Architecture/security decisions: [ADR-025](../../docs/adr/025-flutter-connect-lifecycle.md).

`lib/main.dart`: login/main screen, themes and accessibility.
`lib/connection.dart`: cancellable lifecycle, idempotent intent, remote revoke tracking.
`lib/api.dart`: bounded HTTPS requests, no redirects/logging/persistence, serialized refresh.
`lib/native_tunnel.dart`: versioned allowlist channel; private key never enters Dart.
`android/app/src/main/java/...`: application-scoped native owner and lease timer.
`test` uses explicitly fake adapters; `integration_test` is a separate test entrypoint.
Never ship an APK built from an integration_test target.

## Android UI — Soft Orbit (2026-10-22)

Главная с орбитальной карточкой подключения, отдельный выбор сервера и вкладки
VPN / Трафик / Профиль используют существующие API/controller. Общая тёмная и
светлая тема; оформление и навигация не меняют живое подключение. Новый профиль
не добавляет постоянное хранение сессии или управление аккаунтом.
Проверки и ограничения: [UI report](../../docs/verification-android-soft-orbit.md).

## I-025 Android v3 (2026-10-23)

Persistent Keystore-wrapped identity/session, opt-in renewable windows and local
split-app selection: see `../../docs/runbooks/android-windows-v3.md` and ADR-040.
Use `scripts/build_flutter_client.ps1 -WindowTunnels` from repository root.
Earlier sections describe the bounded v2 harness; v3 never falls back to v2.
Local checks passed (70 Flutter / 17 Android JVM tests, analyze/lint/debug/release).
Physical acceptance remains pending. Actual results and the resolved intermediate
disk-space failure are recorded in `../../docs/verification-i-025.md`.

Physical follow-up: native Keystore/process/permission checks passed on Pixel
(API 37), plus real local Flutter/API/PostgreSQL refresh/logout/lost-response.
See `../../docs/verification-i-025-physical.md`; v3 dataplane remains pending
prepared staging, and no remote changes were performed.
