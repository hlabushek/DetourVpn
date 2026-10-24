# ADR-040 — Android identity, durable session and v3 lifecycle (I-025)

2026-10-23. Local implementation; partial physical native/session checks passed.
Full v3 dataplane/network acceptance pending prepared staging; see verification-i-025-physical.md.
This changes only Android/client behavior over the existing I-024 contract.
No backend migration, external deployment or new dependency is introduced.

## Identity and session

The upstream X25519 device key is generated locally, encrypted using the platform
AES-256-GCM implementation and a non-exportable AndroidKeyStore wrapping key.
X25519 itself is not claimed to be hardware-backed or non-exportable: its private
material must be available to the upstream userspace engine in process memory.
No new cryptographic primitive or custom key derivation is introduced. GCM uses
provider-generated randomized IVs, 128-bit authentication tags and record/app/version
AAD. Device and session records are distinct AtomicFiles in noBackupFilesDir;
manifest and extraction rules exclude backups/device transfer. No plaintext disk
copy, log, method-channel private-key export or configuration persistence exists.
AndroidString/SDK key objects cannot be reliably zeroized; rooted process access
and compromised OS are outside this storage boundary. StrongBox/hardware backing,
biometric unlock and memory-forensics protection are not asserted.

The refresh token is origin-bound and encrypted; access tokens stay in RAM.
Startup validates the session via server refresh before showing the account.
Refresh first erases the durable old token, then performs the one-use request,
then saves the new token. Crash/response loss in between requires login rather
than replaying an ambiguously rotated token. Storage writes are serialized;
logout invalidates the session epoch, clears memory and awaits durable erasure.
Closing a UI/API object preserves the saved refresh; logout explicitly deletes it.
Invalid/inaccessible device ciphertext fails closed, without generating a new
identity over the damaged record. App-data reset loses the device identity;
the old device must be revoked separately by an authenticated operator/owner.

## V3 admission and renewal

`VPN_WINDOW_TUNNELS=true` is an explicit build-time switch. Default v2 remains
available for earlier bounded harnesses; there is no automatic fallback from v3.
After process restart native stops, the session is revalidated, and old connections
for that session device are revoked before new admission. VPN never auto-resumes.
Create/renew intents keep their original idempotency keys across ambiguous replies.
Disconnect cancels the controller epoch, stops native first, waits for in-flight
work and reconciles remote closure. Server HTTP 202 is not closure acceptance.

Each client request reserves 16 MiB, within the server's 64 MiB/90s maximum.
Renewal begins with <=55s remaining, using one outstanding intent. The client
checks connection/device key/node/runtime/slot/generation, immutable replay and
stable configuration. Only a subsequent GET with active ACK permits native
deadline extension. Native runs serialized operations and independently checks
binding/generation and elapsedRealtime; replay cannot extend a deadline and an
expired/down/disconnected window cannot be revived. Deadlines use conservative
wall plus monotonic bounds with the server's 6s native margin. Quota/ownership/
protocol rejection stops locally and requests revoke; network errors retain only
the existing bounded window. Unknown server counters retain holds per ADR-038.

Flutter polling is not guaranteed to run in deep Doze or after process death.
The native worker deadline watchdog is also subject to device CPU suspension;
the node kernel deadline/budget remains the authoritative independent cutoff.
This issue does not claim uninterrupted background use, an Android always-on kill
switch or automatic network/peer recovery. These need physical measurement before
ordinary daily use. A fast transfer can exhaust 16 MiB before time-based renewal;
native payload counters are not substituted for the outer UDP meter.

## DNS, IPv6 and split applications

The existing strict IPv4/full-default-route/numeric-endpoint/numeric-IPv4-DNS parser
is retained. Remote app directives, routes allowing bypass, IPv6 routes/addresses
and hostname bootstrap are rejected. The reviewed pinned GoBackend adds configured
DNS to VpnService and, for a single default-route peer, does not call allowFamily;
unconfigured IPv6 stays blocked for included apps. This is intentional IPv6
blocking, not IPv6 tunnel support. Actual DNS/Private DNS/IPv6 leak acceptance is
pending; SDK and OS semantics alone are not physical evidence.

User-selected excluded packages are injected only after validating the remote
profile. They are local settings, never uploaded as installed-app telemetry.
Selection shows launcher-visible packages and preserves saved selections; it
uses package visibility queries, not QUERY_ALL_PACKAGES. Max 63 user exclusions,
validated installed package IDs; missing packages fail connection instead of
silently weakening the routing choice. Changes require native down and no deadline.
DetourVpn itself is additionally excluded from v3 so its HTTPS renewal can proceed
when the tunnel budget/route is unavailable. This control-plane bypass is disclosed.
Excluded applications use normal networking including DNS/IPv6; no VPN protection
is claimed for them. Shared UID, Android lockdown, Private DNS and OEM differences
must be checked on the actual phone. There is no allowBypass call or inclusion mode.

Official platform semantics consulted:
- https://developer.android.com/reference/android/net/VpnService.Builder
- https://developer.android.com/privacy-and-security/keystore

## Acceptance boundary

Local fake API/native controller tests and JVM tests cover state/protocol/parser
logic; Android build/lint checks compilation. Actual Keystore persistence,
process restart, foreground/background, Doze, Wi-Fi/mobile, permission revoke,
split app routing and DNS/IPv6 require a connected physical device and prepared
v3 staging. Remote rollout/multiuser daily acceptance remains I-028; trusted
Telegram relay remains I-026. No payment work is included.
