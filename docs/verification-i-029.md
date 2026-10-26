# I-029 local verification — 2026-10-25

## Implemented local path

- Android system-start callback now passes the `VpnService` into
  `ClientApplication`; the pinned GoBackend patch no longer treats a null
  restart intent as permission to revive any saved peer. The callback starts
  only a fresh authenticated bootstrap. A matching service-destroy callback
  closes the relay/headless engine and clears native deadline state.
- A headless Flutter entrypoint uses the encrypted origin-bound refresh session,
  validates the persisted node against API availability, uses the fixed-origin
  protected relay, and delegates reconciliation and new lease admission to the
  existing `WindowConnectionController`.
- Failures disconnect locally and ask Android to stop the service. Debug builds
  advertise Always-on to permit controlled acceptance; release builds set
  `SUPPORTS_ALWAYS_ON=false`. Lockdown remains a system setting controlled by
  the user.
- The app retains the idle VPN service during the initial system bootstrap so
  the relay is not stopped by the controller's first native disconnect. Normal
  active-tunnel disconnect still follows GoBackend's shutdown lifecycle.

## Checks run

| Check | Result |
|---|---|
| Prepared a fresh pinned SDK tree under `.cache/i-029-final3/` | Passed; existing `.cache/i-011/` tree preserved |
| Flutter tests: `always_on_recovery_test.dart`, `split_apps_test.dart`, `window_connection_test.dart`, `protected_api_transport_test.dart`, `native_tunnel_test.dart` | 20 passed |
| Dart analyzer on touched Flutter sources and focused test | Passed, no issues |
| Android `testDebugUnitTest` | 23 passed across six suites |
| Android `assembleDebug` | Passed |
| Merged manifest | Debug `SUPPORTS_ALWAYS_ON=true`; release `false` |
| App lint | Not clean: 2 `PropertyEscape` errors in local Windows `local.properties`; 4 dependency/version warnings. No app-source issue reported. |
| Tunnel lint | Not clean: 5 Android API-26 `java.time` errors in the pinned upstream config parser at minSdk 24, plus 5 warnings; these files were not changed by I-029. |
| Release assemble | Failed at existing generated `GeneratedPluginRegistrant.java`: it references `dev.flutter.plugins.integration_test`, absent from release compile classpath. Release manifest processing succeeded and showed Always-on false. |

## Recheck — 2026-10-26

The previous I-028 runner selected `.cache/i-011/sdk-go-only`, whose prepared
receipt predates the callback API used by `ClientApplication`. The matching
prepared SDK already existed at `.cache/i-029-final3/sdk-go-only`; its patch
SHA-256 matches the current `apps/android/patches/go-only.patch`. This is a
build-input selection problem, not a missing callback implementation.

The tunnel Gradle configuration now pins the expected patch fingerprint and
rejects a prepared SDK receipt with a different hash. A negative Gradle check
against the stale I-011 cache failed early with the intended stale-hash error.
With `-PawgPreparedDir=<path to .cache/i-029-final3/sdk-go-only>`, the Flutter
debug APK build passed and `:app:testDebugUnitTest` passed all 23 tests across
six suites. The resulting debug APK was not installed. `adb devices -l`
reported no attached device.

This confirms source compatibility and deterministic local tests only. Android
system callback delivery, Lockdown behavior, fresh lease recovery, and service
failure recovery remain unverified on a physical device. Release builds still
opt out of Always-on.

## Diagnostic follow-up — 2026-10-26

The beta owner APK was rebuilt with `VPN_WINDOW_TUNNELS=true` and the approved
beta API origin after the device reported a generic connection failure. A
read-only unauthenticated HTTPS request to the v3 node-catalog path returned
401 with valid TLS, confirming the public route reaches API authentication; it
does not confirm runtime node readiness. No native initialization exception
appeared in filtered Android logs, and the installed APK contains the pinned
`libwg-go.so` for arm64 and x86_64.

The owner then reported `invalid_response` after a transient `native_unavailable`
message. To localize the failing operation without exposing data, debug UI now
displays the regex-bounded stable error code and one fixed operation-stage name
alongside the friendly message. Release builds never display either; no URL,
UUID, response body or credential is shown. Focused Flutter tests: 30 passed;
direct Dart analysis: no issues. The updated APK was installed with
`adb install -r`, preserving app data.

