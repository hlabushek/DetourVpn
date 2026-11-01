# ADR-070 — established tunnel status from recent inbound progress

Status: accepted for I-028 client status correctness.

## Context

The physical Egress receipt
`i-028-egress-diagnostic-20261031T111729Z-a655b866.json` recorded an
established native tunnel, validated network, acknowledged renewal, remaining
lease, and successful same-UID staging egress while the UI phase was
`reconnecting`. The native handshake age was 73 seconds. The current UI rule
labels a session connected only while the last handshake is at most 60 seconds
old, although an active WireGuard tunnel can carry traffic without repeating a
handshake during every 60-second interval.

## Decision

Keep the fresh-handshake rule for initial connection confirmation. For an
already established window, continue to show `connected` when the native tunnel
is up, the underlying validated network is present, the local lease remains
valid, and the native adapter observed an increase in inbound tunnel bytes
within the last 60 seconds. Outbound-only traffic is insufficient. An unavailable
counter provides no new confirmation; a counter reset clears prior activity.
When neither a fresh handshake nor recent inbound progress exists, show
`reconnecting`.

The byte counter is approximate local adapter telemetry only. It is not used
for quotas, settlement, authorization, lease extension, routing, or teardown.
Initial connection still requires the existing fresh-handshake barrier.

## Consequences

This avoids presenting a healthy established data path as reconnecting solely
because the handshake age crossed 60 seconds. An idle tunnel may still show
`reconnecting` after the freshness interval; that conservative state is
intentional until a fresh handshake or inbound traffic is observed. Android
providers without a reliable inbound counter continue to use the handshake
rule. Physical verification is required for the changed source-bound APK.
