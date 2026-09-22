"""
ui/playlist_card.py — Card Biblioteca con cover single/grilla, nombre, count, estado caché
Reusa tokens OLED, Shimmer opcional, sin controles raros.
"""

from __future__ import annotations

import flet as ft

from core.library_models import LibraryLoadState, PlaylistSummary
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for
from ui.tokens import (
    ACCENT, BG_HOVER, BG_LIST, BG_SURFACE, BORDER_LIGHT, BORDER_MUTED,
    SKELETON_DARK, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)


def _cover_single(url: str, size: int = 140) -> ft.Control:
    if url:
        content = ft.Image(src=url, fit=ft.BoxFit.COVER, error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=28))
    else:
        content = ft.Container(content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=28), alignment=ft.Alignment.CENTER)
    return ft.Container(width=size, height=size, border_radius=8, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=content)


def _cover_grid(urls: list[str], size: int = 140) -> ft.Control:
    # hasta 4 covers en 2x2
    cells = []
    for i in range(4):
        url = urls[i] if i < len(urls) else ""
        if url:
            c = ft.Image(src=url, fit=ft.BoxFit.COVER, error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=16))
        else:
            c = ft.Container(bgcolor=BG_SURFACE, content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=16), alignment=ft.Alignment.CENTER)
        cells.append(ft.Container(expand=True, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, border_radius=4, content=c))
    return ft.Container(width=size, height=size, border_radius=8, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=ft.Column(controls=[
        ft.Row(controls=cells[0:2], spacing=2, expand=True),
        ft.Row(controls=cells[2:4], spacing=2, expand=True),
    ], spacing=2, expand=True))


class PlaylistCard(ft.Container):
    def __init__(self, summary: PlaylistSummary, state: LibraryLoadState = LibraryLoadState.READY, on_tap=None):
        cover: ft.Control
        if len(summary.cover_urls) >= 2:
            cover = _cover_grid(summary.cover_urls[:4])
        else:
            cover = _cover_single(summary.cover_urls[0] if summary.cover_urls else "")

        badge = None
        if state == LibraryLoadState.STALE:
            badge = ft.Container(content=ft.Text("CACHÉ", size=8, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD), bgcolor=ft.Colors.with_opacity(0.85, "#FF8A3B"), padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=6)
        elif state == LibraryLoadState.ERROR:
            badge = ft.Container(content=ft.Text("ERROR", size=8, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD), bgcolor=ft.Colors.with_opacity(0.85, "#FF3D4D"), padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=6)

        name = ft.Text(summary.name, size=13, color=TEXT_PRIMARY, font_family=font_family_for(summary.name, "semibold"), max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        meta = ft.Text(f"{summary.track_count} canciones • {summary.platform}", size=11, color=TEXT_MUTED, font_family=FONT_TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

        # stack cover + badge top-right
        cover_stack = ft.Stack(controls=[cover] + ([ft.Container(content=badge, alignment=ft.Alignment.TOP_RIGHT, padding=ft.Padding.all(6))] if badge else []))

        content = ft.Column(controls=[cover_stack, name, meta], spacing=6, tight=True)
        super().__init__(
            content=content,
            width=160,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=12,
            padding=ft.Padding.all(10),
            ink=True,
            on_click=lambda e: on_tap(summary) if on_tap else None,
            animate=ft.Animation(120, ft.AnimationCurve.EASE_OUT),
            on_hover=self._on_hover,
        )

    def _on_hover(self, e: ft.HoverEvent) -> None:
        self.bgcolor = BG_HOVER if e.data == "true" else BG_SURFACE
        try:
            self.update()
        except Exception:
            pass
