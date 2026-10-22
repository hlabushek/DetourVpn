#!/usr/bin/env bash
# Local Linux/WSL build only. Inputs must already be downloaded and licence terms reviewed.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
work=${VPN_ANDROID_BUILD_DIR:-/tmp/vpn-android-build}
ndk_archive="$work/ndk.zip"
go_archive=${VPN_ANDROID_GO_ARCHIVE:-/tmp/go1.25.14.linux-amd64.tar.gz}
test "$(uname -sm)" = 'Linux x86_64'
echo 'fdf33d9f6c1b3f16e5459d53a82c7d2201edbcc4  '"$ndk_archive" | sha1sum -c -
echo 'a21ae5633a269bcd7e90cf767e48225633795e99d831742cbf3397064fee7712  '"$go_archive" | sha256sum -c -
mkdir -p "$work/ndk" "$work/go" "$work/source"
if [ ! -f "$work/ndk/android-ndk-r26b/source.properties" ]; then
    python3 - "$ndk_archive" "$work/ndk" <<'PY'
import os, pathlib, stat, sys, zipfile
root = pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(sys.argv[1]) as archive:
    for entry in archive.infolist():
        path = root / entry.filename
        if not path.resolve().is_relative_to(root):
            raise SystemExit("Invalid archive path")
        mode = entry.external_attr >> 16
        if stat.S_ISLNK(mode):
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(archive.read(entry).decode(), path)
        else:
            archive.extract(entry, root)
            if not entry.is_dir():
                path.chmod(mode & 0o777)
PY
fi
if [ ! -f "$work/go/.boottime-patched" ]; then
    tar -xzf "$go_archive" --strip-components=1 -C "$work/go"
    patch --batch --fuzz=0 -p1 -d "$work/go" < "$repo/.cache/i-011/sdk-go-only/tunnel/tools/libwg-go/goruntime-boottime-over-monotonic.diff"
    touch "$work/go/.boottime-patched"
fi
cp "$repo/.cache/i-011/sdk-go-only/tunnel/tools/libwg-go/"* "$work/source/"
export PATH="$work/go/bin:$PATH"
export GOTOOLCHAIN=local CGO_ENABLED=1 GOOS=android
cd "$work/source"
go mod download
go mod verify
ndk="$work/ndk/android-ndk-r26b/toolchains/llvm/prebuilt/linux-x86_64"
for abi in arm64-v8a x86_64; do
    case "$abi" in
        arm64-v8a) export GOARCH=arm64; target=aarch64-linux-android24 ;;
        x86_64) export GOARCH=amd64; target=x86_64-linux-android24 ;;
    esac
    export CC="$ndk/bin/clang"
    export CGO_CFLAGS="--target=$target --sysroot=$ndk/sysroot"
    export CGO_LDFLAGS="--target=$target --sysroot=$ndk/sysroot -Wl,-z,max-page-size=16384 -Wl,--build-id=none -Wl,-soname=libwg-go.so"
    output="$repo/.cache/i-011/sdk-go-only/jniLibs/$abi"
    mkdir -p "$output"
    go build -mod=readonly -tags linux -trimpath -buildvcs=false \
        -ldflags='-buildid=' -buildmode=c-shared -o "$output/libwg-go.so"
    "$ndk/bin/llvm-readelf" -l "$output/libwg-go.so" > "$work/$abi-elf.txt"
    go version -m "$output/libwg-go.so" > "$work/$abi-modules.txt"
    sha256sum "$output/libwg-go.so"
done
go list -m -json all > "$work/android-module-graph.json"
python3 - "$work/android-module-graph.json" "$repo/.cache/i-011/sdk-go-only" "$work/go" <<'PY'
import json, pathlib, re, shutil, sys
text = pathlib.Path(sys.argv[1]).read_text()
prepared = pathlib.Path(sys.argv[2])
licenses = prepared / "assets" / "third-party"
licenses.mkdir(parents=True, exist_ok=True)
shutil.copyfile(prepared / "COPYING", licenses / "awg-android-Apache-2.0.txt")
shutil.copyfile(pathlib.Path(sys.argv[3]) / "LICENSE", licenses / "go-BSD-3-Clause.txt")
decoder, position, inventory = json.JSONDecoder(), 0, []
while position < len(text):
    position = re.compile(r"\s*").match(text, position).end()
    if position >= len(text): break
    module, position = decoder.raw_decode(text, position)
    if "Dir" not in module or module.get("Main"): continue
    destination = licenses / module["Path"].replace("/", "_")
    copied = []
    for source in pathlib.Path(module["Dir"]).iterdir():
        if source.is_file() and source.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "COPYRIGHT")):
            destination.mkdir(exist_ok=True)
            shutil.copyfile(source, destination / source.name)
            copied.append(source.name)
    inventory.append({"module": module["Path"], "version": module["Version"], "root_notices": copied})
(licenses / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
(licenses / "MODIFICATIONS.txt").write_text(
    "Local I-011 GoBackend-only patch: Java logging/root tools removed; foreground/lifecycle/socket protection; "
    "Go UAPI/logging removed. Go CLOCK_BOOTTIME patch applied. No custom cryptography. "
    "Module graph root notices are not a completed per-file release licence review.\n")
PY
echo 'Android native libraries built; no tunnel started'
