"""
ui/player_queue.py — Panel lateral derecho de cola reordenable
══════════════════════════════════════════════════════════════
Usa ``ft.ReorderableListView`` con ``item_extent=ITEM_H``,
``build_controls_on_demand=True`` y ``ft.ReorderableDragHandle`` por fila.
Abre y cierra con la misma familia de animación 200-300 ms del rail.
"""

from __future__ import annotations

import os

import flet as ft

from core.playback import PlayableTrack
from ui.fonts import FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for, mono_family
from ui.song_row import ITEM_H
from ui.tokens import (
    ACCENT, ACCENT_HALO, BG_HOVER, BG_PANEL, BG_SURFACE, BORDER_MUTED, CHIP_BG,
    PLAYER_QUEUE_ANIM_MS, PLAYER_QUEUE_W, RADIUS_BUTTON, RADIUS_INPUT,
    RAIL_ITEM_ANIM_MS, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, app_text, section_label


class PlayerQueuePanel(ft.Container):
    """Panel lateral derecho de cola de reproducción."""

    def __init__(self, page: ft.Page, player_bar) -> None:
        super().__init__(
            width=0,
            bgcolor=BG_PANEL,
            border=ft.Border.only(left=ft.BorderSide(0.8, BORDER_MUTED)),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            animate=ft.Animation(PLAYER_QUEUE_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        self._page_ref = page
        self.player_bar = player_bar
        self._is_open: bool = False

        self._count_txt = app_text(
            "0 pistas", size=11, color=TEXT_MUTED, font_family=FONT_TEXT
        )
        self._clear_btn = _ghost_btn(
            "Limpiar",
            ft.Icons.DELETE_SWEEP_OUTLINED,
            lambda _e: self.player_bar.clear_queue(),
            height=30,
        )
        self._close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=16,
            icon_color=TEXT_MUTED,
            tooltip="Cerrar cola",
            on_click=lambda _e: self.set_open(False),
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
                    ft.Icon(ft.Icons.QUEUE_MUSIC_OUTLINED, size=40, color=TEXT_DIM),
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
            animate_opacity=ft.Animation(RAIL_ITEM_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
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
        self.width = PLAYER_QUEUE_W if self._is_open else 0
        self._body_box.opacity = 1.0 if self._is_open else 0.0
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
                content=ft.Icon(ft.Icons.DRAG_INDICATOR, size=16, color=TEXT_DIM),
            ),
        )

        lead_icon = ft.Icon(
            ft.Icons.GRAPHIC_EQ_ROUNDED if is_current else ft.Icons.PLAY_ARROW_ROUNDED,
            size=16,
            color=ACCENT if is_current else TEXT_MUTED,
        )
        lead_btn = ft.Container(
            width=26,
            height=26,
            border_radius=RADIUS_BUTTON,
            alignment=ft.Alignment.CENTER,
            ink=True,
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
                        font_family=font_family_for(title, bold=is_current),
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

        remove_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=14,
            icon_color=TEXT_DIM,
            tooltip="Quitar de la cola",
            on_click=lambda _e, pos=queue_pos: self.player_bar.remove_queue_index(pos),
            style=ft.ButtonStyle(
                padding=4,
                bgcolor={ft.ControlState.HOVERED: BG_HOVER},
                shape=ft.RoundedRectangleBorder(radius=RADIUS_BUTTON),
            ),
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
