#!/usr/bin/env bash
# Source reachability supplements binary scanning, which can over-report c-shared symbols.
set -euo pipefail
work=${VPN_ANDROID_BUILD_DIR:-/tmp/vpn-android-build}
export PATH="$work/go/bin:/usr/bin:/bin"
export GOTOOLCHAIN=local GOOS=android GOARCH=arm64 CGO_ENABLED=1
ndk="$work/ndk/android-ndk-r26b/toolchains/llvm/prebuilt/linux-x86_64"
export CC="$ndk/bin/clang"
export CGO_CFLAGS="--target=aarch64-linux-android24 --sysroot=$ndk/sysroot"
export CGO_LDFLAGS="$CGO_CFLAGS"
cd "$work/source"
# cgo JNI exports are entered from Java, not Go main. Make each export a source-analysis
# root under an audit-only tag. This file is NEVER included in the Android binary build.
cat > audit_roots.go <<'GO'
//go:build vpn_audit

package main

// STATIC ANALYSIS ONLY: do not execute an artifact built with vpn_audit.
func init() {
    h := awgTurnOn("", -1, "")
    awgGetConfig(h)
    awgGetSocketV4(h)
    awgGetSocketV6(h)
    awgTurnOff(h)
    awgVersion()
}
GO
go list -deps -tags linux > "$work/android-linked-packages.txt"
go list -m -json all > "$work/android-modules.json"
/tmp/vpn-govuln-1.9.0/govulncheck -json -tags linux,vpn_audit ./... > "$work/govuln-source-arm64.json"
