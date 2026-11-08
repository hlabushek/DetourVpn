#!/usr/bin/env bash
# I-030 read-only collector for the isolated beta v3 unknown-window incident.
# Run as root on the beta VPN node (--node) or beta API host (--api).
# Emits only fixed labels, timestamps, counters, hashes, and allow-listed states.
set -Eeuo pipefail
export LC_ALL=C

mode="${1:-}"
case "$mode" in
  --node)
    if [[ "$(id -u)" != 0 ]]; then
      printf 'collector=blocked; reason=root_required\n'
      exit 2
    fi
    printf 'collector=I-030; target=beta_node; mode=read_only\n'
    systemctl show detourvpn-beta-node-agent.service \
      --property=LoadState --property=ActiveState --property=SubState \
      --property=NRestarts --property=ExecMainStartTimestamp \
      --property=ActiveEnterTimestamp --property=ExecMainExitTimestamp \
      --property=Result | awk -F= '
        $1 == "LoadState" || $1 == "ActiveState" || $1 == "SubState" ||
        $1 == "NRestarts" || $1 == "ExecMainStartTimestamp" ||
        $1 == "ActiveEnterTimestamp" || $1 == "ExecMainExitTimestamp" ||
        $1 == "Result" { print "agent_" tolower($1) "=" $2 }
      '
    for spec in \
      'window_daemon.py:/opt/detourvpn-node/window_daemon.py' \
      'window_gate.py:/opt/detourvpn-node/window_gate.py' \
      'window_runtime.py:/opt/detourvpn-node/vpn_api/tunnels/window_runtime.py' \
      'window_journal.py:/opt/detourvpn-node/vpn_api/tunnels/window_journal.py'; do
      label=${spec%%:*}
      path=${spec#*:}
      if [[ -f "$path" && ! -L "$path" ]]; then
        hash=$(sha256sum -- "$path" | cut -d ' ' -f1)
        case "$label" in
          window_daemon.py) expected=dda31d7be8fff44530479db9517c5c805a6f5eed3b7976882f3f914c716594f8 ;;
          window_gate.py) expected=7f055892a77d47e03c0ea4f1b820ce1ed1399c06a3156e8f9bc70f3594030dd7 ;;
          window_runtime.py) expected=a039904113e6e28e55347c5f22cd3f097214ae3fc371661926f2a8c88d4edc3a ;;
          window_journal.py) expected=21aa35fb016dea53860a94a66f340215bc683051be18d93fadd4ef4d21fe2df6 ;;
          *) expected=unavailable ;;
        esac
        if [[ "$hash" == "$expected" ]]; then match=true; else match=false; fi
        printf 'deployed_source=%s; sha256=%s; matches_checked_out=%s\n' "$label" "$hash" "$match"
      else
        printf 'deployed_source=%s; state=missing_or_untrusted\n' "$label"
      fi
    done
    python3 - <<'PY'
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime

now = datetime.now(UTC).isoformat(timespec="seconds")
print(f"node_snapshot_utc={now}")
path = "/var/lib/detourvpn-node/state.sqlite"
try:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    rows = db.execute("SELECT slot, state, grant_json FROM slots ORDER BY slot").fetchall()
    receipts = db.execute("SELECT payload, slot, acknowledged FROM receipts").fetchall()
    pending = sum(not bool(row[2]) for row in receipts)
    print(
        "journal_summary="
        f"slot_count:{len(rows)};receipt_count:{len(receipts)};"
        f"unacknowledged_count:{pending};acknowledged_count:{len(receipts)-pending}"
    )
    for slot, state, grant_raw in rows:
        grant = json.loads(grant_raw)
        print(
            f"journal_slot={slot};state={state};"
            f"generation={grant.get('generation', 'unknown')}"
        )
    matched = []
    for payload_raw, slot, acknowledged in receipts:
        payload = json.loads(payload_raw)
        if (slot == 1 and payload.get("generation") == 2
                and payload.get("observation") == "unknown"
                and payload.get("observed_bytes") is None):
            matched.append((payload, bool(acknowledged)))
    if len(matched) == 1:
        payload, acknowledged = matched[0]
        print(
            "target_receipt="
            f"count:1;observation:{payload.get('observation')};"
            f"gate_closed:{str(payload.get('gate_closed')).lower()};"
            f"acknowledged:{str(acknowledged).lower()}"
        )
    else:
        print(f"target_receipt_count={len(matched)}")
    db.close()