The owner subsequently observed both codes at the fixed `device_key_read` stage.
This stage precedes device registration, so no VPN API request has yet been made.
The background refresh path had classified any non-`ClientError` exception as
`invalid_response`, masking native method-channel/key-store failures. It now maps
such exceptions to `native_unavailable`; explicit API-shape errors continue to
use `invalid_response`. A deterministic regression test asserts the stage and
that no API calls occur. This corrects the report, but does not establish the
underlying Android keystore/native initialization cause; device logs are still
needed when the phone is next available. Debug builds now also log a fixed
method name and exception class for a failed native method call under
`DetourVpnNativeCall`; exception messages and request data are omitted.

The captured log then identified `stage=awg_backend failure=RuntimeException`.
Inspection showed the just-installed APK had been built with an incomplete
prepared SDK tree that contained no `jniLibs/libwg-go.so`; the full prepared beta
v3 tree has both arm64 and x86_64 libraries and the same expected patch hash.
The APK was rebuilt from that tree, and its ZIP contents were checked for both
libraries before reinstalling with `adb install -r`. After restart, the current
process had no `DetourVpnNativeInit` error, confirming the local native startup
failure is corrected.

The first post-fix attempt passed native initialization and reached lease create,
which the beta API rejected as `insufficient_window_funding`. The latest owner
smoke receipt records 337,868 bytes remaining after 710,708 bytes of measured
use, while the client requested a 1 MiB reservation. To continue the short
owner-only native/handshake check without another quota grant, the debug build
accepts a compile-time test window budget and was built with 262,144 bytes for
this beta attempt only. The normal client default remains 1 MiB. The arm64
native library was verified in the APK and the APK was installed over the
existing app without clearing data. No beta ledger or server configuration is
changed by this local setting.

Rapid retries also produced a `rate_limited` response at `key_challenge`; the
limiter's reset time is not exposed, so do not retry rapidly. A guarded
owner-only beta top-up then added one additional 1 MiB credit after verifying
the unique active account, spent first grant, and zero reserved balance. The
total grant is now 2 MiB; available quota increased by exactly 1 MiB and the
admin session was logged out. Receipt:
`../evidence/i-030-beta-owner-topup-20261026.json`.

A second strictly bounded owner-only acceptance credit added 4 MiB after
verifying the previous 2 MiB grant and zero reserved balance. The confirmed
total grant is 6 MiB with zero reserved bytes; receipt:
`../evidence/i-030-beta-owner-acceptance-credit-20261026.json`.

After the top-up and reinstall, the owner reports another real Android VPN
session: the system VPN indicator appeared, Telegram loaded and delivered
messages, and the visible traffic balance fell from about 1.1 MB to about
300 KB. Traffic then stalled; the owner disconnected manually. A subsequent
connect was denied as `insufficient_window_funding` at `lease_create`. This
supports that the short tunnel carried real app traffic and that new access is
fail-closed when the remaining balance cannot fund another 256 KiB window.
The report does not verify TikTok, a handshake receipt, DNS/IPv6 privacy, or
Always-on/Lockdown. Receipt:
`../evidence/i-029-owner-android-tunnel-report-20261026.json`.

`flutter analyze` itself exited because its analysis server received truncated
JSON in this environment. Running the bundled Dart analyzer directly on the
changed Flutter files passed.

## Not accepted yet

- Read-only ADB on the Pixel confirmed Android VPN `CONNECTED` and `VALIDATED`,
  with the IPv4 default route on `tun0`, IPv6 marked unreachable, DNS set to
  `1.1.1.1`, and underlying Wi-Fi. `tun0` receive/transmit counters increased
  during an 8-second interval. This confirms active TUN traffic but not its
  packet contents, per-app attribution, or absence of DNS/IPv6 leaks. Receipt:
  `../evidence/i-029-android-active-vpn-20261026.json`.
- The owner also reported Telegram messages arriving through the active session;
  no independent server handshake receipt was captured. System delivery of the
  Always-on callback, Lockdown DNS reachability, fresh lease recovery, service
  failure/restart, and recovery behavior remain unverified on Android/OEM hardware.
- No expiry/revoke/outage integration test exercised the new headless entrypoint
  end to end. The deterministic Dart negative tests verify that missing encrypted
  session or a non-HTTPS origin cannot request a relay or a new lease.
- The debug build is for controlled testing only. Do not invite friends or route
  sensitive traffic through it; I-028 privacy acceptance and the separate beta
  environment/operations gates remain open. Do not claim a kill switch.

## Next acceptance action

The owner-only physical connection check now has system-level confirmation.
Further I-029 acceptance needs a planned included/excluded routing scenario and
separate Android Always-on/Lockdown recovery checks; do not repeat generic
connect attempts. Packet-level DNS/IPv6 and independent handshake evidence
remain open.
