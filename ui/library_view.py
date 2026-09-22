"""
ui/library_view.py — Contenedor Biblioteca: Tabs Playlists/Descargas + selector plataforma + grid + detail
"""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import PLATFORM_ORDER
from core.library_models import LibraryLoadState
from services.library_state import LibraryState
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT
from ui.playlist_card import PlaylistCard
from ui.playlist_detail_view import PlaylistDetailView
from ui.tokens import (
    ACCENT, BG_HOVER, BG_LIST, BG_SURFACE, BORDER_LIGHT, BORDER_MUTED,
    TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, app_text, section_label


PLATFORM_ICONS = {
    "YouTube Music": ft.Icons.VIDEO_LIBRARY_OUTLINED,
    "Apple Music": ft.Icons.APPLE,
    "Spotify": ft.Icons.MUSIC_NOTE,
}


class LibraryView(ft.Container):
    def __init__(self, page: ft.Page, library_state: LibraryState, service):
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self.library_state = library_state
        self.service = service
        self.library_state.set_service(service)
        self._tab = "playlists"  # playlists | downloads
        self._platform: str | None = None
        self._detail = None  # PlaylistDetailView
        self._build()
        self.library_state.subscribe(lambda: self._refresh())
        # inicial: selecciona primera plataforma si hay
        if PLATFORM_ORDER:
            self._platform = PLATFORM_ORDER[0]
            self.library_state.select_platform(self._platform)
            # auto sync esa fuente
            try:
                asyncio.create_task(self.library_state.sync_source(self._platform))
            except Exception:
                pass

    def _build(self) -> None:
        # tabs
        try:
            self._tabs = ft.SegmentedButton(
                selected=[self._tab],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE}, color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED}),
                segments=[
                    ft.Segment(value="playlists", label=ft.Text("Playlists", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="downloads", label=ft.Text("Descargas", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=self._on_tab_change,
            )
        except Exception:
            self._tabs = ft.Dropdown(value=self._tab, width=200, options=[ft.dropdown.Option("playlists", "Playlists"), ft.dropdown.Option("downloads", "Descargas")], on_select=self._on_tab_change)  # type: ignore

        # selector plataforma
        self._platform_row = ft.Row(spacing=8, wrap=True)
        self._rebuild_platform_selector()

        self._status_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._refresh_btn = _ghost_btn("Actualizar", ft.Icons.REFRESH, lambda e: self._on_refresh(), width=140, height=36)

        self._grid = ft.GridView(expand=True, max_extent=180, child_aspect_ratio=0.72, spacing=12, run_spacing=12, visible=True)
        self._empty = ft.Container(expand=True, visible=False, alignment=ft.Alignment.CENTER, content=ft.Column(controls=[
            ft.Icon(ft.Icons.LIBRARY_MUSIC_OUTLINED, size=48, color=TEXT_DIM),
            app_text("Sin playlists cacheadas", size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            app_text("Elige una fuente para sincronizar", size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8))
        self._loading_bar = ft.ProgressBar(visible=False, color=ACCENT, bgcolor=BG_SURFACE)

        self._downloads_placeholder = ft.Container(expand=True, visible=False, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER, content=ft.Column(controls=[
            ft.Container(content=ft.Icon(ft.Icons.CONSTRUCTION, size=52, color=TEXT_DIM), bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_LIGHT), border_radius=20, padding=ft.Padding.all(20)),
            app_text("Descargas", size=18, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            app_text("yt-dlp + player + progreso — pronto.", size=12, color=TEXT_MUTED, font_family=FONT_TEXT, text_align=ft.TextAlign.CENTER),
            app_text("Función en desarrollo", size=11, color=TEXT_DIM, font_family=FONT_TEXT),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8))

        self._list_stack = ft.Stack(controls=[self._grid, self._empty, self._downloads_placeholder], expand=True)

        body = ft.Column(controls=[
            self._tabs,
            ft.Divider(height=1, color=BORDER_MUTED),
            section_label("FUENTE"),
            self._platform_row,
            ft.Row(controls=[self._status_text, ft.Container(expand=True), self._refresh_btn], spacing=8),
            self._loading_bar,
            ft.Container(expand=True, content=self._list_stack),
        ], spacing=10, expand=True)

        self.content = ft.Container(content=body, padding=ft.Padding.all(12), expand=True)

    def _rebuild_platform_selector(self) -> None:
        self._platform_row.controls.clear()
        for plat in PLATFORM_ORDER:
            selected = plat == self._platform
            icon = PLATFORM_ICONS.get(plat, ft.Icons.MUSIC_NOTE)
            btn = ft.Container(
                content=ft.Row(controls=[ft.Icon(icon, size=18, color=ACCENT if selected else TEXT_DIM), ft.Text(plat, size=12, color=TEXT_PRIMARY if selected else TEXT_MUTED, font_family=FONT_HEADLINE)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=ACCENT if selected else BG_SURFACE,  # simple; halo variant could be used
                border=ft.Border.all(0.8, ACCENT if selected else BORDER_LIGHT),
                border_radius=10,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                ink=True,
                data=plat,
                on_click=self._on_platform_click,
            )
            # use halo for selected to match rail
            if selected:
                try:
                    from ui.tokens import ACCENT_HALO
                    btn.bgcolor = ACCENT_HALO
                except Exception:
                    pass
            self._platform_row.controls.append(btn)

    def _on_tab_change(self, e) -> None:
        try:
            val = e.control.selected[0] if hasattr(e.control, "selected") else e.control.value
        except Exception:
            val = "playlists"
        self._tab = val
        self._sync_tab_visibility()
        try:
            self.update()
        except Exception:
            pass

    def _sync_tab_visibility(self) -> None:
        is_playlists = self._tab == "playlists"
        self._platform_row.visible = is_playlists
        self._refresh_btn.visible = is_playlists
        self._status_text.visible = is_playlists
        self._grid.visible = is_playlists and self._detail is None
        self._empty.visible = is_playlists and self._detail is None and len(self._grid.controls) == 0
        self._downloads_placeholder.visible = not is_playlists and self._detail is None
        # detail overrides
        if self._detail is not None:
            self._grid.visible = False
            self._empty.visible = False
            self._downloads_placeholder.visible = False

    def _on_platform_click(self, e) -> None:
        plat = str(e.control.data)
        self._platform = plat
        self.library_state.select_platform(plat)
        self._rebuild_platform_selector()
        # auto sync
        try:
            asyncio.create_task(self.library_state.sync_source(plat))
        except Exception:
            pass
        self._refresh()
        try:
            self.update()
        except Exception:
            pass

    def _on_refresh(self) -> None:
        if not self._platform:
            return
        try:
            asyncio.create_task(self.library_state.refresh_source(self._platform))
        except Exception:
            pass

    def _on_open_playlist(self, summary) -> None:
        # push detail
        self._detail = PlaylistDetailView(self._page_ref, summary, self.service, on_back=self._on_back_from_detail)
        self.content = self._detail
        try:
            self.update()
        except Exception:
            pass

    def _on_back_from_detail(self) -> None:
        self._detail = None
        self._build()  # rebuild to restore grid state (simple)
        self._refresh()
        try:
            self.update()
        except Exception:
            pass

    def _refresh(self) -> None:
        # called via library_state notify
        if self._detail is not None:
            return
        # rebuild grid for selected platform
        if self._tab != "playlists":
            self._sync_tab_visibility()
            try:
                self.update()
            except Exception:
                pass
            return
        plat = self._platform or (PLATFORM_ORDER[0] if PLATFORM_ORDER else None)
        if not plat:
            return
        snap = self.library_state.get_snapshot(plat)
        state = snap.state
        # status
        if state == LibraryLoadState.LOADING:
            self._loading_bar.visible = True
            self._status_text.value = "Sincronizando..."
        elif state == LibraryLoadState.STALE:
            self._loading_bar.visible = False
            self._status_text.value = f"Caché obsoleta · {snap.error[:80]}" if snap.error else "Caché obsoleta"
        elif state == LibraryLoadState.ERROR:
            self._loading_bar.visible = False
            self._status_text.value = snap.error[:120] if snap.error else "Error"
        elif state == LibraryLoadState.READY:
            self._loading_bar.visible = False
            self._status_text.value = f"{len(snap.items)} playlists · {snap.synced_at[:10] if snap.synced_at else ''}"
        else:
            self._loading_bar.visible = False
            self._status_text.value = ""
        # grid
        self._grid.controls.clear()
        for summ in snap.items:
            card = PlaylistCard(summ, state=state, on_tap=self._on_open_playlist)
            self._grid.controls.append(card)
        self._sync_tab_visibility()
        # if empty
        if not snap.items and state not in (LibraryLoadState.LOADING,):
            self._empty.visible = True
            self._grid.visible = False
        try:
            self.update()
        except Exception:
            pass
