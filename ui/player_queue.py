"""
ui/player_queue.py — Panel lateral derecho de cola reordenable
══════════════════════════════════════════════════════════════
Usa ``ft.ReorderableListView`` con ``item_extent=ITEM_H``,
``build_controls_on_demand=True`` y ``ft.ReorderableDragHandle`` por fila.
Vive como overlay posicionado en el ``Stack`` raíz (anclado a la derecha),
no como hermano en el ``Row``: abrirlo no cambia el ancho de la vista
activa, así que las listas y la grilla no se redimensionan durante la
animación. El deslizamiento es por ``offset``/opacidad (transform), no
por ancho, y comparte duración de propósito con el rail
(``PLAYER_QUEUE_ANIM_MS`` = ``MOTION_PANEL_MS``).
"""

from __future__ import annotations

import asyncio
import os

import flet as ft

from core.playback import PlayableTrack
from ui.fonts import FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for, mono_family
from ui.icons import PIcons, app_icon, app_icon_button
from ui.song_row import ITEM_H
from ui.tokens import (
    ACCENT, ACCENT_HALO, BG_DEEP, BG_HOVER, BG_PANEL, BG_SURFACE, BORDER_MUTED,
    CHIP_BG, PLAYER_QUEUE_ANIM_MS, PLAYER_QUEUE_W, RADIUS_BUTTON, RADIUS_INPUT,
    TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, app_text, section_label


class PlayerQueuePanel(ft.Container):
    """Panel lateral derecho de cola de reproducción (overlay deslizante)."""

    def __init__(self, page: ft.Page, player_bar) -> None:
        super().__init__(
            width=PLAYER_QUEUE_W,
            right=0,
            top=0,
            bottom=0,
            bgcolor=BG_PANEL,
            border=ft.Border.only(left=ft.BorderSide(0.8, BORDER_MUTED)),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=ft.BoxShadow(
                blur_radius=18,
                color=ft.Colors.with_opacity(0.45, BG_DEEP),
                offset=ft.Offset(-4, 0),
            ),
            offset=ft.Offset(1, 0),
            opacity=0.0,
            animate_offset=ft.Animation(PLAYER_QUEUE_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_opacity=ft.Animation(PLAYER_QUEUE_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            visible=False,
        )
        self._page_ref = page
        self.player_bar = player_bar
        self._is_open: bool = False
        self._open_gen: int = 0

        self._count_txt = app_text(
            "0 pistas", size=11, color=TEXT_MUTED, font_family=FONT_TEXT
        )
        self._clear_btn = _ghost_btn(
            "Limpiar",
            PIcons.TRASH,
            lambda _e: self.player_bar.clear_queue(),
            height=30,
        )
        self._close_btn = app_icon_button(
            PIcons.X,
            size=16,
            icon_color=TEXT_MUTED,
            tooltip="Cerrar cola",
            on_click=lambda _e: self.set_open(False),
            width=30,
            height=30,
        )

        self._list_view = ft.ReorderableListView(
            item_extent=ITEM_H,
            build_controls_on_demand=True,
            show_default_drag_handles=False,
            on_reorder=self._on_reorder,
            expand=True,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )

        self._empty_box = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                controls=[
                    app_icon(PIcons.QUEUE, size=40, color=TEXT_DIM, fill=True),
                    app_text(
                        "Cola vacía",
                        size=13,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_BOLD,
                    ),
                    app_text(
                        "Reproduce una pista desde Descargas o Biblioteca Local",
                        size=11,
                        color=TEXT_MUTED,
                        font_family=FONT_TEXT,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
        )

        self._body_box = ft.Container(
            width=PLAYER_QUEUE_W,
            opacity=0.0,
            animate_opacity=ft.Animation(PLAYER_QUEUE_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            padding=ft.Padding.all(12),
            expand=True,
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    section_label("COLA DE REPRODUCCIÓN"),
                                    self._count_txt,
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            self._clear_btn,
                            self._close_btn,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=BORDER_MUTED),
                    ft.Stack(
                        controls=[self._list_view, self._empty_box],
                        expand=True,
                        fit=ft.StackFit.EXPAND,
                    ),
                ],
                spacing=8,
                expand=True,
            ),
        )
        self.content = self._body_box
        self.player_bar.subscribe(self.refresh)
        self.refresh()

    @property
    def is_open(self) -> bool:
        return self._is_open

    def toggle(self) -> None:
        self.set_open(not self._is_open)

    def set_open(self, open_state: bool) -> None:
        self._is_open = bool(open_state)
        self._open_gen += 1
        gen = self._open_gen
        if self._is_open:
            # Primero se monta en el árbol; la animación de entrada parte del
            # frame siguiente para que el cliente interpolar offset/opacidad
            # desde el estado cerrado en vez de saltar al final.
            self.visible = True
            try:
                self.update()
            except Exception:
                pass
            try:
                self._page_ref.run_task(self._reveal_open, gen)
            except Exception:
                self._apply_open_state()
        else:
            self.offset = ft.Offset(1, 0)
            self.opacity = 0.0
            self._body_box.opacity = 0.0
            try:
                self.update()
            except Exception:
                pass
            try:
                self._page_ref.run_task(self._hide_after_close, gen)
            except Exception:
                self.visible = False
                try:
                    self.update()
                except Exception:
                    pass

    def _apply_open_state(self) -> None:
        self.offset = ft.Offset(0, 0)
        self.opacity = 1.0
        self._body_box.opacity = 1.0
        try:
            self.update()
        except Exception:
            pass

    async def _reveal_open(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._open_gen or not self._is_open:
            return
        self._apply_open_state()

    async def _hide_after_close(self, gen: int) -> None:
        await asyncio.sleep(PLAYER_QUEUE_ANIM_MS / 1000)
        if gen != self._open_gen or self._is_open:
            return
        self.visible = False
        try:
            self.update()
        except Exception:
            pass

    def _on_reorder(self, e: ft.OnReorderEvent) -> None:
        old_idx = getattr(e, "old_index", None)
        new_idx = getattr(e, "new_index", None)
        if old_idx is None or new_idx is None:
            return
        self.player_bar.move_queue_item(int(old_idx), int(new_idx))

    def _build_row(self, queue_pos: int, item: PlayableTrack, is_current: bool) -> ft.Container:
        tr = item.track
        title = tr.name or os.path.basename(item.path)
        artist = tr.artist or "Archivo local"
        ext_badge = (item.container or "AUDIO").upper()
        stable_key = f"qrow_{id(item)}_{item.path}"

        drag_handle = ft.ReorderableDragHandle(
            mouse_cursor=ft.MouseCursor.GRAB,
            content=ft.Container(
                width=24,
                height=ITEM_H - 12,
                alignment=ft.Alignment.CENTER,
                content=app_icon(PIcons.DOTS_SIX_VERTICAL, size=16, color=TEXT_DIM),
            ),
        )

        lead_icon = app_icon(
            PIcons.WAVEFORM if is_current else PIcons.PLAY,
            size=14,
            color=ACCENT if is_current else TEXT_MUTED,
            fill=is_current,
        )
        lead_btn = ft.Container(
            width=26,
            height=26,
            border_radius=RADIUS_BUTTON,
            alignment=ft.Alignment.CENTER,
            tooltip="Reproducir esta pista",
            on_click=lambda _e, pos=queue_pos: self.player_bar.select_queue_index(pos),
            content=lead_icon,
        )

        meta = ft.Container(
            expand=True,
            ink=True,
            on_click=lambda _e, pos=queue_pos: self.player_bar.select_queue_index(pos),
            content=ft.Column(
                controls=[
                    app_text(
                        title,
                        size=12,
                        color=ACCENT if is_current else TEXT_PRIMARY,
                        font_family=font_family_for(title, "bold" if is_current else "regular"),
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    app_text(
                        artist,
                        size=10,
                        color=TEXT_MUTED,
                        font_family=font_family_for(artist),
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=1,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )

        fmt_chip = ft.Container(
            bgcolor=CHIP_BG,
            border=ft.Border.all(0.6, BORDER_MUTED),
            border_radius=4,
            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            tooltip=item.stream_tooltip,
            content=app_text(
                ext_badge,
                size=9,
                color=TEXT_MUTED,
                font_family=mono_family("medium"),
            ),
        )

        remove_btn = app_icon_button(
            PIcons.X,
            size=14,
            icon_color=TEXT_DIM,
            hover_color_icon=ACCENT,
            tooltip="Quitar de la cola",
            on_click=lambda _e, pos=queue_pos: self.player_bar.remove_queue_index(pos),
            width=24,
            height=24,
        )

        return ft.Container(
            key=stable_key,
            height=ITEM_H,
            bgcolor=ACCENT_HALO if is_current else BG_SURFACE,
            border=ft.Border.all(0.6, ACCENT if is_current else BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            margin=ft.Margin.only(bottom=4),
            content=ft.Row(
                controls=[drag_handle, lead_btn, meta, fmt_chip, remove_btn],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def refresh(self) -> None:
        q = self.player_bar.queue
        items = q.ordered_tracks()
        cur = q.current
        self._count_txt.value = f"{len(items)} {'pista' if len(items) == 1 else 'pistas'}"
        self._clear_btn.disabled = not items
        self._list_view.controls = [
            self._build_row(idx, pt, pt is cur)
            for idx, pt in enumerate(items)
        ]
        self._empty_box.visible = not items
        self._list_view.visible = bool(items)
        try:
            self.update()
        except Exception:
            pass
