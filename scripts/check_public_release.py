"""Fail a release when tracked files expose common secrets or local identities."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

FORBIDDEN_NAMES = {".env", "credentials.json"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".p12", ".pfx"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "nonempty TypeSafe key": re.compile(r"(?m)^TYPESAFE_API_KEY[ \t]*=[ \t]*[^\s#]+"),
    "Windows user path": re.compile(r"(?i)\b[A-Z]:\\{1,2}Users\\{1,2}[^\\\r\n]+\\{1,2}"),
    "Unix user path": re.compile(r"(?m)(?:/Users|/home)/[^/\s]+/"),
}


def scan_file(path: Path, relative: str) -> list[str]:
    normalized = relative.replace("\\", "/")
    name = Path(normalized).name.lower()
    if name in FORBIDDEN_NAMES and normalized != ".env.example":
        return [f"{relative}: forbidden private filename"]
    if Path(name).suffix.lower() in FORBIDDEN_SUFFIXES:
        return [f"{relative}: forbidden private file type"]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    return [f"{relative}: {label}" for label, pattern in PATTERNS.items() if pattern.search(text)]


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def audit(root: Path, files: list[str] | None = None) -> list[str]:
    selected = tracked_files(root) if files is None else files
    findings: list[str] = []
    for relative in selected:
        findings.extend(scan_file(root / relative, relative))
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = audit(root)
    if findings:
        print("PUBLIC_RELEASE_CHECK=FAILED")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(f"PUBLIC_RELEASE_CHECK=OK tracked_files={len(tracked_files(root))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
