# A1 — Test baseline, 2026-11-27

Branch `claude/connection-reliability`, commit `d6f9cb6`, clean tree (only the untracked night plan).
Flutter 3.47.7 (stable). JDK 21 from Android Studio (`javac --release 17`). No device, no Gradle.

| Check | Command | Result |
|---|---|---|
| `flutter test` (in place) | `apps/client> flutter test --no-pub` with `PUB_CACHE` set | **431 passed, 0 failed** ("All tests passed!", exit 0) |
| `flutter analyze` (ASCII copy) | copy `lib`, `test`, `pubspec.yaml`, `pubspec.lock`, `analysis_options.yaml` to a scratch folder; `flutter pub get --offline`; `flutter analyze` | **1 issue**: the expected `assets/orbit/` asset-directory warning (folder not copied). Exit 1 because of that warning only. |
| `dart format` (ASCII copy) | `dart format --output=none --set-exit-if-changed lib test` | 75 files, 0 changed, exit 0 |
| Java substitute, main run | `LOCALAPPDATA_POSIX="$(cygpath -u "$LOCALAPPDATA")" bash docs/overnight/A1_java_substitute.sh` | javac exit 0; JUnit **274 passed** (35 test classes) |
| Java substitute, run from `apps/client/android/app` | same script (second JUnit run) | **11 passed** (`ConnectionWiringTest`, `RestartInPlacePatchTest`, `SystemIntegrationWiringTest`) |

Java total: 285 passed, 0 failed.

## Notes

- The Java substitute is the HANDOVER procedure, now written down as
  `docs/overnight/A1_java_substitute.sh`. It compiles against the `c6` patched tunnel tree
  (`.cache/i-011/sdk-go-only-c6-20261117`), whose date matches the last change to
  `apps/android/patches/go-only.patch` (commit `59f928b`). It writes to a fresh temp folder and
  deletes nothing.
- First run showed 4 failures in `ConnectionWiringTest`: `NoSuchFileException` for
  `src/main/java/.../ClientApplication.java`. Cause: that test reads source files relative to
  `apps/client/android/app`, like the two tests HANDOVER already names. Moving it into the second
  run fixed it. Not a product defect. HANDOVER step 3 should list three such tests, not two.
- This is a **deviation from Gradle**: no `testDebugUnitTest`, no manifest merge, no lint, no
  native build. Not verified on a phone.
- Server tests (pytest) were not part of A1; see B2 for the focused run.
