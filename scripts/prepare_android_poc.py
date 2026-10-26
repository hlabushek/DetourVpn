"""Materialize an exact, reviewed SDK tree. No downloads, secrets, or remote actions."""

import argparse
import difflib
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "ff15093bf3856fea0947923474870b86efb682f0"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-dir",
        default=".cache/i-011/sdk-go-only",
        help="prepared output directory; an existing prepared tree is never overwritten",
    )
    args = parser.parse_args()
    source = ROOT / ".cache/i-011/awg-android"
    target = (ROOT / args.target_dir).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError as failure:
        raise SystemExit("Prepared SDK target must stay inside the repository cache") from failure
    patch = ROOT / "apps/android/patches/go-only.patch"
    git_executable = shutil.which("git")
    if git_executable is None:
        raise SystemExit("git is required to apply the reviewed SDK patch")
    archive = subprocess.check_output(  # noqa: S603
        ["git", "-C", str(source), "archive", REVISION, "tunnel", "COPYING"]  # noqa: S607
    )
    target.mkdir(parents=True, exist_ok=True)
    prepared_manifest = target / "PREPARED.json"
    if not prepared_manifest.exists() and any(target.iterdir()):
        raise SystemExit("Refusing to overwrite a non-empty unprepared SDK target")
    # Never overwrite a prepared tree: preserve native artifacts and fail on source drift.
    if prepared_manifest.exists():
        receipt = json.loads(prepared_manifest.read_text())
        if receipt["patch_sha256"] != hashlib.sha256(patch.read_bytes()).hexdigest():
            raise SystemExit("Prepared patch differs; use a fresh sdk-go-only directory")
        for name, digest in receipt["source_sha256"].items():
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != digest:
                raise SystemExit("Prepared source drift")
        print("Prepared SDK sources verified")
        return
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(target, filter="data")
    subprocess.run(  # noqa: S603
        [
            git_executable,
            "apply",
            f"--directory={target.relative_to(ROOT).as_posix()}",
            str(patch),
        ],
        cwd=ROOT,
        check=True,
    )
    manifest = target / "tunnel/src/main/AndroidManifest.xml"
    text = (
        manifest.read_text()
        .replace(
            'android:exported="false"',
            'android:exported="false" android:foregroundServiceType="systemExempted"',
        )
        .replace(
            "</service>",
            '<meta-data android:name="android.net.VpnService.SUPPORTS_ALWAYS_ON" '
            'android:value="false" /></service>',
        )
    )
    manifest.write_text(text, encoding="utf-8", newline="\n")
    hashes = {
        str(p.relative_to(target)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [target / "COPYING", *sorted((target / "tunnel").rglob("*"))]
        if p.is_file()
    }
    receipt = {
        "sdk_revision": REVISION,
        "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "source_sha256": hashes,
    }
    (target / "PREPARED.json").write_text(json.dumps(receipt, indent=2) + "\n")
    # Keep an auditable readable patch of all prepared modifications in the ignored tree.
    diff: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle.getmembers():
            if member.isfile() and member.name.endswith((".java", ".go", ".xml")):
                stream = bundle.extractfile(member)
                if stream is None:
                    raise RuntimeError("Missing archived source")
                before = stream.read().decode().splitlines(True)
                after = (target / member.name).read_text().splitlines(True)
                diff.extend(difflib.unified_diff(before, after, member.name, member.name))
    (target / "prepared.diff").write_text("".join(diff), encoding="utf-8", newline="\n")
    print("Pinned GoBackend-only SDK prepared; native build still required")


if __name__ == "__main__":
    main()
