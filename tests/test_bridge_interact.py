# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""The bridge wired into the real ``core.interact``, run in subprocesses.

Needs DFL's interact deps (numpy, opencv-python, tqdm, colorama); no
TensorFlow. ``tests/bridge_stub.py`` stands in for ``main.py <op>``.
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = ROOT / "tests" / "bridge_stub.py"

EXTRACT_ANSWERS = {"v": 1, "policy": "answers_then_default", "ask_timeout_s": 0, "answers": [
    {"key": "continue_extraction", "match": ["Continue extraction"], "value": True, "kind": "bool"},
    {"key": "image_size", "match": ["Image size"], "value": 4096, "kind": "int"},
    {"key": "face_type", "match": ["Face type"], "value": "head", "kind": "str"},
]}


def _base_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("RECASTER_")}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _events(run_dir):
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class BridgeRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run"
        self.run_dir.mkdir()
        (self.run_dir / "control.jsonl").write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def _env(self, **extra):
        env = _base_env()
        env.update({"RECASTER_BRIDGE": "1", "RECASTER_RUN_DIR": str(self.run_dir),
                    "RECASTER_RUN_ID": "run-test", "RECASTER_PROTOCOL": "1"})
        env.update(extra)
        return env

    def _run(self, scenario, answers=EXTRACT_ANSWERS, env=None, timeout=60):
        if answers is not None:
            (self.run_dir / "answers.json").write_text(json.dumps(answers))
        return subprocess.run([sys.executable, "-u", str(STUB), "extract", scenario], cwd=str(ROOT),
                              env=env or self._env(), capture_output=True, text=True, timeout=timeout)

    def test_disabled_is_stock_interact(self):
        """RECASTER_BRIDGE unset: the upstream InteractDesktop, bridge never imported."""
        code = ("import sys; sys.path.insert(0, '.'); from core.interact import interact as io; "
                "print(type(io).__name__, 'recaster_bridge' in sys.modules)")
        env = _base_env()
        env["RECASTER_RUN_DIR"] = str(self.run_dir)  # a run dir alone doesn't activate it
        out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.split(), ["InteractDesktop", "False"], out.stderr)
        self.assertFalse((self.run_dir / "events.jsonl").exists())

    def test_disabled_stub_reads_stdin_as_upstream(self):
        env = _base_env()
        proc = subprocess.run([sys.executable, "-u", str(STUB), "extract", "ask"], cwd=str(ROOT),
                              env=env, input="head\n", capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("head", proc.stdout)
        self.assertEqual(_events(self.run_dir), [])

    def test_happy_path_events(self):
        proc = self._run("ok")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        events = _events(self.run_dir)
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "hello")
        self.assertEqual(types[-1], "done")
        hello = events[0]
        self.assertEqual((hello["protocol"], hello["bridge_version"], hello["op"], hello["run"]),
                         (1, "1.1.0", "extract", "run-test"))
        answered = [e for e in events if e["type"] == "answered"]
        self.assertEqual([(a["text"], a["value"], a["source"]) for a in answered], [
            ("Continue extraction?", True, "answers"),
            ("Image size", 2048, "answers"),          # clipped to valid_range like DFL
            ("Face type", "head", "answers"),
            ("Max number of faces from image", 7, "default"),
        ])
        progress = [(e["current"], e["total"]) for e in events if e["type"] == "progress"]
        self.assertEqual(progress[0], (0, 3))
        self.assertEqual(progress[-1], (3, 3))
        self.assertIn("dfl_error", [e.get("code") for e in events if e["type"] == "warning"])
        self.assertEqual((events[-1]["status"], events[-1]["exit_code"]), ("ok", 0))
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        self.assertIn("Extracting", proc.stdout + proc.stderr)  # tqdm output is unchanged

    def test_nonzero_exit_is_done_error(self):
        proc = self._run("exit2")
        self.assertEqual(proc.returncode, 2)
        done = _events(self.run_dir)[-1]
        self.assertEqual((done["type"], done["status"], done["exit_code"]), ("done", "error", 2))

    def test_crash_emits_fatal_error_then_done(self):
        proc = self._run("crash")
        self.assertEqual(proc.returncode, 1)
        events = _events(self.run_dir)
        error = [e for e in events if e["type"] == "error"][0]
        self.assertTrue(error["fatal"])
        self.assertIn("RuntimeError: boom", error["message"])
        self.assertIn("Traceback", error["traceback"])
        self.assertEqual([e["type"] for e in events].count("done"), 1)
        self.assertEqual(events[-1]["status"], "error")
        self.assertIn("RuntimeError: boom", proc.stderr)  # the default hook still prints

    def test_ask_policy_waits_for_control_answer(self):
        (self.run_dir / "answers.json").write_text(json.dumps(
            {"v": 1, "policy": "answers_then_ask", "ask_timeout_s": 30, "answers": []}))
        proc = subprocess.Popen([sys.executable, "-u", str(STUB), "extract", "ask"], cwd=str(ROOT),
                                env=self._env(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.time() + 30
            prompt = None
            while time.time() < deadline and prompt is None:
                prompt = next((e for e in _events(self.run_dir) if e["type"] == "prompt"), None)
                time.sleep(0.05)
            self.assertIsNotNone(prompt)
            self.assertEqual((prompt["kind"], prompt["default"], prompt["choices"]),
                             ("str", "wf", ["f", "wf", "head"]))
            with open(self.run_dir / "control.jsonl", "a") as f:
                f.write(json.dumps({"v": 1, "seq": 1, "cmd": "answer", "id": prompt["id"], "value": "f"}) + "\n")
            self.assertEqual(proc.wait(30), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stderr.close()
        answered = [e for e in _events(self.run_dir) if e["type"] == "answered"][0]
        self.assertEqual((answered["value"], answered["source"]), ("f", "client"))

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_stop_cancels_and_terminates_worker_processes(self):
        child_report = self.run_dir / "child.txt"
        child_pid_file = self.run_dir / "child.pid"
        env = self._env(STUB_CHILD_REPORT=str(child_report), STUB_CHILD_PID=str(child_pid_file))
        (self.run_dir / "answers.json").write_text(json.dumps(EXTRACT_ANSWERS))
        proc = subprocess.Popen([sys.executable, "-u", str(STUB), "extract", "slow"], cwd=str(ROOT),
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            deadline = time.time() + 60
            while time.time() < deadline and not child_report.exists():
                time.sleep(0.05)
            self.assertTrue(child_report.exists(), "worker process never started")
            # Spawned worker processes inherit RECASTER_BRIDGE=1 but use the stock interact
            self.assertEqual(child_report.read_text(), "InteractDesktop")
            child_pid = int(child_pid_file.read_text())
            with open(self.run_dir / "control.jsonl", "a") as f:
                f.write('{"v":1,"seq":1,"cmd":"stop","save":false}\n')
            self.assertEqual(proc.wait(15), 130)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.stderr.close()
        events = _events(self.run_dir)
        self.assertEqual([e["type"] for e in events].count("hello"), 1)  # only the owner writes
        self.assertEqual(events[-2]["type"], "state")
        self.assertEqual((events[-1]["type"], events[-1]["status"]), ("done", "cancelled"))
        deadline = time.time() + 5
        while time.time() < deadline and _alive(child_pid):
            time.sleep(0.05)
        self.assertFalse(_alive(child_pid), "DFL worker process survived the stop")


class ProbeTests(unittest.TestCase):
    def test_probe_prints_one_json_line(self):
        proc = subprocess.run([sys.executable, "-u", "-m", "recaster_bridge.probe", "--json", "--no-devices"],
                              cwd=str(ROOT), env=_base_env(), capture_output=True, text=True, timeout=180)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, proc.stdout + proc.stderr)
        info = json.loads(lines[0])
        self.assertEqual((info["protocol"], info["bridge_version"]), (1, "1.1.0"))
        self.assertIsNone(info["devices"])
        self.assertEqual(proc.returncode, 0 if info["tf_version"] else 1)
        for key in ("python", "numpy", "cv2", "cv2_has_highgui", "onnxruntime_importable"):
            self.assertIn(key, info)

    @unittest.skipUnless(importlib.util.find_spec("tensorflow"), "needs TensorFlow (a runtime env)")
    def test_probe_lists_devices_with_tensorflow(self):
        # CUDA_VISIBLE_DEVICES="" like the release smoke: DFL's device init pops it
        env = dict(_base_env(), CUDA_VISIBLE_DEVICES="", TF_CPP_MIN_LOG_LEVEL="2")
        proc = subprocess.run([sys.executable, "-u", "-m", "recaster_bridge.probe", "--json"],
                              cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
        info = json.loads([line for line in proc.stdout.splitlines() if line.startswith("{")][-1])
        self.assertIsInstance(info["devices"], list, proc.stderr)
        for dev in info["devices"]:
            self.assertEqual(set(dev), {"index", "name", "total_mem_gb"})
        if sys.platform == "darwin" and importlib.util.find_spec("tensorflow_metal"):
            self.assertIn("METAL", [d["name"] for d in info["devices"]])


class ProbeBrokenTensorflowTests(unittest.TestCase):
    """A TensorFlow that find_spec() sees but that fails (or crashes) on import must
    not hang the probe in Devices.initialize_main_env() (QA regression, REC-187)."""

    def _probe_with_fake_tf(self, body, expect_json=True):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tensorflow").mkdir()
            (Path(tmp) / "tensorflow" / "__init__.py").write_text(body)
            env = dict(_base_env(), PYTHONPATH=tmp, PYTHONNOUSERSITE="1")
            proc = subprocess.Popen([sys.executable, "-u", "-m", "recaster_bridge.probe", "--json"],
                                    cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    start_new_session=True)
            try:
                out, err = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                self.fail("probe hung (> 60 s) with a TensorFlow that fails to import")
        if not expect_json:
            return  # a hard crash in the probe's own TF import can't print JSON; it just must not hang
        lines = [line for line in out.decode().splitlines() if line.startswith("{")]
        self.assertTrue(lines, err.decode())
        info = json.loads(lines[-1])
        self.assertIsNone(info["devices"])
        self.assertIsNone(info["tf_version"])
        self.assertIn("device enumeration failed", err.decode())

    def test_tf_import_error(self):
        self._probe_with_fake_tf('raise ImportError("libcudart.so.12: cannot open shared object file")\n')

    def test_tf_import_crash(self):
        self._probe_with_fake_tf("import os\nos._exit(245)\n", expect_json=False)


if __name__ == "__main__":
    unittest.main()
