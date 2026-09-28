# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC.
"""Headless training bridge tests with a fake trainer (no TensorFlow/GPU)."""

import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from recaster_bridge.protocol import EventWriter
from recaster_bridge.train import PauseGate, _write_preview, run_headless_training


class _Control:
    def set_command_handler(self, handler):
        self.handler = handler


class _Session:
    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.writer = EventWriter(self.run_dir / "events.jsonl", "train-test")
        self.control = _Control()

    def set_training_stop(self, callback):
        self.stop = callback

    def events(self):
        return [json.loads(line) for line in (self.run_dir / "events.jsonl").read_text().splitlines()]


def _wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the training bridge")


class PauseGateTests(unittest.TestCase):
    def test_pauses_at_boundary_then_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _Session(tmp)
            gate = PauseGate(session.writer)
            gate.pause()
            stopped = threading.Event()
            thread = threading.Thread(target=lambda: (gate.wait_until_resumed(lambda: False), stopped.set()))
            thread.start()
            _wait_until(lambda: any(e.get("state") == "paused" for e in session.events()))
            self.assertFalse(stopped.is_set())
            gate.resume()
            thread.join(2)
            self.assertTrue(stopped.is_set())
            self.assertEqual([e["state"] for e in session.events() if e["type"] == "state"],
                             ["paused", "training"])
            session.writer.close()

    def test_close_can_be_processed_while_paused(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _Session(tmp)
            gate = PauseGate(session.writer)
            gate.pause()
            self.assertTrue(gate.wait_until_resumed(lambda: True))
            session.writer.close()


class HeadlessTrainingTests(unittest.TestCase):
    def test_preview_file_preserves_rgb_channels(self):
        import cv2

        with tempfile.TemporaryDirectory() as tmp:
            rgb = np.zeros((8, 8, 3), dtype=np.float32)
            rgb[:, :, 0] = 1.0
            relative = _write_preview(tmp, 0, rgb)
            bgr = cv2.imread(str(Path(tmp) / relative))
            self.assertEqual(relative, "previews/0.jpg")
            self.assertGreater(int(bgr[0, 0, 2]), 240)
            self.assertLess(int(bgr[0, 0, 0]), 10)

    def test_events_navigation_save_and_graceful_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _Session(tmp)
            received = queue.Queue()

            def trainer(to_trainer, from_trainer, ready, **kwargs):
                self.assertIsInstance(kwargs["pause_gate"], PauseGate)
                from_trainer.put({"op": "tb", "action": "step", "step": 8,
                                  "step_time": np.float32(0.25), "src_loss": np.float32(0.4),
                                  "dst_loss": np.float32(0.5)})
                from_trainer.put({"op": "show", "iter": 8,
                                  "previews": [("first", object()), ("second", object())],
                                  "loss_history": object()})
                while True:
                    command = to_trainer.get(timeout=3)
                    received.put(command)
                    if command["op"] == "save":
                        from_trainer.put({"op": "saved", "iter": 8})
                    elif command["op"] == "close":
                        from_trainer.put({"op": "close"})
                        return

            with mock.patch("recaster_bridge.train._write_preview", side_effect=["previews/0.jpg", "previews/1.jpg"]), \
                 mock.patch("recaster_bridge.train._write_loss_history", return_value="loss_history.npy"):
                thread = threading.Thread(target=run_headless_training,
                                          args=(session, trainer, {"model_class_name": "SAEHD"}))
                thread.start()
                _wait_until(lambda: hasattr(session.control, "handler"))
                _wait_until(lambda: any(e["type"] == "preview" for e in session.events()))
                session.control.handler({"cmd": "next_preview"})
                _wait_until(lambda: any(e.get("index") == 1 for e in session.events()))
                session.control.handler({"cmd": "save"})
                _wait_until(lambda: any(e.get("state") == "saved" for e in session.events()))
                session.stop(True)
                thread.join(3)
                self.assertFalse(thread.is_alive())

            commands = [received.get_nowait() for _ in range(received.qsize())]
            self.assertEqual(commands, [{"op": "save"}, {"op": "close", "save": True}])
            events = session.events()
            iteration = next(e for e in events if e["type"] == "iteration")
            self.assertEqual(iteration["iter"], 8)
            self.assertAlmostEqual(iteration["time"], 0.25)
            self.assertAlmostEqual(iteration["losses"][0], 0.4)
            self.assertAlmostEqual(iteration["losses"][1], 0.5)
            selected = [e for e in events if e["type"] == "preview"][-1]
            self.assertEqual((selected["index"], selected["name"], selected["path"]),
                             (1, "second", "previews/1.jpg"))
            self.assertEqual(selected["loss_history"], "loss_history.npy")
            session.writer.close()

    def test_missing_close_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _Session(tmp)
            with self.assertRaisesRegex(RuntimeError, "without a close event"):
                run_headless_training(session, lambda *args, **kwargs: None, {})
            session.writer.close()
