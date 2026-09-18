"""Verify the configured key with the provider's model-list GET; no inference request."""
from __future__ import annotations

import json
import os
import ssl
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edgehunter.intelligence.environment import load_typesafe_env  # noqa: E402
from edgehunter.research.btc_challenger import write_report  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    load_typesafe_env(ROOT / ".env")
    key = os.environ.get("TYPESAFE_API_KEY", "")
    report: dict = {"key_present": bool(key), "inference_requested": False,
                    "authentication_verified": False, "endpoint": "GET /v1/models"}
    if not key:
        report["status"] = "TYPESAFE_API_KEY_MISSING"
    else:
        try:
            with httpx.Client(timeout=10, verify=ssl.create_default_context(), follow_redirects=False) as client:
                response = client.get("https://api.typesafe.ai/v1/models",
                                      headers={"Authorization": "Bearer " + key})
            report["http_status"] = response.status_code
            if response.status_code == 200:
                payload = response.json()
                names = [item["name"] for item in payload["models"]]
                if not all(isinstance(name, str) and name.startswith("jev-") for name in names):
                    raise ValueError("unexpected model list")
                report.update(status="AUTHENTICATED_MODEL_LIST", authentication_verified=True, models=names)
            else:
                report["status"] = "MODEL_LIST_HTTP_ERROR"
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            report["status"] = "MODEL_LIST_UNAVAILABLE"
    write_report(ROOT / "reports/jev_access.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
