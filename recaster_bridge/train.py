# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Headless training adapter for ``main.py train`` under recaster_bridge.

DFL's ``trainerThread`` still owns the model and its sample generators. This
module owns the control queue, writes previews to the run directory, and emits
small JSON events; the app never imports TensorFlow or receives numpy arrays
through the protocol.
"""

import os
import queue
import threading
from pathlib import Path


class PauseGate:
    """Pause at iteration boundaries without suspending the process or GPU."""

    def __init__(self, writer):
        self._condition = threading.Condition()
        self._paused = False
        self._writer = writer

    def pause(self):
        with self._condition:
            self._paused = True
            self._condition.notify_all()

    def resume(self):
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def wait_until_resumed(self, handle_commands):
        """Return True if a close command arrived while paused."""
        with self._condition:
            paused = self._paused
        if not paused:
            return False
        self._writer.emit("state", state="paused")
        while True:
            if handle_commands():
                return True
            with self._condition:
                if not self._paused:
                    break
                self._condition.wait(0.1)
        self._writer.emit("state", state="training")
        return False


def _write_preview(run_dir, index, image):
    import cv2
    import numpy as np

    previews_dir = Path(run_dir) / "previews"
    previews_dir.mkdir(exist_ok=True)
    path = previews_dir / f"{index}.jpg"
    temporary = previews_dir / f"{index}.tmp.jpg"
    # Trainer.main's preview arrays are RGB floats; OpenCV writes BGR bytes.
    rgb = np.clip(image * 255.0, 0, 255).astype(np.uint8)
    if not cv2.imwrite(str(temporary), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)):
        raise OSError(f"Could not write training preview {temporary}")
    os.replace(temporary, path)
    return str(path.relative_to(run_dir))


def _write_loss_history(run_dir, history):
    import numpy as np

    path = Path(run_dir) / "loss_history.npy"
    temporary = Path(run_dir) / "loss_history.tmp.npy"
    with open(temporary, "wb") as output:
        np.save(output, history, allow_pickle=False)
    os.replace(temporary, path)
    return str(path.relative_to(run_dir))


def run_headless_training(session, trainer_fn, kwargs):
    """Run DFL's trainer, bridging controls and data to JSON-line events."""
    to_trainer = queue.Queue()
    from_trainer = queue.Queue()
    ready = threading.Event()
    navigation = queue.Queue()
    gate = PauseGate(session.writer)
    selected = 0
    preview_paths = []
    preview_names = []
    current_iter = 0
    loss_path = None
    error = None
    started = False
    saw_close = False

    def stop_training(save):
        to_trainer.put({"op": "close", "save": bool(save)})
        gate.resume()

    def on_command(command):
        name = command["cmd"]
        if name in ("save", "backup", "preview"):
            to_trainer.put({"op": name})
        elif name == "pause":
            gate.pause()
        elif name == "resume":
            gate.resume()
        elif name in ("next_preview", "prev_preview"):
            navigation.put(name)
        else:
            session.writer.emit("warning", code="unsupported_command",
                                message=f"'{name}' is not supported for training")

    session.set_training_stop(stop_training)
    session.control.set_command_handler(on_command)
    session.writer.emit("phase", phase="initializing")
    thread = threading.Thread(target=trainer_fn, args=(to_trainer, from_trainer, ready),
                              kwargs={**kwargs, "pause_gate": gate}, name="dfl-trainer",
                              daemon=True)
    thread.start()

    def emit_preview(index):
        if preview_paths:
            session.writer.emit("preview", iter=current_iter, index=index,
                                count=len(preview_paths), name=preview_names[index],
                                path=preview_paths[index], loss_history=loss_path)

    while thread.is_alive() or not from_trainer.empty():
        try:
            item = from_trainer.get(timeout=0.1)
        except queue.Empty:
            item = None
        if item is not None:
            op = item.get("op")
            if op == "tb" and item.get("action") == "step":
                current_iter = int(item["step"])
                losses = [float(x) for x in (item.get("src_loss"), item.get("dst_loss"))
                          if x is not None]
                if not started:
                    started = True
                    session.writer.emit("state", state="training")
                session.writer.emit("iteration", iter=current_iter,
                                    time=float(item["step_time"]), losses=losses)
            elif op == "show":
                current_iter = int(item.get("iter", current_iter))
                previews = item.get("previews") or []
                preview_paths = [_write_preview(session.run_dir, i, image)
                                 for i, (_, image) in enumerate(previews)]
                preview_names = [str(name) for name, _ in previews]
                selected = min(selected, max(0, len(preview_paths) - 1))
                history = item.get("loss_history")
                if history is not None:
                    loss_path = _write_loss_history(session.run_dir, history)
                for index in range(len(preview_paths)):
                    emit_preview(index)
            elif op == "saved":
                session.writer.emit("state", state="saved", iter=int(item.get("iter", current_iter)))
            elif op == "error":
                error = str(item.get("message", "Training failed"))
                session.writer.emit("error", code="internal", message=error, fatal=True)
            elif op == "close":
                saw_close = True
                break
        while not navigation.empty():
            direction = navigation.get_nowait()
            if preview_paths:
                selected = (selected + (1 if direction == "next_preview" else -1)) % len(preview_paths)
                emit_preview(selected)

    thread.join()
    if error:
        raise RuntimeError(error)
    if not saw_close:
        raise RuntimeError("Training thread ended without a close event")
