"""Privacy-conscious JSONL experiment logging."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import as_jsonable


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
