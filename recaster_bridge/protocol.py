# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""JSON-lines protocol v1: event writer and control-line parsing.

Event envelope (every line of events.jsonl):
    {"v": 1, "seq": <int from 1>, "ts": <unix float>, "run": "<run id>", "type": "<type>", ...}

Control envelope (every line of control.jsonl, written by the caller):
    {"v": 1, "seq": <int>, "cmd": "<cmd>", ...}
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

PROTOCOL = 1

EVENT_TYPES = frozenset({
    "hello", "phase", "progress", "iteration", "preview", "prompt", "answered",
    "state", "warning", "error", "alive", "done",
})

CONTROL_COMMANDS = frozenset({
    "heartbeat", "stop", "save", "backup", "preview", "next_preview", "prev_preview",
    "pause", "resume", "answer", "key",
})

KEY_ALLOWLIST = frozenset({"p", "s", "b", "enter", "space"})

ERROR_CODES = frozenset({"import_error", "gpu_init", "oom", "missing_input", "model_locked", "internal"})

DONE_STATUSES = frozenset({"ok", "error", "cancelled"})

MAX_LINE_BYTES = 1 << 20


def _json_default(value: Any) -> Any:
    # numpy scalars and paths show up in DFL values; keep the line valid JSON
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


class EventWriter:
    """Appends protocol events to ``events.jsonl``.

    A single fd opened with O_APPEND, one ``os.write`` per line, so a reader
    never sees two events interleaved. Thread-safe; ``seq`` is monotonic.
    """

    def __init__(self, path: Path, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self._lock = threading.Lock()
        self._seq = 0
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        self._fd: Optional[int] = os.open(str(self.path), flags, 0o600)

    def emit(self, type_: str, **fields: Any) -> int:
        """Write one event; returns its seq (0 if the writer is closed)."""
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type_}")
        with self._lock:
            if self._fd is None:
                return 0
            self._seq += 1
            event = {"v": PROTOCOL, "seq": self._seq, "ts": round(time.time(), 3),
                     "run": self.run_id, "type": type_}
            event.update(fields)
            line = json.dumps(event, default=_json_default, separators=(",", ":")) + "\n"
            data = line.encode("utf-8")
            if len(data) > MAX_LINE_BYTES:
                data = (json.dumps({"v": PROTOCOL, "seq": self._seq, "ts": event["ts"],
                                    "run": self.run_id, "type": "warning", "code": "event_too_large",
                                    "message": f"{type_} event dropped ({len(data)} bytes)"},
                                   separators=(",", ":")) + "\n").encode("utf-8")
            try:
                os.write(self._fd, data)
            except OSError:
                return 0
            return self._seq

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None


def parse_control_line(line: bytes) -> Optional[dict]:
    """A valid v1 control command, or None (malformed, wrong version, not an object)."""
    if len(line) > MAX_LINE_BYTES:
        return None
    try:
        data = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != PROTOCOL:
        return None
    if not isinstance(data.get("cmd"), str):
        return None
    return data
