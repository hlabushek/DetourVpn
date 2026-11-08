"""Locally authenticated, audited I-030 beta.22 unknown-window resolution (ADR-089).

Charges the exact 16 MiB funded budget of ONE closed, unresolved `unknown` window as
operator-adjudicated compensation (ADR-082 mechanism). Nothing is sent to the server until
the owner types credentials in this local window and every read-only precondition holds.
"""

from __future__ import annotations

import getpass
import hashlib
import http.client
import json
import re
import ssl
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"
EVIDENCE_FILE = EVIDENCE / "i-030-beta22-unknown-diagnostic-20261108.json"
ORIGIN = "https://193.68.88.239"
SSH_HOST = "193.68.88.239"
EXPECTED_HOST = "hiplet-137728"
SSH_IDENTITY = Path.home() / ".ssh" / "detourvpn-beta-20261026"
EVIDENCE_DIGEST = "461d5458c0527aa2f04e19d9e7e8bcd50f9ce30f892d9418cb37b50221cc0ea1"
AMOUNT = 16_777_216
CONNECT_ATTEMPTS = 5


class SafeFailure(Exception):
    def __init__(self, code: str, http_status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _post_connect_retry(path: str, headers: dict[str, str],
                        payload: bytes | None) -> object:
    """POST that retries only while connecting; once sent, exactly one attempt."""
    parts = urlsplit(ORIGIN)
    for attempt in range(CONNECT_ATTEMPTS):
        conn = http.client.HTTPSConnection(
            parts.hostname or "", parts.port or 443, timeout=25,
            context=ssl.create_default_context(),
        )
        try:
            try:
                conn.connect()
            except (OSError, ssl.SSLError):
                if attempt + 1 == CONNECT_ATTEMPTS:
                    raise SafeFailure("beta_api_unavailable") from None
                time.sleep(4 * (attempt + 1))
                continue
            try:
                conn.request("POST", path, body=payload, headers=headers)
                response = conn.getresponse()
                raw = response.read()
            except (OSError, http.client.HTTPException):
                raise SafeFailure("beta_api_unavailable_after_send") from None
            if response.status >= 400:
                raise SafeFailure("beta_api_http_error", response.status)
            try:
                return json.loads(raw)
            except ValueError:
                raise SafeFailure("beta_api_unavailable_after_send") from None
        finally:
            conn.close()
    raise SafeFailure("beta_api_unavailable")


def request_json(method: str, path: str, body: dict[str, object] | None = None,
                 token: str | None = None) -> dict[str, object] | list[object]:
    headers = {"Accept": "application/json"}
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, separators=(",", ":")).encode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if method == "POST":
        return _post_connect_retry(path, headers, payload)  # type: ignore[return-value]
    attempts = 4
    for attempt in range(attempts):
        try:
            with urlopen(
                Request(ORIGIN + path, data=payload, headers=headers, method=method),
                timeout=25,
            ) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            raise SafeFailure("beta_api_http_error", exc.code) from None
        except (URLError, TimeoutError, OSError, ValueError):
            if attempt + 1 == attempts:
                raise SafeFailure("beta_api_unavailable") from None
            time.sleep(4 * (attempt + 1))
    raise SafeFailure("beta_api_unavailable")


def readonly_sql(statement: str) -> list[str]:
    if not SSH_IDENTITY.is_file():
        raise SafeFailure("approved_beta_identity_unavailable")
    remote = (
        "set -eu; "
        f"test \"$(hostname -s)\" = '{EXPECTED_HOST}'; "
        "docker exec -i detourvpn-beta-postgres-1 sh -c "
        "'PGPASSWORD=\"$(cat /run/secrets/pg_runtime)\" exec psql "
        "-X -qAt -v ON_ERROR_STOP=1 -h 127.0.0.1 -U vpn_api "
        "-d detourvpn_beta'"
    )
    command = [
        "ssh.exe", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
        "-i", str(SSH_IDENTITY), f"root@{SSH_HOST}", remote,
    ]
    # Statements are BEGIN READ ONLY ... ROLLBACK, so repeating after a dropped SSH
    # connection is safe (the beta host resets part of new connections).
    attempts = 5
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                command, input=statement, capture_output=True, text=True,
                encoding="utf-8", timeout=30, check=False,
            )
            if result.returncode == 0:
                return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.TimeoutExpired):
            pass
        if attempt + 1 < attempts:
            time.sleep(3 * (attempt + 1))
    raise SafeFailure("beta_readonly_query_unavailable")


def save_receipt(name: str, data: dict[str, object]) -> Path:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = EVIDENCE / f"i-030-{name}-{stamp}.json"
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target


