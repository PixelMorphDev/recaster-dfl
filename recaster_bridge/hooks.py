# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Bridge session: activation guards, lifecycle events and exit hooks.

``activate()`` returns a session only in the process that ran ``main.py``:
DFL's Subprocessor children inherit ``RECASTER_BRIDGE=1`` and re-import
``core.interact``, and they must fall back to the stock interact without
writing events. The first activation records its pid in
``RECASTER_BRIDGE_OWNER_PID``; children inherit it and see a different pid.

The session guarantees exactly one ``done`` event for Python-level exits:
    - an uncaught exception  -> error{fatal} + done{error}
    - exit(code)             -> done{ok} for 0, done{error} otherwise (atexit)
    - control stop           -> state{stopping} + done{cancelled}, then the
                                process exits with EXIT_CANCELLED
"""

import atexit
import builtins
import multiprocessing
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Mapping, Optional

from . import (ANSWERS_FILE, CONTROL_FILE, ENV_HEARTBEAT, ENV_OWNER_PID, ENV_RUN_ID,
               EVENTS_FILE, read_version, run_dir_from_env)
from .answers import AnswerBook, load_answers
from .control import ControlReader
from .protocol import EventWriter

EXIT_CANCELLED = 130

_session: Optional["BridgeSession"] = None
_session_lock = threading.Lock()


def _classify(exc: BaseException) -> str:
    name = type(exc).__name__
    if isinstance(exc, ImportError):
        return "import_error"
    if isinstance(exc, MemoryError) or "ResourceExhausted" in name or "OutOfMemory" in name:
        return "oom"
    if isinstance(exc, FileNotFoundError):
        return "missing_input"
    return "internal"


def _read_dfl_commit(source_dir: Path) -> Optional[str]:
    """``commit:`` from the release artifact's SOURCE.txt (None in a git checkout)."""
    try:
        text = (source_dir / "SOURCE.txt").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "commit":
            return value.strip() or None
    return None


class BridgeSession:
    def __init__(self, run_dir: Path, run_id: str, *, heartbeat_s: float = 0,
                 op: Optional[str] = None, exit_func=os._exit):
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.op = op
        self.writer = EventWriter(self.run_dir / EVENTS_FILE, run_id)
        self.answers: AnswerBook = AnswerBook()
        self._exit_func = exit_func
        self._done_lock = threading.Lock()
        self._done = False
        self._exit_code: Optional[int] = None
        self._stopping = False
        self.control = ControlReader(self.run_dir / CONTROL_FILE, self.writer,
                                     on_stop=self.request_stop, heartbeat_s=heartbeat_s)

    # -- lifecycle ------------------------------------------------------------

    def start(self, source_dir: Path) -> None:
        version = read_version()
        self.writer.emit(
            "hello",
            protocol=version["protocol"],
            bridge_version=version["bridge"],
            dfl_commit=_read_dfl_commit(source_dir),
            op=self.op,
            pid=os.getpid(),
            python=sys.executable,
            tf_version=None,
        )
        self.answers, problems = load_answers(self.run_dir / ANSWERS_FILE)
        for problem in problems:
            self.writer.emit("warning", code="answers", message=problem)
        self.control.start()

    @property
    def is_done(self) -> bool:
        return self._done

    def finish(self, status: str, exit_code: int, **summary) -> bool:
        """Emit the single ``done`` event. False if it was already emitted."""
        with self._done_lock:
            if self._done:
                return False
            self._done = True
        self.writer.emit("done", status=status, exit_code=exit_code, summary=summary)
        return True

    def request_stop(self, save: bool, reason: str) -> None:
        """Stop the run. Non-training ops have nothing to save: cancel now.

        Called from the control thread. DFL's worker processes are terminated
        first (they are daemonic and would otherwise die with the parent only
        on a clean interpreter exit, which os._exit skips).
        """
        with self._done_lock:
            if self._stopping or self._done:
                return
            self._stopping = True
        self.writer.emit("state", state="stopping", reason=reason)
        for child in multiprocessing.active_children():
            try:
                child.terminate()
            except Exception:
                pass
        self.finish("cancelled", EXIT_CANCELLED)
        self.control.halt()
        self.writer.close()
        self._exit_func(EXIT_CANCELLED)

    # -- exit hooks -----------------------------------------------------------

    def install_hooks(self) -> None:
        previous_hook = sys.excepthook

        def excepthook(exc_type, exc, tb):
            if issubclass(exc_type, KeyboardInterrupt):
                self.finish("cancelled", EXIT_CANCELLED)
            else:
                self.writer.emit("error", code=_classify(exc), message=f"{exc_type.__name__}: {exc}",
                                 traceback="".join(traceback.format_exception(exc_type, exc, tb))[-8000:],
                                 fatal=True)
                self.finish("error", 1)
            previous_hook(exc_type, exc, tb)

        sys.excepthook = excepthook

        # exit(code) is how main.py ends; remember the code for the atexit hook
        for owner in (sys, builtins):
            original = getattr(owner, "exit", None)
            if original is None:
                continue
            setattr(owner, "exit", self._wrap_exit(original))

        atexit.register(self._atexit)

    def _wrap_exit(self, original):
        session = self

        def exit(code=None):  # noqa: A001 - mirrors the builtin
            session._exit_code = code
            return original(code)

        exit.__wrapped__ = original
        return exit

    def _atexit(self) -> None:
        code = self._exit_code
        if code is None or code is True:
            code_int = 0
        elif isinstance(code, int):
            code_int = code
        else:
            code_int = 1  # exit("message") prints it and exits 1
        self.finish("ok" if code_int == 0 else "error", code_int)
        self.control.halt()
        self.writer.close()


def _is_owner_process(env: Mapping[str, str]) -> bool:
    if multiprocessing.current_process().name != "MainProcess":
        return False
    owner = env.get(ENV_OWNER_PID)
    return owner is None or owner == str(os.getpid())


def activate(env: Optional[Mapping[str, str]] = None, *, source_dir: Optional[Path] = None,
             argv=None) -> Optional[BridgeSession]:
    """The process-wide bridge session, created on first call; None when inactive."""
    global _session
    env = os.environ if env is None else env
    with _session_lock:
        if _session is not None:
            return _session
        run_dir = run_dir_from_env(env)
        if run_dir is None or not _is_owner_process(env):
            return None
        os.environ[ENV_OWNER_PID] = str(os.getpid())
        argv = sys.argv if argv is None else argv
        op = argv[1] if len(argv) > 1 else None
        try:
            heartbeat_s = float(env.get(ENV_HEARTBEAT, "0") or 0)
        except ValueError:
            heartbeat_s = 0
        session = BridgeSession(run_dir, env.get(ENV_RUN_ID) or run_dir.name,
                                heartbeat_s=heartbeat_s, op=op)
        if source_dir is None:
            source_dir = Path(argv[0]).resolve().parent if argv else Path.cwd()
        session.install_hooks()
        session.start(source_dir)
        _session = session
        return session


def current_session() -> Optional[BridgeSession]:
    return _session
