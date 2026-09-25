# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""A blank-prompt menu answers only the prompt right after it (bridge 1.2.0, REC-189 QA).

A stale menu must not answer a later blank prompt: the next prompt of any
kind, or a later log line, consumes or replaces it.
"""
import types
import unittest
from recaster_bridge.answers import Answer, AnswerBook
from recaster_bridge.interact import bridge_class

class _Desk:
    pg_bar = None
    def __init__(self): pass
    def log_info(self, msg, end='\n'): pass
    def log_err(self, msg, end='\n'): pass

class _W:
    def __init__(self): self.events = []
    def emit(self, t, **f): self.events.append((t, f))

def _io(answers):
    s = types.SimpleNamespace(answers=AnswerBook(tuple(answers)), writer=_W(), op="merge", control=None)
    return bridge_class(_Desk)(s)

class StaleContextTests(unittest.TestCase):
    def test_menu_answers_only_the_next_prompt(self):
        io = _io([Answer("mask", ("Choose mask mode:",), 7, context=True)])
        io.log_info("Choose mask mode: \n(0) full\n")
        self.assertEqual(io.input_int("", 1), 7)
        self.assertEqual(io.input_int("", 1), 1)  # no new menu: default, not the stale one

    def test_text_prompt_consumes_the_menu(self):
        io = _io([Answer("mask", ("Choose mask mode:",), 7, context=True)])
        io.log_info("Choose mask mode: \n(0) full\n")
        io.input_int("Choose erode mask modifier", 0)
        self.assertEqual(io.input_int("", 1), 1)

    def test_later_log_line_replaces_the_menu(self):
        io = _io([Answer("mask", ("Choose mask mode:",), 7, context=True)])
        io.log_info("Choose mask mode: \n(0) full\n")
        io.log_info("Loading model...")
        self.assertEqual(io.input_int("", 1), 1)

if __name__ == "__main__":
    unittest.main()
