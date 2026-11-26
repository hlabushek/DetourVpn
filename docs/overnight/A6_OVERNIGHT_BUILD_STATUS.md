# A6 — Is `scripts/run_overnight_build.ps1` still needed? (read-only), 2026-11-27

The script was **read, not run**. Nothing was built, prepared, moved or signed.

## What the script would do

1. Prepare the `c6` SDK tree (`.cache\i-011\sdk-go-only-c6-20261117`) with
   `prepare_android_poc.py`, staged in `build\sdk-prep-c6` and then moved into `.cache`, unless
   `PREPARED.json` already exists there (never overwritten).
2. Copy `jniLibs` and `assets` from the `c5` tree if missing, and stop unless only
   `GoBackend.java` differs between `c5` and `c6`.
3. Check `PREPARED.json` `patch_sha256` against a hard-coded hash and that both `libwg-go.so`
   files (arm64-v8a, x86_64) exist.
4. Ask for the API origin, keystore path, keystore password (masked), key alias and key password.
5. Put the signing values in environment variables and run
   `scripts\build_flutter_client.ps1 -ApiOrigin <origin> -WindowTunnels -WindowSeconds 3600`;
   remove the passwords from the environment afterwards.
6. Print the APK path and an `adb install -r` command. It does not install, push or deploy.

## Current facts (read-only checks, 2026-11-27)

| Fact | Result |
|---|---|
| `c6` tree has `PREPARED.json` | yes |
| `c6` `patch_sha256` = hash in the script = SHA-256 of `apps/android/patches/go-only.patch` | yes, all three equal |
| `c6` has both `libwg-go.so` files | yes |
| `build_flutter_client.ps1` picks a prepared tree by matching the pinned patch hash | yes (it scans `.cache\i-011\*` for a matching `PREPARED.json`), so it finds `c6` without this script |
| A newer owner path exists | yes: `scripts/release_app_beta37.ps1` and the menu `scripts/beta37_release_console.ps1` (commit `d6f9cb6`), which call `build_beta_apk.ps1` → `build_flutter_client.ps1` with `-WindowSeconds 3600` |
| The script checks for a clean tree and records the commit | **no** (CLAUDE.md requires a build from a committed commit with the hash in the receipt); the beta.37 release script has a clean-tree preflight |
| Last change to the script | `39724f4` (ADR-150, `-WindowSeconds 3600`) |

## Recommendation

**No longer needed.** Its one-time job (steps 1-3: prepare and verify the `c6` tree) is already
done, and steps 4-6 duplicate the beta.37 release path, which is newer and has the clean-tree
preflight this script lacks. Use `scripts/beta37_release_console.ps1` (or
`release_app_beta37.ps1`) for the next APK.

Suggested owner action (not done overnight, because it removes or changes a file): either mark the
script as superseded in its header comment, or remove it in a separate reviewed commit; HANDOVER,
ADR-150 and `docs/runbooks/deploy-window-3600.md` still mention it and would need the same note.
Keep it if you want a build without the beta.37 version bump; in that case add a
`git status --porcelain` check and print `git rev-parse --short HEAD` before building.

## Files changed in A6

- `docs/overnight/A6_OVERNIGHT_BUILD_STATUS.md` (this file).
