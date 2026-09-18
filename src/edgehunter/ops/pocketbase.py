"""Pinned, isolated PocketBase lifecycle; never changes another project's instance."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import IO, Any

import httpx

from edgehunter.storage.pocketbase import PocketBaseClient


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        identity = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{identity}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], capture_output=True)
        if result.returncode:
            raise RuntimeError("Could not restrict local state ACL; no credentials written")
    else:
        path.chmod(0o700)


class PocketBaseRuntime:
    def __init__(self, project_root: Path | str, *, data_dir: Path | str | None = None, port: int = 0) -> None:
        self.root = Path(project_root).resolve()
        self.state = Path(data_dir).resolve() if data_dir else self.root / ".local" / "pocketbase"
        self.port = port
        self.url = ""
        self.process: subprocess.Popen[bytes] | None = None
        self.log: IO[bytes] | None = None
        self.credentials_path = self.state / "credentials.json"
        self.manifest = json.loads((self.root / "pocketbase" / "releases.json").read_text(encoding="utf-8"))
        system = platform.system().lower()
        machine = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
        self.target = f"{system}_{machine}"
        self.binary = self.root / ".tools" / "pocketbase" / self.manifest["version"] / self.target / ("pocketbase.exe" if os.name == "nt" else "pocketbase")

    def install(self) -> dict[str, str]:
        if self.target not in self.manifest["sha256"]:
            raise RuntimeError(f"Unsupported PocketBase target: {self.target}")
        self.binary.parent.mkdir(parents=True, exist_ok=True)
        version = self.manifest["version"]
        archive = self.binary.parent / f"pocketbase_{version}_{self.target}.zip"
        expected = self.manifest["sha256"][self.target]
        if not archive.exists():
            url = f"https://github.com/pocketbase/pocketbase/releases/download/v{version}/{archive.name}"
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(80 * 1024 * 1024 + 1)
            if len(data) > 80 * 1024 * 1024:
                raise RuntimeError("PocketBase archive exceeded size limit")
            if hashlib.sha256(data).hexdigest() != expected:
                raise RuntimeError("PocketBase download checksum mismatch")
            archive.write_bytes(data)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Pinned PocketBase archive integrity failure")
        with zipfile.ZipFile(archive) as zipped:
            binary_data = zipped.read(self.binary.name)
        if not self.binary.exists() or hashlib.sha256(self.binary.read_bytes()).digest() != hashlib.sha256(binary_data).digest():
            self.binary.write_bytes(binary_data)
        if os.name != "nt":
            self.binary.chmod(0o755)
        return {"version": version, "target": self.target, "sha256": expected, "status": "VERIFIED"}

    def _flags(self) -> list[str]:
        return ["--dir", str(self.state / "pb_data"), "--migrationsDir", str(self.root / "pocketbase" / "pb_migrations"),
                "--hooksDir", str(self.root / "pocketbase" / "pb_hooks"), "--automigrate=false"]

    def provision(self) -> dict[str, str]:
        self.install()
        secure_directory(self.state)
        if not self.credentials_path.exists():
            if (self.state / "pb_data" / "data.db").exists():
                raise RuntimeError("Existing PocketBase database requires its original restricted credentials; refusing to replace identity")
            credentials = {"admin_email": "human-local@edgehunter.invalid", "admin_password": secrets.token_urlsafe(36),
                           "daemon_email": "daemon@edgehunter.invalid", "daemon_password": secrets.token_urlsafe(36)}
            descriptor = os.open(self.credentials_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(credentials, stream)
        env = os.environ.copy()
        env["EDGEHUNTER_PB_BOOTSTRAP"] = self.credentials_path.read_text(encoding="utf-8")
        result = subprocess.run([str(self.binary), "migrate", "up", *self._flags()], capture_output=True, timeout=45, env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            # PB diagnostics may include request data: preserve locally, not in exceptions/logs.
            (self.state / "migration-error.log").write_bytes(result.stdout + result.stderr)
            raise RuntimeError("PocketBase migration failed; inspect restricted migration-error.log")
        return {"status": "MIGRATED", "credentials_path": str(self.credentials_path)}

    def start(self) -> str:
        if self.process and self.process.poll() is None:
            return self.url
        self.provision()
        if self.port == 0:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                self.port = probe.getsockname()[1]
        credentials = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        env = os.environ.copy()
        env.pop("EDGEHUNTER_PB_BOOTSTRAP", None)
        env["EDGEHUNTER_PB_HUMAN_EMAIL"] = credentials["admin_email"]
        self.url = f"http://127.0.0.1:{self.port}"
        self.log = (self.state / "pocketbase.log").open("ab")
        self.process = subprocess.Popen([str(self.binary), "serve", "--http", f"127.0.0.1:{self.port}", *self._flags()],
            stdout=self.log, stderr=self.log, env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 20
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    self.stop()
                    raise RuntimeError("PocketBase exited; inspect restricted local log")
                try:
                    if client.get(self.url + "/api/health").status_code == 200:
                        return self.url
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("PocketBase health startup timeout")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()
        self.process = None
        self.log = None

    async def daemon_client(self) -> PocketBaseClient:
        credentials = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        client = PocketBaseClient(self.url)
        try:
            await client.login(credentials["daemon_email"], credentials["daemon_password"])
        except Exception:
            await client.close()
            raise
        return client

    async def human_client(self, *, simulated: bool = False) -> PocketBaseClient:
        """Demo/testing only. Runtime daemon must never call this method."""
        if not simulated:
            raise PermissionError("Programmatic human login is permitted only for clearly labelled local simulation")
        credentials = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        client = PocketBaseClient(self.url)
        try:
            await client.login(credentials["admin_email"], credentials["admin_password"], collection="_superusers")
        except Exception:
            await client.close()
            raise
        return client

    def backup(self, destination: Path | str) -> dict[str, Any]:
        """Restricted local snapshot, using SQLite backup; plaintext credentials excluded.

        Stop the dedicated control instance first. Files/remote storage are unsupported
        by this project's JSON-only collections, so refuse unknown file payloads.
        """
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("Stop the dedicated PocketBase process before taking its snapshot")
        destination = Path(destination).resolve()
        if destination.exists():
            raise FileExistsError("PocketBase backup destination must be new")
        data = self.state / "pb_data"
        if not (data / "data.db").is_file():
            raise FileNotFoundError("PocketBase data.db is missing")
        storage = data / "storage"
        if storage.exists() and any(path.is_file() for path in storage.rglob("*")):
            raise RuntimeError("File storage requires PocketBase native backup; this adapter only snapshots JSON collections")
        secure_directory(destination)
        files: dict[str, str] = {}
        for source in sorted(data.glob("*.db")):
            target = destination / source.name
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as origin:
                with sqlite3.connect(target) as copied:
                    origin.backup(copied)
                    if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("PocketBase snapshot failed SQLite integrity check")
            files[source.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        metadata: dict[str, Any] = {
            "format": "edgehunter-pocketbase-v1", "pocketbase_version": self.manifest["version"],
            "created_unix": time.time(), "live_disabled": True, "credentials_included": False,
            "files": files,
            "migrations": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted((self.root / "pocketbase" / "pb_migrations").glob("*.js"))},
            "hooks": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted((self.root / "pocketbase" / "pb_hooks").glob("*.js"))},
        }
        (destination / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return {"path": str(destination), "status": "LOCAL_SNAPSHOT_VERIFIED", **metadata}

    def inspect_backup(self, backup: Path | str) -> dict[str, Any]:
        """Verify a local manifest and SQLite schemas without modifying source or runtime."""
        backup = Path(backup).resolve()
        metadata = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or metadata.get("format") != "edgehunter-pocketbase-v1":
            raise ValueError("Unsupported PocketBase backup format")
        if metadata.get("pocketbase_version") != self.manifest["version"]:
            raise ValueError("PocketBase version mismatch; explicit migration plan required")
        if metadata.get("live_disabled") is not True or metadata.get("credentials_included") is not False:
            raise ValueError("Unsafe PocketBase backup metadata")
        files = metadata.get("files")
        if not isinstance(files, dict) or "data.db" not in files:
            raise ValueError("PocketBase backup has no data.db")
        for name, digest in files.items():
            if not isinstance(name, str) or Path(name).name != name or not name.endswith(".db"):
                raise ValueError("Unsafe snapshot file path")
            path = backup / name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("PocketBase snapshot checksum mismatch")
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("PocketBase snapshot database corruption")
                if name == "data.db":
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"_migrations", "commands", "approval_requests", "approval_audit", "service_accounts"} <= tables:
                        raise ValueError("PocketBase snapshot schema mismatch")
        for folder, key in (("pb_migrations", "migrations"), ("pb_hooks", "hooks")):
            expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted((self.root / "pocketbase" / folder).glob("*.js"))}
            if metadata.get(key) != expected:
                raise ValueError("PocketBase hook/migration revision differs; explicit upgrade review required")
        return {"status": "VERIFIED", "live_disabled": True, "files": len(files), "metadata": metadata}

    def restore(self, backup: Path | str, destination: Path | str, *, dry_run: bool = True,
                credentials_source: Path | str | None = None) -> dict[str, Any]:
        """Restore only to a new isolated directory; never overwrite active state."""
        report = self.inspect_backup(backup)
        destination = Path(destination).resolve()
        if destination.exists():
            raise FileExistsError("Restore requires a new isolated destination")
        if destination == self.state or self.state in destination.parents:
            raise ValueError("Restore cannot target the current PocketBase state")
        if dry_run:
            return {**report, "status": "DRY_RUN_VERIFIED", "destination": str(destination)}
        credentials: bytes | None = None
        if credentials_source is not None:
            credentials = Path(credentials_source).read_bytes()
            parsed = json.loads(credentials)
            if not isinstance(parsed, dict) or not all(parsed.get(key) for key in (
                "admin_email", "admin_password", "daemon_email", "daemon_password"
            )):
                raise ValueError("Invalid separate credential source")
        secure_directory(destination)
        data = destination / "pb_data"
        data.mkdir(mode=0o700)
        for name in report["metadata"]["files"]:
            shutil.copyfile(Path(backup) / name, data / name)
        if credentials is not None:
            descriptor = os.open(destination / "credentials.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(credentials)
        (destination / "RESTORE_REQUIRES_RECONCILIATION.json").write_text(json.dumps({
            "live_disabled": True, "reason": "Restore the matching financial journal and reconcile before use",
            "restored_unix": time.time(), "source": str(Path(backup).resolve()),
        }, indent=2), encoding="utf-8")
        return {"status": "RESTORED_ISOLATED", "destination": str(destination), "live_disabled": True,
                "reconciliation_required": True, "credentials_supplied_separately": credentials is not None}

    def __enter__(self) -> PocketBaseRuntime:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()
