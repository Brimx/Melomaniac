"""
ui/playlist_card.py — Card Biblioteca con cover single/grilla, nombre, count, estado caché
Reusa tokens OLED, Shimmer opcional, sin controles raros.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

import flet as ft

from core.library_models import LibraryLoadState, PlaylistSummary
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for
from ui.theme.icons import PIcons
from ui.components.song_row import _thumb_content_for
from ui.theme.tokens import (
    ACCENT, BG_HOVER, BG_LIST, BG_SURFACE, BORDER_LIGHT, BORDER_MUTED,
    ERROR_COL, SKELETON_DARK, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY, WARNING,
    MOTION_HOVER_MS,
)
from ui.components.widgets import (
    control_page,
    open_external,
    safe_update,
)
from ui.components.context_menu import (
    ContextMenuAction,
    build_context_menu_button,
    wrap_with_context_menu,
)


def _cover_single(url: str, size: int = 140) -> ft.Control:
    content = _thumb_content_for(url, box=size, icon_size=28)
    return ft.Container(width=size, height=size, border_radius=8, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, alignment=ft.Alignment.CENTER, content=content)


def _cover_grid(urls: list[str], size: int = 140) -> ft.Control:
    # hasta 4 covers en 2x2
    cells = []
    for i in range(4):
        url = urls[i] if i < len(urls) else ""
        c = _thumb_content_for(url, box=(size - 2) // 2, icon_size=16)
        cells.append(ft.Container(expand=True, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, border_radius=4, alignment=ft.Alignment.CENTER, content=c))
    return ft.Container(width=size, height=size, border_radius=8, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=ft.Column(controls=[
        ft.Row(controls=cells[0:2], spacing=2, expand=True),
        ft.Row(controls=cells[2:4], spacing=2, expand=True),
    ], spacing=2, expand=True))


class PlaylistCard(ft.Container):
    def __init__(
        self,
        summary: PlaylistSummary,
        state: LibraryLoadState = LibraryLoadState.READY,
        on_tap=None,
        *,
        on_play: Optional[Callable[[PlaylistSummary], Any]] = None,
        on_shuffle_play: Optional[Callable[[PlaylistSummary], Any]] = None,
        on_add_to_queue: Optional[Callable[[PlaylistSummary], Any]] = None,
        on_download: Optional[Callable[[PlaylistSummary], Any]] = None,
        on_open_external: Optional[Callable[[PlaylistSummary], Any]] = None,
        on_remove: Optional[Callable[[PlaylistSummary], Any]] = None,
        extra_actions: Optional[Sequence[ContextMenuAction]] = None,
    ):
        self.summary = summary
        self._on_tap = on_tap
        self._on_play = on_play
        self._on_shuffle_play = on_shuffle_play
        self._on_add_to_queue = on_add_to_queue
        self._on_download = on_download
        self._on_open_external = on_open_external
        self._on_remove = on_remove
        self._extra_actions = list(extra_actions or ())

        cover: ft.Control
        if len(summary.cover_urls) >= 2:
            cover = _cover_grid(summary.cover_urls[:4])
        else:
            cover = _cover_single(summary.cover_urls[0] if summary.cover_urls else "")

        badge = None
        if state == LibraryLoadState.STALE:
            badge = ft.Container(content=ft.Text("CACHÉ", size=8, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD), bgcolor=ft.Colors.with_opacity(0.85, WARNING), padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=6)
        elif state == LibraryLoadState.ERROR:
            badge = ft.Container(content=ft.Text("ERROR", size=8, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD), bgcolor=ft.Colors.with_opacity(0.85, ERROR_COL), padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=6)

        name = ft.Text(summary.name, size=13, color=TEXT_PRIMARY, font_family=font_family_for(summary.name, "semibold"), max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        count = f"{summary.track_count} canciones" if summary.track_count is not None else "Conteo al abrir"
        meta = ft.Text(f"{count} • {summary.platform}", size=11, color=TEXT_MUTED, font_family=FONT_TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

        # stack cover + badge top-right
        cover_stack = ft.Stack(controls=[cover] + ([ft.Container(content=badge, alignment=ft.Alignment.TOP_RIGHT, padding=ft.Padding.all(6))] if badge else []))

        actions = self.context_actions()
        self._menu_btn = build_context_menu_button(
            self.context_actions,
            tooltip="Opciones de playlist",
            width=24,
            height=24,
            icon_size=15,
            visible=bool(actions),
        )
        footer = ft.Row(
            controls=[ft.Container(content=meta, expand=True), self._menu_btn],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        col = ft.Column(controls=[cover_stack, name, footer], spacing=6, tight=True)
        content = wrap_with_context_menu(
            col,
            self.context_actions,
            title=summary.name,
            get_page=lambda: control_page(self),
        )
        super().__init__(
            content=content,
            width=160,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=12,
            padding=ft.Padding.all(10),
            ink=True,
            on_click=lambda e: on_tap(summary) if on_tap else None,
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            on_hover=self._on_hover,
        )

    def context_actions(self) -> list[ContextMenuAction]:
        """Construye las acciones contextuales disponibles para esta tarjeta."""
        summary = self.summary
        actions: list[ContextMenuAction] = []
        if self._on_tap is not None:
            actions.append(
                ContextMenuAction(
                    "Abrir playlist",
                    icon=PIcons.FOLDER_OPEN,
                    on_click=lambda: self._on_tap(summary),
                )
            )
        if self._on_play is not None:
            actions.append(
                ContextMenuAction(
                    "Reproducir",
                    icon=PIcons.PLAY,
                    on_click=lambda: self._on_play(summary),
                )
            )
        if self._on_shuffle_play is not None:
            actions.append(
                ContextMenuAction(
                    "Aleatorio",
                    icon=PIcons.SHUFFLE,
                    on_click=lambda: self._on_shuffle_play(summary),
                )
            )
        if self._on_add_to_queue is not None:
            actions.append(
                ContextMenuAction(
                    "Añadir a la cola",
                    icon=PIcons.QUEUE,
                    on_click=lambda: self._on_add_to_queue(summary),
                )
            )
        if self._on_download is not None:
            actions.append(
                ContextMenuAction(
                    "Descargar playlist",
                    icon=PIcons.DOWNLOAD_SIMPLE,
                    on_click=lambda: self._on_download(summary),
                )
            )
        if self._on_open_external is not None or summary.external_url:
            def _open_ext() -> None:
                if self._on_open_external is not None:
                    self._on_open_external(summary)
                else:
                    open_external(control_page(self), summary.external_url or "")

            actions.append(
                ContextMenuAction(
                    "Ver en plataforma",
                    icon=PIcons.ARROW_SQUARE_OUT,
                    on_click=_open_ext,
                )
            )
        if self._on_remove is not None:
            actions.append(
                ContextMenuAction(
                    "Quitar de la Biblioteca",
                    icon=PIcons.TRASH,
                    on_click=lambda: self._on_remove(summary),
                    danger=True,
                    divider_before=bool(actions),
                )
            )
        actions.extend(self._extra_actions)
        return actions

    def _on_hover(self, e: ft.HoverEvent) -> None:
        hovering = e.data == "true" or e.data is True
        self.bgcolor = BG_HOVER if hovering else BG_SURFACE
        self.scale = 1.02 if hovering else 1.0
        self.border = ft.Border.all(0.8, ACCENT if hovering else BORDER_LIGHT)
        safe_update(self)
