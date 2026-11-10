"""Structured JSON request logs (one line per event) with request_id, duration, status, tokens."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

from app.config import log_dir

_lock = threading.Lock()


def new_request_id() -> str:
    return "req-" + uuid.uuid4().hex[:12]


def log_event(event: str, request_id: str = "-", **fields) -> dict:
    """Append a JSON line. Callers must pass already-masked text (see guardrails.mask_pii)."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
           "event": event, "request_id": request_id, **fields}
    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    with _lock, open(d / "app.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def recent_logs(n: int = 100) -> list[dict]:
    p = log_dir() / "app.jsonl"
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
