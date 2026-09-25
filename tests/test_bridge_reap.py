# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""The bridge reaps its process group when the caller dies or stops heartbeating.

``tests/bridge_stub.py tree`` stands in for a DFL run with multiprocessing
workers, a SIGTERM-ignoring worker, a grandchild and the resource tracker.
Like the Recaster runner, the stub leads its own session. The caller's death
is simulated by running it under an intermediate parent that is SIGKILLed.
Group membership is checked with ``ps`` here, not with the bridge's helper.
"""
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
REAP_DEADLINE_S = 6   # parent poll (1 s) + SIGTERM grace (2 s) + SIGKILL, with CI slack

ANSWERS = {"v": 1, "policy": "answers_then_default", "ask_timeout_s": 0, "answers": []}

# Starts argv[3:] as a session leader, records its pid in argv[1], waits for it
INTERMEDIATE = """
import subprocess, sys
child = subprocess.Popen(sys.argv[2:], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=open(sys.argv[1] + ".err", "w"))
open(sys.argv[1], "w").write(str(child.pid))
child.wait()
"""


def _events(run_dir):
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _live_group(pgid):
    """Non-zombie pids in process group ``pgid``, from ps."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat="], capture_output=True, text=True,
                         timeout=10).stdout
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == str(pgid) and not parts[2].startswith("Z"):
            pids.append(int(parts[0]))
    return pids


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _killpg(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


@unittest.skipIf(os.name == "nt", "POSIX process groups")
class GroupReapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run"
        self.run_dir.mkdir()
        (self.run_dir / "control.jsonl").write_text("")
        (self.run_dir / "answers.json").write_text(json.dumps(ANSWERS))
        self.tree_file = Path(self.tmp.name) / "tree.json"
        self.groups = []
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._kill_groups)

    def _kill_groups(self):
        for pgid in self.groups:
            _killpg(pgid)

    def _env(self, heartbeat_s):
        env = {k: v for k, v in os.environ.items() if not k.startswith("RECASTER_")}
        env.update({"PYTHONUNBUFFERED": "1", "RECASTER_BRIDGE": "1", "RECASTER_RUN_DIR": str(self.run_dir),
                    "RECASTER_RUN_ID": "run-reap", "RECASTER_PROTOCOL": "1",
                    "RECASTER_HEARTBEAT_S": str(heartbeat_s), "STUB_TREE_PIDS": str(self.tree_file)})
        return env

    def _stub_argv(self):
        return [sys.executable, "-u", str(STUB), "extract", "tree"]

    def _wait_tree(self, proc, heartbeat=False):
        deadline = time.time() + 60
        seq = 0
        while time.time() < deadline and not self.tree_file.exists():
            self.assertIsNone(proc.poll(), "the stub exited before its process tree was up")
            if heartbeat:
                seq += 1
                with open(self.run_dir / "control.jsonl", "a") as f:
                    f.write(json.dumps({"v": 1, "seq": seq, "cmd": "heartbeat"}) + "\n")
            time.sleep(0.2)
        self.assertTrue(self.tree_file.exists(), "the stub's process tree never came up")
        tree = json.loads(self.tree_file.read_text())
        self.assertTrue({"plain", "ignores_sigterm", "grandchild", "grandchild_of_worker"} <= set(tree), tree)
        return tree

    def _assert_group_gone(self, pgid, tree, since):
        deadline = since + REAP_DEADLINE_S
        while time.time() < deadline and _live_group(pgid):
            time.sleep(0.1)
        left = _live_group(pgid)
        names = {name: pid for name, pid in tree.items() if pid in left}
        self.assertEqual(left, [], f"left in the group after {REAP_DEADLINE_S} s: {names}")

    def _assert_cancelled(self, warning_code, reason):
        events = _events(self.run_dir)
        types = [e["type"] for e in events]
        self.assertEqual(types.count("done"), 1, types)
        self.assertEqual((events[-1]["type"], events[-1]["status"], events[-1]["exit_code"]),
                         ("done", "cancelled", 130))
        self.assertIn(warning_code, [e.get("code") for e in events if e["type"] == "warning"])
        stopping = [e for e in events if e["type"] == "state" and e.get("state") == "stopping"]
        self.assertEqual([e["reason"] for e in stopping], [reason])

    def test_parent_death_reaps_the_group(self):
        pid_file = Path(self.tmp.name) / "stub.pid"
        parent = subprocess.Popen([sys.executable, "-c", INTERMEDIATE, str(pid_file), *self._stub_argv()],
                                  cwd=str(ROOT), env=self._env(heartbeat_s=60), start_new_session=True)
        self.groups.append(parent.pid)
        deadline = time.time() + 30
        while time.time() < deadline and not pid_file.exists():
            time.sleep(0.05)
        stub_pid = int(pid_file.read_text())
        self.groups.append(stub_pid)
        tree = self._wait_tree(parent)
        self.assertEqual(sorted(_live_group(stub_pid)), sorted(set(tree.values()) | {stub_pid}))

        os.kill(parent.pid, signal.SIGKILL)  # the app dies; nothing sends stop or kills the group
        parent.wait(10)
        killed_at = time.time()
        self._assert_group_gone(stub_pid, tree, killed_at)
        self._assert_cancelled("parent_lost", "parent_exited")

    def test_heartbeat_loss_reaps_the_group(self):
        proc = subprocess.Popen(self._stub_argv(), cwd=str(ROOT), env=self._env(heartbeat_s=2),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        self.groups.append(proc.pid)
        try:
            tree = self._wait_tree(proc, heartbeat=True)
            last_heartbeat = time.time()  # the caller hangs: no more heartbeats
            # heartbeat timeout (2 s) + SIGTERM grace (2 s) + SIGKILL
            self.assertEqual(proc.wait(2 + REAP_DEADLINE_S), 130)  # the bridge went last, not killpg'd
            self._assert_group_gone(proc.pid, tree, last_heartbeat + 2)
        finally:
            if proc.poll() is None:
                _killpg(proc.pid)
                proc.wait(10)
            proc.stderr.close()
        self._assert_cancelled("heartbeat_lost", "heartbeat")

    def test_never_reaps_a_group_it_does_not_lead(self):
        # The stub runs in its caller's group: a stop must not signal the caller
        holder = "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))"
        caller = subprocess.Popen([sys.executable, "-c", holder, *self._stub_argv()], cwd=str(ROOT),
                                  env=self._env(heartbeat_s=60), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        self.groups.append(caller.pid)
        tree = self._wait_tree(caller)
        with open(self.run_dir / "control.jsonl", "a") as f:
            f.write('{"v":1,"seq":1,"cmd":"stop","save":false}\n')
        self.assertEqual(caller.wait(30), 130)  # the caller lived to report the stub's exit
        # Old behaviour here: only multiprocessing children get SIGTERM
        self.assertTrue(_alive(tree["ignores_sigterm"]))
        self.assertTrue(_alive(tree["grandchild_of_worker"]))
        events = _events(self.run_dir)
        self.assertEqual((events[-1]["type"], events[-1]["status"]), ("done", "cancelled"))


if __name__ == "__main__":
    unittest.main()
