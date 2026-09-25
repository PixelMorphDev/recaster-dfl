# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Describe the env this interpreter runs in, for the lock: ``<env>/bin/python env_info.py <platform>``.

Stdlib only (it runs inside the packed env). Prints one JSON object:
``python``, ``pins`` (the versions the lock records), ``min_os`` (macOS: the
highest ``macosx_X_Y`` wheel tag installed) and ``min_driver`` (CUDA 12:
the driver floor of the CUDA 12 wheels). Exits 1 if a forbidden package is
installed or a required pin is missing or off.
"""

import json
import re
import sys
from importlib import metadata

FORBIDDEN = ("onnxruntime", "onnxruntime-gpu", "insightface", "opencv-python-headless", "torch")

# name -> exact version required (None: record whatever is installed)
PINS = {
    "linux-x86_64-cuda12": {"tensorflow": "2.16.2", "numpy": "1.26.4", "opencv-python": "4.10.0.84",
                            "nvidia-cudnn-cu12": None},
    "macos-arm64-metal": {"tensorflow": "2.16.2", "tensorflow-metal": "1.2.0", "numpy": "1.26.4",
                          "opencv-python": "4.10.0.84"},
}
MIN_DRIVER = {"linux-x86_64-cuda12": "525.60"}   # CUDA 12.x minor-version compatibility floor (design 4.6)
MIN_MACOS = (11, 0)                               # conda-forge osx-arm64 baseline


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed() -> dict:
    out = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            out[_norm(name)] = dist
    return out


def _min_macos(dists) -> str:
    best = MIN_MACOS
    for dist in dists:
        wheel = dist.read_text("WHEEL") or ""
        for match in re.finditer(r"^Tag:.*-macosx_(\d+)_(\d+)_", wheel, re.MULTILINE):
            best = max(best, (int(match.group(1)), int(match.group(2))))
    return f"{best[0]}.{best[1]}"


def main(argv) -> int:
    platform = argv[1]
    installed = _installed()
    problems = [f"forbidden package installed: {name}" for name in FORBIDDEN if _norm(name) in installed]
    pins = {}
    for name, required in PINS[platform].items():
        dist = installed.get(_norm(name))
        if dist is None:
            problems.append(f"missing: {name}")
            continue
        pins[name] = dist.version
        if required is not None and dist.version != required:
            problems.append(f"{name} {dist.version}, expected {required}")
    if platform == "linux-x86_64-cuda12" and not pins.get("nvidia-cudnn-cu12", "").startswith("8.9."):
        problems.append(f"nvidia-cudnn-cu12 {pins.get('nvidia-cudnn-cu12')}, expected 8.9.x")
    info = {"python": ".".join(str(v) for v in sys.version_info[:3]), "pins": pins}
    if platform in MIN_DRIVER:
        info["min_driver"] = MIN_DRIVER[platform]
    if platform.startswith("macos-"):
        info["min_os"] = _min_macos(installed.values())
    for problem in problems:
        print(problem, file=sys.stderr)
    print(json.dumps(info))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
