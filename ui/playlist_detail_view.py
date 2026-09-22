"""
ui/playlist_detail_view.py — Detalle playlist Biblioteca: header + Ver en plataforma + lista tracks bajo demanda
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

import flet as ft

from core.library_models import PlaylistSummary
from core.models import Track
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for, mono_family
from ui.song_row import ITEM_H, SkeletonRow
from ui.tokens import (
    ACCENT, BG_LIST, BG_SURFACE, BORDER_LIGHT, BORDER_MUTED, BORDER_ROW,
    SKELETON_DARK, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, _primary_btn, app_text


class PlaylistDetailView(ft.Container):
    def __init__(self, page: ft.Page, summary: PlaylistSummary, service, on_back: Callable[[], None]):
        super().__init__(expand=True, bgcolor=BG_LIST)
        self.page = page
        self.summary = summary
        self.service = service
        self.on_back = on_back
        self._tracks: list[Track] = []
        self._loading = True
        self._error = ""

        # header
        cover_url = summary.cover_urls[0] if summary.cover_urls else ""
        if cover_url:
            cover = ft.Container(width=120, height=120, border_radius=10, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=ft.Image(src=cover_url, fit=ft.BoxFit.COVER, error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM)))
        else:
            cover = ft.Container(width=120, height=120, border_radius=10, bgcolor=SKELETON_DARK, content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=32), alignment=ft.Alignment.CENTER)

        self._title = app_text(summary.name, size=16, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD)
        self._desc = app_text(summary.description or "", size=11, color=TEXT_MUTED, font_family=FONT_TEXT, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS) if summary.description else ft.Container()
        self._count = app_text(f"{summary.track_count} canciones • {summary.platform}", size=11, color=TEXT_DIM, font_family=mono_family("light"))

        self._open_btn = _ghost_btn("Ver playlist en plataforma", ft.Icons.OPEN_IN_NEW, lambda e: self._launch(summary.external_url), width=260, height=40) if summary.external_url else ft.Container()

        header = ft.Container(
            bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_LIGHT), border_radius=12, padding=ft.Padding.all(12),
            content=ft.Row(controls=[cover, ft.Column(controls=[self._title, self._count, self._desc, self._open_btn], spacing=6, expand=True, alignment=ft.MainAxisAlignment.CENTER)], spacing=12),
        )

        # list area
        self._skeleton_wrap = ft.Column(controls=[SkeletonRow(i) for i in range(10)], spacing=0, visible=True)
        self._list_view = ft.ListView(expand=True, spacing=0, visible=False)
        self._error_box = ft.Container(visible=False, bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_MUTED), border_radius=10, padding=ft.Padding.all(12), content=ft.Column(controls=[
            app_text("No se pudo cargar", size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
            _ghost_btn("Reintentar", ft.Icons.REFRESH, lambda e: self.reload()),
        ], spacing=8))

        body = ft.Column(controls=[
            ft.Row(controls=[ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=TEXT_MUTED, on_click=lambda e: on_back()), app_text("Volver", size=12, color=TEXT_MUTED)], spacing=4),
            header,
            ft.Divider(height=1, color=BORDER_MUTED),
            ft.Stack(controls=[self._skeleton_wrap, self._list_view, self._error_box], expand=True),
        ], spacing=10, expand=True)

        self.content = ft.Container(content=body, padding=ft.Padding.all(12), expand=True)
        # kick load
        try:
            asyncio.create_task(self.reload())
        except Exception:
            pass

    def _launch(self, url: str) -> None:
        if not url:
            return
        try:
            self.page.launch_url(url)
        except Exception:
            pass

    async def reload(self) -> None:
        self._loading = True
        self._error = ""
        self._skeleton_wrap.visible = True
        self._list_view.visible = False
        self._error_box.visible = False
        try:
            self.update()
        except Exception:
            pass
        try:
            meta, tracks = await self.service.fetch_playlist(self.summary.platform, self.summary.id, None)
            # enriquecer portal: si summary no tenía cover pero playlist tiene tracks con covers, tomar hasta 4 para grilla (no persistir tracks)
            self._tracks = tracks
            self._loading = False
            self._list_view.controls = []
            for i, tr in enumerate(tracks, 1):
                row = self._build_track_row(tr, i)
                self._list_view.controls.append(row)
            self._skeleton_wrap.visible = False
            self._list_view.visible = True
        except Exception as exc:
            self._loading = False
            self._error = str(exc)[:400]
            self._skeleton_wrap.visible = False
            self._error_box.visible = True
            try:
                self._error_box.content.controls[1].value = self._error  # type: ignore
            except Exception:
                pass
        try:
            self.update()
        except Exception:
            pass

    def _build_track_row(self, track: Track, index: int) -> ft.Container:
        thumb = ft.Container(width=48, height=48, border_radius=6, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=ft.Image(src=track.img_url, fit=ft.BoxFit.COVER, error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=16)) if track.img_url else ft.Container(content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=16), alignment=ft.Alignment.CENTER))
        title = ft.Text(track.name, size=13, color=TEXT_PRIMARY, font_family=font_family_for(track.name, "semibold"), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        artist = ft.Text(track.artist, size=11, color=TEXT_MUTED, font_family=font_family_for(track.artist), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        dur = ft.Text(track.duration, size=11, color=TEXT_DIM, font_family=mono_family("light"))
        row = ft.Container(
            height=ITEM_H, bgcolor=BG_LIST, border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)), padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            ink=True, on_click=lambda e: self._launch(track.source_url or ""),
            content=ft.Row(controls=[
                ft.Container(content=ft.Text(str(index), size=11, color=TEXT_MUTED, font_family=mono_family("light")), width=28, alignment=ft.Alignment.CENTER),
                thumb,
                ft.Column(controls=[title, artist], spacing=1, expand=3, tight=True, alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(content=dur, width=48, alignment=ft.Alignment.CENTER),
                ft.Icon(ft.Icons.OPEN_IN_NEW, size=14, color=TEXT_DIM),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )
        return row
