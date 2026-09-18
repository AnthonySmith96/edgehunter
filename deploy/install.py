"""Render a reviewable Linux installation package; never mutate host services.

No flags install packages, create accounts, change a firewall or enable a service.
The default prints a dry-run plan. --output writes files only into a NEW directory.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
from pathlib import Path


def render(*, host: str, port: int, admin_bind: str, release_root: str, architecture: str,
           profile: str = "vpn") -> dict[str, str]:
    if len(host) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in host.split(".")
    ):
        raise ValueError("Host must be a plain lowercase DNS name")
    if not 1024 <= port <= 65535:
        raise ValueError("Choose an inspected unprivileged dedicated PocketBase port")
    address = ipaddress.ip_address(admin_bind)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError("Management bind must be an inspected private or loopback address")
    if not re.fullmatch(r"/[A-Za-z0-9_/-]+", release_root) or ".." in release_root or "//" in release_root:
        raise ValueError("Release path must be an absolute Linux path without traversal or whitespace")
    if architecture not in {"amd64", "arm64"} or profile not in {"vpn", "web"}:
        raise ValueError("Unsupported deployment target")
    root = Path(__file__).resolve().parents[1]
    version = json.loads((root / "pocketbase" / "releases.json").read_text(encoding="utf-8"))["version"]
    substitutions = {
        "HOST": host, "PB_PORT": str(port), "RELEASE_ROOT": release_root,
        "ADMIN_BIND": f"[{address}]" if address.version == 6 else str(address),
        "PB_BINARY": f"{release_root}/.tools/pocketbase/{version}/linux_{architecture}/pocketbase",
    }
    files: dict[str, str] = {}
    for name in ("edgehunter-pocketbase.service", "edgehunter-daemon.service", f"nginx-{profile}.conf"):
        content = (root / "deploy" / "templates" / (name + ".in")).read_text(encoding="utf-8")
        for key, value in substitutions.items():
            content = content.replace("@" + key + "@", value)
        if re.search(r"@[A-Z_]+@", content):
            raise ValueError("Unresolved installation placeholder")
        files[name] = content
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="control.example.invalid")
    parser.add_argument("--pb-port", type=int, default=8097)
    parser.add_argument("--admin-bind", default="127.0.0.1")
    parser.add_argument("--release-root", default="/srv/edgehunter/current")
    parser.add_argument("--architecture", choices=("amd64", "arm64"), default="amd64")
    parser.add_argument("--profile", choices=("vpn", "web"), default="vpn")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    files = render(host=args.host, port=args.pb_port, admin_bind=args.admin_bind,
                   release_root=args.release_root, architecture=args.architecture, profile=args.profile)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        for name, content in files.items():
            (args.output / name).write_text(content, encoding="utf-8", newline="\n")
    print(json.dumps({"status": "RENDERED_FOR_REVIEW" if args.output else "DRY_RUN",
                      "host_modified": False, "live_disabled": True,
                      "output": str(args.output) if args.output else None,
                      "files": {name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()},
                      "pending": ["authorized host preflight", "accounts and credential separation", "nginx -t",
                                  "systemd-analyze verify", "TLS/VPN or MFA", "external backup", "remote device tests"]}, indent=2))


if __name__ == "__main__":
    main()
