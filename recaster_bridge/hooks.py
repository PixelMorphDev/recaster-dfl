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

A stop (control, heartbeat lost, parent exited) also reaps the process
group when this process leads it (the Recaster runner starts DFL in its own
session): SIGTERM to every other member, ``REAP_TERM_GRACE_S`` to exit, then
SIGKILL. This process goes last: ``done`` is written and the event file
closed first, and only a member that survives SIGKILL (or a group that
can't be listed) makes it ``killpg(SIGKILL)`` its own group, itself included.

multiprocessing's resource tracker is left alone: it ignores SIGTERM, and a
SIGKILL would stop it from unlinking the named semaphores it tracks (on
macOS they persist until reboot). It reads EOF once this process and its
workers are gone, cleans up and exits on its own; if it doesn't, the
Recaster runner's group kill after this process exits covers it.
"""

import atexit
import builtins
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import List, Mapping, Optional

from . import (ANSWERS_FILE, CONTROL_FILE, ENV_HEARTBEAT, ENV_OWNER_PID, ENV_RUN_ID,
               EVENTS_FILE, read_version, run_dir_from_env)
from .answers import AnswerBook, load_answers
from .control import ControlReader
from .protocol import EventWriter

EXIT_CANCELLED = 130
REAP_TERM_GRACE_S = 2.0   # SIGTERM -> this long -> SIGKILL
REAP_KILL_WAIT_S = 1.0    # SIGKILL -> this long for the members to go
_REAP_POLL_S = 0.1
_PS_TIMEOUT_S = 5

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


def _has_proc() -> bool:
    return os.path.exists("/proc/self/stat")


def group_members(pgid: int, exclude: int) -> Optional[List[int]]:
    """Live pids in process group ``pgid`` other than ``exclude`` (zombies skipped).

    None when the group can't be listed. Linux reads ``/proc``; elsewhere
    ``ps`` (its own pid is left out: it runs in this group).
    """
    if _has_proc():
        out = []
        try:
            names = os.listdir("/proc")
        except OSError:
            return None
        for name in names:
            if not name.isdigit() or int(name) == exclude:
                continue
            try:
                with open(f"/proc/{name}/stat", "rb") as f:
                    stat = f.read().decode("utf-8", "replace")
            except OSError:
                continue  # exited meanwhile
            # "pid (comm) state ppid pgrp ...": comm may hold spaces and parens
            fields = stat.rpartition(")")[2].split()
            if len(fields) >= 3 and fields[0] not in ("Z", "X") and fields[2] == str(pgid):
                out.append(int(name))
        return out
    ps = "/bin/ps" if os.path.exists("/bin/ps") else "ps"
    try:
        proc = subprocess.Popen([ps, "-A", "-o", "pid=,pgid=,stat="], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        text, _ = proc.communicate(timeout=_PS_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = []
    for line in text.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid = int(parts[0])
        if int(parts[1]) == pgid and pid not in (exclude, proc.pid) and not parts[2].startswith("Z"):
            out.append(pid)
    return out


def _signal_all(pids: List[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def resource_tracker_pid() -> Optional[int]:
    """Pid of the resource tracker this process started, if any."""
    try:
        from multiprocessing import resource_tracker
    except ImportError:
        return None
    pid = getattr(getattr(resource_tracker, "_resource_tracker", None), "_pid", None)
    return pid if isinstance(pid, int) and pid > 0 else None


def _reapable(pgid: int, me: int, spare: Optional[int]) -> Optional[List[int]]:
    members = group_members(pgid, me)
    if members is None or spare is None:
        return members
    return [pid for pid in members if pid != spare]


def _wait_group_empty(pgid: int, me: int, spare: Optional[int], timeout: float) -> Optional[List[int]]:
    deadline = time.monotonic() + timeout
    while True:
        members = _reapable(pgid, me, spare)
        if not members or time.monotonic() >= deadline:
            return members
        time.sleep(_REAP_POLL_S)


def leads_process_group() -> bool:
    if os.name == "nt":
        return False
    try:
        return os.getpgrp() == os.getpid()
    except OSError:
        return False


def reap_process_group(term_grace_s: float = REAP_TERM_GRACE_S,
                       kill_wait_s: float = REAP_KILL_WAIT_S) -> bool:
    """SIGTERM, then SIGKILL, every other member of this process's group.

    Only when this process leads the group (otherwise the group belongs to
    whoever started it). The resource tracker is never signalled (see the
    module docstring). True when nothing else but it is left in the group.
    """
    if not leads_process_group():
        return True
    me = pgid = os.getpid()
    spare = resource_tracker_pid()
    members = _reapable(pgid, me, spare)
    if members is None:
        return False
    if not members:
        return True
    _signal_all(members, signal.SIGTERM)
    members = _wait_group_empty(pgid, me, spare, term_grace_s)
    if members is None:
        return False
    if members:
        _signal_all(members, signal.SIGKILL)
        members = _wait_group_empty(pgid, me, spare, kill_wait_s)
    return members == []


def kill_own_process_group() -> None:
    """Last resort: SIGKILL the whole group this process leads, itself included."""
    if leads_process_group():
        try:
            os.killpg(os.getpgrp(), signal.SIGKILL)
        except OSError:
            pass


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
        self._stop_thread: Optional[threading.Thread] = None
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

        Called from the control thread. ``done{cancelled}`` is written and the
        event file closed before any worker is signalled, so a crash that a
        dying worker causes in DFL's main thread can't turn it into
        ``done{error}``. Then DFL's worker processes are terminated (they are
        daemonic and would otherwise die with the parent only on a clean
        interpreter exit, which os._exit skips) and the rest of the process
        group is reaped (grandchildren, workers that ignore SIGTERM).
        """
        with self._done_lock:
            if self._stopping or self._done:
                return
            self._stopping = True
            self._stop_thread = threading.current_thread()
        self.writer.emit("state", state="stopping", reason=reason)
        self.finish("cancelled", EXIT_CANCELLED)
        self.control.halt()
        self.writer.close()  # nothing is written after done, even while the group is reaped
        for child in multiprocessing.active_children():
            try:
                child.terminate()
            except Exception:
                pass
        try:
            group_clean = reap_process_group()
        except Exception:
            group_clean = False
        if not group_clean:
            kill_own_process_group()
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
        stop_thread = self._stop_thread
        if stop_thread is not None and stop_thread is not threading.current_thread():
            # DFL's main thread ended while a stop reaps the group (a killed worker
            # can make it exit): let the stop finish; it ends the process
            stop_thread.join(REAP_TERM_GRACE_S + REAP_KILL_WAIT_S + 2 * _PS_TIMEOUT_S)
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
