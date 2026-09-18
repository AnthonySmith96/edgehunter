"""Load only explicit TypeSafe variables from a local dotenv file; never evaluate text."""
from __future__ import annotations

import os
import re
from pathlib import Path

ALLOWED_KEYS = frozenset({
    "TYPESAFE_API_KEY", "TYPESAFE_MODEL", "TYPESAFE_BUDGET_USD",
    "TYPESAFE_MAX_CALL_USD", "TYPESAFE_TIMEOUT_SECONDS",
})


def load_typesafe_env(path: Path) -> None:
    if not path.exists():
        return
    for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if key not in ALLOWED_KEYS:
            continue
        if not separator:
            raise ValueError(f"dotenv line {index}: missing equals sign")
        value = value.strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end+1:].strip() and not value[end+1:].lstrip().startswith("#")):
                raise ValueError(f"dotenv line {index}: malformed quoted value")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        # Process environment takes precedence; no interpolation or shell execution.
        if key not in os.environ:
            os.environ[key] = value
