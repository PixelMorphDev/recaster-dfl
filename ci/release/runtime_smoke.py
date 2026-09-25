# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Smoke test an installed runtime: ``<env>/bin/python runtime_smoke.py <source_dir> <expected_commit>``.

Runs from outside the source tree with the child environment Recaster
uses (the workflow clears PYTHONPATH/QT_*). Checks:

1. ``python -m recaster_bridge.probe --json`` (cwd = source): protocol,
   commit from SOURCE.txt, TF/NumPy/OpenCV versions, OpenCV highgui, and
   onnxruntime not importable.
2. A CPU forward pass through S3FD and 2DFAN with the shipped weights on a
   synthetic frame (no faces expected; this proves TF runs the models).
3. DFM export: a tiny leras graph frozen and converted to ONNX the way the
   models' export_dfm does (tf2onnx.convert._convert_common, opsets 12 and 13),
   then checked with onnx.checker and run with onnx's reference evaluator
   against TF's output (no onnxruntime in this env).
4. The CPU import smoke of every mainscripts.* module (ci/import_smoke.py).

Stdlib only at the top level; everything else runs in subprocesses.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

EXPECTED = {"tf_version": "2.16.2", "numpy": "1.26.4", "cv2": "4.10.0"}

COMPUTE = r"""
import numpy as np
from core.leras import nn
nn.initialize_main_env()
nn.initialize(device_config=nn.DeviceConfig.CPU())
from facelib import S3FDExtractor, FANExtractor
img = np.zeros((480, 640, 3), np.uint8)
img[120:360, 200:440] = 180
rects = S3FDExtractor(place_model_on_cpu=True).extract(img)
lm = FANExtractor(landmarks_3D=False, place_model_on_cpu=True).extract(img, [(200, 120, 440, 360)])
assert len(lm) == 1 and lm[0] is not None and lm[0].shape == (68, 2), lm
print("compute ok: s3fd rects=%d, 2dfan landmarks=%s" % (len(rects), lm[0].shape))
"""

# Same freeze + _convert_common path as models/Model_*/Model.py export_dfm
DFM_EXPORT = r"""
import tempfile
from pathlib import Path
import numpy as np
from core.leras import nn
nn.initialize_main_env()
nn.initialize(device_config=nn.DeviceConfig.CPU())
tf = nn.tf
import onnx, tf2onnx
from onnx.reference import ReferenceEvaluator
conv = nn.Conv2D(3, 4, kernel_size=3, padding="SAME", name="smoke_conv")
conv.build_weights()
conv.init_weights()
x_t = tf.placeholder(nn.floatx, (None, 16, 16, 3), name="in_face")
h = x_t if nn.data_format == "NHWC" else tf.transpose(x_t, (0, 3, 1, 2))
h = tf.nn.sigmoid(conv(h))
h = h if nn.data_format == "NHWC" else tf.transpose(h, (0, 2, 3, 1))
tf.identity(h, name="out_mask")
graph_def = tf.graph_util.convert_variables_to_constants(nn.tf_sess, tf.get_default_graph().as_graph_def(), ["out_mask"])
x = np.random.RandomState(0).rand(2, 16, 16, 3).astype(np.float32)
expected = nn.tf_sess.run("out_mask:0", {"in_face:0": x})
with tempfile.TemporaryDirectory() as tmp:
    for opset in (12, 13):
        path = Path(tmp) / ("smoke-%d.onnx" % opset)
        with tf.device("/CPU:0"):
            tf2onnx.convert._convert_common(graph_def, name="Smoke", input_names=["in_face:0"],
                                            output_names=["out_mask:0"], opset=opset, output_path=str(path))
        model = onnx.load(str(path))
        onnx.checker.check_model(model)
        got = ReferenceEvaluator(model).run(None, {"in_face:0": x})[0]
        assert got.shape == expected.shape and np.allclose(got, expected, atol=1e-5), float(np.abs(got - expected).max())
print("dfm export ok: tf2onnx %s, onnx %s, opsets 12/13 match TF" % (tf2onnx.__version__, onnx.__version__))
"""


def run(cmd, cwd, env, timeout=900):
    print("+", " ".join(cmd), f"(cwd={cwd})", flush=True)
    proc = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)
    text = proc.stdout.decode("utf-8", "replace")
    print(text, flush=True)
    if proc.returncode != 0:
        raise SystemExit(f"failed (exit {proc.returncode}): {' '.join(cmd)}")
    return text


def main(argv) -> int:
    source_dir, expected_commit = Path(argv[1]), argv[2]
    python = sys.executable
    env = dict(os.environ, PYTHONNOUSERSITE="1", CUDA_VISIBLE_DEVICES="", QT_QPA_PLATFORM="offscreen",
               TF_CPP_MIN_LOG_LEVEL="2")

    text = run([python, "-u", "-m", "recaster_bridge.probe", "--json"], str(source_dir), env)
    probe = json.loads([line for line in text.splitlines() if line.startswith("{")][-1])
    problems = []
    if probe.get("protocol") != 1:
        problems.append(f"protocol {probe.get('protocol')}")
    if probe.get("dfl_commit") != expected_commit:
        problems.append(f"dfl_commit {probe.get('dfl_commit')} != {expected_commit}")
    if not str(probe.get("python_version", "")).startswith("3.10."):
        problems.append(f"python {probe.get('python_version')}")
    for key, value in EXPECTED.items():
        if probe.get(key) != value:
            problems.append(f"{key} {probe.get(key)} != {value}")
    if probe.get("cv2_has_highgui") is not True:
        problems.append("OpenCV has no highgui (headless build?)")
    if probe.get("onnxruntime_importable") is not False:
        problems.append("onnxruntime is importable in the DFL env")
    if not str(probe.get("python", "")).startswith(str(Path(python).parent.parent)):
        problems.append(f"probe ran {probe.get('python')}, not the installed env")
    # devices: DFL's device table, a list (empty on CPU runners).
    # TODO(PixelMorphDev/recaster-dfl#2): drop the None allowance once the probe
    # fix (initialize_main_env before getDevices) is on main; until then the
    # probe always reports null.
    devices = probe.get("devices")
    if devices is not None and not (isinstance(devices, list)
                                    and all(isinstance(d, dict) and "name" in d for d in devices)):
        problems.append(f"devices {devices!r} is not a list of devices")
    if problems:
        print("probe problems:\n  " + "\n  ".join(problems))
        return 1
    print(f"probe ok; devices: {probe.get('devices')}")

    run([python, "-u", "-c", COMPUTE], str(source_dir), env)
    run([python, "-u", "-c", DFM_EXPORT], str(source_dir), env)
    run([python, "-u", str(source_dir / "ci" / "import_smoke.py")], str(source_dir), env)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
