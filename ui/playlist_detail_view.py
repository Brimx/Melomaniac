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
from ui.song_row import ITEM_H, LibrarySkeletonRow
from ui.tokens import (
    ACCENT, BG_HOVER, BG_LIST, BG_SURFACE, BORDER_LIGHT, BORDER_MUTED, BORDER_ROW,
    SKELETON_DARK, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, _primary_btn, app_text


class PlaylistDetailView(ft.Container):
    def __init__(self, page: ft.Page, summary: PlaylistSummary, service, on_back: Callable[[], None]):
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
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

        # list area — skeletons dinámicos según altura disponible (paridad Inicio)
        self._skeletons: list[LibrarySkeletonRow] = []
        self._skeleton_tasks: list[asyncio.Task] = []
        self._skeleton_list = ft.ListView(expand=True, spacing=0)
        self._skeleton_wrap = ft.Container(content=self._skeleton_list, expand=True, visible=True)
        self._sync_skeleton_count()
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
        """Abre URL externa. ft.Page.launch_url es coroutine: hay que schedulearla.

        Llamarla sync devuelve coroutine 'never awaited' y no abre nada.
        Se schedulean con create_task y fallback a webbrowser si no hay loop.
        """
        if not url:
            return
        page = getattr(self, "_page_ref", None)
        result = None
        try:
            if page is not None and hasattr(page, "launch_url"):
                result = page.launch_url(url)
        except Exception:
            result = None
        if asyncio.iscoroutine(result):
            try:
                asyncio.get_running_loop().create_task(result)
                return
            except RuntimeError:
                try:
                    result.close()
                except Exception:
                    pass
        elif result is not None:
            return  # runtime sync que sí abrió
        try:
            import webbrowser  # fallback desktop

            webbrowser.open(url)
        except Exception:
            pass

    def _calc_detail_skeleton_count(self) -> int:
        """Cuántos LibrarySkeletonRow caben — espejo de main_ui._calc_skeleton_count.

        Resta breadcrumb + header (~150) + divider + padding para overfill.
        """
        try:
            page = getattr(self, "_page_ref", None)
            h = (page.height if page else None) or (page.window.height if page and getattr(page, "window", None) else None) or 900
            if not h or h < 600:
                h = 900
        except Exception:
            h = 900
        header_h = 200  # breadcrumb 36 + header card ~150 + divider/pad
        avail = max(180, h - header_h - 48)
        return max(8, min(20, int(avail // ITEM_H)))

    def _sync_skeleton_count(self) -> None:
        """Recrea skeletons si cambió el count (on_resize) — paridad Inicio."""
        try:
            new_cnt = self._calc_detail_skeleton_count()
            if new_cnt != len(self._skeletons):
                self._stop_skeleton_pulse()
                self._skeletons = [LibrarySkeletonRow(i) for i in range(new_cnt)]
                self._skeleton_list.controls = self._skeletons
                if self._skeleton_wrap.visible:
                    self._ensure_skeletons_pulsing()
                try:
                    self._skeleton_list.update()
                except Exception:
                    pass
        except Exception:
            pass

    def _ensure_skeletons_pulsing(self) -> None:
        try:
            for sk in self._skeletons:
                try:
                    self._skeleton_tasks.append(asyncio.create_task(sk.start_pulse()))
                except Exception:
                    pass
        except Exception:
            pass

    def _stop_skeleton_pulse(self) -> None:
        for sk in self._skeletons:
            try:
                sk.stop_pulse()
            except Exception:
                pass
        for t in self._skeleton_tasks:
            try:
                if not t.done():
                    t.cancel()
            except Exception:
                pass
        self._skeleton_tasks = []

    async def reload(self) -> None:
        self._loading = True
        self._error = ""
        self._sync_skeleton_count()
        self._skeleton_wrap.visible = True
        self._list_view.visible = False
        self._error_box.visible = False
        self._ensure_skeletons_pulsing()
        try:
            self.update()
        except Exception:
            pass
        try:
            meta, tracks = await self.service.fetch_playlist(self.summary.platform, self.summary.id, None)
            self._tracks = tracks
            self._loading = False
            # header count real tras cargar (fix Apple count 0 en summary)
            try:
                self._count.value = f"{len(tracks)} canciones • {self.summary.platform}"
            except Exception:
                pass
            self._list_view.controls = []
            for i, tr in enumerate(tracks, 1):
                row = self._build_track_row(tr, i)
                self._list_view.controls.append(row)
            self._stop_skeleton_pulse()
            self._skeleton_wrap.visible = False
            self._list_view.visible = True
        except Exception as exc:
            self._loading = False
            self._error = str(exc)[:400]
            self._stop_skeleton_pulse()
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
        """Fila Biblioteca: [#][thumb48][título/artista expand3][álbum expand2][dur48][open32].

        Misma geometría que SongRow Inicio (spacing 16, ITEM_H) + columna
        álbum. Hover: BG_HOVER + scale 1.01. Click: press scale 0.99 +
        opacidad 0.85 antes de abrir URL.
        """
        thumb = ft.Container(width=48, height=48, border_radius=6, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=ft.Image(src=track.img_url, fit=ft.BoxFit.COVER, error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=16)) if track.img_url else ft.Container(content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=16), alignment=ft.Alignment.CENTER))
        title = ft.Text(track.name, size=13, color=TEXT_PRIMARY, font_family=font_family_for(track.name, "semibold"), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        artist = ft.Text(track.artist, size=11, color=TEXT_MUTED, font_family=font_family_for(track.artist), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        album = ft.Text(track.album or "—", size=11, color=TEXT_MUTED, font_family=font_family_for(track.album or ""), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        dur = ft.Text(track.duration, size=11, color=TEXT_DIM, font_family=mono_family("light"))
        row = ft.Container(
            height=ITEM_H, bgcolor=BG_LIST, border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)), padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            ink=True,
            animate=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(120, ft.AnimationCurve.EASE_OUT),
            animate_opacity=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
            content=ft.Row(controls=[
                ft.Container(content=ft.Text(str(index), size=11, color=TEXT_MUTED, font_family=mono_family("light")), width=28, alignment=ft.Alignment.CENTER),
                thumb,
                ft.Column(controls=[title, artist], spacing=1, expand=3, tight=True, alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(content=album, expand=2, alignment=ft.Alignment.CENTER_LEFT),
                ft.Container(content=dur, width=48, alignment=ft.Alignment.CENTER),
                ft.Container(content=ft.Icon(ft.Icons.OPEN_IN_NEW, size=14, color=TEXT_DIM), width=32, alignment=ft.Alignment.CENTER),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

        def _on_hover(e: ft.HoverEvent) -> None:
            hovering = e.data == "true"
            row.bgcolor = BG_HOVER if hovering else BG_LIST
            row.scale = 1.01 if hovering else 1.0
            try:
                row.update()
            except Exception:
                pass

        def _on_click(_e) -> None:
            async def _press_and_open() -> None:
                try:
                    row.scale = 0.99
                    row.opacity = 0.85
                    row.update()
                except Exception:
                    pass
                try:
                    await asyncio.sleep(0.08)
                except asyncio.CancelledError:
                    return
                try:
                    row.scale = 1.0
                    row.opacity = 1.0
                    row.update()
                except Exception:
                    pass
                self._launch(track.source_url or "")

            try:
                asyncio.create_task(_press_and_open())
            except Exception:
                self._launch(track.source_url or "")

        row.on_hover = _on_hover
        row.on_click = _on_click
        return row
