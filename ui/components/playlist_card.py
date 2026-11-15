"""
ui/playlist_card.py — Card Biblioteca con cover single/grilla, nombre, count, estado caché
Reusa tokens OLED, Shimmer opcional, sin controles raros.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional, Sequence

import flet as ft

from core.library_models import LibraryLoadState, PlaylistSummary
from services import settings_store
from ui.theme.fonts import FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for, mono_family
from ui.theme.icons import PIcons
from ui.components.song_row import _thumb_content_for
from ui.theme.tokens import (
    ACCENT, BG_DEEP, BG_HOVER, BG_SURFACE, BORDER_LIGHT, BORDER_MUTED,
    ERROR_COL, FS_META, MOTION_HOVER_MS, RADIUS_SHARP, RADIUS_SM,
    SKELETON_DARK, SPACE_0, SPACE_1, SPACE_2, TEXT_MUTED, TEXT_PRIMARY, WARNING,
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


_OBI_LABELS = {
    "YouTube Music": "YOUTUBE",
    "Apple Music": "APPLE",
    "Spotify": "SPOTIFY",
    "Local": "LOCAL",
}


def _should_show_obi() -> bool:
    getter = getattr(settings_store, "get_show_platform_obi", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception:
            return True
    try:
        return bool(settings_store.load_settings().get("show_platform_obi", True))
    except Exception:
        return True


def _obi_label_for(platform: str, explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit.strip().upper()[:12]
    mapped = _OBI_LABELS.get(platform)
    if mapped:
        return mapped
    return str(platform or "").strip().upper()[:10]


def _build_obi_strip(label: str, cover_size: int = 140) -> ft.Control:
    """Lomo obi vertical sobre el borde izquierdo de la portada (estilo demo)."""
    strip_len = max(72, int(cover_size * 0.72))
    strip_w = 15
    return ft.Container(
        width=strip_len,
        height=strip_w,
        bgcolor=TEXT_PRIMARY,
        border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.35, BG_DEEP))),
        alignment=ft.Alignment.CENTER,
        rotate=ft.Rotate(-math.pi / 2),
        left=-(strip_len - strip_w) / 2,
        top=(cover_size - strip_w) / 2,
        content=ft.Text(
            label,
            size=FS_META,
            color=BG_SURFACE,
            font_family=mono_family("regular"),
            max_lines=1,
            no_wrap=True,
        ),
    )


def _cover_single(url: str, size: int = 140) -> ft.Container:
    content = _thumb_content_for(url, box=size, icon_size=28)
    return ft.Container(
        width=size,
        height=size,
        border_radius=RADIUS_SM,
        bgcolor=SKELETON_DARK,
        border=ft.Border.all(1, BORDER_MUTED),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        alignment=ft.Alignment.CENTER,
        content=content,
    )


def _cover_grid(urls: list[str], size: int = 140) -> ft.Container:
    # hasta 4 covers en 2x2
    cells = []
    for i in range(4):
        url = urls[i] if i < len(urls) else ""
        c = _thumb_content_for(url, box=(size - 2) // 2, icon_size=16)
        cells.append(ft.Container(expand=True, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, border_radius=RADIUS_SHARP, alignment=ft.Alignment.CENTER, content=c))
    return ft.Container(
        width=size,
        height=size,
        border_radius=RADIUS_SM,
        border=ft.Border.all(1, BORDER_MUTED),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Column(controls=[
            ft.Row(controls=cells[0:2], spacing=SPACE_1, expand=True),
            ft.Row(controls=cells[2:4], spacing=SPACE_1, expand=True),
        ], spacing=SPACE_1, expand=True),
    )


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
        obi_label: Optional[str] = None,
        subtitle_line2: Optional[str] = None,
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

        cover_size = 140
        if len(summary.cover_urls) >= 2:
            self._cover_box = _cover_grid(summary.cover_urls[:4], size=cover_size)
        else:
            self._cover_box = _cover_single(summary.cover_urls[0] if summary.cover_urls else "", size=cover_size)

        badge = None
        if state == LibraryLoadState.STALE:
            badge = ft.Container(content=ft.Text("CACHÉ", size=FS_META, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD), bgcolor=ft.Colors.with_opacity(0.85, WARNING), padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=RADIUS_SM)
        elif state == LibraryLoadState.ERROR:
            badge = ft.Container(content=ft.Text("ERROR", size=FS_META, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD), bgcolor=ft.Colors.with_opacity(0.85, ERROR_COL), padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=RADIUS_SM)

        stack_controls: list[ft.Control] = [self._cover_box]
        obi_text = _obi_label_for(summary.platform, obi_label)
        if _should_show_obi() and obi_text:
            stack_controls.append(_build_obi_strip(obi_text, cover_size=cover_size))
        if badge is not None:
            stack_controls.append(ft.Container(content=badge, alignment=ft.Alignment.TOP_RIGHT, padding=ft.Padding.all(6)))

        cover_stack = ft.Stack(
            width=cover_size,
            height=cover_size,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            controls=stack_controls,
        )

        name = ft.Text(
            summary.name,
            size=13,
            color=TEXT_PRIMARY,
            font_family=font_family_for(summary.name, "semibold"),
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        line1_text = f"{summary.track_count} canciones" if summary.track_count is not None else "Conteo al abrir"
        line2_default = (
            f"Sync {summary.synced_at[:10]}"
            if getattr(summary, "synced_at", "")
            else summary.platform
        )
        line2_val = subtitle_line2 if subtitle_line2 is not None else line2_default

        meta_line1 = ft.Text(
            line1_text,
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("regular"),
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        meta_line2 = ft.Text(
            line2_val,
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("regular"),
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

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
            controls=[
                ft.Column(controls=[meta_line1, meta_line2], spacing=SPACE_0, tight=True, expand=True),
                self._menu_btn,
            ],
            spacing=SPACE_1,
            vertical_alignment=ft.CrossAxisAlignment.END,
        )

        col = ft.Column(controls=[cover_stack, name, footer], spacing=SPACE_2, tight=True)
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
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_SM,
            padding=ft.Padding.all(10),
            ink=True,
            on_click=lambda e: on_tap(summary) if on_tap else None,
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            on_hover=self._on_hover,
        )
        self._isolated = True

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
        self.border = ft.Border.all(0.8, ACCENT if hovering else BORDER_MUTED)
        if hasattr(self, "_cover_box"):
            self._cover_box.border = ft.Border.all(1, BORDER_LIGHT if hovering else BORDER_MUTED)
        safe_update(self)
