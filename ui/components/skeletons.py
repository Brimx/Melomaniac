"""Filas placeholder con shimmer, pulso acotado y soporte de reduced-motion."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import flet as ft

from ui.theme.fonts import mono_family
from ui.theme.tokens import (
    BG_SURFACE,
    BORDER_ROW,
    MOTION_REVEAL_MS,
    MOTION_SKELETON_MS,
    SKELETON_DARK,
    TEXT_MUTED,
)

# Altura fija de fila para cálculos de virtualización
ITEM_H = 64

# Secuencia acotada en ASCII puro para el barrido de entrada en skeletons (una sola pieza mono)
_ASCII_REVEAL_FRAMES = (
    "[=----]",
    "[==---]",
    "[===--]",
    "[====-]",
    "[=====]",
)
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

        self._reveal_text = ft.Text(
            "",
            size=10,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
            opacity=1.0,
        )
        self._reveal_wrap = ft.Container(
            content=self._reveal_text,
            left=reveal_left,
            top=0,
            bottom=0,
            alignment=ft.Alignment.CENTER_LEFT,
            visible=False,
            opacity=0.0,
            animate_opacity=ft.Animation(MOTION_REVEAL_MS, ft.AnimationCurve.EASE_OUT),
        )

        reduce = is_reduced_motion(reduced_motion=reduced_motion)
        super().__init__(
            height=ITEM_H,
            padding=padding,
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
            content=ft.Stack(controls=[_shimmer, self._reveal_wrap]),
            opacity=1.0,
            scale=1.0 if reduce else 0.98,
            animate_scale=None if reduce else ft.Animation(MOTION_SKELETON_MS, ft.AnimationCurve.EASE_IN_OUT),
        )

    def _safe_update(self, ctrl: ft.Control | None = None) -> None:
        target = ctrl if ctrl is not None else self
        try:
            target.update()
        except Exception:
            pass

    def _reset_reveal(self) -> None:
        self._reveal_text.value = ""
        self._reveal_wrap.opacity = 0.0
        self._reveal_wrap.visible = False

    async def _run_ascii_reveal(self) -> None:
        """Barrido ASCII de una sola pasada acotada a MOTION_REVEAL_MS."""
        try:
            page = self.page
        except Exception:
            page = None
        if is_reduced_motion(page, reduced_motion=self._reduced_motion):
            return
        step_s = (MOTION_REVEAL_MS / 1000.0) / len(_ASCII_REVEAL_FRAMES)
        self._reveal_wrap.visible = True
        self._reveal_wrap.opacity = 1.0
        try:
            for frame in _ASCII_REVEAL_FRAMES:
                self._reveal_text.value = frame
                self._safe_update(self._reveal_wrap)
                await asyncio.sleep(step_s)
        finally:
            self._reset_reveal()
            self._safe_update(self._reveal_wrap)

    async def start_pulse(self) -> None:
        """Barrido ASCII acotado al activar + zoom sutil 0.98↔1.0 (600ms, stagger 60ms)."""
        self._pulse_task = asyncio.current_task()
        try:
            page = self.page
        except Exception:
            page = None
        if is_reduced_motion(page, reduced_motion=self._reduced_motion):
            self._reset_reveal()
            self.scale = 1.0
            self._safe_update()
            return
        try:
            await asyncio.sleep(0.06 * self._index)
        except asyncio.CancelledError:
            return
        try:
            await self._run_ascii_reveal()
            pulse_s = MOTION_SKELETON_MS / 1000.0
            while True:
                self.scale = 1.0
                self._safe_update()
                await asyncio.sleep(pulse_s)
                self.scale = 0.98
                self._safe_update()
                await asyncio.sleep(pulse_s)
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


def start_skeleton_pulse_tasks(
    skeletons: list[_BaseSkeletonRow],
    tasks: list[asyncio.Task],
) -> None:
    """Lanza las tareas de pulso skeleton solo si hay un event loop activo."""
    if tasks or not skeletons:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for sk in skeletons:
        tasks.append(loop.create_task(sk.start_pulse()))


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
        self._num = ft.Container(width=28, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=55, height=55, border_radius=8, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._status = ft.Container(width=26, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._chk = ft.Container(width=32, height=18, border_radius=4, bgcolor=BG_SURFACE)

        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur, self._status, self._chk],
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
        self._num = ft.Container(width=28, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=55, height=55, border_radius=8, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=3, bgcolor=BG_SURFACE)
        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur],
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
        self._num = ft.Container(width=28, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=48, height=48, border_radius=6, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._action = ft.Container(width=32, height=10, border_radius=3, bgcolor=BG_SURFACE)
        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur, self._action],
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        super().__init__(
            _index,
            row=_row,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            reveal_left=108,
            reduced_motion=reduced_motion,
        )

