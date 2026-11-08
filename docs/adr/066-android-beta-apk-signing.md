# ADR-066 — signing locally distributed Android beta APKs

- Status: stable beta application ID selected (`com.detourvpn.client`); owner-kept signing key provisioned outside the checkout; beta.2-to-beta.6 physical updates verified, beta.7 is the next physical diagnostic build, connection acceptance remains open.
- Scope: ROADMAP phase 6 private Android APK distribution.
- Platform reference: [Android app signing](https://developer.android.com/studio/publish/app-signing).

## Context

The client release variant previously had no signing configuration. The latest
owner beta install used a debug APK. Android requires an APK distributed outside
the debug workflow to carry a release signature, and updates must preserve the
application ID and signing identity. The owner selected `com.detourvpn.client`
as the stable private beta ID before the first tester release.

## Decision

- Use the Android Gradle Plugin's built-in release signing configuration; add no
  signing library or custom cryptography.
- Build the private Pixel tester APK for `arm64-v8a` only by default. Keep the
  Flutter client script configurable for the existing arm64+x86_64 universal
  build when one artifact must support both architectures. This avoids shipping
  a second large native engine library to the Pixel.
- Signed beta builds always select the v3 window-tunnel API/controller. The
  legacy v2 build remains available only through the lower-level local client
  build script, preventing a beta APK from silently targeting closed v2 admission.
- `scripts/build_beta_apk.ps1 -PhysicalTestBuild` is a signed pre-acceptance
  device-test package path. It retains formatting, static analysis, dependency
  lock checks, release compilation, signing, and receipt verification, while
  skipping automated test suites. It explicitly keeps distribution unaccepted;
  ordinary builds continue to run their automated tests.
- Read the keystore path, store password, key alias, and key password only from
  the `DETOURVPN_BETA_*` environment variables of the local build process. Keep
  the keystore outside the repository. Do not put credentials in Gradle files,
  `gradle.properties`, command-line arguments, source snapshots, or receipts.
- The supported `scripts/build_flutter_client.ps1` rejects relative paths,
  paths inside the source checkout, and missing keystore files before creating
  the build snapshot. Gradle independently requires an absolute existing file.
- With all four values absent, retain the current unsigned release behavior.
  If only some values are present, fail configuration and name only the missing
  variable names. When all values are present, require an absolute path to an
  existing keystore file and assign that signing config only to `release`.
- Refuse release signing while `applicationId` is the temporary placeholder
  `org.example.vpnclient`; this
  prevents the temporary placeholder from becoming a signed tester package.
- The owner keeps signing key custody and recovery independent of the checkout,
  and retains the same signing identity for updates. The old debug install is
  not update-compatible with the beta key.
- Before distribution, record the application ID, version, artifact SHA-256,
  signer certificate fingerprint, and an update-install result in a separate
  sanitized receipt. Never record the keystore path, aliases, or passwords.
- `scripts/verify_beta_apk.py` may create a local pre-distribution receipt after
  `aapt` reads package metadata and the SDK's `apksigner.jar` verifies the
  signature through `java -jar` with shell invocation disabled. This avoids
  Windows batch-file argument mangling for Unicode checkout paths. It
  rejects example IDs and records neither the APK path nor signing secrets. It
  always leaves physical update installation unverified and distribution
  unaccepted; it does not change the acceptance decision.

## Consequences

- The build can produce a signed local beta artifact once the owner has supplied
  a stable package identity and a securely managed key.
- Configuration support alone does not prove that a key exists, an APK is
  signed, the signer is trusted, or updates work. Until those checks pass, no
  tester distribution is accepted.
- Direct APK distribution leaves key custody and recovery with the project
  owner. Loss or compromise can prevent safe updates and requires a separately
  reviewed migration plan.

## Verification

- With all beta signing variables unset, `:app:signingReport` shows no release
  signing configuration.
- A partial variable set fails with missing variable names and no values.
- The owner created a JKS signing key outside the checkout. Passwords were
  entered locally; signing variables were scoped to the build process and
  cleared afterward. The owner still needs a separate recoverable backup.
- The owner selected `com.detourvpn.client` as the permanent beta application
  ID. The old debug install uses a different signer and is not an update test.
- A signed `com.detourvpn.client` release APK, version `0.1.0-beta.1`, passed
  metadata and signature verification. Sanitized receipt:
  `../evidence/i-030-beta-apk-20261101T172209Z.json`.
- Pixel beta `0.1.0-beta.2` (version code 3) was accidentally built without
  `VPN_WINDOW_TUNNELS=true`; it exposed the v2 `admission_closed` response and
  hid the profile Split link. Beta `0.1.0-beta.3` (version code 4) fixes the
  build default and targets arm64-v8a. The x86_64 ABI remains available through
  the configurable lower-level universal build.
- The beta.2 receipt still records `update_install: not_verified` and
  `distribution: not_accepted`; a later ADB update installed beta.3 over beta.2
  and the foreground activity launched. Beta.4 is the next update gate; no tester
  distribution is accepted. The placeholder guard continues to reject
  `org.example.vpnclient` if the build ID is reverted.
- Beta.4 increments the package to version code 5 and `0.1.0-beta.4`. Its signed
  update installed over beta.3 on the Pixel and launched; no VPN transport was
  active afterward. Receipt:
  `../evidence/i-030-beta-apk-install-20261103T165855Z.json`. Connection and
  server-side revoke acceptance remain open.
- Beta.5 increments the package to version code 6 and `0.1.0-beta.5`. It
  preserves native start failure categories through the Android method channel
  and clarifies the disabled button while a failed attempt awaits server-side
  closure. It is a diagnostic/UX candidate only; engine start, revoke, and
  data-path acceptance require a signed install and controlled physical retest.
- Beta.6 source increments the package to version code 7 and `0.1.0-beta.6`.
  On native start failure, release logcat records only the exception class and
  up to three cause classes; messages, profiles, endpoints, and keys are
  excluded. The first release assembly attempt stopped because the Windows
  system disk ran out of space; a subsequent signed build passed receipt checks
  and installed on Pixel. No beta.6 connection attempt has been performed yet.
  Receipt: `../evidence/i-030-beta-apk-install-20261103T175900Z.json`.
- Beta.7 source increments the package to version code 8 and `0.1.0-beta.7`.
  It adds fixed native-start substage labels for libbox setup, config creation,
  and service start; only labels and exception class names go to logcat. The
  signed update installed and launched on Pixel. The subsequent physical
  attempt repeatedly failed during `libbox_config` with safe category
  `proxyerror`; ADB confirmed no VPN transport. Pinned Amnezia Box source shows
  AWG is registered as an endpoint, not an outbound. Beta.8 (version code 9)
  moves the AWG config to top-level `endpoints` and keeps tag `awg` for existing
  routing and DNS references. Beta.8 was signed, installed over beta.7, and
  launched on Pixel; ADB confirms no VPN transport after install. Its first
  attempts advanced past config parsing but failed during `libbox_service_start`
  with `proxyerror`. Beta.9 (version code 10) adds fixed safe categories to
  distinguish AWG endpoint start from TUN inbound/platform startup without
  logging native messages. Release Java compilation passed; the signed beta.9
  APK was installed over beta.8 and launched on Pixel; ADB confirmed no VPN
  transport after install. Receipt:
  `../evidence/i-030-beta-apk-install-20261103T1837Z.json`. One physical
  diagnostic attempt still failed at `libbox_service_start` with `proxyerror`
  and no VPN transport. Beta.10 (version code 11) adds fixed allowlisted
  classification from exception messages in memory; the text itself is never
  logged. The signed beta.10 update installed over beta.9 and launched on Pixel;
  ADB confirms no VPN transport after install. Its physical attempt classified
  the failure as the broad `awg_endpoint_start` category. Beta.11 (version code
  12) prioritizes nested AWG config/tun markers before that outer category.
  Beta.11 was installed over beta.10 and launched on Pixel; ADB confirms no
  VPN transport after install. Receipt:
  `../evidence/i-030-beta-apk-install-20261103T1852Z.json`. Its diagnostic
  attempt, handshake, egress, and revoke closure remain open until separately
  evidenced.
- Beta.11 produced two ADB-observed `libbox_service_start` failures classified
  as `awg_endpoint_start`; VPN transport remained inactive. `IpcSet` and
  `tun.Start` are already marked separately in the pinned AWG endpoint, leaving
  `awgDevice.Up()` as the next unclassified start call (hypothesis only).
  Beta.12 (version code 13) adds the fixed `awg_device_up` cause marker to the
  reproducible upstream patch and allowlisted Android classifier. Build,
  install, physical diagnostic, handshake, egress, and revoke closure are
  pending. Do not repeat Connect while server revoke is pending.- Focused tests cover metadata parsing, placeholder rejection, sanitized
  receipt fields, failed signature/no partial receipt, artifact mutation, and
  preservation of an existing receipt. Synthetic fixtures do not replace the
  real signed artifact receipt above.

2026-11-02 custody clarification: the owner-run restore from the BitLocker-
protected removable backup produced a signed APK whose signer matched the
previous beta, and both JKS passwords were accepted by that build. The
temporary restore copy was removed. This closes the JKS restore/password check
for the beta signing key; it does not verify that the separate BitLocker
recovery-key file on the PC is readable or independently recoverable. Evidence:
`../evidence/i-030-beta-signing-restore-20261101.json`.

2026-11-02 owner follow-up: the owner confirms that the separate BitLocker
recovery-key file exists on the PC. The file path and contents were not
recorded or inspected, and no key value was read. This closes the owner's
recovery-key-file presence check; confidentiality and actual recovery were not
independently tested. See
`../evidence/i-030-owner-ops-confirmation-20261102.json`.
