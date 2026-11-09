"""Offline CycloneDX 1.5 inventory of the Android/Flutter/toolchain dependencies.

Standard library only, no network, no installation. Inputs are the committed lockfiles
(`gradle.lockfile`, `pubspec.lock`), pins in `apps/client/toolchain.json`, the Gradle wrapper
properties, `third_party/amnezia-box/source-lock.json`, and the compose image references.
Licenses are read from local caches (Gradle `.pom`, Pub `LICENSE`) by keyword; this is an
inventory aid and NOT a legal determination: unknown stays `NOASSERTION`.

Usage: python -I scripts/generate_native_sbom.py OUTPUT_DIR
"""

import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
GRADLE_CACHE = HOME / ".gradle" / "caches" / "modules-2" / "files-2.1"
PUB_CACHE = HOME / "AppData" / "Local" / "Pub" / "Cache" / "hosted" / "pub.dev"
LOCKFILES = [
    "apps/android/app/gradle.lockfile",
    "apps/android/tunnel/gradle.lockfile",
    "apps/client/android/app/gradle.lockfile",
    "apps/client/android/tunnel-gradle.lockfile",
]
KEYWORDS: list[tuple[str, str]] = [
    ("apache license", "Apache-2.0"),
    ("mit license", "MIT"),
    ("permission is hereby granted, free of charge", "MIT"),
    ("redistribution and use in source and binary forms", "BSD-style"),
    ("eclipse public license", "EPL"),
    ("gnu general public license", "GPL-family"),
    ("gnu lesser general public license", "LGPL-family"),
    ("mozilla public license", "MPL"),
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def guess_license(text: str) -> str:
    lowered = " ".join(text.lower().split())
    for needle, name in KEYWORDS:
        if needle in lowered:
            return name
    return "NOASSERTION"


def pom_license(group: str, name: str, version: str) -> str:
    base = GRADLE_CACHE / group / name / version
    for pom in sorted(base.glob("*/*.pom")) if base.is_dir() else []:
        try:
            tree = ET.fromstring(pom.read_text(encoding="utf-8", errors="replace"))  # noqa: S314 - local Gradle cache POMs
        except ET.ParseError:
            continue
        names = [
            (child.text or "").strip()
            for node in tree.iter()
            if node.tag.rsplit("}", 1)[-1] == "license"
            for child in node
            if child.tag.rsplit("}", 1)[-1] == "name"
        ]
        text = " ".join(item for item in names if item)
        if text:
            found = guess_license(text)
            return found if found != "NOASSERTION" else text[:80]
    return "NOASSERTION"


def maven_components() -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for relative in LOCKFILES:
        path = ROOT / relative
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line or line.startswith("empty="):
                continue
            coordinate, configs = line.split("=", 1)
            group, name, version = coordinate.split(":")
            key = f"{group}:{name}:{version}"
            entry = merged.setdefault(
                key,
                {
                    "group": group,
                    "name": name,
                    "version": version,
                    "lockfiles": set(),
                    "release": False,
                },
            )
            entry["lockfiles"].add(relative)  # type: ignore[attr-defined]
            if "releaseRuntimeClasspath" in configs.split(","):
                entry["release"] = True
    components: list[dict[str, object]] = []
    for key in sorted(merged):
        item = merged[key]
        group, name, version = str(item["group"]), str(item["name"]), str(item["version"])
        license_id = pom_license(group, name, version)
        components.append(
            {
                "type": "library",
                "bom-ref": f"maven:{key}",
                "group": group,
                "name": name,
                "version": version,
                "purl": f"pkg:maven/{group}/{name}@{version}",
                "licenses": [{"license": {"name": license_id}}],
                "properties": [
                    {
                        "name": "detour:in-release-runtime-classpath",
                        "value": str(item["release"]).lower(),
                    },
                    {"name": "detour:lockfiles", "value": ",".join(sorted(item["lockfiles"]))},  # type: ignore[arg-type]
                ],
            }
        )
    return components


def pub_components() -> list[dict[str, object]]:
    text = (ROOT / "apps/client/pubspec.lock").read_text(encoding="utf-8")
    components: list[dict[str, object]] = []
    for block in re.split(r"\n  (?=\S[^\n]*:\n    dependency:)", text.split("packages:\n", 1)[1]):
        name = block.split(":", 1)[0].strip()
        fields = dict(
            re.findall(r"^\s+(dependency|source|version|sha256): \"?([^\"\n]+)\"?$", block, re.M)
        )
        if "version" not in fields:
            continue
        version = fields["version"]
        license_id = "NOASSERTION"
        for candidate in (PUB_CACHE / f"{name}-{version}").glob("LICEN[SC]E*"):
            license_id = guess_license(
                candidate.read_text(encoding="utf-8", errors="replace")[:6000]
            )
            break
        component: dict[str, object] = {
            "type": "library",
            "bom-ref": f"pub:{name}@{version}",
            "name": name,
            "version": version,
            "purl": f"pkg:pub/{name}@{version}",
            "licenses": [{"license": {"name": license_id}}],
            "properties": [
                {"name": "detour:dependency-kind", "value": fields.get("dependency", "")},
                {"name": "detour:source", "value": fields.get("source", "")},
            ],
        }
        if "sha256" in fields:
            component["hashes"] = [{"alg": "SHA-256", "content": fields["sha256"]}]
        components.append(component)
    return sorted(components, key=lambda c: str(c["name"]))


def toolchain_components() -> list[dict[str, object]]:
    tools = json.loads((ROOT / "apps/client/toolchain.json").read_text(encoding="utf-8"))
    wrapper = (ROOT / "apps/client/gradle-wrapper.properties").read_text(encoding="utf-8")
    gradle = re.search(r"gradle-([\d.]+)-bin", wrapper)
    gradle_sha = re.search(r"distributionSha256Sum=([0-9a-f]{64})", wrapper)
    amnezia = json.loads(
        (ROOT / "third_party/amnezia-box/source-lock.json").read_text(encoding="utf-8")
    )
    settings = (ROOT / "apps/client/android/settings.gradle.kts").read_text(encoding="utf-8")
    agp = re.search(r'com\.android\.application"\) version "([\d.]+)"', settings)
    kotlin = re.search(r'org\.jetbrains\.kotlin\.android"\) version "([\d.]+)"', settings)
    ndk_dir = HOME / "AppData" / "Local" / "Android" / "Sdk" / "ndk"
    items: list[tuple[str, str, str, str, str]] = [
        (
            "Flutter SDK",
            tools["version"],
            tools["sha256"],
            "BSD-3-Clause (upstream; not verified here)",
            "toolchain.json archive",
        ),
        (
            "Gradle",
            gradle.group(1) if gradle else "?",
            gradle_sha.group(1) if gradle_sha else "",
            "Apache-2.0 (upstream; not verified here)",
            "gradle-wrapper.properties",
        ),
        (
            "Android Gradle Plugin",
            agp.group(1) if agp else "?",
            "",
            "Apache-2.0 (upstream; not verified here)",
            "settings.gradle.kts",
        ),
        (
            "Kotlin Gradle plugin",
            kotlin.group(1) if kotlin else "?",
            "",
            "Apache-2.0 (upstream; not verified here)",
            "settings.gradle.kts",
        ),
        (
            "Amnezia Box",
            amnezia["version"],
            amnezia["archive_sha256"],
            amnezia["license"],
            "third_party/amnezia-box/source-lock.json; embedded in the beta APK",
        ),
    ]
    components = [
        {
            "type": "application" if name == "Amnezia Box" else "framework",
            "bom-ref": f"tool:{name}@{version}",
            "name": name,
            "version": version,
            "licenses": [{"license": {"name": license_text}}],
            "description": source,
            **({"hashes": [{"alg": "SHA-256", "content": digest}]} if digest else {}),
        }
        for name, version, digest, license_text, source in items
    ]
    if ndk_dir.is_dir():
        for entry in sorted(ndk_dir.iterdir()):
            components.append(
                {
                    "type": "framework",
                    "bom-ref": f"tool:android-ndk@{entry.name}",
                    "name": "Android NDK (installed on the build machine)",
                    "version": entry.name,
                    "licenses": [{"license": {"name": "Android SDK terms / NOASSERTION"}}],
                    "description": "Installed locally; the APK build version is not recorded.",
                }
            )
    return components


def image_components() -> list[dict[str, object]]:
    found: dict[str, str] = {}
    for path in (ROOT / "infra").rglob("*.y*ml"):
        for match in re.finditer(
            r"^\s*image:\s*(\S+)", path.read_text(encoding="utf-8", errors="replace"), re.M
        ):
            found[match.group(1)] = path.relative_to(ROOT).as_posix()
    return [
        {
            "type": "container",
            "bom-ref": f"image:{image}",
            "name": image.split("@")[0].rsplit(":", 1)[0],
            "version": image.split(":", 1)[1] if ":" in image else "unpinned",
            "description": f"referenced in {where}; OS packages inside are not inventoried",
            "licenses": [{"license": {"name": "NOASSERTION"}}],
        }
        for image, where in sorted(found.items())
    ]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: generate_native_sbom.py OUTPUT_DIR", file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    parts = {
        "maven": maven_components(),
        "pub": pub_components(),
        "toolchain": toolchain_components(),
        "containers": image_components(),
    }
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [{"name": "scripts/generate_native_sbom.py"}],
            "component": {"type": "application", "name": "com.detourvpn.client (beta)"},
        },
        "components": [item for group in parts.values() for item in group],
    }
    (out / "android-flutter-toolchain.cdx.json").write_text(
        json.dumps(bom, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary: dict[str, object] = {}
    for label, group in parts.items():
        licenses = [str(c["licenses"][0]["license"]["name"]) for c in group]  # type: ignore[index]
        summary[label] = {
            "components": len(group),
            "noassertion": sum(1 for item in licenses if item == "NOASSERTION"),
        }
    release = [
        c
        for c in parts["maven"]
        if {"name": "detour:in-release-runtime-classpath", "value": "true"} in c["properties"]
    ]  # type: ignore[operator]
    summary["maven_release_runtime"] = {
        "components": len(release),
        "licenses": sorted({str(c["licenses"][0]["license"]["name"]) for c in release}),  # type: ignore[index]
        "noassertion": [
            f"{c['group']}:{c['name']}:{c['version']}"
            for c in release
            if c["licenses"][0]["license"]["name"] == "NOASSERTION"
        ],  # type: ignore[index]
    }
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
