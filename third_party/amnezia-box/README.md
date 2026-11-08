# Amnezia Box source pin

The exact upstream source archive used for the Android libbox integration is
stored here so the corresponding source is available with this repository.
`source-lock.json` pins the release, peeled commit, and archive checksum. The
archive includes the upstream license and additional name/association term.

The generated AAR is a build artifact and is not committed here. Build it with
the repository script `scripts/build_amnezia_box_android.ps1`; that script uses
Go 1.25.5, SagerNet gomobile 0.1.8, OpenJDK 17, and Android NDK. Keep any local
patches alongside the source and include them with the corresponding source in
every beta distribution. The build applies both `peer-stats.patch` and
`i030-network-monitor-race.patch`; the latter synchronizes Android network
callback state reads/writes and serializes concurrent interface updates.

See the repository-root `THIRD_PARTY_NOTICES.md` for the current source and
distribution notice.

DetourVpn is not affiliated with or endorsed by Amnezia. Do not use Amnezia
branding or imply that relationship in the app or beta materials.
