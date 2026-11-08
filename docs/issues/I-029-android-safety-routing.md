# I-029 — Android safety controls and per-app routing

## Goal

Add the Android VPN safety and routing controls needed by an advanced client,
reusing the existing AmneziaWG GoBackend and Android `VpnService` APIs. Do not
replace the Flutter application with the full AmneziaVPN client.

## Current baseline

- The client already supports excluding selected installed apps from the VPN.
- The client now also supports the complementary included-app mode locally;
  an empty included-app list is rejected to avoid accidental full-tunnel routing.
- The split-routing screen now exposes an explicit all-apps-through-VPN mode,
  searches launcher-visible apps by label or package ID, shows local app icons,
  puts selected apps first, and persists changes immediately. Its top switch
  remembers the last split mode; `include` can be chosen before selecting an
  app, while connecting with an empty allowlist still fails closed. See ADR-078.
- Owner-reported Pixel 8 Pro smoke: with Chrome selected for VPN and Firefox
  outside that selection, `api.ipify.org` showed Helsinki egress in Chrome and
  the owner's Russian egress in Firefox. This is a useful per-app routing
  observation, not packet evidence or DNS/IPv6 leak acceptance.
- Owner confirmed the updated signed beta UI on the Pixel: split on/off and
  mode selection work, Chrome can be selected and remains selected after
  reopening, and selected apps appear first. This is an owner-confirmed UI
  smoke only; it does not independently verify the native routing path.
- The selected server node is stored locally as a validated UUID preference;
  after sign-in it is restored only if the API still reports that node available.
  It is not a lease, credential, or authority to restore a tunnel.
- On 2026-11-03 beta.3 reached `native_tunnel_start` but the local libbox start
  failed; cleanup then surfaced `revoke_unconfirmed` and hid the original safe
  native failure category. ADB later showed no active VPN transport. The client
  now preserves the allowlisted native failure, disables repeated action while
  revoke reconciliation is pending, and continues the existing bounded poll.
  Beta.4 is now signed and installed on the Pixel; the specific native cause and
  successful data path remain unverified until a single controlled connection
  attempt after revoke/node preflight.
- Owner's beta.4 retest showed `native_unavailable` at `native_tunnel_start`.
  APK inspection confirms `libbox.so` and `libwg-go.so` are packaged, but the
  Android method-channel handler was rewriting native start failures to
  `native_operation_failed`, which Dart displayed as the misleading generic
  engine-unavailable message. Start failures now use the shared safe error
  classification; if prior key/proof calls succeeded but native provider
  initialization did not, the client reports `native_engine_unavailable`.
  While server closure is pending after a failed attempt, the button now says
  it is waiting for that attempt to close. This improves diagnosis and UX; it
  does not repair or prove a successful engine start. The changes are staged
  as beta.5 (version code 6). The signed update was installed on Pixel and
  launched; its physical connection attempt returned `native_start_failed`.
- Owner's beta.5 physical attempt returned `native_start_failed` at
  `native_tunnel_start`. This confirms a native start exception but does not
  identify its cause. The signed release suppresses the exception class, so
  beta.6 source is changed to record only exception class names and a bounded
  cause chain to local logcat; exception messages and tunnel data remain
  excluded. Flutter analysis passed; the first release assembly stopped when
  the Windows system disk ran out of space, then a subsequent signed beta.6
  build installed on Pixel. No connection attempt has been made with beta.6;
  one attempt after server closure is needed to read that local diagnostic.
- Owner's beta.6 attempt also returned `native_start_failed` at
  `native_tunnel_start`. Filtered local logcat contains only
  `failure_types=proxyerror`; VPN transport is absent. Beta.7 source added fixed
  setup/config/service-start labels with exception class names only. The owner
  then attempted beta.7 on Pixel; filtered ADB logcat repeatedly showed
  `start_stage=libbox_config failure_types=proxyerror` before TUN creation, with
  no VPN transport. Source review against the exact pinned Amnezia Box tree
  found the AWG adapter is registered as an endpoint, while the config factory
  emitted it as an outbound. Beta.8 moves the AWG object into top-level
  `endpoints` and retains its `awg` tag for routing and DNS references. This
  addresses the identified schema mismatch. The signed beta.8 (version code 9)
  update installed over beta.7 and launched on Pixel; ADB confirms no VPN
  transport after install. Receipt:
  `../evidence/i-030-beta-apk-install-20261103T1820Z.json`. Successful endpoint
  creation, service start, handshake, egress, and revoke closure still require a
  controlled physical attempt. The first beta.8 attempt advanced past config
  parsing but failed during `libbox_service_start` with `proxyerror`; no VPN
  transport was active. Beta.9 adds fixed error-category logging from Libbox
  for AWG endpoint vs TUN inbound startup, plus Android `openTun` entry/success
  and safe exception-class markers. It never emits native log messages. Beta.9
  is version code 10; Android release Java compilation passed. The signed APK
  installed over beta.8 and launched on Pixel; ADB confirms no VPN
  transport after install. A controlled beta.9 attempt still failed at
  `libbox_service_start` with `proxyerror`, without a fixed subcategory or VPN
  transport. Receipt:
  `../evidence/i-030-beta-apk-install-20261103T1837Z.json`. One diagnostic
  physical attempt with improved cause classification remains pending.
