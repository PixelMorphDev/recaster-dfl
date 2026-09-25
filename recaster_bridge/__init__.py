# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Recaster bridge: a file-based JSON-lines side channel for DeepFaceLab runs.

Active only when ``RECASTER_BRIDGE=1`` and ``RECASTER_RUN_DIR`` names a run
directory, and only in the process that ran ``main.py`` (DFL's spawned
Subprocessor children fall back to the stock interact). With the variable
unset nothing in this package is imported and DFL behaves exactly as upstream.

Run directory (created by the caller before launch):

    events.jsonl    bridge -> caller, one JSON event per line
    control.jsonl   caller -> bridge, one JSON command per line
    answers.json    prompt answers, read once at start
    stdout.log      written by the caller (the bridge doesn't touch it)

This package must stay importable without TensorFlow and must never import
the calling application's code.
"""

import os
from pathlib import Path
from typing import Mapping, Optional

ENV_BRIDGE = "RECASTER_BRIDGE"
ENV_RUN_DIR = "RECASTER_RUN_DIR"
ENV_RUN_ID = "RECASTER_RUN_ID"
ENV_PROTOCOL = "RECASTER_PROTOCOL"
ENV_HEARTBEAT = "RECASTER_HEARTBEAT_S"
ENV_OWNER_PID = "RECASTER_BRIDGE_OWNER_PID"

EVENTS_FILE = "events.jsonl"
CONTROL_FILE = "control.jsonl"
ANSWERS_FILE = "answers.json"

_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"


def read_version() -> dict:
    """``{"protocol": int, "bridge": str}`` from the VERSION file."""
    out = {"protocol": None, "bridge": None}
    try:
        text = _VERSION_FILE.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key == "protocol":
            try:
                out["protocol"] = int(value)
            except ValueError:
                pass
        elif key == "bridge":
            out["bridge"] = value
    return out


def run_dir_from_env(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The run dir when the bridge is requested and the dir exists, else None."""
    env = os.environ if env is None else env
    if env.get(ENV_BRIDGE) != "1":
        return None
    value = env.get(ENV_RUN_DIR)
    if not value:
        return None
    path = Path(value)
    return path if path.is_dir() else None
