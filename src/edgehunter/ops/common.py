from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def json_ready(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def doctor(root: Path) -> dict[str, Any]:
    space = shutil.disk_usage(root)
    return {"schema_version": 1, "checked_at": datetime.now(UTC).isoformat(),
            "python": sys.version.split()[0], "platform": platform.platform(),
            "architecture": platform.machine(), "free_disk_gb": round(space.free / 1e9, 2),
            "workspace": str(root), "mode_ceiling": "PAPER", "live_capability": False,
            "python_supported": (3, 11) <= sys.version_info[:2] < (3, 14),
            "disk_ok": space.free > 1_000_000_000,
            "deployment": "NOT_DEPLOYED", "incremental_provider_budget": "0",
            "secrets": "REDACTED; project credentials stay in restricted .local directories",
            "local_support": "Native Windows local paper supported; Linux deployment templates provided"}
