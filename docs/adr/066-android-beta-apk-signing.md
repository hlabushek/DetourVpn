# ADR-066 — signing locally distributed Android beta APKs

- Status: stable beta application ID selected (`com.detourvpn.client`); owner-kept signing key provisioned outside the checkout; physical update install remains unverified.
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
- The receipt still records `update_install: not_verified` and
  `distribution: not_accepted`; no physical update or tester distribution is
  evidenced. The placeholder guard continues to reject `org.example.vpnclient`
  if the build ID is reverted.
- Focused tests cover metadata parsing, placeholder rejection, sanitized
  receipt fields, failed signature/no partial receipt, artifact mutation, and
  preservation of an existing receipt. Synthetic fixtures do not replace the
  real signed artifact receipt above.
