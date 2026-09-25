# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Control channel: polls ``control.jsonl`` by byte offset on a daemon thread.

Handles ``heartbeat``, ``stop`` and ``answer``. Training commands (save,
backup, preview, pause, ...) arrive with the training slice; until then they
are acknowledged with a ``warning`` event. Unknown commands are ignored with
a warning too. Also runs the heartbeat watchdog (no control line for
``heartbeat_s`` seconds -> stop), the parent watch (with a heartbeat set:
the parent pid changed, i.e. the caller died and this process was
reparented -> stop within ``parent_poll_s``) and emits ``alive`` every
``alive_s``.
"""

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .protocol import CONTROL_COMMANDS, KEY_ALLOWLIST, MAX_LINE_BYTES, EventWriter, parse_control_line

_NOT_YET_SUPPORTED = frozenset({"save", "backup", "preview", "next_preview", "prev_preview",
                                "pause", "resume", "key"})


class ControlReader(threading.Thread):
    def __init__(self, path: Path, writer: EventWriter, *,
                 on_stop: Callable[[bool, str], None],
                 heartbeat_s: float = 0, alive_s: float = 15, poll_s: float = 0.1,
                 parent_poll_s: float = 1.0, clock: Callable[[], float] = time.monotonic,
                 getppid: Callable[[], int] = os.getppid):
        super().__init__(name="recaster-bridge-control", daemon=True)
        self.path = Path(path)
        self._writer = writer
        self._on_stop = on_stop
        self._heartbeat_s = heartbeat_s
        self._alive_s = alive_s
        self._poll_s = poll_s
        self._clock = clock
        self._offset = 0
        self._buffer = b""
        self._discarding = False
        self._halt = threading.Event()
        self._answers: Dict[str, Any] = {}
        self._answer_cond = threading.Condition()
        self._last_contact = clock()
        self._last_alive = clock()
        self._parent_poll_s = parent_poll_s
        self._getppid = getppid
        self._parent_pid = getppid() if heartbeat_s else None
        self._last_parent_check = clock()
        self.invalid_lines = 0

    # -- public -------------------------------------------------------------

    def halt(self) -> None:
        self._halt.set()

    def wait_answer(self, prompt_id: str, timeout: Optional[float]) -> Tuple[bool, Any]:
        """Block until the caller answers ``prompt_id`` (timeout None = forever)."""
        deadline = None if not timeout else time.monotonic() + timeout
        with self._answer_cond:
            while prompt_id not in self._answers:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False, None
                self._answer_cond.wait(0.1 if remaining is None else min(remaining, 0.1))
            return True, self._answers.pop(prompt_id)

    # -- thread -------------------------------------------------------------

    def run(self) -> None:
        while not self._halt.is_set():
            self.poll_once()
            self._halt.wait(self._poll_s)

    def poll_once(self) -> None:
        for line in self._read_lines():
            cmd = parse_control_line(line)
            if cmd is None:
                self.invalid_lines += 1
                continue
            self._last_contact = self._clock()
            self._dispatch(cmd)
        now = self._clock()
        if self._heartbeat_s and now - self._last_contact > self._heartbeat_s:
            self._last_contact = now  # fire once
            self._writer.emit("warning", code="heartbeat_lost",
                              message=f"No heartbeat for {self._heartbeat_s:g}s; stopping")
            self._on_stop(True, "heartbeat")
        if self._parent_pid is not None and now - self._last_parent_check >= self._parent_poll_s:
            self._last_parent_check = now
            ppid = self._getppid()
            if ppid != self._parent_pid:
                self._writer.emit("warning", code="parent_lost",
                                  message=f"Parent process {self._parent_pid} exited (now {ppid}); stopping")
                self._parent_pid = None  # fire once
                self._on_stop(True, "parent_exited")
        if self._alive_s and now - self._last_alive >= self._alive_s:
            self._last_alive = now
            self._writer.emit("alive")

    def _read_lines(self):
        try:
            with open(self.path, "rb") as f:
                f.seek(self._offset)
                chunk = f.read()
        except OSError:
            return []
        if not chunk:
            return []
        self._offset += len(chunk)
        data = self._buffer + chunk
        *lines, self._buffer = data.split(b"\n")
        out = []
        for line in lines:
            if self._discarding:
                self._discarding = False  # tail of an oversized line
                continue
            if line.strip():
                out.append(line)
        if len(self._buffer) > MAX_LINE_BYTES:
            self._buffer = b""
            self._discarding = True
            self.invalid_lines += 1
        return out

    def _dispatch(self, cmd: dict) -> None:
        name = cmd["cmd"]
        if name == "heartbeat":
            return
        if name == "stop":
            self._on_stop(bool(cmd.get("save", False)), "control")
            return
        if name == "answer":
            prompt_id = cmd.get("id")
            if not isinstance(prompt_id, str):
                self._writer.emit("warning", code="bad_command", message="answer without an id")
                return
            with self._answer_cond:
                self._answers[prompt_id] = cmd.get("value")
                self._answer_cond.notify_all()
            return
        if name == "key" and cmd.get("key") not in KEY_ALLOWLIST:
            self._writer.emit("warning", code="bad_command", message=f"key not allowed: {cmd.get('key')!r}")
            return
        if name in _NOT_YET_SUPPORTED:
            self._writer.emit("warning", code="unsupported_command",
                              message=f"'{name}' is not supported for this operation")
            return
        if name not in CONTROL_COMMANDS:
            self._writer.emit("warning", code="unknown_command", message=f"unknown command {name!r}")
