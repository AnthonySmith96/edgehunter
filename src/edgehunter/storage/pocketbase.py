"""Restricted PocketBase projections. This client is never financial authority."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx


class PocketBaseClient:
    def __init__(self, url: str, *, token: str = "", timeout: float = 10.0) -> None:
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("PocketBase URL must not contain credentials, query or fragment")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("PocketBase requires HTTPS except on loopback")
        self.url = url.rstrip("/")
        self.token = token
        self.http = httpx.AsyncClient(base_url=self.url, timeout=timeout, trust_env=False)

    async def close(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not path.startswith("/api/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("PocketBase requests must use a canonical API path")
        response = await self.http.request(method, path, headers={"Authorization": self.token}, **kwargs)
        response.raise_for_status()
        result = response.json() if response.content else {}
        if not isinstance(result, dict):
            raise ValueError("PocketBase returned an invalid response object")
        return result

    async def login(self, email: str, password: str, *, collection: str = "service_accounts") -> dict[str, Any]:
        result = await self.request("POST", f"/api/collections/{collection}/auth-with-password", json={"identity": email, "password": password})
        return self._accept_auth(result)

    async def refresh(self, *, collection: str = "service_accounts") -> dict[str, Any]:
        result = await self.request("POST", f"/api/collections/{collection}/auth-refresh")
        return self._accept_auth(result)

    async def control_identity(self) -> str:
        """Read the designated actor through the authenticated control API, not admin login."""
        result = await self.request("GET", "/api/edgehunter/control-identity")
        actor = result.get("actor_id")
        if not isinstance(actor, str) or not re.fullmatch(r"[a-z0-9]{15}", actor):
            raise ValueError("PocketBase returned an invalid control actor")
        return actor

    def _accept_auth(self, result: dict[str, Any]) -> dict[str, Any]:
        token, record = result.get("token"), result.get("record")
        if not isinstance(token, str) or not token or not isinstance(record, dict):
            raise ValueError("PocketBase returned invalid authentication data")
        self.token = token
        return record

    async def list(self, collection: str, *, filter: str = "", page: int = 1, per_page: int = 200) -> dict[str, Any]:
        return await self.request("GET", f"/api/collections/{collection}/records", params={"filter": filter, "page": page, "perPage": per_page})

    async def get(self, collection: str, record_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/collections/{collection}/records/{record_id}")

    async def create(self, collection: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/api/collections/{collection}/records", json=payload)

    async def update(self, collection: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PATCH", f"/api/collections/{collection}/records/{record_id}", json=payload)

    async def upsert_projection(self, collection: str, business_id: str, payload: dict[str, Any], *, revision: int = 1, status: str = "", as_of: str | None = None) -> dict[str, Any]:
        """Business-key identity + monotonic revision makes retries/out-of-order safe."""
        if collection in {"approval_requests", "config_requests", "commands", "approval_audit", "service_accounts", "_superusers"}:
            raise ValueError("This collection is not an operational projection")
        record = {"business_id": business_id, "schema_version": 1, "revision": revision,
                  "payload": payload, "status": status, "as_of": as_of or datetime.now(UTC).isoformat()}
        # JSON quoting is for PocketBase filter syntax, never shell interpolation.
        query = "business_id = " + json.dumps(business_id)
        found = (await self.list(collection, filter=query))["items"]
        if found:
            if int(found[0]["revision"]) >= revision:
                return dict(found[0])
            return await self.update(collection, found[0]["id"], record)
        try:
            return await self.create(collection, record)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            # An equal-key concurrent writer may have won. Never mask other errors.
            found = (await self.list(collection, filter=query))["items"]
            if found and int(found[0]["revision"]) >= revision:
                return dict(found[0])
            raise

    def record_url(self, collection: str, record_id: str) -> str:
        if not all(re.fullmatch(r"[A-Za-z0-9_]+", value) for value in (collection, record_id)):
            raise ValueError("Invalid collection or record identity")
        return f"{self.url}/_/#/collections?" + urlencode({"collectionId": collection, "recordId": record_id})
