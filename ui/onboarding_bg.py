"""
ui/onboarding_bg.py — Fondo animado del onboarding
════════════════════════════════════════════════════
Red de puntos 2D con profundidad falsa: tres capas con distinta velocidad,
radio y alfa. Los puntos cercanos se unen con líneas (continua, discontinua o
punteada según la arista) cuya opacidad decae con la distancia. Medidas y
alfas viven en ``ui/tokens.py``; los colores, también.
"""

from __future__ import annotations

import asyncio
import math
import random

import flet as ft
import flet.canvas as cv

from ui.theme.tokens import (
    ACCENT, ONBOARDING_BG_DASHES, ONBOARDING_BG_FPS, ONBOARDING_BG_LAYERS,
    ONBOARDING_BG_LINK, ONBOARDING_BG_POINTS, ONBOARDING_BG_SPEED, TEXT_DIM,
)


class OnboardingBackground(cv.Canvas):
    """Canvas que anima la red mientras ``run()`` esté en ejecución."""

    def __init__(self) -> None:
        super().__init__(expand=True, on_resize=self._on_resize)
        self._w, self._h = 1280.0, 800.0
        self._task: asyncio.Task | None = None
        rnd = random.Random()
        self._pts: list[list[float]] = []  # x, y, vx, vy, capa
        for _ in range(ONBOARDING_BG_POINTS):
            layer = rnd.randrange(len(ONBOARDING_BG_LAYERS))
            ang = rnd.uniform(0, math.tau)
            spd = ONBOARDING_BG_SPEED * ONBOARDING_BG_LAYERS[layer][0] * rnd.uniform(0.6, 1.0)
            self._pts.append([
                rnd.uniform(0, self._w), rnd.uniform(0, self._h),
                math.cos(ang) * spd, math.sin(ang) * spd, layer,
            ])
        self._circles = [
            cv.Circle(0, 0, ONBOARDING_BG_LAYERS[int(p[4])][1],
                      ft.Paint(color=ft.Colors.with_opacity(ONBOARDING_BG_LAYERS[int(p[4])][2], ACCENT)))
            for p in self._pts
        ]
        self._frame()

    def _on_resize(self, e) -> None:
        """Reescala las posiciones a la nueva área para cubrir toda la ventana."""
        nw = float(e.width or 0)
        nh = float(e.height or 0)
        if nw <= 0 or nh <= 0:
            return
        sx, sy = nw / self._w, nh / self._h
        for p in self._pts:
            p[0] *= sx
            p[1] *= sy
        self._w, self._h = nw, nh

    def _frame(self, dt: float = 0.0) -> None:
        w, h = self._w, self._h
        for p, c in zip(self._pts, self._circles):
            p[0] = (p[0] + p[2] * dt) % w
            p[1] = (p[1] + p[3] * dt) % h
            c.x, c.y = p[0], p[1]
        link2 = ONBOARDING_BG_LINK ** 2
        lines: list[cv.Shape] = []
        pts = self._pts
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx, dy = pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]
                d2 = dx * dx + dy * dy
                if d2 >= link2:
                    continue
                depth = min(ONBOARDING_BG_LAYERS[int(pts[i][4])][2],
                            ONBOARDING_BG_LAYERS[int(pts[j][4])][2])
                alpha = depth * 0.6 * (1 - math.sqrt(d2) / ONBOARDING_BG_LINK)
                lines.append(cv.Line(
                    pts[i][0], pts[i][1], pts[j][0], pts[j][1],
                    ft.Paint(
                        color=ft.Colors.with_opacity(alpha, TEXT_DIM if (i + j) % 3 else ACCENT),
                        stroke_width=1,
                        stroke_dash_pattern=ONBOARDING_BG_DASHES[(i + j) % len(ONBOARDING_BG_DASHES)],
                        style=ft.PaintingStyle.STROKE,
                    ),
                ))
        self.shapes = [*lines, *self._circles]

    async def run(self) -> None:
        """Bucle de animación; se cancela con ``stop()`` o al cerrar la app."""
        step = 1.0 / ONBOARDING_BG_FPS
        try:
            while True:
                await asyncio.sleep(step)
                self._frame(step)
                self.update()
        except (asyncio.CancelledError, RuntimeError):
            pass

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
