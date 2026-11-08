# ADR-087 — I-030 one supervised beta.22 attempt on the owner phone over LTE

**Status:** DRAFT, needs owner acceptance before the attempt.
**Date:** 2026-11-08
**Issue:** I-030
**Follows:** ADR-084 (3 phone attempts, used), ADR-086 (2 control runs, used)

## Context

ADR-086 results: the project's pinned core plus the real API window flow completes a
handshake from a datacenter host and fails from the owner's home network. The node,
slot, profile (incl. `HeaderProtectionKey`) and window flow are therefore sound.
Still unexplained: beta.22 on the phone over LTE got one handshake request through
(148 B in, 116 B out on the node), then silence, `tun0` tx 15 / rx 0. Candidate
causes: the carrier path (drops responses or later packets), or a beta.22 client
defect (GoBackend glue, key/profile handling, MTU/routes, retry behavior).
Open check before concluding: the node counted a 116-byte response while the control
client counted 92 B received for its handshake; confirm whether that is only counter
semantics (padding) or a real difference.

## Decision (proposed)

1. **One** owner Connect attempt on the phone, LTE only (Wi-Fi off), beta.22 as-is,
   split mode "selected apps" with Chrome, following the ADR-083 preflight, bounded probe
   and postflight sequence. A new ADR is needed for any further attempt.
2. During the attempt, read-only on the node (by a command the owner approves
   in chat): peer transfer and latest-handshake of the slot every ~5 s for 90 s, the
   gate counter, and (separate "yes") a header-only UDP capture on the slot port
   for 90 s covering at least two keepalive intervals: direction, length, time, ports;
   no payload, no file.
3. On the phone, read-only via ADB: network state, `tun0` counters before/after.
4. Interpretation:
   - node sees repeated phone packets but the phone shows rx 0: responses are lost or
     rejected between node and phone (carrier path or client parsing);
   - node sees only the first request: the phone stops sending (client defect) or the
     carrier drops later packets;
   - handshake completes: LTE works; the earlier failures were network/transient,
     and egress is then checked with the bounded IP probe.
5. Budget: up to 16 MiB quota; the window is closed by Disconnect plus postflight.
   No node or client change before the result.

## Result (2026-11-08, owner present)

The attempt was accepted by the owner and run once on LTE. The node showed a handshake
and 50-109 KB of peer traffic in both directions; Chrome (the only selected app) showed
the node-side IP while Yandex Browser showed the mobile IP; Disconnect and postflight
closed the connection with reserved 0 and a debit of 197,523 bytes. Evidence:
`docs/evidence/i-030-beta22-lte-success-20261108.json`. The header-only capture was
not used. The earlier failures remain unexplained; this is one success, not proof of
repeatability. The budget of ADR-087 (one attempt) is used.

## Consequences and limits

- One attempt is evidence, not proof of a working beta; friends are not invited on
  the strength of it. Wi-Fi remains known-bad on the owner's network.
- Next step after the result: client fix, port/transport change (443/UDP or TCP
  transport under a separate ADR), or an operator-path note for testers.
