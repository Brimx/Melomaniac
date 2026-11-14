"""
ui/setup/onboarding_bg.py — Fondos animados del onboarding
══════════════════════════════════════════════════════════
1) ``OnboardingBackground``: red de puntos 2D con profundidad falsa (3 capas,
   enlaces acotados por distancia, 20-24 fps) y enmascarado aritmético
   opcional alrededor de zonas circulares.
2) ``OnboardingDiscoBackground``: modo DISCO (ficha modo H, 0 coste por frame
   en los discos). Cada disco se divide en capa estática (cuerpo, canto doble,
   surcos, etiqueta, agujero) + capa giratoria de baja frecuencia (reflejo
   difuminado + sello asimétrico) con ``animate_rotation`` encadenado en
   ``on_animation_end`` tras el montaje, en el mismo sentido (~1.1 y ~0.7 rpm).
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import io
import math
import random
from typing import Callable, Sequence

import flet as ft
import flet.canvas as cv
from PIL import Image, ImageDraw, ImageFilter

from ui.theme.tokens import (
    ACCENT,
    ONBOARDING_BG_DASHES,
    ONBOARDING_BG_FPS,
    ONBOARDING_BG_LAYERS,
    ONBOARDING_BG_LINK,
    ONBOARDING_BG_POINTS,
    ONBOARDING_BG_SPEED,
    ONBOARDING_DISC_BLUR,
    ONBOARDING_DISC_BODY,
    ONBOARDING_DISC_HIGHLIGHT,
    ONBOARDING_DISC_MASK_MARGIN,
    ONBOARDING_DISC_SEAL,
    ONBOARDING_DISC_SPECS,
    TEXT_DIM,
)

_HIGHLIGHT_TEX_SIZE = 512


def _hex_rgb(argb_hex: str) -> tuple[int, int, int]:
    raw = argb_hex.lstrip("#")
    if len(raw) == 8:
        raw = raw[2:]
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


@lru_cache(maxsize=1)
def _highlight_png_bytes() -> bytes:
    """Genera una sola vez el reflejo especular difuminado (baja frecuencia)."""
    size = _HIGHLIGHT_TEX_SIZE
    r = size * 0.48
    c = size / 2.0
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.pieslice([c - r * 0.98, c - r * 0.98, c + r * 0.98, c + r * 0.98], -58, 18, fill=150)
    draw.pieslice([c - r * 0.74, c - r * 0.74, c + r * 0.74, c + r * 0.74], -46, 10, fill=90)
    mask = mask.filter(ImageFilter.GaussianBlur(ONBOARDING_DISC_BLUR))
    out = Image.new("RGBA", (size, size), (*_hex_rgb(ONBOARDING_DISC_HIGHLIGHT), 0))
    out.putalpha(mask)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _disc_geometry(
    w: float, h: float, spec: tuple[float, float, float, float, float, bool]
) -> tuple[float, float, float, float, float, str]:
    cx_frac, cy_frac, r_frac, groove_alpha, rpm, use_accent = spec
    r = max(w, h) * r_frac
    ox = w * cx_frac
    oy = h * cy_frac
    ink = ACCENT if use_accent else TEXT_DIM
    return ox, oy, r, groove_alpha, rpm, ink


def _build_static_disc(ox: float, oy: float, r: float, groove_alpha: float, ink: str) -> cv.Canvas:
    """Capa estática del disco: cuerpo, canto doble, surcos, etiqueta y agujero."""
    size = int(2 * r + 4)
    c = size / 2.0
    shapes: list[cv.Shape] = [
        cv.Circle(c, c, r, ft.Paint(color=ft.Colors.with_opacity(0.6, ONBOARDING_DISC_BODY))),
        cv.Circle(
            c, c, r,
            ft.Paint(color=ft.Colors.with_opacity(0.26, ink), stroke_width=1, style=ft.PaintingStyle.STROKE),
        ),
        cv.Circle(
            c, c, r - 2.5,
            ft.Paint(color=ft.Colors.with_opacity(0.22, TEXT_DIM), stroke_width=1, style=ft.PaintingStyle.STROKE),
        ),
    ]
    rr = r * 0.31
    k = 0
    while rr < r - 3:
        shapes.append(
            cv.Circle(
                c, c, rr,
                ft.Paint(
                    color=ft.Colors.with_opacity(groove_alpha, ink if k % 2 else TEXT_DIM),
                    stroke_width=1,
                    style=ft.PaintingStyle.STROKE,
                ),
            )
        )
        rr += 3.2
        k += 1
    shapes.append(
        cv.Circle(
            c, c, r * 0.30,
            ft.Paint(color=ft.Colors.with_opacity(0.22, ACCENT), stroke_width=1, style=ft.PaintingStyle.STROKE),
        )
    )
    shapes.append(
        cv.Circle(
            c, c, r * 0.30 * 0.24,
            ft.Paint(color=ft.Colors.with_opacity(0.26, TEXT_DIM), stroke_width=1, style=ft.PaintingStyle.STROKE),
        )
    )
    return cv.Canvas(width=size, height=size, shapes=shapes, left=ox - c, top=oy - c)


def _build_rotating_disc(
    ox: float, oy: float, r: float, rpm: float, on_anim_end: Callable[[ft.ControlEvent], None]
) -> ft.Container:
    """Capa giratoria de baja frecuencia: reflejo difuminado + sello asimétrico."""
    size = int(2 * r + 4)
    c = size / 2.0
    dur_ms = max(1000, round(60_000 / abs(rpm)))
    highlight = ft.Image(
        src=_highlight_png_bytes(),
        width=size,
        height=size,
        filter_quality=ft.FilterQuality.HIGH,
    )
    seal = cv.Canvas(
        width=size,
        height=size,
        shapes=[
            cv.Circle(
                c - r * 0.15,
                c - r * 0.16,
                2.5,
                ft.Paint(color=ft.Colors.with_opacity(0.5, ONBOARDING_DISC_SEAL)),
            )
        ],
    )
    return ft.Container(
        width=size,
        height=size,
        left=ox - c,
        top=oy - c,
        rotate=ft.Rotate(angle=0, alignment=ft.Alignment.CENTER),
        animate_rotation=ft.Animation(dur_ms, ft.AnimationCurve.LINEAR),
        on_animation_end=on_anim_end,
        content=ft.Stack(controls=[highlight, seal], width=size, height=size),
    )


class OnboardingBackground(cv.Canvas):
    """Canvas que anima la red mientras ``run()`` esté en ejecución."""

    def __init__(
        self,
        *,
        reduced_motion: bool = False,
        disc_zones: Sequence[tuple[float, float, float]] = (),
        on_resize_cb: Callable[[float, float], None] | None = None,
    ) -> None:
        super().__init__(expand=True, on_resize=self._on_resize)
        self._w, self._h = 1280.0, 800.0
        self._task: asyncio.Task | None = None
        self._reduced_motion = bool(reduced_motion)
        self._disc_zones: list[tuple[float, float, float]] = list(disc_zones)
        self._on_resize_cb = on_resize_cb
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
            cv.Circle(
                0, 0, ONBOARDING_BG_LAYERS[int(p[4])][1],
                ft.Paint(color=ft.Colors.with_opacity(ONBOARDING_BG_LAYERS[int(p[4])][2], ACCENT)),
            )
            for p in self._pts
        ]
        self._frame()

    def set_disc_zones(self, zones: Sequence[tuple[float, float, float]]) -> None:
        """Actualiza las zonas circulares (cx, cy, r_mas_margen_cuadrado) a enmascarar."""
        self._disc_zones = list(zones)

    def _is_masked(self, x: float, y: float) -> bool:
        return any((x - cx) ** 2 + (y - cy) ** 2 < mr2 for cx, cy, mr2 in self._disc_zones)

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
        if self._on_resize_cb is not None:
            self._on_resize_cb(nw, nh)
        self._frame(0.0)

    def _frame(self, dt: float = 0.0) -> None:
        w, h = self._w, self._h
        zones = self._disc_zones
        alive = [True] * len(self._pts)
        for i, (p, c) in enumerate(zip(self._pts, self._circles)):
            p[0] = (p[0] + p[2] * dt) % w
            p[1] = (p[1] + p[3] * dt) % h
            c.x, c.y = p[0], p[1]
            visible = not zones or not self._is_masked(p[0], p[1])
            alive[i] = visible
            alpha = ONBOARDING_BG_LAYERS[int(p[4])][2] if visible else 0.0
            c.paint.color = ft.Colors.with_opacity(alpha, ACCENT)
        link2 = ONBOARDING_BG_LINK ** 2
        lines: list[cv.Shape] = []
        pts = self._pts
        for i in range(len(pts)):
            if not alive[i]:
                continue
            for j in range(i + 1, len(pts)):
                if not alive[j]:
                    continue
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
        if self._reduced_motion:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None


class OnboardingDiscoBackground(ft.Stack):
    """Fondo DISCO (modo H): dos discos en esquinas opuestas + red enmascarada."""

    def __init__(self, *, reduced_motion: bool = False) -> None:
        super().__init__(expand=True)
        self._reduced_motion = bool(reduced_motion)
        self._mounted = False
        self._spinning = False
        self._spin_task: asyncio.Task | None = None
        self._w, self._h = 1280.0, 800.0
        self._rotating: list[ft.Container] = []
        self._net = OnboardingBackground(
            reduced_motion=self._reduced_motion,
            on_resize_cb=self._on_net_resize,
        )
        self._rebuild_discs(self._w, self._h)

    def _on_anim_end(self, e: ft.ControlEvent) -> None:
        if not self._spinning or not self._mounted or self._reduced_motion:
            return
        cur = (e.control.rotate.angle or 0.0) if e.control.rotate else 0.0
        e.control.rotate = ft.Rotate(angle=cur + math.tau, alignment=ft.Alignment.CENTER)
        try:
            e.control.update()
        except Exception:
            pass

    def _rebuild_discs(self, w: float, h: float) -> None:
        self._w, self._h = w, h
        static_layers: list[ft.Control] = []
        rotating_layers: list[ft.Container] = []
        zones: list[tuple[float, float, float]] = []
        for spec in ONBOARDING_DISC_SPECS:
            ox, oy, r, groove_alpha, rpm, ink = _disc_geometry(w, h, spec)
            static_layers.append(_build_static_disc(ox, oy, r, groove_alpha, ink))
            rotating_layers.append(_build_rotating_disc(ox, oy, r, rpm, self._on_anim_end))
            zones.append((ox, oy, (r + ONBOARDING_DISC_MASK_MARGIN) ** 2))
        self._rotating = rotating_layers
        self._net.set_disc_zones(zones)
        self._net._frame(0.0)
        self.controls = [*static_layers, *rotating_layers, self._net]

    def _on_net_resize(self, nw: float, nh: float) -> None:
        if abs(nw - self._w) < 2 and abs(nh - self._h) < 2:
            return
        self._rebuild_discs(nw, nh)
        if self._mounted:
            try:
                self.update()
            except Exception:
                pass
            if self._spinning and not self._reduced_motion:
                self._schedule_spin()

    def _schedule_spin(self) -> None:
        if self._spin_task is not None and not self._spin_task.done():
            self._spin_task.cancel()
        self._spin_task = asyncio.create_task(self._deferred_spin())

    async def _deferred_spin(self) -> None:
        # Esperar un tick tras el montaje para que Flet confirme el ángulo inicial 0.
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            return
        if not self._spinning or not self._mounted or self._reduced_motion:
            return
        for ctrl in self._rotating:
            cur = (ctrl.rotate.angle or 0.0) if ctrl.rotate else 0.0
            ctrl.rotate = ft.Rotate(angle=cur + math.tau, alignment=ft.Alignment.CENTER)
            try:
                ctrl.update()
            except Exception:
                pass

    def did_mount(self) -> None:
        self._mounted = True
        if self._spinning and not self._reduced_motion:
            self._schedule_spin()

    def will_unmount(self) -> None:
        self._mounted = False
        self.stop()

    def start(self) -> None:
        self._net.start()
        if self._reduced_motion:
            return
        self._spinning = True
        if self._mounted:
            self._schedule_spin()

    def stop(self) -> None:
        self._spinning = False
        if self._spin_task is not None and not self._spin_task.done():
            self._spin_task.cancel()
        self._spin_task = None
        self._net.stop()
