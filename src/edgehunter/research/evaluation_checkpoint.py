"""Versioned development caches; a begun holdout is never silently replayed."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(values: Iterable[Any]) -> str:
    """Hash a canonical array incrementally without copying the whole dataset."""
    digest = hashlib.sha256(b"[")
    for index, value in enumerate(values):
        if index:
            digest.update(b",")
        if is_dataclass(value) and not isinstance(value, type):
            value = asdict(value)
        digest.update(_json(value).encode())
    digest.update(b"]")
    return digest.hexdigest()


def evaluation_identity(
    *, rows: Iterable[Any], specs: Iterable[Any], config: Any,
    costs: Iterable[Any], implementation_paths: Iterable[Path],
) -> dict[str, str]:
    import numpy as np

    paths = [*implementation_paths, Path(__file__)]
    return {
        "dataset_sha256": fingerprint(rows),
        "candidate_specs_sha256": fingerprint(specs),
        "config_and_costs_sha256": fingerprint([config, *costs]),
        "implementation_sha256": fingerprint([
            (path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths
        ]),
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
    }


class DevelopmentCheckpoint:
    """One directory belongs to one exact ordered experiment, including code.

    Completed candidates and final results survive restarts. If execution stopped
    after the holdout began but before its result was saved, recovery refuses to
    reopen it. This guard is scoped to this directory, not a global study registry.
    """

    SCHEMA = "edgehunter.development_checkpoint.v1"

    def __init__(self, directory: Path, identity: Mapping[str, str], candidate_count: int) -> None:
        if candidate_count < 1:
            raise ValueError("checkpoint candidates required")
        self.directory = directory
        self.candidate_count = candidate_count
        self.identity = dict(identity)
        directory.mkdir(parents=True, exist_ok=True)
        expected = {
            "schema": self.SCHEMA, "identity": self.identity,
            "candidate_count": candidate_count,
        }
        manifest = directory / "manifest.json"
        if manifest.exists():
            if json.loads(manifest.read_text(encoding="utf-8")) != expected:
                raise ValueError("checkpoint fingerprint mismatch; use a new directory")
        else:
            self._write(manifest, expected)

    def _write(self, path: Path, value: Any) -> None:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=self.directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_json(value) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _candidate_path(self, index: int) -> Path:
        if not 0 <= index < self.candidate_count:
            raise ValueError("checkpoint candidate index out of range")
        return self.directory / f"candidate_{index:05d}.json"

    def _envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"payload": payload, "sha256": fingerprint([self.identity, payload])}

    def _read(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        payload = value.get("payload")
        if not isinstance(payload, dict) or value.get("sha256") != fingerprint([self.identity, payload]):
            raise ValueError(f"corrupt checkpoint: {path.name}")
        return payload

    def load_candidate(self, index: int) -> dict[str, Any] | None:
        payload = self._read(self._candidate_path(index))
        if payload is None:
            return None
        if payload.get("index") != index or not isinstance(payload.get("record"), dict):
            raise ValueError("invalid checkpoint candidate")
        record: dict[str, Any] = payload["record"]
        return record

    def save_candidate(self, index: int, record: dict[str, Any]) -> None:
        if (self.directory / "holdout_started.json").exists():
            raise ValueError("development is closed after holdout begins")
        self._write(self._candidate_path(index), self._envelope({"index": index, "record": record}))

    def load_final(self) -> dict[str, Any] | None:
        result = self._read(self.directory / "final.json")
        if result is None and (self.directory / "holdout_started.json").exists():
            raise ValueError("holdout already started without a saved result; automatic replay refused")
        return result

    def begin_holdout(self) -> None:
        # Exclusive creation also stops two resumed processes opening this holdout.
        try:
            with (self.directory / "holdout_started.json").open("x", encoding="utf-8") as handle:
                handle.write(_json({"identity": self.identity}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as error:
            raise ValueError("holdout already started; automatic replay refused") from error

    def save_final(self, result: dict[str, Any]) -> None:
        self._write(self.directory / "final.json", self._envelope(result))
