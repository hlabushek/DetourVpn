"""Read-only owner MFA preflight for one isolated beta physical test."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import ssl
import subprocess
import time
import sys
from datetime import UTC, datetime
from pathlib import Path
from http.client import HTTPException, HTTPSConnection
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://193.68.88.239"
APP_ID = "com.detourvpn.client"
SUPPORTED_APP_VERSIONS = {
    "0.1.0-beta.20": 21,
    "0.1.0-beta.21": 22,
    "0.1.0-beta.22": 23,
}
BETA_SIGNER_SHA256 = "6b99a02bee6dc8dba659af64fcd025782fc3011a41099168e3f3a766b84ef3b8"
# ADR-084: beta.22 requests 16 MiB windows; a renewal pins a second hold while the first is open.
MIN_WINDOW_BYTES = 16_777_216
CONNECT_ATTEMPTS = 5


def expected_apk_receipt(
    version_name: str, version_code: int, folder: Path | None = None
) -> tuple[str, str]:
    receipts = folder or ROOT / "docs" / "evidence"
    candidates: list[tuple[datetime, str, str]] = []
    for path in receipts.glob("i-030-beta-apk-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
            artifact_hash = record["artifact_sha256"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        verification = record.get("verification")
        if (
            record.get("application_id") != APP_ID
            or record.get("version_name") != version_name
            or record.get("version_code") != version_code
            or not isinstance(verification, dict)
            or verification.get("aapt_metadata") != "passed"
            or verification.get("apksigner_signature") != "passed"
            or record.get("signer_certificate_sha256") != [BETA_SIGNER_SHA256]
            or not isinstance(artifact_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", artifact_hash)
        ):
            continue
        candidates.append((stamp, artifact_hash, path.name))
    if not candidates:
        raise RuntimeError("supported_beta_signed_receipt_missing")
    _, artifact_hash, receipt_name = max(candidates)
    return artifact_hash, receipt_name


def device_preflight() -> dict[str, object]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("android_adb_unavailable")
    adb = Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe"

    def run(args: list[str], code: str, *, binary: bool = False) -> bytes | str:
        try:
            completed = subprocess.run(
                [str(adb), *args], capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError(code) from None
        if completed.returncode != 0:
            raise RuntimeError(code)
        return completed.stdout if binary else completed.stdout.decode(errors="replace")

    if not adb.is_file():
        raise RuntimeError("android_adb_unavailable")
    device_rows = [
        line for line in str(run(["devices"], "android_adb_unavailable")).splitlines()
        if re.match(r"^\S+\s+device(?:\s|$)", line)
    ]
    if len(device_rows) != 1:
        raise RuntimeError("single_authorized_android_device_required")

    model = str(run(["-d", "shell", "getprop", "ro.product.model"],
                    "android_device_query_failed")).strip()
    if model != "Pixel 8 Pro":
        raise RuntimeError("expected_pixel_8_pro_required")
    package = str(run(["-d", "shell", "dumpsys", "package", APP_ID],
                      "android_package_query_failed"))
    version_name = re.search(r"\bversionName=([^\s]+)", package)
    version_code = re.search(r"\bversionCode=(\d+)", package)
    if version_name is None or version_code is None:
        raise RuntimeError("installed_beta_version_unavailable")
    installed_version = version_name.group(1)
    installed_version_code = int(version_code.group(1))
    if SUPPORTED_APP_VERSIONS.get(installed_version) != installed_version_code:
        raise RuntimeError("supported_beta_version_not_installed")
    expected_sha256, receipt_name = expected_apk_receipt(
        installed_version, installed_version_code
    )

    paths = str(run(["-d", "shell", "pm", "path", APP_ID],
                    "android_apk_path_unavailable"))
    base_apk = re.search(r"(?m)^package:(\S+/base\.apk)\s*$", paths)
    if base_apk is None:
        raise RuntimeError("android_base_apk_unavailable")
    apk_bytes = run(["-d", "exec-out", "cat", base_apk.group(1)],
                    "android_apk_read_failed", binary=True)
    if not isinstance(apk_bytes, bytes) or hashlib.sha256(apk_bytes).hexdigest() != expected_sha256:
        raise RuntimeError("installed_apk_does_not_match_signed_receipt")

    connectivity = str(run(["-d", "shell", "dumpsys", "connectivity"],
                           "android_connectivity_query_failed"))
    active_vpn = bool(re.search(r"(?im)^\s*Transports:.*\bVPN\b", connectivity))
    always_on = str(run(["-d", "shell", "settings", "get", "secure",
                         "always_on_vpn_app"], "android_vpn_settings_unavailable")).strip()
    lockdown = str(run(["-d", "shell", "settings", "get", "secure",
                        "always_on_vpn_lockdown"], "android_vpn_settings_unavailable")).strip()
    if active_vpn or always_on != "null" or lockdown != "0":
        raise RuntimeError("android_vpn_must_be_off_without_lockdown")
    return {
        "authorized_device_count": 1,
        "expected_device_model": True,
        "application_id": APP_ID,
        "version_name": installed_version,
        "version_code": installed_version_code,
        "signed_apk_receipt": receipt_name,
        "installed_base_apk_matches_signed_receipt": True,
        "active_vpn_transport": False,
        "target_always_on_configured": False,
        "lockdown": False,
    }


def _post_once_connected(path: str, headers: dict[str, str], data: bytes | None) -> object:
    """POST with retries only while the TCP/TLS connection is being established.

    A reset during connect() means no request byte reached the server, so repeating is
    safe and cannot burn a TOTP code. Once the request is sent, nothing is retried.
    """
    parts = urlsplit(ORIGIN)
    for attempt in range(CONNECT_ATTEMPTS):
        conn = HTTPSConnection(
            parts.hostname or "", parts.port or 443, timeout=20,
            context=ssl.create_default_context(),
        )
        try:
            try:
                conn.connect()
            except (OSError, ssl.SSLError):
                if attempt + 1 == CONNECT_ATTEMPTS:
                    raise RuntimeError("beta_api_unavailable") from None
                time.sleep(4 * (attempt + 1))
                continue
            try:
                conn.request("POST", path, body=data, headers=headers)
                response = conn.getresponse()
                payload = response.read()
            except (OSError, HTTPException):
                raise RuntimeError("beta_api_unavailable_after_send") from None
            if response.status >= 400:
                raise RuntimeError(f"beta_api_http_{response.status}")
            try:
                return json.loads(payload)
            except ValueError:
                raise RuntimeError("beta_api_unavailable_after_send") from None
        finally:
            conn.close()
    raise RuntimeError("beta_api_unavailable")


def call(method: str, path: str, body: dict[str, str] | None = None,
         token: str | None = None) -> object:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if method == "POST":
        # The beta host resets bursts of new connections. A write is retried only before the
        # request is sent (connect phase); after that it is attempted exactly once.
        return _post_once_connected(path, headers, data)
    # Idempotent reads are retried on any transient failure.
    attempts = 4 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            with urlopen(
                Request(ORIGIN + path, data=data, headers=headers, method=method), timeout=20
            ) as res:
                return json.loads(res.read())
        except HTTPError as exc:
            raise RuntimeError(f"beta_api_http_{exc.code}") from None
        except (URLError, TimeoutError, OSError, ValueError):
            if attempt + 1 == attempts:
                raise RuntimeError("beta_api_unavailable") from None
            time.sleep(4 * (attempt + 1))
    raise RuntimeError("beta_api_unavailable")


def save(data: dict[str, object]) -> Path:
    folder = ROOT / "docs" / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"i-030-owner-preflight-{stamp}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    stage = "input"
    token: str | None = None
    result: dict[str, object] = {
        "issue": "I-030", "timestamp_utc": datetime.now(UTC).isoformat(),
        "target": "isolated_beta", "mode": "read_only",
    }
    try:
        stage = "android_preflight"
        device = device_preflight()
        result["device_preflight"] = device
        print("Read-only supported-beta Android and isolated beta preflight; no quota or connection changes.")
        owner = getpass.getpass("Owner Telegram numeric ID (hidden): ")
        password = getpass.getpass("beta-owner admin password (hidden): ")
        totp = getpass.getpass("Current TOTP code (hidden): ")
        if not re.fullmatch(r"[1-9][0-9]{0,14}", owner):
            raise RuntimeError("owner_input_invalid")
        if not 16 <= len(password) <= 256 or not re.fullmatch(r"[0-9]{6}", totp):
            raise RuntimeError("mfa_input_invalid")
        stage = "mfa_login"
        login = call("POST", "/api/admin/v1/login", {
            "username": "beta-owner", "password": password, "code": totp,
        })
        password = ""
        totp = ""
        if not isinstance(login, dict) or login.get("role") != "admin":
            raise RuntimeError("mfa_login_not_confirmed")
        token = str(login.get("access_token", ""))
        if not token:
            raise RuntimeError("mfa_login_not_confirmed")
        stage = "owner_lookup"
        users = call("GET", f"/api/admin/v1/users?q={owner}", token=token)
        owner = ""
        while isinstance(users, list) and len(users) == 1 and isinstance(users[0], list):
            users = users[0]
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
            raise RuntimeError("owner_lookup_not_unique")
        user = users[0]
        user_id = str(user.get("id", ""))
        if user.get("status") != "active" or not re.fullmatch(r"[0-9a-fA-F-]{36}", user_id):
            raise RuntimeError("owner_not_active")
        stage = "window_status"
        status = call("GET", f"/api/admin/v1/users/{user_id}/window-status", token=token)
        if not isinstance(status, dict):
            raise RuntimeError("window_status_shape_invalid")
        state = status.get("latest_connection_state")
        non_closed = status.get("non_closed_connection_count")
        reserved = status.get("reserved_bytes")
        if state not in (None, "pending", "active", "revoke_pending", "closed"):
            raise RuntimeError("window_status_state_invalid")
        if not isinstance(non_closed, int) or non_closed < 0 or not isinstance(reserved, int) or reserved < 0:
            raise RuntimeError("window_status_amount_invalid")
        stage = "quota_read"
        account = call("GET", f"/api/admin/v1/users/{user_id}", token=token)
        if not isinstance(account, dict) or not isinstance(account.get("quota"), dict):
            raise RuntimeError("quota_shape_invalid")
        quota = account["quota"]
        amounts = {key: quota.get(key) for key in
                   ("granted_bytes", "used_bytes", "reserved_bytes", "available_bytes")}
        if any(not isinstance(value, int) or value < 0 for value in amounts.values()):
            raise RuntimeError("quota_amount_invalid")
        if (amounts["reserved_bytes"] != reserved
                or amounts["available_bytes"] != amounts["granted_bytes"]
                - amounts["used_bytes"] - amounts["reserved_bytes"]):
            raise RuntimeError("quota_window_status_mismatch")
        gate = "closed" if state == "closed" and non_closed == 0 and reserved == 0 else "open"
        quota_ready = amounts["available_bytes"] >= MIN_WINDOW_BYTES
        preflight_result = "pass" if gate == "closed" and quota_ready else "blocked"
        result.update({
            "latest_state": state if state is not None else "none",
            "non_closed_connections": non_closed,
            "reserved_bytes": reserved,
            "revoke_gate": gate,
            "quota": {"status": "read_only", **amounts},
            "minimum_window_bytes": MIN_WINDOW_BYTES,
            "available_for_one_window": quota_ready,
            "result": preflight_result,
            "identifiers_credentials_and_raw_data_exported": False,
        })
        path = save(result)
        print(f"beta_owner_preflight={preflight_result}; latest_state={result['latest_state']}; "
              f"non_closed_connections={non_closed}; reserved_bytes={reserved}; revoke_gate={gate}; "
              f"available_bytes={amounts['available_bytes']}; one_window_available={str(quota_ready).lower()}")
        print(f"sanitized_receipt={path}")
        return 0 if preflight_result == "pass" else 2
    except RuntimeError as exc:
        result.update({"result": "not_confirmed", "failure_stage": stage,
                       "failure_code": str(exc),
                       "identifiers_credentials_and_raw_data_exported": False})
        path = save(result)
        print(f"beta_owner_preflight=not_confirmed; stage={stage}; "
              f"failure={exc}; sanitized_receipt={path}")
        return 1
    finally:
        owner = ""
        password = ""
        totp = ""
        if token:
            try:
                call("POST", "/api/admin/v1/logout", {}, token=token)
            except RuntimeError:
                pass


if __name__ == "__main__":
    sys.exit(main())
