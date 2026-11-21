#!/usr/bin/env bash
# One-time migration: dump the current API database (old host) and restore it into Neon.
#
# Run it on the OLD host, after stopping the API so the dump is consistent:
#   sudo systemctl stop <old-api-service>        # stop writes first (names differ per host)
#   SOURCE_DATABASE_URL='<LOCAL_URL>' TARGET_DATABASE_URL='<NEON_DIRECT_URL>' \
#     bash scripts/migrate_db_to_neon.sh
#
# The dump file contains personal and account data. It is written with mode 0600 to a
# directory you choose (DUMP_DIR) and must be deleted after a verified restore.
# Nothing here is committed or printed. URLs are read from the environment only.
#
# Requirements: pg_dump and pg_restore whose major version is >= the source server and
# matches the Neon server (check: psql "$TARGET_DATABASE_URL" -c 'show server_version').
#
# Use the DIRECT (non-pooled) Neon endpoint, not the "-pooler" host (ADR-166).

set -euo pipefail

die() { printf '[migrate] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[migrate] %s\n' "$*"; }

: "${SOURCE_DATABASE_URL:?set SOURCE_DATABASE_URL (old host, local URL)}"
: "${TARGET_DATABASE_URL:?set TARGET_DATABASE_URL (Neon direct URL, sslmode=verify-full)}"
DUMP_DIR="${DUMP_DIR:-$(mktemp -d)}"
DUMP_FILE="${DUMP_DIR}/vpn-api-$(date -u +%Y%m%dT%H%M%SZ).dump"

command -v pg_dump >/dev/null || die "pg_dump not found"
command -v pg_restore >/dev/null || die "pg_restore not found"
[[ "${TARGET_DATABASE_URL}" != *"-pooler"* ]] || die "use the direct Neon endpoint, not the pooler"

umask 077
mkdir -p "${DUMP_DIR}"

log "dumping source (custom format, no owners or privileges)"
pg_dump --format=custom --no-owner --no-privileges --file "${DUMP_FILE}" "${SOURCE_DATABASE_URL}"
[[ -s "${DUMP_FILE}" ]] || die "dump is empty"
log "dump written (path not printed; mode 0600)"

log "restoring into Neon (single transaction, stops on first error)"
pg_restore --no-owner --no-privileges --single-transaction --exit-on-error \
  --dbname "${TARGET_DATABASE_URL}" "${DUMP_FILE}"

log "verifying row counts for the public schema on both sides"
count_tables() {
  psql --no-psqlrc -At "$1" -c \
    "select table_name || ' ' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I', table_name), false, true, '')))[1]::text from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1;"
}
count_tables "${SOURCE_DATABASE_URL}" > "${DUMP_DIR}/source-counts.txt"
count_tables "${TARGET_DATABASE_URL}" > "${DUMP_DIR}/target-counts.txt"
if diff -q "${DUMP_DIR}/source-counts.txt" "${DUMP_DIR}/target-counts.txt" >/dev/null; then
  log "row counts match for every table"
else
  die "row counts differ; see ${DUMP_DIR}/source-counts.txt and target-counts.txt"
fi

log "Alembic head check: run on the new server after deploy (see README)"
log "done. Delete the dump when you are satisfied: rm -f '${DUMP_FILE}'"