except Exception as exc:
    print(f"journal_snapshot=unavailable;category={type(exc).__name__}")

try:
    with open("/run/detourvpn-node/slots/1.json", encoding="utf-8") as source:
        ready = json.load(source)
    result = subprocess.run(
        ["/usr/bin/nsenter", "-t", str(ready["native_pid"]), "-n", "--",
         "/usr/sbin/nft", "-j", "list", "table", "inet", "detour_window_v3"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if result.returncode:
        raise RuntimeError("kernel_query_failed")
    entries = json.loads(result.stdout).get("nftables", [])
    live = [item["set"] for item in entries
            if item.get("set", {}).get("name") == "live"]
    counters = [item["counter"] for item in entries
                if item.get("counter", {}).get("name") == "passed"]
    live_now = bool(live) and bool(live[0].get("elem"))
    count = counters[0].get("bytes") if len(counters) == 1 else None
    if type(count) is not int or count < 0:
        count = "unavailable"
    print(f"kernel_snapshot=read_only;live={str(live_now).lower()};counter_bytes={count}")
except Exception as exc:
    print(f"kernel_snapshot=unavailable;category={type(exc).__name__}")

try:
    raw = subprocess.run(
        ["/usr/bin/journalctl", "-u", "detourvpn-beta-node-agent.service",
         "--since", "2026-11-04 11:30:00 UTC", "--no-pager", "-o", "json"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    allowed = {"startup_close", "control_poll", "shutdown_close", "preflight",
               "node_agent_failed", "control_unavailable", "control_recovered",
               "startup_gate_close_failed", "kernel_gate_operation_failed",
               "final_counter_requires_closed_gate", "kernel_meter_invalid",
               "journal_receipt_conflict", "journal_ack_conflict"}
    events = []
    for line in raw.stdout.splitlines():
        try:
            row = json.loads(line)
            stamp = datetime.fromtimestamp(
                int(row["__REALTIME_TIMESTAMP"]) / 1_000_000, UTC
            ).isoformat(timespec="seconds")
            message = row.get("MESSAGE", "")
            if isinstance(message, str) and message.startswith("{"):
                event = json.loads(message)
                categories = [event.get(key) for key in ("event", "error", "stage")]
                safe = [item for item in categories if item in allowed]
                if safe:
                    events.append((stamp, "/".join(safe)))
            elif isinstance(message, str):
                if "Starting DetourVpn isolated beta v3 window agent" in message:
                    events.append((stamp, "systemd_starting"))
                elif "Started DetourVpn isolated beta v3 window agent" in message:
                    events.append((stamp, "systemd_started"))
                elif "Stopped DetourVpn isolated beta v3 window agent" in message:
                    events.append((stamp, "systemd_stopped"))
                elif "Scheduled restart job" in message:
                    events.append((stamp, "systemd_restart_scheduled"))
                elif "Main process exited" in message:
                    events.append((stamp, "systemd_main_process_exited"))
                elif "Failed to start DetourVpn isolated beta v3 window agent" in message:
                    events.append((stamp, "systemd_start_failed"))
        except Exception:
            continue
    print(f"journal_fixed_event_count={len(events)}")
    for stamp, category in events[-100:]:
        print(f"journal_event_utc={stamp};category={category}")
except Exception as exc:
    print(f"journal_snapshot=unavailable;category={type(exc).__name__}")
PY
    ;;
  --api)
    if [[ "$(id -u)" != 0 ]]; then
      printf 'collector=blocked; reason=root_required\n'
      exit 2
    fi
    cd /opt/detourvpn-beta
    printf 'collector=I-030; target=beta_api; mode=read_only\n'
    docker compose -f infra/beta/compose.yaml exec -T postgres sh -c \
      'export PGPASSWORD="$(cat /run/secrets/pg_migrator)"; exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --username=vpn_migrator --dbname=detourvpn_beta --tuples-only --no-align' <<'SQL'
BEGIN READ ONLY;
SELECT 'unknown_count='||count(*)
  ||'; receipt_created_min='||coalesce(to_char(min(r.created_at AT TIME ZONE 'UTC'),'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'none')
  ||'; receipt_created_max='||coalesce(to_char(max(r.created_at AT TIME ZONE 'UTC'),'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'none')
  ||'; hold_created_min='||coalesce(to_char(min(h.created_at AT TIME ZONE 'UTC'),'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'none')
  ||'; hold_created_max='||coalesce(to_char(max(h.created_at AT TIME ZONE 'UTC'),'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'none')
  ||'; generation_min='||coalesce(min(h.generation)::text,'none')
  ||'; generation_max='||coalesce(max(h.generation)::text,'none')
  ||'; slot_min='||coalesce(min(h.slot_id)::text,'none')
  ||'; slot_max='||coalesce(max(h.slot_id)::text,'none')
  ||'; gate_closed_all='||coalesce(bool_and((r.receipt->>'gate_closed')::boolean)::text,'none')
  ||'; runtime_bound_all='||coalesce(bool_and((r.receipt->>'runtime_id')=h.runtime_id::text)::text,'none')
FROM peer_window_receipts r
JOIN peer_window_holds h ON h.id=r.window_id
WHERE r.observation='unknown' AND r.observed_bytes IS NULL;
WITH target AS (
  SELECT h.id, h.user_id, h.connection_id, h.generation, h.created_at,
         h.not_after, r.created_at AS receipt_created_at
  FROM peer_window_holds h
  JOIN peer_window_receipts r ON r.window_id=h.id AND r.user_id=h.user_id
  WHERE r.observation='unknown' AND r.observed_bytes IS NULL
)
SELECT 'connection_created='||to_char(c.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
  ||'; connection_state='||c.state
  ||'; connection_generation='||c.generation
  ||'; generation1_hold_created='||coalesce(to_char(p.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'none')
  ||'; generation1_not_after='||coalesce(to_char(p.not_after AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'none')
  ||'; generation1_receipt='||coalesce(pr.observation,'missing')
  ||'; generation1_receipt_created='||coalesce(to_char(pr.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'none')
  ||'; gen1_close_to_deadline_ms='||coalesce(round(extract(epoch FROM (p.not_after-pr.created_at))*1000)::bigint::text,'none')
  ||'; gen2_hold_to_receipt_ms='||round(extract(epoch FROM (t.receipt_created_at-t.created_at))*1000)::bigint
  ||'; gen2_receipt_after_gen1_ms='||coalesce(round(extract(epoch FROM (t.receipt_created_at-pr.created_at))*1000)::bigint::text,'none')
  ||'; connection_hold_count='||(SELECT count(*) FROM peer_window_holds hx WHERE hx.connection_id=t.connection_id AND hx.user_id=t.user_id)
FROM target t
JOIN peer_connections c ON c.id=t.connection_id AND c.user_id=t.user_id
LEFT JOIN peer_window_holds p ON p.connection_id=t.connection_id AND p.user_id=t.user_id AND p.generation=t.generation-1
LEFT JOIN peer_window_receipts pr ON pr.window_id=p.id AND pr.user_id=p.user_id;
WITH target AS (
  SELECT h.connection_id, h.user_id, h.generation
  FROM peer_window_holds h
  JOIN peer_window_receipts r ON r.window_id=h.id AND r.user_id=h.user_id
  WHERE r.observation='unknown' AND r.observed_bytes IS NULL
)
SELECT 'generation1_counter_at_or_above_half_budget='||(r.observed_bytes >= h.amount_bytes/2)::text
  ||'; generation1_counter_equal_budget='||(r.observed_bytes = h.amount_bytes)::text
FROM target t
JOIN peer_window_holds h ON h.connection_id=t.connection_id AND h.user_id=t.user_id AND h.generation=t.generation-1
JOIN peer_window_receipts r ON r.window_id=h.id AND r.user_id=h.user_id;
ROLLBACK;
SQL
    ;;
  *)
    printf 'usage: collect_i030_unknown_readonly.sh --node|--api\n' >&2
    exit 2
    ;;
esac
