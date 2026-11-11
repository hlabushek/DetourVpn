# ADR-117 — The app UI adopts a running, natively renewed tunnel instead of stopping it

**Status:** proposed 2026-11-11 (connection reliability, `docs/overnight/RELIABILITY_ROOT_CAUSE.md` §2).
**Amends:** ADR-097 (native renewal), ADR-098 (service restore).

## Context
Since ADR-097 the VPN keeps running and renewing in the app process after the Flutter UI engine is
gone. The UI engine belongs to `ClientActivity` (a `FlutterActivity` with no cached engine), so it
is destroyed when the activity is destroyed: the user swipes the app away, or Android reclaims the
activity after a long time in the background. The process and the tunnel stay.

When the user opens the app again, a new `WindowConnectionController` starts with
`_synced == false`. Its first `refresh()` runs `_reconcile()`, which calls `native.disconnect()`
and revokes every non-closed connection of this device. The app itself stops the working VPN.
The native `disconnect` also clears `resume_connected`, so boot restore is off afterwards.

Separately, the headless restore engine (ADR-098) answers `startNativeRenewal` with
`notImplemented`. Flutter turns that into `MissingPluginException`, which is not a
`PlatformException`. `_startNativeRenewal` catches only `ClientError`, so with 900 s windows every
restore attempt ends in `native_unavailable`. The restore then stops the tunnel and the service.

## Decision
1. `NativeSnapshot` gains two optional fields from the native side: `connectionId` and
   `windowGeneration` (the `WindowDeadline` fence values). Both are values Dart already received
   from the API; they are not secrets.
2. In `_reconcile()`, before stopping native, the controller reads a snapshot. It **adopts** the
   tunnel only if all of these hold:
   - native is up, native renewal is running, and native reports a connection id;
   - the API list for this device contains that connection with state `active`;
   - the response passes the same checks as `_validate` (ids, device, meter, contract version,
     public key equal to the local key, slot range, config present and bounded);
   - the server generation is not older than the native generation.
   Then it sets the connection id, grant, config, selected node and deadline (from native), and
   revokes any other non-closed connection of this device. Otherwise it keeps today's behaviour:
   stop native and revoke everything.
3. `_startNativeRenewal` treats any failure (including `MissingPluginException`) as "native
   renewal unavailable" and keeps Dart-driven renewal. The headless restore engine now answers
   `startNativeRenewal` with `false` instead of `notImplemented`. It still does not run native
   renewal itself (Lockdown would need `VpnService.protect()`; see ADR-098 limits).

## Security considerations
- Adoption needs the server to list the connection for this device's session, native to report
  the same connection, and the key to match. No new endpoint, credential or permission.
- A mismatch of any field falls back to the fail-closed stop and revoke.
- The adopted deadline is the native one, which never moves backwards (`WindowDeadline`).

## Limits / not verified
- Not verified on a physical device. The UI-engine lifecycle is standard Flutter behaviour, but the
  exact trigger on the owner's phone (swipe-away, activity reclaim) is not confirmed.
- If native renewal had already stopped (refused), the tunnel is not adopted and is stopped as before.
