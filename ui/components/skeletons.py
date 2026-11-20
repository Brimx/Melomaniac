"""Filas placeholder con shimmer, pulso acotado y soporte de reduced-motion."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import flet as ft

from ui.theme.fonts import mono_family
from ui.theme.tokens import (
    ACCENT,
    BG_SURFACE,
    BORDER_MUTED,
    BORDER_ROW,
    FS_META,
    MOTION_REVEAL_MS,
    MOTION_SKELETON_MS,
    RADIUS_SHARP,
    RADIUS_SM,
    SKELETON_DARK,
    SPACE_4,
    TEXT_MUTED,
)

# Altura fija de fila para cálculos de virtualización
ITEM_H = 64

# Patrón determinista estilo ascii-reveal (dither/scan) en 3 TextSpan por fila
_ASCII_BASE_PATTERN = "░░░░▒░░░▒░░░░▒░░░▒░░░░▒░░░"
_ASCII_BAND_GLYPHS = ("▒▓▓█▓▒", "▓█▓▒░", "▒▓█▓▒")
_ASCII_ROW_LENGTHS = (22, 18, 25, 20, 24, 17, 23, 19)
_ASCII_WAVE_STEP_S = 0.055
_REDUCED_MOTION_TRUTHY = frozenset({"1", "true", "yes", "on", "reduce"})
_REDUCED_MOTION_FALSY = frozenset({"0", "false", "no", "off"})


def is_reduced_motion(page: ft.Page | None = None, *, reduced_motion: bool | None = None) -> bool:
    """Devuelve True si debe omitirse el movimiento decorativo (entorno o página)."""
    if reduced_motion is not None:
        return bool(reduced_motion)
    for var in ("MELOMANIAC_REDUCED_MOTION", "REDUCED_MOTION", "PREFERS_REDUCED_MOTION"):
        raw = os.environ.get(var, "").strip().lower()
        if raw in _REDUCED_MOTION_TRUTHY:
            return True
    if os.environ.get("GTK_ENABLE_ANIMATIONS", "").strip().lower() in _REDUCED_MOTION_FALSY:
        return True
    if page is not None:
        if getattr(page, "reduced_motion", False):
            return True
        media = getattr(page, "media", None)
        if media is not None and getattr(media, "disable_animations", False):
            return True
    try:
        from services.settings_store import get_reduce_motion

        if get_reduce_motion():
            return True
    except Exception:
        pass
    return False


class _BaseSkeletonRow(ft.Container):
    """Base compartida para filas skeleton con Shimmer, barrido ASCII acotado y pulso."""

    def __init__(
        self,
        _index: int,
        *,
        row: ft.Row,
        padding: ft.Padding,
        reveal_left: float,
        reduced_motion: bool | None = None,
    ):
        self._pulse_task: Optional[asyncio.Task] = None
        self._index = _index
        self._reduced_motion = reduced_motion

        try:
            _shimmer: ft.Control = ft.Shimmer(
                base_color=ft.Colors.with_opacity(0.35, SKELETON_DARK),
                highlight_color=ft.Colors.with_opacity(0.9, BG_SURFACE),
                content=row,
            )
        except Exception:
            _shimmer = row

        row_len = _ASCII_ROW_LENGTHS[_index % len(_ASCII_ROW_LENGTHS)]
        self._ascii_base = _ASCII_BASE_PATTERN[:row_len]
        self._span_left = ft.TextSpan(
            self._ascii_base,
            style=ft.TextStyle(color=BORDER_MUTED, font_family=mono_family("light"), size=FS_META),
        )
        self._span_band = ft.TextSpan(
            "",
            style=ft.TextStyle(color=ACCENT, font_family=mono_family("light"), size=FS_META),
        )
        self._span_right = ft.TextSpan(
            "",
            style=ft.TextStyle(color=BORDER_MUTED, font_family=mono_family("light"), size=FS_META),
        )
        self._reveal_text = ft.Text(
            spans=[self._span_left, self._span_band, self._span_right],
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
            no_wrap=True,
        )
        self._reveal_wrap = ft.Container(
            content=self._reveal_text,
            left=reveal_left,
            top=0,
            bottom=0,
            alignment=ft.Alignment.CENTER_LEFT,
            visible=True,
            opacity=0.85,
            animate_opacity=ft.Animation(MOTION_REVEAL_MS, ft.AnimationCurve.EASE_OUT),
        )

        reduce = is_reduced_motion(reduced_motion=reduced_motion)
        super().__init__(
            height=ITEM_H,
            padding=padding,
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
            content=ft.Stack(controls=[_shimmer, self._reveal_wrap]),
            opacity=1.0,
            scale=1.0,
        )

    def _safe_update(self, ctrl: ft.Control | None = None) -> None:
        target = ctrl if ctrl is not None else self
        try:
            target.update()
        except Exception:
            pass

    def _reset_reveal(self) -> None:
        self._span_left.text = self._ascii_base
        self._span_band.text = ""
        self._span_right.text = ""
        self._reveal_wrap.visible = True

    def render_ascii_frame(self, phase: int, *, static: bool = False) -> None:
        """Actualiza los 3 TextSpan con una banda diagonal determinista (sin nodos por carácter)."""
        base = self._ascii_base
        n = len(base)
        if static or n <= 0:
            self._span_left.text = base
            self._span_band.text = ""
            self._span_right.text = ""
            self._safe_update(self._reveal_text)
            return
        band = _ASCII_BAND_GLYPHS[(phase // 2 + self._index) % len(_ASCII_BAND_GLYPHS)]
        bw = len(band)
        cycle = n + bw + 6
        pos = (phase - self._index * 2) % cycle - bw
        b_start = max(0, pos)
        b_end = min(n, pos + bw)
        if b_end <= b_start:
            self._span_left.text = base
            self._span_band.text = ""
            self._span_right.text = ""
        else:
            slice_start = b_start - pos
            slice_end = slice_start + (b_end - b_start)
            self._span_left.text = base[:b_start]
            self._span_band.text = band[slice_start:slice_end]
            self._span_right.text = base[b_end:]
        self._safe_update(self._reveal_text)

    async def start_pulse(self) -> None:
        """Compatibilidad unitaria: corre el barrido ASCII sobre esta sola fila."""
        self._pulse_task = asyncio.current_task()
        try:
            page = self.page
        except Exception:
            page = None
        if is_reduced_motion(page, reduced_motion=self._reduced_motion):
            self.render_ascii_frame(0, static=True)
            return
        phase = 0
        try:
            while True:
                self.render_ascii_frame(phase)
                phase += 1
                await asyncio.sleep(_ASCII_WAVE_STEP_S)
        except asyncio.CancelledError:
            pass

    def stop_pulse(self) -> None:
        if self._pulse_task:
            self._pulse_task.cancel()
            self._pulse_task = None
        try:
            self._reset_reveal()
            self.scale = 1.0
        except Exception:
            pass


async def _run_shared_skeleton_wave(skeletons: list[_BaseSkeletonRow]) -> None:
    """Loop único compartido que anima la banda diagonal ascii-reveal sobre todas las filas."""
    if not skeletons:
        return
    try:
        page = skeletons[0].page
    except Exception:
        page = None
    reduced = is_reduced_motion(page, reduced_motion=skeletons[0]._reduced_motion)
    if reduced:
        for sk in skeletons:
            sk.render_ascii_frame(0, static=True)
        return
    phase = 0
    try:
        while True:
            for sk in skeletons:
                sk.render_ascii_frame(phase)
            phase += 1
            await asyncio.sleep(_ASCII_WAVE_STEP_S)
    except asyncio.CancelledError:
        pass


def start_skeleton_pulse_tasks(
    skeletons: list[_BaseSkeletonRow],
    tasks: list[asyncio.Task],
) -> None:
    """Lanza un único loop compartido de ascii-reveal solo si hay event loop activo."""
    if tasks or not skeletons:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    tasks.append(loop.create_task(_run_shared_skeleton_wave(list(skeletons))))


def stop_skeleton_pulse_tasks(
    skeletons: list[_BaseSkeletonRow],
    tasks: list[asyncio.Task],
) -> None:
    """Detiene y cancela todas las tareas de pulso skeleton de forma segura."""
    for sk in skeletons:
        try:
            sk.stop_pulse()
        except Exception:
            pass
    for task in tasks:
        try:
            if not task.done():
                task.cancel()
        except Exception:
            pass
    tasks.clear()


class SkeletonRow(_BaseSkeletonRow):
    """
    Fila placeholder con Shimmer + zoom sutil (reemplaza opacity pulse).

    Usa ft.Shimmer(base/highlight) para efecto deslizante y mantiene
    animate_scale 600ms para zoom. Dinámico por viewport se calcula en
    ui/main_ui.py (_calc_skeleton_count) y se muestra entre cambios de
    vista / organizar (ver main_ui _show_reload_shimmer).
    """

    def __init__(self, _index: int, *, reduced_motion: bool | None = None):
        # placeholders — dentro de Shimmer serán animados por gradiente; bgcolor es base
        self._num = ft.Container(width=28, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=55, height=55, border_radius=RADIUS_SM, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._status = ft.Container(width=26, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._chk = ft.Container(width=32, height=18, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)

        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur, self._status, self._chk],
            spacing=SPACE_4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        super().__init__(
            _index,
            row=_row,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            reveal_left=115,
            reduced_motion=reduced_motion,
        )



class PreviewSkeletonRow(_BaseSkeletonRow):
    """
    Skeleton espejo de Preview (sin Estado/Sel) — mismo ritmo Shimmer+zoom.
    """

    def __init__(self, _index: int, *, reduced_motion: bool | None = None):
        self._num = ft.Container(width=28, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=55, height=55, border_radius=RADIUS_SM, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur],
            spacing=SPACE_4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        super().__init__(
            _index,
            row=_row,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            reveal_left=115,
            reduced_motion=reduced_motion,
        )


class LibrarySkeletonRow(_BaseSkeletonRow):
    """
    Skeleton Biblioteca — clon de SkeletonRow con zonas adaptadas al detail.

    Gestión tiene [#][thumb][title][album][dur][status][chk]; Biblioteca
    detail tiene [#][thumb48][title][album][dur][open-btn]: se atenúan esas
    6 zonas (sin status/chk). Mismo Shimmer + scale 0.98↔1.0 600ms stagger
    e ITEM_H para paridad exacta con Gestión.
    """

    def __init__(self, _index: int, *, reduced_motion: bool | None = None):
        self._num = ft.Container(width=28, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=48, height=48, border_radius=RADIUS_SM, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        self._action = ft.Container(width=32, height=10, border_radius=RADIUS_SHARP, bgcolor=BG_SURFACE)
        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur, self._action],
            spacing=SPACE_4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        super().__init__(
            _index,
            row=_row,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            reveal_left=108,
            reduced_motion=reduced_motion,
        )

