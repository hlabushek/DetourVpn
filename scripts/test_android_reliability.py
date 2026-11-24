"""Run pure JVM reliability tests; no Gradle, APK, native build, device or network."""

import argparse
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--hamcrest", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / ".cache/reliability-unit-tests"
    output.mkdir(parents=True, exist_ok=True)
    package = "org/example/vpnpoc"
    base = root / "apps/client/android/app/src"
    names = ["ConnectionSupervisor", "TunnelProbePacket", "WindowDeadline"]
    sources = [base / "main/java" / package / f"{name}.java" for name in names]
    sources += [base / "test/java" / package / f"{name}Test.java" for name in names]
    cp = os.pathsep.join(str(p.resolve()) for p in [output, args.junit, args.hamcrest])
    suffix = ".exe" if os.name == "nt" else ""
    subprocess.run(  # noqa: S603 - explicit local tool and argument list; no shell
        [
            str(args.java_home / "bin" / f"javac{suffix}"),
            "-encoding",
            "UTF-8",
            "--release",
            "17",
            "-cp",
            cp,
            "-d",
            str(output),
            *map(str, sources),
        ],
        check=True,
    )
    subprocess.run(  # noqa: S603 - explicit local tool and argument list; no shell
        [
            str(args.java_home / "bin" / f"java{suffix}"),
            "-cp",
            cp,
            "org.junit.runner.JUnitCore",
            *[f"org.example.vpnpoc.{name}Test" for name in names],
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