def verify_evidence_file() -> None:
    try:
        digest = hashlib.sha256(EVIDENCE_FILE.read_bytes()).hexdigest()
    except OSError:
        raise SafeFailure("evidence_file_unavailable") from None
    if digest != EVIDENCE_DIGEST:
        raise SafeFailure("evidence_digest_mismatch")


def main() -> int:
    stage = "evidence_check"
    token: str | None = None
    action_response_received = False
    try:
        verify_evidence_file()
        print("I-030 isolated beta.22: one audited 16 MiB compensation (ADR-089).")
        print("Enter account ID, password and TOTP only in this local window.")
        owner = getpass.getpass("Owner Telegram numeric ID (hidden): ")
        password = getpass.getpass("beta-owner admin password (hidden): ")
        totp = getpass.getpass("Current TOTP code (hidden): ")
        if not re.fullmatch(r"[1-9][0-9]{0,14}", owner):
            raise SafeFailure("owner_input_invalid")
        if not 16 <= len(password) <= 256:
            raise SafeFailure("password_input_invalid")
        if not re.fullmatch(r"[0-9]{6}", totp):
            raise SafeFailure("totp_input_invalid")

        stage = "mfa_login"
        login = request_json("POST", "/api/admin/v1/login", {
            "username": "beta-owner", "password": password, "code": totp,
        })
        password = ""
        totp = ""
        if not isinstance(login, dict) or login.get("role") != "admin":
            raise SafeFailure("mfa_login_not_confirmed")
        token = str(login.get("access_token", ""))
        if not token:
            raise SafeFailure("mfa_login_not_confirmed")

        stage = "owner_lookup"
        users = request_json("GET", f"/api/admin/v1/users?q={owner}", token=token)
        owner = ""
        while isinstance(users, list) and len(users) == 1 and isinstance(users[0], list):
            users = users[0]
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
            raise SafeFailure("owner_lookup_not_unique")
        account = users[0]
        user_id = str(account.get("id", ""))
        if account.get("status") != "active" or not re.fullmatch(
            r"[0-9a-fA-F-]{36}", user_id
        ):
            raise SafeFailure("owner_lookup_not_active")

        stage = "pre_action_status"
        before = request_json(
            "GET", f"/api/admin/v1/users/{user_id}/window-status", token=token
        )
        if not isinstance(before, dict) or (
            before.get("latest_connection_state") != "closed"
            or before.get("non_closed_connection_count") != 0
            or before.get("reserved_bytes") != AMOUNT
        ):
            raise SafeFailure("beta_preconditions_not_met")

        stage = "unique_incident_readonly_match"
        # Exactly one unresolved unknown window of this user, and it must be the one the
        # server will accept: closed connection whose current window/generation is this hold.
        candidate_sql = f"""BEGIN READ ONLY;
SELECT count(*)::text || '|' || coalesce(min(h.id::text),'') || '|' ||
       coalesce(min(h.amount_bytes)::text,'')
FROM peer_window_holds h
JOIN peer_window_receipts r ON r.window_id=h.id AND r.user_id=h.user_id
JOIN peer_connections c ON c.id=h.connection_id AND c.user_id=h.user_id
LEFT JOIN peer_window_resolutions p ON p.window_id=h.id AND p.user_id=h.user_id
WHERE h.user_id='{user_id}'::uuid AND h.generation=2 AND h.slot_id=1
  AND h.amount_bytes={AMOUNT} AND r.observation='unknown'
  AND r.observed_bytes IS NULL AND r.operation_id IS NULL
  AND r.receipt->>'gate_closed'='true' AND c.state='closed'
  AND c.window_id=h.id AND c.generation=h.generation AND p.window_id IS NULL;
SELECT 'all_unresolved_unknown=' || count(*)::text
FROM peer_window_holds h
JOIN peer_window_receipts r ON r.window_id=h.id AND r.user_id=h.user_id
LEFT JOIN peer_window_resolutions p ON p.window_id=h.id AND p.user_id=h.user_id
WHERE h.user_id='{user_id}'::uuid AND r.observation='unknown' AND p.window_id IS NULL;
ROLLBACK;
"""
        candidates = readonly_sql(candidate_sql)
        if len(candidates) != 2 or candidates[1] != "all_unresolved_unknown=1":
            raise SafeFailure("incident_match_not_unique")
        match = re.fullmatch(rf"1\|([0-9a-fA-F-]{{36}})\|{AMOUNT}", candidates[0])
        if not match:
            raise SafeFailure("incident_match_not_unique")
        window_id = match.group(1)

        stage = "mfa_audited_compensation"
        idempotency_key = str(uuid.uuid4())
        action = {
            "operation": "resolve_unknown_window",
            "idempotency_key": idempotency_key,
            "reason": (
                "I-030 beta.22 unknown closed window; full funded 16 MiB budget charged "
                "as operator-adjudicated compensation; root cause unavailable."
            ),
            "user_id": user_id,
            "resource_id": window_id,
            "evidence_digest": EVIDENCE_DIGEST,
        }
        request_json("POST", "/api/admin/v1/actions", action, token=token)
        action_response_received = True

        stage = "post_action_database_audit"
        audit_sql = f"""BEGIN READ ONLY;
SELECT count(*)::text || '|' ||
 count(*) FILTER(WHERE p.kind='charge_full_budget' AND p.amount_bytes={AMOUNT})::text || '|' ||
 count(*) FILTER(WHERE q.status='applied' AND q.source_type='peer_window_resolution'
   AND q.kind='debit' AND q.amount_bytes={AMOUNT})::text || '|' ||
 count(*) FILTER(WHERE l.source_type='peer_window_resolution' AND l.kind='debit'
   AND l.delta_bytes=-{AMOUNT})::text || '|' ||
 count(*) FILTER(WHERE r.observation='unknown' AND r.observed_bytes IS NULL
   AND r.operation_id IS NULL)::text
FROM peer_window_resolutions p
JOIN peer_window_holds h ON h.id=p.window_id AND h.user_id=p.user_id
JOIN peer_window_receipts r ON r.window_id=h.id AND r.user_id=h.user_id
JOIN quota_operations q ON q.id=p.operation_id AND q.user_id=p.user_id
JOIN quota_ledger l ON l.operation_id=q.id AND l.user_id=q.user_id
WHERE p.window_id='{window_id}'::uuid AND p.idempotency_key='{idempotency_key}'::uuid
  AND p.user_id='{user_id}'::uuid AND p.amount_bytes={AMOUNT}
  AND p.evidence_digest='{EVIDENCE_DIGEST}';
ROLLBACK;
"""
        if readonly_sql(audit_sql) != ["1|1|1|1|1"]:
            raise SafeFailure("post_action_database_audit_not_confirmed")

        stage = "post_action_status"
        after = request_json(
            "GET", f"/api/admin/v1/users/{user_id}/window-status", token=token
        )
        if not isinstance(after, dict) or (
            after.get("latest_connection_state") != "closed"
            or after.get("non_closed_connection_count") != 0
            or after.get("reserved_bytes") != 0
        ):
            raise SafeFailure("post_action_gate_not_closed")

        receipt = {
            "issue": "I-030", "timestamp_utc": datetime.now(UTC).isoformat(),
            "target": "isolated_beta", "result": "success",
            "kind": "charge_full_budget", "amount_bytes": AMOUNT,
            "evidence_sha256": EVIDENCE_DIGEST,
            "measured_traffic_claimed": False,
            "original_unknown_receipt_preserved": True,
            "resolution_count": 1, "applied_resolution_debit_count": 1,
            "ledger_debit_count": 1,
            "pre_status": {"latest_state": "closed", "non_closed_connections": 0,
                           "reserved_bytes": AMOUNT},
            "post_status": {"latest_state": "closed", "non_closed_connections": 0,
                            "reserved_bytes": 0, "revoke_gate": "closed"},
            "identifiers_credentials_and_raw_data_exported": False,
        }
        path = save_receipt("unknown-resolution-beta22", receipt)
        print(f"i030_unknown_resolution=success; amount_bytes={AMOUNT}; "
              "unknown_receipt=preserved; reserved_bytes=0; revoke_gate=closed")
        print(f"sanitized_receipt={path}")
        return 0
    except SafeFailure as exc:
        failure: dict[str, object] = {
            "issue": "I-030", "timestamp_utc": datetime.now(UTC).isoformat(),
            "target": "isolated_beta", "result": "not_confirmed",
            "failure_stage": stage, "failure_code": exc.code,
            "action_response_received": action_response_received,
            "identifiers_credentials_and_raw_data_exported": False,
        }
        if exc.http_status is not None:
            failure["http_status"] = exc.http_status
        path = save_receipt("unknown-resolution-beta22-attempt", failure)
        print(f"i030_unknown_resolution=not_confirmed; stage={stage}; "
              f"code={exc.code}; sanitized_receipt={path}")
        return 1
    finally:
        if token:
            try:
                request_json("POST", "/api/admin/v1/logout", {}, token=token)
            except SafeFailure:
                pass


if __name__ == "__main__":
    sys.exit(main())
