# recaster-dfl CI: CPU import smoke for the DFL entry-point modules.
#
# Run from the repo root:  python ci/import_smoke.py
# Imports every mainscripts.* module (the targets of `main.py <op>`) plus the
# model packages, then asserts the forbidden packages are not importable in
# this env. Does not load weights or initialize a device.
import importlib
import importlib.util
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

MODULES = sorted(
    f"mainscripts.{p.stem}" for p in (ROOT / "mainscripts").glob("*.py")
    if p.stem not in ("__init__", "dev_misc")
) + ["models", "facelib", "samplelib", "DFLIMG", "merger", "XSegEditor.XSegEditor"]

FORBIDDEN = ["onnxruntime", "insightface", "torch"]


def main() -> int:
    failures = []
    for name in MODULES:
        try:
            importlib.import_module(name)
            print(f"ok      {name}")
        except Exception:
            failures.append(name)
            print(f"FAIL    {name}\n{traceback.format_exc()}")

    for name in FORBIDDEN:
        if importlib.util.find_spec(name) is not None:
            failures.append(f"forbidden:{name}")
            print(f"FAIL    forbidden package importable: {name}")

    try:
        from importlib.metadata import distribution, PackageNotFoundError
        try:
            distribution("opencv-python-headless")
            failures.append("forbidden:opencv-python-headless")
            print("FAIL    forbidden dist installed: opencv-python-headless")
        except PackageNotFoundError:
            pass
    except ImportError:
        pass

    print(f"\n{len(MODULES) - sum(1 for f in failures if not f.startswith('forbidden:'))}/{len(MODULES)} modules imported")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
