# Connection reliability — server data to collect (owner steps)

These steps are **read-only**. You run them yourself on the beta API server. Claude does not run
them and has no server access in this task.

**Do not paste into chat:** IP addresses, hostnames, passwords, tokens, subscription links, raw
logs, or full IDs. Shorten every ID to its first 8 characters, for example `3f2a9c1e…`. Times and
states are enough.

## What we want to learn
For the affected phone (one device), around the time the VPN dropped or a connect failed:
1. Each connection: when it was created, its state now (`pending`, `active`, `revoke_pending`,
   `closed`), and how many window generations it had.
2. Each window: when it was issued, when it ended (`not_after`), and when the node closed it.
3. Whether a new connection was revoked within seconds of being created. That would point to two
   app engines fighting (root cause §5).
4. Whether the gap between windows is larger than the window itself. That would point to a missed
   renewal during sleep (root cause §3).

## Step 1 — open a read-only database session
On the API server, in the install folder (normally `/opt/detourvpn`):

```bash
cd /opt/detourvpn
```

```bash
sudo docker compose -f infra/closed/compose.yaml exec postgres psql -U vpn_bootstrap -d detourvpn
```

`psql` asks for the password itself; type it only there, never in chat. If your database user or
names are different, use yours. As the first command inside `psql`, make the session read-only:

```sql
BEGIN READ ONLY;
```

## Step 2 — find the device
If you know the device ID, skip this step. Otherwise list the newest Android devices of your
account (replace `<USER_ID>`):

```sql
SELECT left(id::text, 8) AS device, platform, created_at, revoked_at
FROM devices WHERE user_id = '<USER_ID>' ORDER BY created_at DESC LIMIT 5;
```

Then use the full ID of the right row as `<DEVICE_ID>` below (do not share it in full).

## Step 3 — connections of that device (last 2 days)
```sql
SELECT left(id::text, 8) AS conn, left(node_id::text, 8) AS node, state, generation,
       created_at, (renew_token_digest IS NOT NULL) AS native_token
FROM peer_connections
WHERE device_id = '<DEVICE_ID>' AND created_at > now() - interval '2 days'
ORDER BY created_at;
```

## Step 4 — window timeline of that device
```sql
SELECT left(h.connection_id::text, 8) AS conn, h.generation, h.created_at AS issued,
       h.not_after, r.created_at AS closed_at, r.observation,
       round(extract(epoch FROM (h.not_after - h.created_at))) AS window_s,
       round(extract(epoch FROM (r.created_at - h.not_after))) AS closed_after_end_s
FROM peer_window_holds h
JOIN peer_connections c ON c.id = h.connection_id
LEFT JOIN peer_window_receipts r ON r.window_id = h.id
WHERE c.device_id = '<DEVICE_ID>' AND h.created_at > now() - interval '2 days'
ORDER BY h.created_at;
```

How to read it:
- `window_s` about 900 means the 15-minute windows are in use. About 90 means an old build.
- A connection whose last generation's `not_after` passed with no next generation means a missed
  renewal. Note the time and compare it with when the phone was asleep.
- A connection created and closed within a few seconds, with `generation = 1`, right before
  another connection of the same device, means it was revoked while connecting.

## Step 5 — node restarts in the same period
```sql
SELECT left(node_id::text, 8) AS node, left(runtime_id::text, 8) AS runtime, created_at
FROM peer_runtime_epochs WHERE created_at > now() - interval '2 days' ORDER BY created_at;
```

A new runtime right before a failed connect explains `window_not_active` at `lease_validation`
without any phone fault.

Close the session:

```sql
ROLLBACK;
```

```sql
\q
```

## Step 6 — API request outcomes (no IPs, no IDs)
This prints only the time, the request type and the HTTP status for window requests. It removes
addresses and IDs. Replace the time if needed.

```bash
sudo journalctl -u detourvpn-api.service --since '2 days ago' --no-pager -o short-iso | grep -oE '^[0-9T:+-]+|"(GET|POST|DELETE) /api/v1/window-tunnels[^"]*" [0-9]{3}' | paste - - | sed -E 's#/[0-9a-f-]{36}#/<id>#g' | grep window-tunnels | tail -n 300
```

If the output is empty, the API may not log requests; say so, that is also useful.

## What to send back
- The rows from steps 3, 4 and 5, with shortened IDs.
- From step 6, only the lines in the 10 minutes around each drop or failed connect.
- For each drop: the phone time, whether the screen was off, how long, Wi-Fi or mobile data.
