# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Bridged ``core.interact``: prompts answered from answers.json, progress as events.

``core/interact/interact.py`` calls ``make_interact(InteractDesktop)`` when
``RECASTER_BRIDGE=1``. Outside the bridge's owner process (DFL's worker
processes) or without a usable run dir it returns a plain ``InteractDesktop``.

Terminal output is unchanged: tqdm bars and log lines still go to stdout,
which the caller tees to ``stdout.log``. Windows (``show_image``, manual
extract, the XSeg editor) behave as upstream; the headless preview mapping
arrives with the training slice.
"""

import itertools
import time
from typing import Any, Callable, Optional

from .answers import POLICY_ASK
from .hooks import BridgeSession, activate

PROGRESS_INTERVAL_S = 0.1  # progress events are throttled to 10 Hz

_UNITS = {"extract": "images", "merge": "frames", "sort": "images", "xseg": "faces"}

_bridge_classes = {}


def make_interact(desktop_cls):
    """The interact singleton for ``core.interact``."""
    session = activate()
    if session is None:
        return desktop_cls()
    return bridge_class(desktop_cls)(session)


def bridge_class(desktop_cls):
    cls = _bridge_classes.get(desktop_cls)
    if cls is None:
        cls = type("InteractBridge", (_BridgeMixin, desktop_cls), {})
        _bridge_classes[desktop_cls] = cls
    return cls


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, str):
        return {"y": True, "yes": True, "true": True, "n": False, "no": False,
                "false": False}.get(value.strip().lower(), default)
    return bool(value)


class _BridgeMixin:
    """Overrides for InteractBase's input and progress methods."""

    def __init__(self, session: BridgeSession, clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self._bridge = session
        self._clock = clock
        self._prompt_ids = itertools.count(1)
        self._pg_state: Optional[list] = None  # [desc, current, total]
        self._pg_last_emit = 0.0

    # -- progress -----------------------------------------------------------

    def _unit(self) -> str:
        return _UNITS.get(self._bridge.op or "", "files")

    def _emit_progress(self, force: bool = False) -> None:
        state = self._pg_state
        if state is None:
            return
        now = self._clock()
        if not force and now - self._pg_last_emit < PROGRESS_INTERVAL_S:
            return
        self._pg_last_emit = now
        desc, current, total = state
        fields = {"current": int(current), "total": int(total or 0), "unit": self._unit()}
        if desc:
            fields["desc"] = str(desc)
        self._bridge.writer.emit("progress", **fields)

    def progress_bar(self, desc, total, leave=True, initial=0):
        had_bar = self.pg_bar is not None
        super().progress_bar(desc, total, leave=leave, initial=initial)
        if not had_bar:
            self._pg_state = [desc, initial or 0, total]
            self._emit_progress(force=True)

    def progress_bar_inc(self, c):
        super().progress_bar_inc(c)
        if self._pg_state is not None:
            self._pg_state[1] += c
            self._emit_progress()

    def progress_bar_close(self):
        self._emit_progress(force=True)
        self._pg_state = None
        super().progress_bar_close()

    def progress_bar_generator(self, data, desc=None, leave=True, initial=0):
        try:
            total = len(data)
        except TypeError:
            total = 0
        self._pg_state = [desc, initial or 0, total]
        self._emit_progress(force=True)
        for x in super().progress_bar_generator(data, desc=desc, leave=leave, initial=initial):
            yield x
            if self._pg_state is not None:
                self._pg_state[1] += 1
                self._emit_progress()
        self._emit_progress(force=True)
        self._pg_state = None

    # -- logging ------------------------------------------------------------

    def log_err(self, msg, end='\n'):
        super().log_err(msg, end=end)
        text = str(msg).strip()
        if text:
            self._bridge.writer.emit("warning", code="dfl_error", message=text[:4000])

    # -- prompts ------------------------------------------------------------

    def _resolve(self, kind: str, text: str, default: Any, convert: Callable[[Any], Any],
                 **meta) -> Any:
        """Answer one prompt; always emits ``answered``. Never raises."""
        book = self._bridge.answers
        answer = book.find(text)
        source, key = "default", None
        value = default
        if answer is not None:
            source, key = "answers", answer.key
            value = self._convert(convert, answer.value, default)
        elif book.policy == POLICY_ASK:
            prompt_id = f"p{next(self._prompt_ids)}"
            fields = {"id": prompt_id, "kind": kind, "text": text, "default": default}
            fields.update({k: v for k, v in meta.items() if v is not None})
            self._bridge.writer.emit("prompt", **fields)
            got, raw = self._bridge.control.wait_answer(prompt_id, book.ask_timeout_s or None)
            if got:
                source = "client"
                value = self._convert(convert, raw, default)
        event = {"text": text, "value": value, "source": source}
        if key is not None:
            event["key"] = key
        self._bridge.writer.emit("answered", **event)
        print(f"{text.strip()} : {value}")
        return value

    @staticmethod
    def _convert(convert: Callable[[Any], Any], raw: Any, default: Any) -> Any:
        try:
            return convert(raw)
        except Exception:
            return default

    def input(self, s):
        return self._resolve("any", s, "", lambda v: str(v) if v else "")

    def input_bool(self, s, default_value, help_message=None):
        return self._resolve("bool", s, default_value, lambda v: _to_bool(v, default_value))

    def input_int(self, s, default_value, valid_range=None, valid_list=None, add_info=None,
                  show_default_value=True, help_message=None):
        def convert(v):
            i = int(v)
            if valid_range is not None:
                i = int(min(max(i, valid_range[0]), valid_range[1]))
            if valid_list is not None and i not in valid_list:
                return default_value
            return i
        return self._resolve("int", s, default_value, convert,
                             range=list(valid_range) if valid_range is not None else None,
                             choices=list(valid_list) if valid_list is not None else None)

    def input_number(self, s, default_value, valid_list=None, show_default_value=True,
                     add_info=None, help_message=None):
        def convert(v):
            f = float(v)
            if valid_list is not None and f not in valid_list:
                return default_value
            return f
        return self._resolve("number", s, default_value, convert,
                             choices=list(valid_list) if valid_list is not None else None)

    def input_str(self, s, default_value=None, valid_list=None, show_default_value=True,
                  help_message=None):
        def convert(v):
            result = "" if v is None else str(v)
            if valid_list is not None and result.lower() not in [x.lower() for x in valid_list]:
                return default_value
            return result
        return self._resolve("str", s, default_value, convert,
                             choices=list(valid_list) if valid_list is not None else None)

    def input_in_time(self, str, max_time_sec):  # noqa: A002 - upstream's parameter name
        return self._resolve("in_time", str, False, lambda v: _to_bool(v, False))

    def input_skip_pending(self):
        # stdin is not a terminal under the bridge; upstream spawns a process to drain it
        pass
