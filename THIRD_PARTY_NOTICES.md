# Third-party notices

## Amnezia Box / libbox

The Android beta runtime embeds Amnezia Box `v1.260910.0` (peeled commit
`16724484cfa00d9b2c14d8280dc055564e2e12ba`) with the local peer-statistics
patch in `third_party/amnezia-box/peer-stats.patch`. The matching upstream
source archive is `third_party/amnezia-box/amnezia-box-v1.260910.0-src.zip`;
its SHA-256 and build selection are in `third_party/amnezia-box/source-lock.json`.
The source archive includes the upstream `LICENSE` and dependency notices.

Amnezia Box is licensed under GNU GPL version 3 or (at your option) any later
version, with the additional term printed in its `LICENSE`: a derivative may
not use the upstream application's name or imply association without prior
consent. DetourVpn is an independent project and does not imply such
association. Beta distributions must provide the exact corresponding source,
including the local patch, under the applicable GPL terms. This notice is not a
substitute for the complete upstream license text, which is included in the
source archive.

Build the local Android AAR with `scripts/build_amnezia_box_android.ps1`. The
build is pinned to Go 1.25.5, SagerNet gomobile 0.1.8, OpenJDK 17, and Android
ABIs arm64-v8a and x86_64. The generated AAR is a local build artifact and is
not the corresponding source.

## Windows beta client (I-032, ADR-095)

The Windows beta zip contains `detour-engine.exe`, a build of Amnezia Box `v1.260910.0` (same
upstream, peeled commit and licence terms as above) with build tags `with_awg,with_gvisor`, plus two
local patches: `peer-stats.patch` (peer counters) and `windows-peer-stats-stdout.patch` (prints only
the peer accounting tuple on the private stdout pipe when `DETOUR_PEER_STATS=1`). The zip ships the
exact corresponding source in its `SOURCE` folder: the upstream archive, `source-lock.json` and both
patches. Build it with `scripts/build_engine_windows.ps1` (Go 1.25.5, windows/amd64, outside OneDrive).
DetourVpn is not affiliated with or endorsed by Amnezia.

The zip also redistributes the prebuilt `wintun.dll` 0.14.1 (amd64) from wintun.net, signed by
WireGuard LLC. It is pinned by the zip SHA-256 and the DLL SHA-256 in
`third_party/amnezia-box/source-lock.json` and checked again by `scripts/verify_windows_zip.py`.
Wintun is distributed under the "Prebuilt Binaries License" published at wintun.net, which permits
redistribution of the unmodified binary with an application that uses it; it is not part of the
GPL-covered source. Copyright (C) WireGuard LLC.

Go module dependencies of the engine are listed in the upstream `go.mod`/`go.sum` inside the shipped
source archive; a complete SBOM and licence scan are still required before any release beyond the
closed beta (see `docs/dependencies.md`).
