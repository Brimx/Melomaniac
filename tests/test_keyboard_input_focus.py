import types
import unittest

import flet as ft

from ui.components.widgets import app_text_field
from ui.shell.keymap import is_editable_control


class InputFocusRegression(unittest.TestCase):
    def test_page_event_control_uses_tracked_focus(self):
        page_like = types.SimpleNamespace()  # e.control es la Page en Flet 1.1.2
        field = app_text_field(label="Buscar")
        self.assertFalse(is_editable_control(page_like))
        field.on_focus(None)
        self.assertTrue(is_editable_control(page_like))
        field.on_blur(None)
        self.assertFalse(is_editable_control(page_like))

    def test_chains_existing_handlers(self):
        calls = []
        field = app_text_field(label="x", on_focus=lambda e: calls.append("f"),
                               on_blur=lambda e: calls.append("b"))
        field.on_focus(None)
        field.on_blur(None)
        self.assertEqual(calls, ["f", "b"])

    def test_readonly_focused_not_editable(self):
        field = app_text_field(label="x", read_only=True)
        field.on_focus(None)
        self.assertFalse(is_editable_control(object()))
        field.on_blur(None)


if __name__ == "__main__":
    unittest.main()
