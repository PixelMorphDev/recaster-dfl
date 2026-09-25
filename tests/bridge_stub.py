# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Stand-in for ``main.py <op>`` in the bridge tests: the real ``core.interact``
and bridge, no TensorFlow. Usage: ``python tests/bridge_stub.py <op> <scenario>``.
"""
import multiprocessing
import os
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
    elif scenario == "crash":
        io.progress_bar("Extracting", 2)
        raise RuntimeError("boom")
    elif scenario == "exit2":
        exit(2)
