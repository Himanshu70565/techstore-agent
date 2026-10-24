"""Privacy-conscious JSONL experiment logging."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import as_jsonable


class ExperimentLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, event_type: str, **data: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **as_jsonable(data),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=True) + "\n")


class TerminalTrace:
    """Readable terminal trace for model, validation, and browser boundaries."""

    def __init__(self) -> None:
        self.gemini_requests = 0
        self.proposed = 0
        self.approved = 0
        self.rejected = 0
        self.executed = 0
        self.errors: list[str] = []

    def section(self, title: str, step: int, **data: Any) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        print(f"\n========== STEP {step}: {title} [{timestamp}] ==========")
        for key, value in data.items():
            print(f"{key}:")
            if isinstance(value, (dict, list, tuple)):
                print(json.dumps(as_jsonable(value), indent=2, ensure_ascii=True))
            else:
                print(value)

    def error(self, message: str) -> None:
        self.errors.append(message)
        print(f"ERROR: {message}")
