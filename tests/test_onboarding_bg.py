"""Pruebas del fondo DISCO del onboarding y enmascarado de la red."""

from __future__ import annotations

import asyncio
import math
import unittest
from types import SimpleNamespace

import flet as ft
import flet.canvas as cv

from ui.setup.onboarding import ONBOARDING_BG_MODES, OnboardingView
from ui.setup.onboarding_bg import OnboardingBackground, OnboardingDiscoBackground
from ui.theme import tokens


class TestOnboardingDisco(unittest.TestCase):
    def test_fps_bounded_20_to_24(self) -> None:
        self.assertGreaterEqual(tokens.ONBOARDING_BG_FPS, 20)
        self.assertLessEqual(tokens.ONBOARDING_BG_FPS, 24)

    def test_disco_layers_same_direction_and_unmounted_angle_zero(self) -> None:
        bg = OnboardingDiscoBackground(reduced_motion=False)
        # 2 estáticos + 2 giratorios + 1 red = 5 controles
        self.assertEqual(len(bg.controls), 5)
        self.assertIsInstance(bg.controls[0], cv.Canvas)
        self.assertIsInstance(bg.controls[1], cv.Canvas)
        self.assertIsInstance(bg.controls[2], ft.Container)
        self.assertIsInstance(bg.controls[3], ft.Container)
        self.assertIsInstance(bg.controls[4], OnboardingBackground)

        for rot in bg._rotating:
            self.assertEqual(rot.rotate.angle, 0)
            self.assertIsNotNone(rot.animate_rotation)
            self.assertIsNotNone(rot.on_animation_end)

        # Ambos discos giran en el mismo sentido (duraciones distintas ~1.1 y ~0.7 rpm).
        rpms = [spec[4] for spec in tokens.ONBOARDING_DISC_SPECS]
        self.assertTrue(all(r > 0 for r in rpms))
        self.assertGreater(bg._rotating[1].animate_rotation.duration, bg._rotating[0].animate_rotation.duration)

    def test_arithmetic_masking_discards_points_and_edges_on_discs(self) -> None:
        net = OnboardingBackground()
        # Dos puntos dentro del disco en (100, 100) y dos fuera en (800, 500)
        net._pts = [
            [100.0, 100.0, 0.0, 0.0, 0],
            [110.0, 105.0, 0.0, 0.0, 1],
            [800.0, 500.0, 0.0, 0.0, 2],
            [820.0, 510.0, 0.0, 0.0, 2],
        ]
        net._circles = [
            cv.Circle(0, 0, 2, ft.Paint(color=tokens.ACCENT))
            for _ in net._pts
        ]
        net.set_disc_zones([(100.0, 100.0, 80.0 ** 2)])
        net._frame(0.0)

        # Solo 1 arista (entre los dos puntos fuera del disco); los internos quedan ocultos.
        lines = [s for s in net.shapes if isinstance(s, cv.Line)]
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0].x1, 800.0)
        self.assertAlmostEqual(lines[0].x2, 820.0)
        self.assertEqual(net._circles[0].paint.color, ft.Colors.with_opacity(0.0, tokens.ACCENT))
        self.assertEqual(net._circles[1].paint.color, ft.Colors.with_opacity(0.0, tokens.ACCENT))

    def test_reduced_motion_pauses_net_and_discs(self) -> None:
        async def _run() -> None:
            bg = OnboardingDiscoBackground(reduced_motion=True)
            bg.start()
            self.assertIsNone(bg._net._task)
            self.assertFalse(bg._spinning)
            bg.stop()

        asyncio.run(_run())

    def test_onboarding_view_modes_and_chained_rotation(self) -> None:
        fake_page = SimpleNamespace(services=[], on_keyboard_event=None, reduced_motion=False)
        view = OnboardingView(fake_page, bg_mode="disco")
        self.assertIn("disco", ONBOARDING_BG_MODES)
        self.assertIsInstance(view._bg, OnboardingDiscoBackground)

        # Simular montaje y fin de animación encadenado
        view._bg._mounted = True
        view._bg._spinning = True
        rot_ctrl = view._bg._rotating[0]
        rot_ctrl.update = lambda: None
        view._bg._on_anim_end(SimpleNamespace(control=rot_ctrl))
        self.assertAlmostEqual(rot_ctrl.rotate.angle, math.tau)

        view.set_bg_mode("constellation", update=False)
        self.assertIsInstance(view._bg, OnboardingBackground)
        view.set_bg_mode("none", update=False)
        self.assertEqual(view.bg_mode, "none")


if __name__ == "__main__":
    unittest.main()
