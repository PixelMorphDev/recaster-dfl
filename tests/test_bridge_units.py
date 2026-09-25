# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""recaster_bridge unit tests: protocol writer, answers, control. Stdlib only.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
        self.assertEqual(read_version(), {"protocol": 1, "bridge": "1.0.0"})
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


if __name__ == "__main__":
    unittest.main()
