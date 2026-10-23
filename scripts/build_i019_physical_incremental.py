"""I-019 test-target changes only; verify the entire prior build stamp first.

No remote access. Ordinary APKs/native sources remain the validated prior build.
"""

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn/i019-private"
TARGET = ROOT / "apps/client/integration_test/node_failover_live_test.dart"


def stamp(*, prior_target: bytes | None = None) -> str:
    files: list[Path] = []
    for part in (
        "apps/client/lib",
        "apps/client/test",
        "apps/client/integration_test",
        "apps/client/android/app/src",
        "apps/android/app/src",
    ):
        files.extend(p for p in (ROOT / part).rglob("*") if p.is_file())
    for part in (
        "apps/client/pubspec.yaml",
        "apps/client/pubspec.lock",
        "apps/client/android/app/build.gradle.kts",
        "apps/client/android/app/gradle.lockfile",
        "apps/client/android/build.gradle.kts",
        "apps/android/tunnel/build.gradle.kts",
        "apps/android/tunnel/gradle.lockfile",
        "scripts/build_flutter_client.ps1",
        "scripts/run_i019_staging.ps1",
    ):
        files.append(ROOT / part)
    rows = []
    for path in files:
        data = path.read_bytes()
        if prior_target is not None and path == TARGET:
            data = prior_target
        rows.append(str(path).replace("\\", "/").lower() + ":" + hashlib.sha256(data).hexdigest())
    digest = hashlib.sha256("\n".join(sorted(rows)).encode()).digest()
    return "-".join(f"{byte:02X}" for byte in digest)


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("windows_build_required")
    client = Path((PRIVATE / "client-path").read_text())
    if not client.is_relative_to(Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn/i013-builds"):
        raise RuntimeError("unexpected_snapshot_path")
    cached = client / "integration_test/node_failover_live_test.dart"
    current = TARGET.read_bytes()
    if stamp(prior_target=cached.read_bytes()) != (PRIVATE / "build-source-stamp").read_text():
        raise RuntimeError("prior_full_build_inputs_changed")
    expected = stamp()
    cached.write_bytes(current)
    sdk = Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn/toolchains/flutter"
    env = dict(os.environ)
    env["JAVA_HOME"] = r"C:\Program Files\Android\Android Studio\jbr"
    env["PUB_CACHE"] = str(sdk.parent / "pub-cache")
    commands = (
        [
            str(sdk / "bin/dart.bat"),
            "format",
            "--output=none",
            "--set-exit-if-changed",
            "lib",
            "test",
            "integration_test",
        ],
        [str(sdk / "bin/flutter.bat"), "analyze", "--no-pub"],
        [str(sdk / "bin/flutter.bat"), "test", "--no-pub"],
        [
            str(sdk / "bin/flutter.bat"),
            "build",
            "apk",
            "--debug",
            "--no-pub",
            "--target-platform",
            "android-arm64",
            "--target",
            "integration_test/node_failover_live_test.dart",
        ],
    )
    for command in commands:
        subprocess.run(command, cwd=client, env=env, check=True)  # noqa: S603 - verified local build inputs/tools
    if stamp() != expected or cached.read_bytes() != TARGET.read_bytes():
        raise RuntimeError("source_changed_during_build")
    (PRIVATE / "build-source-stamp").write_text(expected)
    print("Prior full build inputs verified; updated physical target analyzed/tested/compiled.")


if __name__ == "__main__":
    main()
