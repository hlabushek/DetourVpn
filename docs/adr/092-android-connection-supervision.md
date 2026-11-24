# ADR-092 — Android connection supervision

Date: 2026-11-12. Scope: the owner's Android reliability rewrite (I-030).
Status: implemented; physical acceptance pending. The follow-up request authorizes local builds and feasible device verification; no production changes.

## Reconnaissance before implementation

- The beta uses `AwgProviderV1` / pinned AmneziaWG GoBackend v3.1.20260814,
  Android SDK source ff15093bf3856fea0947923474870b86efb682f0, Apache-2.0 (its pinned COPYING).
  Libbox is excluded by `apps/client/android/app/build.gradle.kts` (ADR-084).
- Java 17, min API 24, compile/target 36, AGP 8.13.0, Kotlin plugin 2.4.21;
  installed Flutter 3.47.7 (abaf9c523780), Dart 3.13.5, engine deb287481e3c.
  No new dependency is needed.
- `main.dart` owns the visible `WindowConnectionController`; its timer renews
  authenticated 90-second windows. `ClientActivity` bridges `project.vpn/tunnel.v1`.
  `ClientApplication.worker` serializes native start/stop and deadline enforcement.
- Connect: Dart registration/reconciliation/proof/admission -> connectWindow ->
  WindowDeadline -> AwgProviderV1 -> GoBackend VpnService -> TUN fd transferred
  to Go. Go owns the detached fd; partial native startup closes it.
- Disconnect: Dart invalidates pending work -> native generation increments ->
  worker stops GoBackend -> server DELETE reconciles the existing reservation.
- Activity destruction does not explicitly disconnect, but destroying its Flutter
  engine destroys the renewal owner. A recreated controller reconciles by stopping
  the old tunnel. Neither is suitable for an ongoing manual session.
- Network presence is sampled by snapshot; no application recovery controller.
  Core handle existence is `up`; handshake freshness is currently UI success.
  Core/native failure does not have an independent serialized recovery path.
- Always-on uses a separate headless engine and protected API relay; release
  declares Always-on unsupported. Preserve this gate, encrypted session storage,
  quota/idempotency contracts, routing preferences, and IPv4-only policy.

## Decision and boundary

Retain maintained upstream cryptography/TUN code and the existing tested v3
authorization protocol. Rewrite supervision, not those security contracts.
Keep the normal Flutter engine application-owned across Activity detachment;
its native bridge must not retain an Activity. This keeps one API token/renewal
owner, avoiding competing refresh-token rotation in two engines. The native
controller owns network observation, health, generations and transport recovery.
The UI reads snapshots; detaching it is not a disconnect. No raw tunnel profile
is persisted. Legitimate Always-on restart must acquire fresh authorization.

Use serialized network binding on the existing protected engine sockets to
preserve TUN during handover. No make-before-break duplicate core. Preserve
lease expiry even while offline; a retry never extends authorization locally.
Physical network identity and link changes invalidate health. The pinned upstream
StdNetBind uses unconnected UDP ListenPacket sockets, verified with Getpeername
before and after a real UDP send. Android bindSocket therefore applies. A dup fd
references the same socket; it is not a new connection. Fence native sends before
binding both protected sockets and publishing setUnderlyingNetworks. Clear cached
PKTINFO source/interface by parsing a fresh upstream endpoint for each send;
preserve the current roaming destination. Failures keep the route fenced.

Remove the synthetic DNS probe from the live health path: this app's excluded UID
is not entitled to the VPN routing domain. Health comes from authenticated upstream
payloads actually written to TUN, expires after 60 seconds, and is invalidated on
handover. Handshakes/core existence are insufficient. Idle means unconfirmed,
not broken: do not restart a quiet core or escalate retry backoff solely for idle.
This is delivered-payload evidence, not an independent public egress/DNS test.

Add a native packet gate using CLOCK_BOOTTIME, initially closed, to both encrypted
UDP Send and decrypted TUN Write. A validated window supplies at most 90 seconds;
only a live grant can extend, never an expired/cancelled handle. Java/Dart delayed
timers cannot authorize traffic after expiry. A syscall already in flight cannot
be retracted. Locks serialize route/deadline changes; cancellation is atomic and
does not wait for a blocked native monitor. Native handle IDs are not reused.

Native scheduled pulses trigger the retained Dart authorization owner. No widget
lifetime timer owns renewal. Android may suspend the entire process in Doze;
a foreground service is not a wake-lock guarantee. Expiry fails closed, followed
by reconciliation and fresh admission after wake. Continuous service in deep
Doze/overnight remains a physical acceptance question, not a claimed guarantee.

MethodChannel results have a 15-second bound and ignore late completion. On timeout,
cancel native traffic and desired intent without waiting for the native worker;
reject further core operations until process restart. JNI cannot be safely killed
in-process, so a competing core is never started as a timeout workaround.
Persist false intent outside the native queue and fence queued true intent with
its generation. Framework service destruction moves potentially blocking native
teardown off the main thread and fences stale cleanup with the backend epoch.
Notification updates use the existing service startForeground path; foreground
service operation does not depend on granting ordinary notification permission.

System revoke and user disconnect invalidate intent immediately. No boot
receiver, wake lock, force-stop workaround, or new telemetry upload. No guarantee
against direct fallback after OS removal without Android Lockdown.

References reviewed: Android VPN guide
https://developer.android.com/develop/connectivity/vpn and maintained upstream
https://github.com/amnezia-vpn/amneziawg-android . Upstream does not implement
this product's renewable quota protocol; replacing it would change contracts.

## Validation boundary

See [the implementation receipt](../android-reliability-implementation.md) for
actual commands, artifacts and scenario results. Local real encrypted AWG/UDP tests
use explicitly test-only memory TUNs; they are not Android/WAN acceptance. Emulator
JNI/Activity checks use an isolated package without an API origin or account.
Physical Wi-Fi/mobile, Doze overnight, DNS/IPv6 and split-routing evidence remains
required before device acceptance. Android Lockdown is necessary for a system-wide
no-direct-fallback guarantee after the OS removes the VPN.

Primary platform references: [Network.bindSocket](https://developer.android.com/reference/android/net/Network),
[Android VPN guide](https://developer.android.com/develop/connectivity/vpn), and
[AOSP VPN UID ranges](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android15-release/services/core/java/com/android/server/connectivity/Vpn.java).
