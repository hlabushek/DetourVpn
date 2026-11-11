"""Create the owner's Ed25519 update-signing key (ADR-107). Run once, by the owner, locally.

Usage:
  .venv/Scripts/python.exe scripts/app_update_keygen.py \
      --private-key-out <secure-folder-outside-the-repo>/detourvpn-update-signing.pem

Writes the private key as passphrase-encrypted PKCS#8 PEM (the passphrase is asked twice in this
terminal) to a path OUTSIDE this repository; refuses to overwrite an existing file. Prints only
the PUBLIC key, which you then paste into BOTH:
  services/api/src/vpn_api/app_update.py            UPDATE_SIGNING_PUBLIC_KEY
  apps/android/app/src/main/java/org/example/vpnpoc/UpdateSignature.java   PUBLIC_KEY_B64
(a test fails if they differ). Keep a backup of the private key offline: losing it means
installed apps can never accept another in-app update.
"""

import argparse
import base64
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
)

from vpn_api.app_update import key_id  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--private-key-out", type=Path, required=True)
    args = parser.parse_args(argv)
    target: Path = args.private_key_out
    if target.resolve().is_relative_to(ROOT):
        print("private_key_path_inside_repository", file=sys.stderr)
        return 2
    if target.exists():
        print("private_key_path_exists", file=sys.stderr)
        return 2
    passphrase = getpass.getpass("New passphrase for the signing key: ")
    if len(passphrase) < 12 or passphrase != getpass.getpass("Repeat the passphrase: "):
        print("passphrase_rejected: at least 12 characters, typed twice the same", file=sys.stderr)
        return 2
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(passphrase.encode("utf-8"))
    )
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    del key, passphrase
    target.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL: never replace an existing key file. Mode 0600 where the OS supports it.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(pem)
    print(f"private key written to {target}")
    print(f"public_key_b64={base64.b64encode(public).decode('ascii')}")
    print(f"key_id={key_id(public)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
