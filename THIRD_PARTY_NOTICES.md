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
