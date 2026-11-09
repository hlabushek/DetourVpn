"""Local secret scanner for the repository working tree (and optionally git history).

Standard library only; reads files through `git ls-files`, sends nothing anywhere. It never
prints a matched value: a finding is `path:line  rule  fingerprint`, where the fingerprint is the
first 10 hex characters of SHA-256 of the match, enough to allowlist it and not enough to
recover it. Exit code 0 = clean (or only allowlisted), 1 = findings, 2 = usage/IO error.

Allowlist: `scripts/secret_scan_allowlist.txt`, one `rule fingerprint  # reason` per line.
"""

import argparse
import hashlib
import math
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git") or "git"
ALLOWLIST = Path(__file__).with_name("secret_scan_allowlist.txt")
MAX_BYTES = 2 * 1024 * 1024
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".apk",
    ".aab",
    ".jar",
    ".so",
    ".zip",
    ".gz",
    ".enc",
    ".keystore",
    ".jks",
    ".woff",
    ".woff2",
    ".ttf",
    ".pdf",
}

# (rule, regex, group holding the secret value). Values are never printed.
RULES: list[tuple[str, re.Pattern[str], int]] = [
    ("private-key-block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), 0),
    ("telegram-bot-token", re.compile(r"(?<![\w:])(\d{8,10}:[A-Za-z0-9_-]{35})(?![\w-])"), 1),
    ("aws-access-key-id", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"), 1),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b"), 1),
    ("jwt", re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"), 1),
    ("fernet-token", re.compile(r"\b(gAAAAA[A-Za-z0-9_-]{40,}={0,2})"), 1),
    (
        "wireguard-private-key",
        re.compile(r"(?i)\bPrivateKey\s*[:=]\s*[\"']?([A-Za-z0-9+/]{43}=)"),
        1,
    ),
    (
        "url-with-password",
        re.compile(
            r"\b[a-z][a-z0-9+.-]*://[^\s/:@\"']{1,64}:([^\s/@\"']{6,})@(?!\w*(?:host|example))"
        ),
        1,
    ),
    (
        "assigned-secret",
        re.compile(
            r"(?i)\b[\w.-]*(?:secret|passw(?:or)?d|api[_-]?key|private[_-]?key|token)[\w.-]*"
            r"\s*[:=]\s*[\"']([A-Za-z0-9+/_=-]{20,})[\"']"
        ),
        1,
    ),
]

# Obvious placeholders that are not secrets.
PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|changeme|change-me|dummy|fake|test|sample|xxxx|your[-_]|"
    r"<[^>]+>|\$\{|%\(|redacted|0{8,}|1234567890|abcdef)"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    fingerprint: str


def entropy(value: str) -> float:
    counts = {char: value.count(char) for char in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:10]


def scan_text(path: str, text: str) -> Iterator[Finding]:
    for number, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:
            line = line[:4000]
        for rule, pattern, group in RULES:
            for match in pattern.finditer(line):
                value = match.group(group)
                if rule == "assigned-secret":
                    # A hash, a UUID or a low-entropy word is not a credential.
                    if PLACEHOLDER.search(value) or entropy(value) < 3.5:
                        continue
                elif rule != "private-key-block" and PLACEHOLDER.search(value):
                    continue
                yield Finding(path, number, rule, fingerprint(value))


def load_allowlist(path: Path) -> set[tuple[str, str]]:
    allowed: set[tuple[str, str]] = set()
    if not path.exists():
        return allowed
    for raw in path.read_text(encoding="utf-8").splitlines():
        entry = raw.split("#", 1)[0].split()
        if len(entry) == 2:
            allowed.add((entry[0], entry[1]))
    return allowed


def git(*args: str) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell.
        [GIT, "-C", str(ROOT), *args], check=True, capture_output=True
    ).stdout


def tracked_and_untracked() -> list[str]:
    raw = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return [item for item in raw.decode("utf-8", "replace").split("\0") if item]


def scan_tree() -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    count = 0
    for name in tracked_and_untracked():
        path = ROOT / name
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file() or path.is_symlink():
            continue
        if path.stat().st_size > MAX_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            continue
        count += 1
        findings.extend(scan_text(name, data.decode("utf-8", "replace")))
    return findings, count


def scan_history(limit: int) -> tuple[list[Finding], int]:
    """Scan added lines of `git log -p --all`; path is reported with the short commit."""
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell.
        [GIT, "-C", str(ROOT), "log", "--all", "-p", "--no-color", "--format=@@%h", f"-n{limit}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if process.stdout is None:
        raise OSError("no_stdout")
    commit = "?"
    path = "?"
    findings: list[Finding] = []
    commits = 0
    for raw in process.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        if line.startswith("@@") and len(line) <= 14 and " " not in line:
            commit = line[2:]
            commits += 1
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            if Path(path).suffix.lower() in SKIP_SUFFIXES:
                continue
            findings.extend(scan_text(f"{commit}:{path}", line[1:]))
    process.wait()
    return findings, commits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history", type=int, metavar="N", help="also scan last N commits (all refs)"
    )
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    arguments = parser.parse_args()
    try:
        findings, files = scan_tree()
        commits = 0
        if arguments.history:
            history, commits = scan_history(arguments.history)
            findings.extend(history)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"scan_error {type(error).__name__}", file=sys.stderr)
        return 2
    allowed = load_allowlist(arguments.allowlist)
    open_findings = sorted(
        {f for f in findings if (f.rule, f.fingerprint) not in allowed},
        key=lambda f: (f.path, f.line, f.rule),
    )
    for item in open_findings:
        print(f"{item.path}:{item.line}  {item.rule}  {item.fingerprint}")
    print(
        f"files={files} commits={commits} findings={len(open_findings)} "
        f"allowlisted={len(findings) - len(open_findings)}"
    )
    return 1 if open_findings else 0


if __name__ == "__main__":
    sys.exit(main())
