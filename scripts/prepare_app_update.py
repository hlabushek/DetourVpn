"""Build the update manifest for ONE latest APK. Local only: it does not upload anything.

Usage (run by the owner, from the repository root):
  .venv/Scripts/python.exe scripts/prepare_app_update.py --apk <path-to-release.apk> \
      --version-code 30 --version-name 1.2.3 --min-supported-version-code 20 \
      --apk-url https://<PUBLIC_HOST>/downloads/detourvpn-1.2.3.apk \
      --notes-file <path-to-russian-notes.txt> --out <output-dir>

The output is <output-dir>/app-update.json plus a SHA-256 line for the owner's records.
Upload the APK and the JSON yourself, to the path the API serves (see the deploy runbook).
Nothing here touches a server or a signing key.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from vpn_api.app_update import AppUpdateManifest  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--min-supported-version-code", type=int, required=True)
    parser.add_argument("--apk-url", required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if not args.apk.is_file():
        print("apk_not_found", file=sys.stderr)
        return 2
    manifest = AppUpdateManifest(
        version_code=args.version_code,
        version_name=args.version_name,
        min_supported_version_code=args.min_supported_version_code,
        apk_url=args.apk_url,
        apk_sha256=sha256_file(args.apk),
        apk_size_bytes=args.apk.stat().st_size,
        release_notes_ru=args.notes_file.read_text(encoding="utf-8").strip(),
    )
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "app-update.json"
    target.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {target}")
    print(f"apk_sha256={manifest.apk_sha256}")
    print(f"apk_size_bytes={manifest.apk_size_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
