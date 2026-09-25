# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Runtime probe: ``python -u -m recaster_bridge.probe --json`` (cwd = DFL source).

Prints one JSON line with the bridge and library versions. ``--no-devices``
skips TensorFlow device enumeration (fast, works without a GPU driver).
Every field degrades to null instead of failing, so a broken env still
produces a diagnosable line; the exit code is 0 when TensorFlow imports.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from . import read_version
from .hooks import _read_dfl_commit


def _version(module_name: str):
    try:
        module = __import__(module_name)
    except Exception:
        return None
    return getattr(module, "__version__", None)


def _cv2_has_highgui():
    try:
        import cv2
        info = cv2.getBuildInformation()
    except Exception:
        return None
    for line in info.splitlines():
        stripped = line.strip()
        if stripped.startswith("GUI:"):
            return stripped.split(":", 1)[1].strip().upper() not in ("", "NONE", "NO")
    return None


def _devices():
    """DFL's device table, as the trainer sees it; None (with the reason on stderr) if it fails.

    initialize_main_env() enumerates in a multiprocessing child and pops
    CUDA_VISIBLE_DEVICES first, like DFL's own main process; getDevices()
    raises until it has run. Call it before this process imports TensorFlow
    (a fork after TF has started its threads can hang the child), and keep the
    __main__ guard below (spawn start method on macOS/Windows). Skipped without
    an importable TensorFlow, since the child could only fail; a child that dies
    or hangs makes initialize_main_env() raise (NN_DEVICES_TIMEOUT_S, default 120).
    """
    if importlib.util.find_spec("tensorflow") is None:
        print("recaster_bridge.probe: device enumeration skipped: tensorflow is not importable",
              file=sys.stderr, flush=True)
        return None
    try:
        from core.leras import device
        device.Devices.initialize_main_env()
        return [{"index": d.index, "name": d.name, "total_mem_gb": round(d.total_mem_gb, 2)}
                for d in device.Devices.getDevices()]
    except Exception as e:
        print(f"recaster_bridge.probe: device enumeration failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return None


def probe(with_devices: bool = True) -> dict:
    # Devices first: DFL's order (device init, then TensorFlow in this process)
    devices = _devices() if with_devices else None
    version = read_version()
    return {
        "protocol": version["protocol"],
        "bridge_version": version["bridge"],
        "dfl_commit": _read_dfl_commit(Path.cwd()),
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "tf_version": _version("tensorflow"),
        "numpy": _version("numpy"),
        "cv2": _version("cv2"),
        "cv2_has_highgui": _cv2_has_highgui(),
        "onnxruntime_importable": importlib.util.find_spec("onnxruntime") is not None,
        "devices": devices,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="recaster_bridge.probe")
    parser.add_argument("--json", action="store_true", help="print one JSON line (the default)")
    parser.add_argument("--no-devices", action="store_true", help="skip device enumeration")
    args = parser.parse_args(argv)
    result = probe(with_devices=not args.no_devices)
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result["tf_version"] else 1


if __name__ == "__main__":
    sys.exit(main())
