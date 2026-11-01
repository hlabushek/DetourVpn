# ADR-065 — archive a closed I-028 run after reboot

Status: accepted for I-028 staging recovery.

## Context

The I-028 cleanup receipt and archived run data live under persistent root-owned
directories, while `/run` is volatile tmpfs. A host reboot can therefore remove
`/run/detourvpn-i028` after cleanup without changing the persistent receipt. The
previous archive guard treated every missing source as ambiguous and refused a
safe restart.

## Decision

- Permit only the `run` source to be absent when archiving a closed run. The
  persistent cleanup receipt, fixed root-owned source checks, inactive-service
  checks, no-namespace/link/firewall/listener checks, and private archive checks
  remain mandatory.
- Record `run` in `missing_sources` and create an empty private `run/` directory
  in the archive. Never recreate runtime keys, restore slot state, or infer that
  a dangling symlink is absent.
- Continue refusing every other missing source, symlink, active resource, or
  incomplete cleanup receipt.

## Consequences

An explicitly closed run can be archived and restarted after reboot without a
manual runtime-directory repair. The archive records that volatile `/run` state
was absent. This does not change the 30-minute cleanup deadline or acceptance
criteria.
