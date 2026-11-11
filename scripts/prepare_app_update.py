"""Build the SIGNED update manifest for ONE latest APK. Local only: it does not upload anything.

Usage (run by the owner, from the repository root):
  .venv/Scripts/python.exe scripts/prepare_app_update.py --apk <path-to-release.apk> \
      --version-code 30 --version-name 1.2.3 --min-supported-version-code 20 \
      --apk-url https://<PUBLIC_HOST>/downloads/detourvpn-1.2.3.apk \
      --notes-file <path-to-russian-notes.txt> --out <output-dir> \
      --signing-key <path-outside-the-repo>/detourvpn-update-signing.pem

The output is <output-dir>/app-update.json (an Ed25519-signed envelope, ADR-107) plus a SHA-256
line for the owner's records. The key is read from --signing-key (PKCS#8 PEM, made by
scripts/app_update_keygen.py); a passphrase is asked in this terminal. The key is never copied,
printed or written. The script refuses a key inside this repository and checks the result
against the public key compiled into the app before writing anything.
Upload the APK and the JSON yourself, to the path the API serves (see the deploy runbook).
"""

import argparse
import getpass
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import load_pem_private_key  # noqa: E402

from vpn_api.app_update import (  # noqa: E402
    UPDATE_SIGNING_PUBLIC_KEY,
    AppUpdateManifest,
    sign_manifest,
    verify_envelope,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inside_repository(path: Path) -> bool:
    return path.resolve().is_relative_to(ROOT)


def load_signing_key(path: Path) -> Ed25519PrivateKey:
    data = path.read_bytes()
    try:
        key = load_pem_private_key(data, password=None)
    except TypeError:  # encrypted: ask locally, never from an argument or environment
        password = getpass.getpass("Update signing key passphrase: ").encode("utf-8")
        key = load_pem_private_key(data, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing_key_not_ed25519")
    return key


def main(argv: list[str] | None = None, public_key_b64: str = UPDATE_SIGNING_PUBLIC_KEY) -> int:
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
    parser.add_argument("--signing-key", type=Path, required=True)
    args = parser.parse_args(argv)

    if not args.apk.is_file():
        print("apk_not_found", file=sys.stderr)
        return 2
    if not args.signing_key.is_file():
        print("signing_key_not_found", file=sys.stderr)
        return 2
    if inside_repository(args.signing_key):
        print("signing_key_inside_repository", file=sys.stderr)
        return 2
    if not public_key_b64:
        print("app_public_key_missing: run scripts/app_update_keygen.py first", file=sys.stderr)
        return 2
    try:
        key = load_signing_key(args.signing_key)
    except (ValueError, TypeError):
        print("signing_key_unreadable", file=sys.stderr)
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
    envelope = sign_manifest(manifest, key)
    del key
    if verify_envelope(envelope, public_key_b64) is None:
        print("signing_key_does_not_match_app_public_key", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "app-update.json"
    target.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {target}")
    print(f"key_id={envelope['key_id']}")
    print(f"apk_sha256={manifest.apk_sha256}")
    print(f"apk_size_bytes={manifest.apk_size_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