- Beta.10 (version code 11) classifies only fixed allowlisted substrings from
  the in-memory Java exception message into a safe failure category; raw error
  text is never logged. Its physical attempt classified the error as the broad
  `awg_endpoint_start` category; no VPN transport was active. The classifier
  checked that broad wrapper before nested markers. Beta.11 (version code 12)
  checks nested `set ipc config` and `tun start` markers first, then the broad
  endpoint category. Beta.10 installed over beta.9 and launched on Pixel; ADB
  confirms no VPN transport after install. Receipt:
  `../evidence/i-030-beta-apk-install-20261103T1845Z.json`. Release compilation
  passed. Beta.11 installed over beta.10 and launched on Pixel; ADB confirms no
  VPN transport after install. Receipt:
  `../evidence/i-030-beta-apk-install-20261103T1852Z.json`. A refined diagnostic
  attempt and revoke acceptance remain open.
- Beta.11 was accidentally started while the owner reported that server revoke
  was still pending. ADB observed two `libbox_service_start` failures, both
  classified as `awg_endpoint_start`; no VPN transport came up. Since
  `IpcSet` and `tun.Start` already have more specific wrappers, the remaining
  `awgDevice.Up()` call is the next unclassified AWG start step. This is a
  source-based hypothesis, not a confirmed root cause. Beta.12 (version code
  13) adds only the fixed `awg_device_up` cause marker and allowlisted
  classifier. It does not log Go/Java exception text. Its signed physical-test
  build, install, and diagnostic attempt remain pending; do not retry Connect
  until server-side revoke is confirmed.- The included-app mode passed local Flutter/native parser tests and Android
  debug assembly; physical routing acceptance is still pending.
- I-028 has a physical receipt for included/excluded app routing; this does not
  prove every package, Android version, lockdown combination, or OEM behavior.
- The client does not claim Always-on VPN or a kill switch.
- The profile screen links to Android's VPN settings and explicitly warns that
  recovery is experimental and not yet accepted on a physical device; see ADR-042.
- A headless Always-on bootstrap is now implemented locally. It restores only the
  origin-bound encrypted refresh session, validates the saved node against the
  authenticated API, reconciles/revokes prior remote state, and requests a new
  lease through the existing `WindowConnectionController`. Missing session,
  permission, availability, API access, or an ambiguous lease leaves the tunnel
  down. No saved peer config or old grant is restored.
- The pinned GoBackend callback now supplies its `VpnService` to the application;
  the application owns the fixed-origin protected relay and headless Flutter
  engine. Debug manifest advertises Android Always-on for controlled acceptance;
  release manifest explicitly opts out. Lockdown remains user-controlled.
- This local implementation is not physical acceptance. Service start behavior,
  DNS reachability under Lockdown, actual lease recovery, Android/OEM behavior,
  and post-failure recovery remain unverified. Do not call this a kill switch or
  production-ready Always-on.
- The full AmneziaVPN application is GPL-3.0 and uses a different app stack.
  Any source reuse must pass the existing per-component license review.

## Scope

1. Implement safe Android Always-on startup for leased sessions: a system-started
   service must revalidate device ownership and the current server lease before
   bringing up a tunnel. Expired, unavailable, or ambiguous authorization fails
   closed. Do not assume manifest merging implies functional recovery.
2. Explain and link to Android system VPN settings for Always-on and
   “Block connections without VPN” (Lockdown). Never claim the app can silently
   enable system Lockdown.
3. Preserve the current excluded-app mode and add the complementary
   included-app mode, with exactly one routing mode active at a time.
4. Make the Lockdown interaction explicit: Android may block apps excluded
   from the VPN. The UI must not imply excluded apps remain online under
   Lockdown.
5. Keep app selections on-device. Do not upload installed-app lists or add
   `QUERY_ALL_PACKAGES` solely for this feature.
6. Persist only the selected public node identifier needed for restart policy;
   restore it only after API availability validation and clear it on sign-out.
7. No server/API/database changes, protocol additions, production changes, or
   staging runs.

## Acceptance

- Parser rejects malformed, duplicate, over-limit, or conflicting routing
  selections and preserves fail-closed behavior for remote profile directives.
- Flutter settings expose both routing modes and explain which traffic bypasses
  the VPN and the effect of Android Lockdown.
- Flutter settings expose full-tunnel, exclude-selected, and include-selected
  modes behind a split on/off switch; the last split mode and app list survive
  toggling and leaving/reopening. Selected apps appear first; labels, package
  IDs, and local icons are searchable.
- A local/native connection uses the selected mode after reconnect; modifying
  the mode while connected cannot silently claim success before the tunnel is
  rebuilt.
- Android debug build/unit tests and relevant Flutter/parser tests pass; lint has
  environment/upstream failures documented in `../verification-i-029.md`.
- Process/service restart does not revive an expired lease; authorization loss
  must leave the tunnel down and preserve Android Lockdown's block behavior;
  this remains a physical acceptance criterion.
- Physical acceptance on the target Android device separately verifies
  included/excluded egress, DNS/IPv6 policy, Always-on restart, Lockdown behavior,
  and recovery after service failure. Until then, the feature is not called
  production-verified and no kill-switch marketing claim is allowed.

## Security and compatibility risks

- Android Lockdown and per-app exclusions can conflict by design; users may
  lose connectivity for excluded apps.
- Package visibility, shared UIDs, cloned apps, work profiles, Private DNS,
  Android versions, and OEM behavior can alter observed routing.
- Per-app routing is a routing convenience, not a security boundary against a
  hostile app that can bind directly to the TUN interface on a non-rooted
  device.
- Full-client GPL-3.0 code is not copied into the Flutter client without a
  separate licensing decision. Prefer platform APIs and the already selected
  AWG backend.

## Out of scope

Natural-sleep recovery remains within I-028. DNS/IPv6 leak certification,
network-change recovery, autoconnect policy, region selection, multihop,
additional tunnel protocols, and ad/tracker filtering require separate
acceptance criteria and are not implicitly completed by I-029.
