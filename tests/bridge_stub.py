# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Stand-in for ``main.py <op>`` in the bridge tests: the real ``core.interact``
and bridge, no TensorFlow. Usage: ``python tests/bridge_stub.py <op> <scenario>``.

Scenario ``tree`` is a DFL run with a process tree to reap: a plain worker,
a worker that ignores SIGTERM and a worker with a (non-multiprocessing)
grandchild. Their pids, plus the resource tracker's, go to ``STUB_TREE_PIDS``
as JSON once all of them are up.

Scenario ``semlock`` holds a ``multiprocessing.Lock`` (a named POSIX
semaphore owned by the resource tracker) shared with one worker; the
semaphore's name and the tracker's pid go to ``STUB_SEM_INFO`` as JSON.
"""
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def child_report(path):
    # A spawned DFL worker process: re-imports core.interact with the same env
    sys.path.insert(0, str(ROOT))
    from core.interact import interact as child_io
    Path(path).write_text(type(child_io).__name__)
    time.sleep(30)


def worker_plain(ready):
    Path(ready).write_text(str(os.getpid()))
    time.sleep(120)


def worker_ignores_sigterm(ready):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(ready).write_text(str(os.getpid()))
    time.sleep(120)


def worker_with_grandchild(ready):
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(ready).write_text(str(grandchild.pid))
    time.sleep(120)


def worker_holding(lock, ready):
    Path(ready).write_text(str(os.getpid()))
    with lock:
        time.sleep(120)


def _write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def start_semlock(report):
    report = Path(report)
    lock = multiprocessing.Lock()
    ready = report.with_name(report.name + ".worker")
    proc = multiprocessing.Process(target=worker_holding, args=(lock, str(ready)), daemon=True)
    proc.start()
    deadline = time.time() + 60
    while not ready.exists() and time.time() < deadline:
        time.sleep(0.02)
    from multiprocessing import resource_tracker
    _write_json(report, {"name": lock._semlock.name, "worker": proc.pid,
                         "resource_tracker": resource_tracker._resource_tracker._pid})
    return lock


def start_tree(report):
    report = Path(report)
    workers = []
    for name, target in (("plain", worker_plain), ("ignores_sigterm", worker_ignores_sigterm),
                         ("grandchild", worker_with_grandchild)):
        ready = report.with_name(f"{report.name}.{name}")
        proc = multiprocessing.Process(target=target, args=(str(ready),), daemon=True)
        proc.start()
        workers.append((name, proc, ready))
    pids = {}
    deadline = time.time() + 60
    for name, proc, ready in workers:
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.02)
        pids[name] = proc.pid
        if name == "grandchild":
            pids["grandchild_of_worker"] = int(ready.read_text())
    from multiprocessing import resource_tracker
    tracker = getattr(getattr(resource_tracker, "_resource_tracker", None), "_pid", None)
    if tracker:
        pids["resource_tracker"] = tracker
    _write_json(report, pids)


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn")
    sys.path.insert(0, str(ROOT))
    from core.interact import interact as io

    scenario = sys.argv[2] if len(sys.argv) > 2 else "ok"

    if scenario == "ok":
        io.input_bool("Continue extraction?", False)
        io.input_int("Image size", 512, valid_range=[256, 2048])
        io.input_str("Face type", "wf", ["f", "wf", "head"])
        io.input_int("Max number of faces from image", 7)
        io.progress_bar("Extracting", 3)
        for _ in range(3):
            io.progress_bar_inc(1)
        io.progress_bar_close()
        io.log_err("something odd")
        exit(0)
    elif scenario == "ask":
        io.input_str("Face type", "wf", ["f", "wf", "head"])
        exit(0)
    elif scenario == "slow":
        child = multiprocessing.Process(target=child_report, args=(os.environ["STUB_CHILD_REPORT"],), daemon=True)
        child.start()
        Path(os.environ["STUB_CHILD_PID"]).write_text(str(child.pid))
        io.progress_bar("Extracting", 1000)
        for _ in range(1000):
            time.sleep(0.05)
            io.progress_bar_inc(1)
        exit(0)
    elif scenario == "tree":
        start_tree(os.environ["STUB_TREE_PIDS"])
        io.progress_bar("Extracting", 2400)
        for _ in range(2400):
            time.sleep(0.05)
            io.progress_bar_inc(1)
        exit(0)
    elif scenario == "semlock":
        held = start_semlock(os.environ["STUB_SEM_INFO"])  # noqa: F841 - keeps the lock alive
        io.progress_bar("Extracting", 2400)
        for _ in range(2400):
            time.sleep(0.05)
            io.progress_bar_inc(1)
        exit(0)
    elif scenario == "crash":
        io.progress_bar("Extracting", 2)
        raise RuntimeError("boom")
    elif scenario == "exit2":
        exit(2)
