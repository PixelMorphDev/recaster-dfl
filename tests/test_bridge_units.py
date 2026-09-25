# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""recaster_bridge unit tests: protocol writer, answers, control. Stdlib only.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recaster_bridge import hooks as hooks_mod  # noqa: E402
from recaster_bridge import probe as probe_mod  # noqa: E402
from recaster_bridge import read_version, run_dir_from_env  # noqa: E402
from recaster_bridge.answers import (POLICY_ASK, POLICY_DEFAULT, Answer, find_answer,  # noqa: E402
                                     load_answers, parse_answers)
from recaster_bridge.control import ControlReader  # noqa: E402
from recaster_bridge.protocol import (EVENT_TYPES, PROTOCOL, EventWriter,  # noqa: E402
                                      parse_control_line)

GOLDEN = ROOT / "tests" / "fixtures" / "dfl_protocol" / "v1"


def _events(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class VersionTests(unittest.TestCase):
    def test_version_file(self):
        self.assertEqual(read_version(), {"protocol": 1, "bridge": "1.1.0"})
        self.assertEqual(PROTOCOL, 1)

    def test_run_dir_needs_flag_and_existing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(run_dir_from_env({"RECASTER_RUN_DIR": tmp}))
            self.assertIsNone(run_dir_from_env({"RECASTER_BRIDGE": "1"}))
            self.assertIsNone(run_dir_from_env({"RECASTER_BRIDGE": "1",
                                                "RECASTER_RUN_DIR": str(Path(tmp) / "missing")}))
            self.assertEqual(run_dir_from_env({"RECASTER_BRIDGE": "1", "RECASTER_RUN_DIR": tmp}),
                             Path(tmp))


class EventWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_envelope_and_monotonic_seq(self):
        w = EventWriter(self.path, "run-1")
        w.emit("hello", protocol=1)
        w.emit("progress", current=1, total=2, unit="images")
        w.close()
        events = _events(self.path)
        self.assertEqual([e["seq"] for e in events], [1, 2])
        for e in events:
            self.assertEqual((e["v"], e["run"]), (1, "run-1"))
            self.assertIsInstance(e["ts"], float)
        self.assertEqual(events[1]["current"], 1)

    def test_unknown_type_rejected(self):
        w = EventWriter(self.path, "r")
        with self.assertRaises(ValueError):
            w.emit("bogus")
        w.close()

    def test_concurrent_writers_never_interleave(self):
        w = EventWriter(self.path, "r")

        def spam():
            for i in range(200):
                w.emit("warning", code="x", message="m" * 500)

        threads = [threading.Thread(target=spam) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        w.close()
        events = _events(self.path)
        self.assertEqual(sorted(e["seq"] for e in events), list(range(1, 801)))

    def test_emit_after_close_is_a_noop(self):
        w = EventWriter(self.path, "r")
        w.close()
        self.assertEqual(w.emit("alive"), 0)


class ControlLineTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_control_line(b'{"v":1,"seq":1,"cmd":"stop"}')["cmd"], "stop")
        for bad in (b"not json", b'{"v":2,"cmd":"stop"}', b'{"v":1}', b"[1]", b"\xff\xfe"):
            self.assertIsNone(parse_control_line(bad), bad)


class GoldenFixtureTests(unittest.TestCase):
    """The v1 fixtures are byte-identical in Recaster (tests/fixtures/dfl_protocol/v1)."""

    def test_event_fixtures_are_valid_v1(self):
        files = sorted(GOLDEN.glob("events_*.jsonl"))
        self.assertTrue(files)
        for path in files:
            events = _events(path)
            self.assertEqual(events[0]["type"], "hello", path.name)
            self.assertEqual(events[-1]["type"], "done", path.name)
            self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
            for e in events:
                self.assertEqual(e["v"], 1)
                self.assertIn(e["type"], EVENT_TYPES)

    def test_control_fixture_parses(self):
        for line in (GOLDEN / "control.jsonl").read_bytes().splitlines():
            self.assertIsNotNone(parse_control_line(line))

    def test_answers_fixture_loads(self):
        book, problems = load_answers(GOLDEN / "answers_extract.json")
        self.assertEqual(problems, [])
        self.assertEqual(book.policy, POLICY_DEFAULT)
        self.assertEqual(book.find("Image size").value, 512)


class AnswerTests(unittest.TestCase):
    def test_first_hit_wins_case_insensitive(self):
        answers = (Answer("preview_mf", ("Preview morph factor",), 1),
                   Answer("morph_factor", ("Morph factor",), 0.5))
        self.assertEqual(find_answer(answers, "[0.5] preview MORPH factor : ").key, "preview_mf")
        self.assertEqual(find_answer(answers, "Morph factor").key, "morph_factor")
        self.assertIsNone(find_answer(answers, ""))
        self.assertIsNone(find_answer(answers, "Batch_size"))

    def test_invalid_entries_are_skipped(self):
        book, problems = parse_answers({"v": 1, "policy": "nope", "answers": [
            {"key": "a", "match": ["A"], "value": 1},
            "junk",
            {"key": "b", "match": []},
            {"key": "c", "match": "C", "value": 3},
        ]})
        self.assertEqual([a.key for a in book.answers], ["a", "c"])
        self.assertEqual(book.policy, POLICY_DEFAULT)
        self.assertEqual(len(problems), 3)

    def test_wrong_version_or_bad_file(self):
        self.assertEqual(parse_answers({"v": 2})[0].answers, ())
        with tempfile.TemporaryDirectory() as tmp:
            book, problems = load_answers(Path(tmp) / "missing.json")
            self.assertEqual((book.answers, problems), ((), []))
            bad = Path(tmp) / "bad.json"
            bad.write_text("{")
            book, problems = load_answers(bad)
            self.assertEqual(book.answers, ())
            self.assertTrue(problems)

    def test_ask_policy(self):
        book, _ = parse_answers({"v": 1, "policy": POLICY_ASK, "ask_timeout_s": 5, "answers": []})
        self.assertEqual((book.policy, book.ask_timeout_s), (POLICY_ASK, 5))


class ControlReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.control = root / "control.jsonl"
        self.control.write_text("")
        self.events = root / "events.jsonl"
        self.writer = EventWriter(self.events, "r")
        self.stops = []
        self.now = [0.0]
        self.reader = ControlReader(self.control, self.writer,
                                    on_stop=lambda save, reason: self.stops.append((save, reason)),
                                    heartbeat_s=60, alive_s=15, clock=lambda: self.now[0])

    def tearDown(self):
        self.writer.close()
        self.tmp.cleanup()

    def _append(self, text):
        with open(self.control, "a") as f:
            f.write(text)

    def test_stop_and_answer(self):
        self._append('{"v":1,"seq":1,"cmd":"answer","id":"p1","value":"wf"}\n{"v":1,"seq":2,"cmd":"stop","save":true}\n')
        self.reader.poll_once()
        self.assertEqual(self.stops, [(True, "control")])
        self.assertEqual(self.reader.wait_answer("p1", 0.1), (True, "wf"))
        self.assertEqual(self.reader.wait_answer("p2", 0.01), (False, None))

    def test_partial_line_waits_for_newline(self):
        self._append('{"v":1,"seq":1,"cmd":"st')
        self.reader.poll_once()
        self.assertEqual(self.stops, [])
        self._append('op"}\n')
        self.reader.poll_once()
        self.assertEqual(self.stops, [(False, "control")])

    def test_unknown_unsupported_and_malformed(self):
        self._append('garbage\n{"v":1,"seq":1,"cmd":"dance"}\n{"v":1,"seq":2,"cmd":"pause"}\n'
                     '{"v":1,"seq":3,"cmd":"key","key":"q"}\n')
        self.reader.poll_once()
        self.assertEqual(self.reader.invalid_lines, 1)
        codes = [e["code"] for e in _events(self.events)]
        self.assertEqual(codes, ["unknown_command", "unsupported_command", "bad_command"])

    def test_heartbeat_watchdog_and_alive(self):
        self.now[0] = 50
        self._append('{"v":1,"seq":1,"cmd":"heartbeat"}\n')
        self.reader.poll_once()  # contact at t=50, alive at t>=15
        self.now[0] = 100
        self.reader.poll_once()
        self.assertEqual(self.stops, [])
        self.now[0] = 111
        self.reader.poll_once()
        self.assertEqual(self.stops, [(True, "heartbeat")])
        types = [e["type"] for e in _events(self.events)]
        self.assertIn("alive", types)
        self.assertIn("warning", types)

    def test_oversized_line_is_skipped(self):
        from recaster_bridge import control as control_mod
        old = control_mod.MAX_LINE_BYTES
        control_mod.MAX_LINE_BYTES = 64
        try:
            self._append('{"v":1,"seq":1,"cmd":"heartbeat","pad":"' + "x" * 200)
            self.reader.poll_once()
            self._append('"}\n{"v":1,"seq":2,"cmd":"stop"}\n')
            self.reader.poll_once()
        finally:
            control_mod.MAX_LINE_BYTES = old
        self.assertEqual(self.stops, [(False, "control")])
        self.assertEqual(self.reader.invalid_lines, 1)



class ParentWatchTests(unittest.TestCase):
    """The parent watch: a reparented bridge (caller died) stops within parent_poll_s."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "control.jsonl").write_text("")
        self.events = root / "events.jsonl"
        self.writer = EventWriter(self.events, "r")
        self.stops = []
        self.now = [0.0]
        self.ppid = [4242]
        self.root = root

    def tearDown(self):
        self.writer.close()
        self.tmp.cleanup()

    def _reader(self, heartbeat_s):
        return ControlReader(self.root / "control.jsonl", self.writer,
                             on_stop=lambda save, reason: self.stops.append((save, reason)),
                             heartbeat_s=heartbeat_s, alive_s=0, parent_poll_s=1.0,
                             clock=lambda: self.now[0], getppid=lambda: self.ppid[0])

    def test_parent_exit_stops_once(self):
        reader = self._reader(heartbeat_s=60)
        self.now[0] = 0.5
        self.ppid[0] = 1
        reader.poll_once()
        self.assertEqual(self.stops, [])  # checked at most once per parent_poll_s
        self.now[0] = 1.0
        reader.poll_once()
        self.assertEqual(self.stops, [(True, "parent_exited")])
        warning = _events(self.events)[-1]
        self.assertEqual((warning["type"], warning["code"]), ("warning", "parent_lost"))
        self.assertIn("4242", warning["message"])
        self.now[0] = 5.0
        reader.poll_once()
        self.assertEqual(len(self.stops), 1)

    def test_any_new_parent_counts(self):
        # Linux may reparent to a subreaper rather than pid 1
        reader = self._reader(heartbeat_s=60)
        self.ppid[0] = 777
        self.now[0] = 2.0
        reader.poll_once()
        self.assertEqual(self.stops, [(True, "parent_exited")])

    def test_same_parent_never_stops(self):
        reader = self._reader(heartbeat_s=60)
        for t in range(1, 30):
            self.now[0] = float(t)
            self._append_heartbeat()
            reader.poll_once()
        self.assertEqual(self.stops, [])

    def test_no_heartbeat_no_parent_watch(self):
        # An unsupervised run (no heartbeat asked for) keeps the old behaviour
        reader = self._reader(heartbeat_s=0)
        self.ppid[0] = 1
        self.now[0] = 10.0
        reader.poll_once()
        self.assertEqual(self.stops, [])
        self.assertEqual(_events(self.events), [])

    def _append_heartbeat(self):
        with open(self.root / "control.jsonl", "a") as f:
            f.write('{"v":1,"seq":1,"cmd":"heartbeat"}\n')


@unittest.skipIf(os.name == "nt", "POSIX process groups")
class GroupMembersTests(unittest.TestCase):
    """hooks.group_members() over /proc (Linux) and ps (macOS, forced elsewhere)."""

    def setUp(self):
        # Two sleepers in a session of their own: the leader and one child
        code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"
        self.leader = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
        self.addCleanup(self._kill)
        deadline = time.time() + 30
        while time.time() < deadline and len(hooks_mod.group_members(self.leader.pid, -1) or []) < 2:
            time.sleep(0.05)

    def _kill(self):
        try:
            os.killpg(self.leader.pid, signal.SIGKILL)
        except OSError:
            pass
        self.leader.wait(10)

    def _check(self):
        members = hooks_mod.group_members(self.leader.pid, -1)
        self.assertEqual(len(members), 2, members)
        self.assertIn(self.leader.pid, members)
        self.assertEqual(hooks_mod.group_members(self.leader.pid, self.leader.pid),
                         [m for m in members if m != self.leader.pid])
        mine = hooks_mod.group_members(os.getpgrp(), os.getpid())
        self.assertNotIn(os.getpid(), mine)
        self.assertNotIn(self.leader.pid, mine)

    def test_lists_the_group(self):
        self._check()

    def test_ps_path(self):
        with mock.patch.object(hooks_mod, "_has_proc", return_value=False):
            self._check()

    @unittest.skipUnless(os.path.exists("/proc/self/stat"), "needs /proc")
    def test_proc_path(self):
        with mock.patch.object(hooks_mod, "_has_proc", return_value=True):
            self._check()

    def test_killed_group_is_empty(self):
        os.killpg(self.leader.pid, signal.SIGKILL)
        self.leader.wait(10)  # the leader is our child; its child is reparented and reaped
        deadline = time.time() + 10
        while time.time() < deadline and hooks_mod.group_members(self.leader.pid, -1):
            time.sleep(0.05)
        self.assertEqual(hooks_mod.group_members(self.leader.pid, -1), [])

    def test_unlistable_group_is_none(self):
        with mock.patch.object(hooks_mod, "_has_proc", return_value=False), \
                mock.patch.object(hooks_mod.subprocess, "Popen", side_effect=OSError("no ps")):
            self.assertIsNone(hooks_mod.group_members(self.leader.pid, -1))


class ReapGuardTests(unittest.TestCase):
    def test_no_reap_unless_group_leader(self):
        with mock.patch.object(hooks_mod, "leads_process_group", return_value=False), \
                mock.patch.object(hooks_mod.os, "kill") as kill, \
                mock.patch.object(hooks_mod.os, "killpg") as killpg:
            self.assertTrue(hooks_mod.reap_process_group())
            hooks_mod.kill_own_process_group()
        kill.assert_not_called()
        killpg.assert_not_called()

    def test_unlistable_group_is_not_clean(self):
        with mock.patch.object(hooks_mod, "leads_process_group", return_value=True), \
                mock.patch.object(hooks_mod, "group_members", return_value=None), \
                mock.patch.object(hooks_mod.os, "kill") as kill:
            self.assertFalse(hooks_mod.reap_process_group())
        kill.assert_not_called()

    def test_term_then_kill_the_stubborn(self):
        listings = iter([[11, 12], [12], [12], []])
        with mock.patch.object(hooks_mod, "leads_process_group", return_value=True), \
                mock.patch.object(hooks_mod, "group_members", side_effect=lambda *a: next(listings)), \
                mock.patch.object(hooks_mod.time, "sleep"), \
                mock.patch.object(hooks_mod.os, "kill") as kill:
            self.assertTrue(hooks_mod.reap_process_group(term_grace_s=0, kill_wait_s=5))
        self.assertEqual(kill.call_args_list, [mock.call(11, signal.SIGTERM), mock.call(12, signal.SIGTERM),
                                               mock.call(12, signal.SIGKILL)])

    def test_resource_tracker_is_never_signalled(self):
        # 13 is the tracker: it ignores SIGTERM and must not be SIGKILLed before it unlinks
        listings = iter([[11, 13], [13], [13]])
        with mock.patch.object(hooks_mod, "leads_process_group", return_value=True), \
                mock.patch.object(hooks_mod, "resource_tracker_pid", return_value=13), \
                mock.patch.object(hooks_mod, "group_members", side_effect=lambda *a: next(listings)), \
                mock.patch.object(hooks_mod.time, "sleep"), \
                mock.patch.object(hooks_mod.os, "kill") as kill:
            self.assertTrue(hooks_mod.reap_process_group(term_grace_s=5, kill_wait_s=5))
        self.assertEqual(kill.call_args_list, [mock.call(11, signal.SIGTERM)])

    def test_only_the_tracker_left_is_clean(self):
        with mock.patch.object(hooks_mod, "leads_process_group", return_value=True), \
                mock.patch.object(hooks_mod, "resource_tracker_pid", return_value=13), \
                mock.patch.object(hooks_mod, "group_members", return_value=[13]), \
                mock.patch.object(hooks_mod.os, "kill") as kill:
            self.assertTrue(hooks_mod.reap_process_group())
        kill.assert_not_called()

    def test_resource_tracker_pid(self):
        from multiprocessing import resource_tracker
        with mock.patch.object(resource_tracker._resource_tracker, "_pid", 4321):
            self.assertEqual(hooks_mod.resource_tracker_pid(), 4321)
        with mock.patch.object(resource_tracker._resource_tracker, "_pid", None):
            self.assertIsNone(hooks_mod.resource_tracker_pid())


class StopOrderTests(unittest.TestCase):
    def test_done_cancelled_is_written_before_workers_are_signalled(self):
        # A worker's death can crash DFL's main thread; its excepthook must not win
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name)
        exits = []
        session = hooks_mod.BridgeSession(run_dir, "run-order", exit_func=exits.append)
        seen = []

        class Child:
            def terminate(self):
                seen.append(session.is_done)
                session.writer.emit("error", code="internal", message="worker died", fatal=True)
                session.finish("error", 1)

        with mock.patch.object(hooks_mod.multiprocessing, "active_children", return_value=[Child()]), \
                mock.patch.object(hooks_mod, "reap_process_group", return_value=True):
            session.request_stop(False, "user")
        self.assertEqual(seen, [True])
        self.assertEqual(exits, [hooks_mod.EXIT_CANCELLED])
        events = _events(run_dir / "events.jsonl")
        self.assertEqual([e["type"] for e in events], ["state", "done"])
        self.assertEqual(events[-1]["status"], "cancelled")


class ProbeDeviceTests(unittest.TestCase):
    """probe._devices() against a fake core.leras.device (no TensorFlow needed)."""

    def _fake_device_module(self, calls, get_error=None, devices=()):
        class Devices:
            @staticmethod
            def initialize_main_env():
                calls.append("initialize_main_env")

            @staticmethod
            def getDevices():
                calls.append("getDevices")
                if "initialize_main_env" not in calls:
                    raise Exception("nn devices are not initialized. Run initialize_main_env() in main process.")
                if get_error:
                    raise get_error
                return list(devices)

        device = types.ModuleType("core.leras.device")
        device.Devices = Devices
        leras = types.ModuleType("core.leras")
        leras.device = device
        core = types.ModuleType("core")
        core.leras = leras
        return {"core": core, "core.leras": leras, "core.leras.device": device}

    def _devices(self, modules, tf_importable=True):
        stderr = io.StringIO()
        spec = object() if tf_importable else None
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(probe_mod.importlib.util, "find_spec", return_value=spec), \
                contextlib.redirect_stderr(stderr):
            result = probe_mod._devices()
        return result, stderr.getvalue()

    def test_initializes_the_device_table_before_reading_it(self):
        calls = []
        metal = types.SimpleNamespace(index=0, name="METAL", total_mem_gb=0.0)
        result, err = self._devices(self._fake_device_module(calls, devices=[metal]))
        self.assertEqual(calls, ["initialize_main_env", "getDevices"])
        self.assertEqual(result, [{"index": 0, "name": "METAL", "total_mem_gb": 0.0}])
        self.assertEqual(err, "")

    def test_no_devices_is_an_empty_list(self):
        result, _err = self._devices(self._fake_device_module([]))
        self.assertEqual(result, [])

    def test_failure_falls_back_to_none_and_says_why(self):
        result, err = self._devices(self._fake_device_module([], get_error=RuntimeError("CUDA driver too old")))
        self.assertIsNone(result)
        self.assertIn("device enumeration failed: RuntimeError: CUDA driver too old", err)

    def test_import_failure_falls_back_to_none_and_says_why(self):
        result, err = self._devices({"core.leras": None})
        self.assertIsNone(result)
        self.assertIn("device enumeration failed: ", err)

    def test_skipped_without_tensorflow(self):
        calls = []
        result, err = self._devices(self._fake_device_module(calls), tf_importable=False)
        self.assertIsNone(result)
        self.assertEqual(calls, [])       # initialize_main_env would block on a dead child
        self.assertIn("tensorflow is not importable", err)

    def test_probe_enumerates_devices_before_importing_tensorflow(self):
        order = []
        with mock.patch.object(probe_mod, "_devices", side_effect=lambda: order.append("devices") or []), \
                mock.patch.object(probe_mod, "_version", side_effect=lambda name: order.append(name)):
            info = probe_mod.probe()
        self.assertEqual(order[0], "devices")
        self.assertEqual(info["devices"], [])
        with mock.patch.object(probe_mod, "_devices", side_effect=AssertionError("enumerated")):
            self.assertIsNone(probe_mod.probe(with_devices=False)["devices"])


if __name__ == "__main__":
    unittest.main()
