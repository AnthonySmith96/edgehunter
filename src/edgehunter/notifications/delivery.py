"""Email cannot approve trading. Sink delivery is explicitly simulated."""
from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx


class Transport(Protocol):
    async def send(self, message: dict[str, Any]) -> tuple[str, str]: ...


@dataclass
class FileSink:
    directory: Path

    async def send(self, message: dict[str, Any]) -> tuple[str, str]:
        if not isinstance(message.get("message_key"), str) or not re.fullmatch(r"[a-f0-9]{64}", message["message_key"]):
            raise ValueError("Sink message key must be a SHA256 identifier")
        self.directory = Path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{message['message_key']}.json"
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps({**message, "transport": "FILE_SINK_SIMULATED", "financial_action": "NONE"}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
        return "DELIVERED", f"sink:{message['message_key']}"


class BrevoTransport:
    """Adapter tested without spending; requires independently authorized config."""
    def __init__(self, *, api_key: str, sender: str, recipient: str, sending_authorized: bool = False,
                 recipient_confirmed: bool = False, client: httpx.AsyncClient | None = None) -> None:
        if not sending_authorized or not recipient_confirmed:
            raise PermissionError("Brevo sending and recipient must be explicitly authorized")
        if not api_key or not sender or not recipient:
            raise ValueError("Brevo credentials and confirmed sender/recipient required")
        self.api_key, self.sender, self.recipient = api_key, sender, recipient
        self.client = client or httpx.AsyncClient(timeout=15, verify=ssl.create_default_context(), trust_env=False)

    async def send(self, message: dict[str, Any]) -> tuple[str, str]:
        try:
            response = await self.client.post("https://api.brevo.com/v3/smtp/email",
                headers={"api-key": self.api_key, "Accept": "application/json"},
                json={"sender": {"email": self.sender}, "to": [{"email": self.recipient}],
                      "subject": message["subject"], "textContent": message["text"],
                      "htmlContent": message["html"], "headers": {"X-EdgeHunter-Key": message["message_key"]}})
            response.raise_for_status()
            try:
                result = response.json()
                provider_id = result.get("messageId") if isinstance(result, dict) else None
            except ValueError as exc:
                raise DeliveryUnknown("Brevo accepted an unparseable response; reconcile before retry") from exc
            if not isinstance(provider_id, str) or not provider_id:
                raise DeliveryUnknown("Brevo acceptance lacks a message ID; reconcile before retry")
            return "SENT", provider_id
        except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as exc:
            raise DeliveryUnknown("Brevo acceptance unknown; review before retry") from exc

    async def close(self) -> None:
        await self.client.aclose()


class DeliveryUnknown(RuntimeError):
    pass


class NotificationOutbox:
    def __init__(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS notifications (
          message_key TEXT PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE,
          category TEXT NOT NULL, subject TEXT NOT NULL, text TEXT NOT NULL, html TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'QUEUED', attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt REAL NOT NULL DEFAULT 0, provider_id TEXT, last_error TEXT,
          acknowledged INTEGER NOT NULL DEFAULT 0, resolved INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL, sent_at REAL);
        """)
        # A prior process may have stopped after transport accepted. Do not retry automatically.
        with self.connection:
            self.connection.execute("UPDATE notifications SET state='UNKNOWN', last_error='PROCESS_INTERRUPTED_DURING_SEND' WHERE state='SENDING'")

    def enqueue(self, dedup_key: str, category: str, subject: str, text: str, *, record_url: str | None = None,
                secrets_to_redact: tuple[str, ...] = ()) -> str:
        if category not in {"CRITICAL", "ACTION_REQUIRED", "DAILY_DIGEST", "WEEKLY_RESEARCH"}:
            raise ValueError("Unsupported notification category")
        if not dedup_key or len(text) > 100_000 or len(subject) > 300 or "\n" in subject or "\r" in subject:
            raise ValueError("Invalid notification length or header")
        for secret in secrets_to_redact:
            if secret:
                subject, text = subject.replace(secret, "[REDACTED]"), text.replace(secret, "[REDACTED]")
        escaped = "<pre>" + html.escape(text) + "</pre>"
        if record_url:
            url = urlsplit(record_url)
            link_parameters = (url.query + " " + url.fragment).lower()
            if (url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password
                or any(word in link_parameters for word in ("token", "password", "api_key", "api-key", "secret"))
                or any(secret and secret in record_url for secret in secrets_to_redact)):
                raise ValueError("Administrative links must not contain credentials")
            escaped += '<p><a href="' + html.escape(record_url, quote=True) + '">Abrir solicitud</a></p>'
            text += "\n\nAcceso: " + record_url
        text += "\n\nAbrir este mensaje no ejecuta ninguna operación."
        escaped += "<p>Abrir este mensaje no ejecuta ninguna operación.</p>"
        key = hashlib.sha256(dedup_key.encode()).hexdigest()
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO notifications(message_key,dedup_key,category,subject,text,html,created_at) VALUES(?,?,?,?,?,?,?)",
                (key, dedup_key, category, subject, text, escaped, time.time()))
        return key

    async def deliver(self, transport: Transport, *, limit: int = 50, now: float | None = None) -> dict[str, int]:
        now = time.time() if now is None else now
        counts = {"delivered": 0, "sent": 0, "failed": 0, "unknown": 0}
        records = self.connection.execute("SELECT * FROM notifications WHERE state IN ('QUEUED','FAILED') AND next_attempt<=? ORDER BY CASE category WHEN 'CRITICAL' THEN 0 ELSE 1 END,created_at LIMIT ?", (now, limit)).fetchall()
        for record in records:
            with self.connection:
                claimed = self.connection.execute("UPDATE notifications SET state='SENDING',attempts=attempts+1 WHERE message_key=? AND state IN ('QUEUED','FAILED')", (record["message_key"],)).rowcount
            if not claimed:
                continue
            try:
                state, provider_id = await transport.send(dict(record))
                if state not in {"SENT", "DELIVERED"}:
                    raise ValueError("Transport returned invalid evidence state")
                with self.connection:
                    self.connection.execute("UPDATE notifications SET state=?,provider_id=?,sent_at=?,last_error=NULL WHERE message_key=?", (state, provider_id, now, record["message_key"]))
                counts[state.lower()] += 1
            except Exception as exc:
                state = "UNKNOWN" if isinstance(exc, DeliveryUnknown) else "FAILED"
                attempts = int(record["attempts"]) + 1
                with self.connection:
                    self.connection.execute("UPDATE notifications SET state=?,last_error=?,next_attempt=? WHERE message_key=?", (state, type(exc).__name__, now + min(3600, 2 ** min(attempts, 10)), record["message_key"]))
                counts[state.lower()] += 1
        return counts

    def delivery_event(self, provider_id: str, status: str, *, authenticated: bool) -> None:
        if not authenticated:
            raise PermissionError("Delivery webhook requires verified source authentication")
        if status not in {"DELIVERED", "BOUNCED", "FAILED"}:
            raise ValueError("Invalid delivery event")
        with self.connection:
            self.connection.execute("UPDATE notifications SET state=? WHERE provider_id=? AND state IN ('SENT','UNKNOWN')", (status, provider_id))

    def acknowledge(self, key: str, *, resolved: bool = False) -> None:
        with self.connection:
            self.connection.execute("UPDATE notifications SET acknowledged=1,resolved=? WHERE message_key=?", (int(resolved), key))

    def records(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM notifications ORDER BY created_at")]

    def close(self) -> None:
        self.connection.close()
