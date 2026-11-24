# ADR-175 — Host identifiers leave the repository; the node allowlist is merged, not replaced

**Status:** accepted. Code and tests committed. **Not run against any host.**

## Context
- **M9.** `scripts/restore_primary_api.sh` and `scripts/activate_beta_caddy_allowlist.ps1`
  committed real public IPv4 addresses and the managed database host name. The repository rule
  forbids such identifiers in committed files. (About 47 other scripts and runbooks carry the
  same identifiers; see "Remaining" below.)
- **M5.** `activate_beta_caddy_allowlist.ps1` rendered the Caddy template with one node IP and
  uploaded it over the live file. Every node IP that `connect_beta_node.py` had appended was
  dropped, so those nodes received 404 on `/poll`.

## Decision
1. **Operator values come from a file outside the repository.**
   - `restore_primary_api.sh` reads `RESTORE_ENV` (default `/root/detourvpn-restore.env`), which
     must be mode 600 and owned by root, with `PUBLIC_IP=` and `DATABASE_HOST=`. It refuses to
     start otherwise. The behaviour is otherwise unchanged; the pinned SHA-256 checks stay.
   - `activate_beta_caddy_allowlist.ps1` reads `%USERPROFILE%\.detourvpn\beta-operator.env`
     with `BETA_HOST=`, `BETA_NODE_IP=`, `BETA_SSH_KEY=` (a key file name under `.ssh`).
2. **Merge, not overwrite (M5).** The script reads the live `/etc/caddy/Caddyfile` over SSH and
   passes it to the new `scripts/caddy_allowlist.py merge <NODE_IP>`. That function keeps every
   IPv4 address already in the single `remote_ip` line, appends the new one once, and changes
   nothing else. It fails closed with fixed codes on no matcher, a duplicate matcher, an empty
   matcher, an invalid entry (including IPv6) or an invalid new address. The merged file is then
   uploaded, validated and reloaded as before, with the same backup and rollback.
3. The template is still used by the repository's Caddy tests. The activation script no longer
   renders it.

## Owner decision needed
- Create `%USERPROFILE%\.detourvpn\beta-operator.env` and `/root/detourvpn-restore.env` on the
  operator workstation and the primary host with the values above. Nothing reads them until
  they exist; both scripts stop with a message if they are missing.
- Whether to rotate the SSH key whose file name was committed (`detourvpn-beta-20261026`). The
  name alone grants nothing, but the owner decides whether it is worth renaming.
- The pinned `SHA_WINDOW_SERVICE` in `restore_primary_api.sh` no longer matches
  `window_service.py` after the ADR-168 and ADR-173 commits, so the restore script refuses to
  run until the pin is updated to the reviewed version. The owner decides which version is
  reviewed; this change does not update the pin.

## Remaining (not done in this item)
- M9 is only partly fixed. These committed files still carry real host addresses or the
  managed database host name, and need the same treatment: `scripts/connect_beta_node.py`,
  `scripts/deploy_beta_api_latest.py`, `scripts/activate_beta_node.py`, many `deploy_*` and
  `check_*` scripts, `scripts/deploy_ha_server_README.md`, and runbooks under `docs/`. The
  list comes from a search for the two addresses and the host name; run it again before
  working through them.

## Tests
- `services/api/tests/test_caddy_allowlist.py`: merge keeps existing nodes and adds the new one;
  idempotent; the other lines are unchanged; each malformed case has a fixed code; the CLI
  reads stdin and refuses a bad address; the PowerShell script merges the live file and no
  longer renders the template over it. The PowerShell file passes the PowerShell parser.
- `services/api/tests/test_operator_scripts_config.py`: no host identifier in the two scripts
  (loopback is allowed); both scripts read their operator file; `restore_primary_api.sh` exits 1
  with "missing" when its operator file is absent (run with Git bash, the same file the
  developer uses).

## Not verified
- The merge runs against real Caddy configurations only in these unit tests. The PowerShell
  path was checked for syntax, not run. No host or SSH connection was used.
