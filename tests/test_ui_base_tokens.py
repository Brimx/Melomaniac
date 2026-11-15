import sys
import unittest
from pathlib import Path

import flet as ft

from ui.components import widgets as w
from ui.theme import tokens as t
from ui.theme.icons import PIcons

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audit_ui  # noqa: E402


class TokensTest(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(t.TEXT_PRIMARY, "#FFF2EDE4")
        self.assertEqual((t.RADIUS_NONE, t.RADIUS_SHARP, t.RADIUS_SM), (0, 3, 6))
        self.assertEqual(t.HANKO, "#FFD9443B")
        self.assertEqual(t.ONBOARDING_DISC_SEAL, t.SEAL_COLOR)
        self.assertEqual((t.MOTION_PRESS_MS, t.MOTION_EXIT_MS), (90, 167))
        for n in ("PAPER_BG", "BORDER_CONTROL", "SHADOW_INK", "ON_ACCENT"):
            self.assertTrue(hasattr(t, n))


class HelpersTest(unittest.TestCase):
    def test_helpers_build(self):
        noop = lambda *a: None
        self.assertIsInstance(w.kicker("hola"), ft.Text)
        self.assertEqual(w.kicker("hola").value, "HOLA")
        tabs = w.tab_underline([("a", "A"), ("b", "B")], "a", noop)
        self.assertEqual(len(tabs.controls), 2)
        self.assertEqual(tabs.controls[0].border.bottom.width, 2)
        self.assertIsInstance(w.table_header(["TÍTULO", ("DUR", None)]), ft.Container)
        es = w.empty_state(PIcons.INFO, "Vacío", "Nada", "Ir", noop)
        self.assertEqual(len(es.controls), 4)
        self.assertEqual(w.hanko_seal("音").border_radius, t.RADIUS_SHARP)
        d = w.collapsible_drawer("REGISTRO", "3 eventos", ft.Text("x"))
        self.assertFalse(d.content.controls[1].visible)
        opts = [("yt", "YouTube", PIcons.YOUTUBE_LOGO), ("sp", "Spotify", PIcons.SPOTIFY_LOGO)]
        self.assertEqual(len(w.platform_select(opts, "yt", noop).items), 2)

    def test_buttons(self):
        b = w._primary_btn("Ok", PIcons.PLAY, lambda e: None)
        self.assertEqual(b.style.shape.radius, t.RADIUS_SHARP)
        self.assertEqual(b.style.animation_duration, t.MOTION_PRESS_MS)
        g = w._ghost_btn("No", None, lambda e: None)
        self.assertEqual(g.style.shape.radius, t.RADIUS_SHARP)


class AuditTest(unittest.TestCase):
    def test_scan_and_strict(self):
        res = audit_ui.scan()
        self.assertIn("hex fuera de tokens", res)
        self.assertEqual(audit_ui.main([]), 0)


if __name__ == "__main__":
    unittest.main()
