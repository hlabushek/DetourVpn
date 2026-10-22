"""Manual LOCAL bot-code client. No code/token output, files, key generation or VPN."""

import argparse
import getpass
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class LocalClient:
    def __init__(self, base: str) -> None:
        target = urlsplit(base)
        if (
            target.scheme != "http"
            or target.hostname != "127.0.0.1"
            or target.port is None
            or target.username is not None
            or target.password is not None
            or target.path not in {"", "/"}
            or target.query
            or target.fragment
        ):
            raise ValueError("Use only http://127.0.0.1:<local-port>")
        self.base = base.rstrip("/") + "/api/v1"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.access: str | None = None

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> Any:
        request = urllib.request.Request(  # noqa: S310 - constructor enforces loopback HTTP base
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"}
            | ({"Authorization": "Bearer " + self.access} if self.access is not None else {}),
            method=method,
        )
        try:
            with self.opener.open(request, timeout=15) as response:  # noqa: S310 - validated loopback
                content = response.read(128 * 1024)
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            # Never print the request/response body or exception URL.
            raise RuntimeError(f"Local API returned HTTP {error.code}") from None
        except (OSError, ValueError):
            raise RuntimeError("Local API request failed") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    client = LocalClient(args.api)
    start = client.call("POST", "/auth/start")
    print("Open this bot link, request a code, then return here:")
    print(start["bot_url"])  # Public pairing ID only; never contains verifier.
    code = getpass.getpass("Code from the bot (hidden): ")
    tokens = client.call(
        "POST",
        "/auth/verify",
        {
            "request_id": start["request_id"],
            "verifier": start["verifier"],
            "code": code,
        },
    )
    code = ""
    start.clear()
    client.access = tokens["access_token"]
    device_id: UUID | None = None
    try:
        profile = client.call("GET", "/me")
        print(f"Signed in. Telegram account: {profile['telegram_subject']}")
        quota = client.call("GET", "/quota")
        print(
            f"Quota in bytes: granted={quota['granted_bytes']}, "
            f"used={quota['used_bytes']}, available={quota['available_bytes']}"
        )
        print("Optional: use an existing ANDROID device public key. Never paste a private key.")
        public_key = input("Public key, or Enter to skip device registration: ").strip()
        if public_key:
            device = client.call(
                "POST",
                "/devices",
                {
                    "platform": "android",
                    "key_type": "x25519",
                    "public_key": public_key,
                },
            )
            device_id = UUID(device["id"])
            print(f"Device registered: {device_id}")
        visible = client.call("GET", "/devices")
        print(f"Active devices in this account: {len(visible)}")
        refreshed = client.call("POST", "/auth/refresh", {"refresh_token": tokens["refresh_token"]})
        tokens.clear()
        tokens = refreshed
        client.access = tokens["access_token"]
        print("Session refreshed; credentials stay in memory.")
        if device_id is not None:
            decision = input("Revoke this device and close its sessions now? [y/N]: ").lower()
            if decision == "y":
                client.call("DELETE", f"/devices/{device_id}")
                client.access = None
                print("Device and its control-plane sessions revoked.")
        if client.access is not None:
            client.call("POST", "/auth/logout")
            client.access = None
            print("Signed out.")
    finally:
        # Best-effort logout on cancellation; never mask the original result.
        if client.access is not None:
            try:
                client.call("POST", "/auth/logout")
            except RuntimeError:
                print("Logout could not be confirmed. Retry logout-all after the next login.")
        client.access = None
        tokens.clear()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as error:
        print(str(error))  # Only locally constructed sanitized errors, no provider exception.
        raise SystemExit(1) from None
    except (KeyboardInterrupt, EOFError):
        print("Cancelled.")
        raise SystemExit(1) from None
