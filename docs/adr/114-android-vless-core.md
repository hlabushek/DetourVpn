# ADR-114: Android VLESS + Reality core choice (preparation only)

Status: proposed (overnight run 4, 2026-11-11). Choice recorded; no engine enabled, no binary
rebuilt, not run on a device. Follows ADR-112 (Windows engine) and ADR-113 (Android gap).

## Context
Panel servers (ADR-111) use VLESS + Reality. The Windows client already generates a sing-box
JSON config for them (ADR-112). Android runs only AmneziaWG through `GoBackend` (ADR-084).
The local `libbox` AARs (`.cache/amnezia-box-build`) come from the pinned Amnezia Box source
(`third_party/amnezia-box/source-lock.json`, v1.260910.0, licence recorded there as
GPL-3.0-or-later), built with tags `with_awg, with_gvisor`. Reality needs `with_utls`, which
these builds do not have (verified in ADR-112 §4 from the pinned source).

## Options
| Option | Licence (from repo/upstream) | Maintenance | APK size | VLESS + Reality + uTLS | Fit with VpnService / foreground service | Coexists with AWG |
|---|---|---|---|---|---|---|
| **A. Amnezia Box `libbox` (current pin), add `with_utls` and a `vless` outbound** | GPL-3.0-or-later (already distributed in the app's engine path; see source-lock) | Same fork as Windows; pinned by hash; owner already builds it locally | Already bundled as AAR; extra size = uTLS code, not measured | `vless` outbound with `tls.reality` and `utls` is the same JSON the Windows generator (ADR-112) builds; needs rebuild with `with_utls` | Same `VpnService` TUN as the sing-box config; needs its own start/stop path beside `GoBackend` | Yes, if only one tunnel runs at a time; both engines are in one process group but not active together |
| B. Stock sing-box `libbox` (SagerNet) | GPL-3.0-or-later (upstream; not checked against the LICENSE file in this run) | Upstream, frequent releases | Second Go runtime (+15–25 MB per ABI, estimate, not measured) | Supports Reality and uTLS upstream; uTLS release notes mention limits for censorship circumvention | Same as A | Second libbox in one process: two Go runtimes; dependency clash risk |
| C. Xray-core (`libXray`, `AndroidLibXrayLite`) + tun2socks | Xray-core MPL-2.0 (from search results; not confirmed in repo LICENSE this run); tun2socks licence not checked | Active upstream; Android bindings are downstream projects | +20–30 MB per ABI (estimate) | Supports VLESS + Reality + uTLS | Two layers (core + tun2socks); more failure modes, battery | Yes, separate process or library |

## Decision
**Option A**: use the Amnezia Box `libbox` already pinned in this repo, and add a `vless`
outbound through a rebuild with `with_utls` added to `android_tags`.

Reasons:
1. The same engine and config shape as Windows (ADR-112), so one generator and one set of
   checks cover both platforms. The panel client code does not need a second config dialect.
2. The source is already pinned by SHA-256 and the licence is already accepted for the engine
   path (ADR-084). Option A adds no new third-party binary and no new download.
3. One Go runtime in the process. B would add a second copy of the same kind of runtime.
4. C is licence-attractive (MPL-2.0 is file-level copyleft) but adds a second config dialect,
   a tun2socks layer and a new supply chain. Not justified before a device test of A.

Rejected for now: B (second runtime, no clear gain over A) and C (second layer, second config,
new supply chain). C remains the fallback if A's `with_utls` rebuild fails the AWG regression
check or if the GPL distribution terms are not acceptable to the owner.

## Not decided here (owner or later issues)
- Whether `with_utls` is also added to `windows_tags` (ADR-112 §4). Same binary family; one decision.
- Licence sign-off: GPL-3.0-or-later for the distributed APK (already true for the engine path,
  but the owner should confirm). Check the upstream LICENSE file, which this run did not do.
- uTLS fingerprint risk: sing-box v1.13 release notes state uTLS is not recommended for
  censorship circumvention because of architectural limits. This may affect the
  "Reality works behind a strict network" promise. Do not promise bypass (AGENTS.md).
- Single active tunnel rule (AWG and VLESS must not both own the TUN) and its service lifecycle
  (ADR-097..099). Future ADR.
- APK size: not measured. Measure after the real engine is added.

## Build chain (verified locally, not run)
- `scripts/build_amnezia_box_android.ps1` builds `libbox-1.260910-arm64.aar` and
  `libbox-1.260910-arm64-x86_64.aar` with gomobile from the pinned archive (SHA-256 checked).
  The tag list is the `android_tags` in `source-lock.json` (currently `with_awg, with_gvisor`).
- The client build excludes `LibboxProviderV1.java` and `LibboxVpnService.java`
  (`apps/client/android/app/build.gradle.kts`), so libbox is not on the live path.
- No prebuilt VLESS AAR is downloaded. Nothing new is fetched in this run.

## Verification
- This ADR is a choice. No engine code is run here. Adapter and gate are unit-tested in
  `apps/client/android/app/src/test/java/org/example/vpnpoc/VlessEngineGateTest.java`.
  Results: standalone javac + JUnit 4.13.2 on JDK 21 (11 tests, OK). Gradle
  `:app:testDebugUnitTest --tests org.example.vpnpoc.VlessEngineGateTest` with Gradle 8.14.3
  (`-PawgPreparedDir=.cache/i-011/sdk-go-only-c4-20261111`, whose `patch_sha256` matches the pinned
  value in `apps/android/tunnel/build.gradle.kts`): BUILD SUCCESSFUL, XML shows 11 tests, 0 failures.
  The default `.cache/i-011/sdk-go-only` receipt is stale (wrong patch hash); not changed here.
- Real verification needs: rebuilt libbox with `with_utls`, the AWG regression check, a
  `vless` config check on a device, and a live panel. None done.

## Links
- ADR-084 (AWG GoBackend, Libbox excluded), ADR-111 (panel servers), ADR-112 (Windows VLESS),
  ADR-113 (Android gap).
- Plan: `docs/overnight/ANDROID_VLESS_PLAN.md`. Owner steps: `docs/overnight/ANDROID_VLESS_OWNER_STEPS.md`.
