# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Prompt answers from ``answers.json``.

    {"v": 1, "policy": "answers_then_default", "ask_timeout_s": 0,
     "answers": [{"key": "batch_size", "match": ["Batch size"], "value": 8,
                  "kind": "int", "locked": false}, ...]}

Matching: case-insensitive substring of the prompt text, answers in file
order, first hit wins. Recaster's app keeps the same algorithm as a test
oracle; both are pinned by a parity test.

Policies:
    answers_then_default  unmatched prompts take DFL's default (never blocks)
    answers_then_ask      unmatched prompts emit a ``prompt`` event and wait
                          for a control ``answer`` (up to ask_timeout_s; 0 = no limit)
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

POLICY_DEFAULT = "answers_then_default"
POLICY_ASK = "answers_then_ask"
POLICIES = (POLICY_DEFAULT, POLICY_ASK)


@dataclass(frozen=True)
class Answer:
    key: str
    match: Tuple[str, ...]
    value: Any
    kind: str = "any"
    locked: bool = False


@dataclass(frozen=True)
class AnswerBook:
    answers: Tuple[Answer, ...] = ()
    policy: str = POLICY_DEFAULT
    ask_timeout_s: float = 0

    def find(self, text: str) -> Optional[Answer]:
        return find_answer(self.answers, text)


def find_answer(answers, text: str) -> Optional[Answer]:
    """First answer with any pattern contained in ``text`` (case-insensitive)."""
    lowered = (text or "").lower()
    for answer in answers:
        if any(pattern.lower() in lowered for pattern in answer.match):
            return answer
    return None


def parse_answers(data: Any) -> Tuple[AnswerBook, List[str]]:
    """(book, problems). Invalid entries are skipped, never raised."""
    problems: List[str] = []
    if not isinstance(data, dict):
        return AnswerBook(), ["answers.json is not an object"]
    if data.get("v") != 1:
        problems.append(f"answers.json version {data.get('v')!r} is not 1")
        return AnswerBook(), problems
    policy = data.get("policy", POLICY_DEFAULT)
    if policy not in POLICIES:
        problems.append(f"unknown policy {policy!r}; using {POLICY_DEFAULT}")
        policy = POLICY_DEFAULT
    timeout = data.get("ask_timeout_s", 0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        problems.append("invalid ask_timeout_s; using 0")
        timeout = 0
    answers = []
    raw = data.get("answers", [])
    if not isinstance(raw, list):
        problems.append("answers is not a list")
        raw = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            problems.append(f"answers[{i}] is not an object")
            continue
        match = entry.get("match")
        if isinstance(match, str):
            match = [match]
        if not isinstance(match, list) or not match or not all(isinstance(m, str) and m for m in match):
            problems.append(f"answers[{i}] has no valid match patterns")
            continue
        answers.append(Answer(
            key=str(entry.get("key", f"answer_{i}")),
            match=tuple(match),
            value=entry.get("value"),
            kind=str(entry.get("kind", "any")),
            locked=bool(entry.get("locked", False)),
        ))
    return AnswerBook(tuple(answers), policy, timeout), problems


def load_answers(path: Path) -> Tuple[AnswerBook, List[str]]:
    """Read ``answers.json``; a missing file is an empty book (DFL defaults)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return AnswerBook(), []
    except OSError as e:
        return AnswerBook(), [f"answers.json unreadable: {e}"]
    try:
        data = json.loads(text)
    except ValueError as e:
        return AnswerBook(), [f"answers.json is not valid JSON: {e}"]
    return parse_answers(data)
