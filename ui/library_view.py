"""
ui/library_view.py — Contenedor Biblioteca: selector plataforma + grid + detail
"""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import LOCAL_LIBRARY_LABEL, PLATFORM_ORDER
from core.library_models import LibraryLoadState
from engine.parsers import scan_audio_folder
from services.library_state import LibraryState
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT
from ui.playlist_card import PlaylistCard
from ui.playlist_detail_view import PlaylistDetailView
from ui.tokens import (
    ACCENT, BG_HOVER, BG_LIST, BG_SURFACE, BORDER_LIGHT,
    TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, app_text, notify, section_label


PLATFORM_ICONS = {
    "YouTube Music": ft.Icons.VIDEO_LIBRARY_OUTLINED,
    "Apple Music": ft.Icons.APPLE,
    "Spotify": ft.Icons.MUSIC_NOTE,
    LOCAL_LIBRARY_LABEL: ft.Icons.FOLDER_OUTLINED,
}
SOURCES = (*PLATFORM_ORDER, LOCAL_LIBRARY_LABEL)


class LibraryView(ft.Container):
    def __init__(self, page: ft.Page, library_state: LibraryState, service,
                 app_state=None, on_open_downloads=None, player_bar=None):
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self.library_state = library_state
        self.service = service
        self.app_state = app_state
        self._on_open_downloads = on_open_downloads
        self.player_bar = player_bar
        self._picker = ft.FilePicker()
        page.services.append(self._picker)
        self.library_state.set_service(service)
        self._platform: str | None = None
        self._detail = None  # PlaylistDetailView
        self._build()
        self.library_state.subscribe(lambda: self._refresh())
        # inicial: selecciona primera plataforma si hay
        if PLATFORM_ORDER:
            self._platform = PLATFORM_ORDER[0]
            self.library_state.select_platform(self._platform)

    def _build(self) -> None:
        # selector plataforma
        self._platform_row = ft.Row(spacing=8, wrap=True)
        self._rebuild_platform_selector()

        self._status_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._refresh_btn = _ghost_btn("Actualizar", ft.Icons.REFRESH, lambda e: self._on_refresh(), width=140, height=36)
        self._add_folder_btn = _ghost_btn("Añadir carpeta", ft.Icons.CREATE_NEW_FOLDER_OUTLINED,
                                          lambda e: self._page_ref.run_task(self._pick_folder), height=36)
        self._add_folder_btn.visible = self._platform == LOCAL_LIBRARY_LABEL

        self._grid = ft.GridView(expand=True, max_extent=180, child_aspect_ratio=0.72, spacing=12, run_spacing=12, visible=True)
        self._empty_title = app_text("Sin playlists cacheadas", size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD)
        self._empty_sub = app_text("Elige una fuente para sincronizar", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._empty = ft.Container(expand=True, visible=False, alignment=ft.Alignment.CENTER, content=ft.Column(controls=[
            ft.Icon(ft.Icons.LIBRARY_MUSIC_OUTLINED, size=48, color=TEXT_DIM),
            self._empty_title,
            self._empty_sub,
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8))
        self._loading_bar = ft.ProgressBar(visible=False, color=ACCENT, bgcolor=BG_SURFACE)

        self._list_stack = ft.Stack(controls=[self._grid, self._empty], expand=True)

        body = ft.Column(controls=[
            section_label("FUENTE"),
            self._platform_row,
            ft.Row(controls=[self._status_text, ft.Container(expand=True), self._add_folder_btn, self._refresh_btn], spacing=8),
            self._loading_bar,
            ft.Container(expand=True, content=self._list_stack),
        ], spacing=10, expand=True)

        self.content = ft.Container(content=body, padding=ft.Padding.all(12), expand=True)

    def _rebuild_platform_selector(self) -> None:
        self._platform_row.controls.clear()
        for plat in SOURCES:
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

    def _on_platform_click(self, e) -> None:
        plat = str(e.control.data)
        self._platform = plat
        self._rebuild_platform_selector()
        if plat != LOCAL_LIBRARY_LABEL:
            self.library_state.select_platform(plat)
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

    async def _pick_folder(self) -> None:
        try:
            path = await self._picker.get_directory_path(dialog_title="Añadir carpeta a la Biblioteca")
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo abrir el selector: {exc}", kind="error")
            return
        if path and not self.library_state.add_local_folder(path):
            notify(self._page_ref, "Esa carpeta ya está en la Biblioteca")

    def _on_refresh(self) -> None:
        if self._platform == LOCAL_LIBRARY_LABEL:
            self._refresh()
            return
        if not self._platform:
            return
        try:
            asyncio.create_task(self.library_state.refresh_source(self._platform))
        except Exception:
            pass

    def _on_open_playlist(self, summary) -> None:
        # push detail
        local = summary.platform == LOCAL_LIBRARY_LABEL

        async def _load_local():
            return None, await asyncio.to_thread(scan_audio_folder, summary.id)

        def _remove_local() -> None:
            self.library_state.remove_local_folder(summary.id)
            self._on_back_from_detail()

        self._detail = PlaylistDetailView(
            self._page_ref, summary, self.service, on_back=self._on_back_from_detail,
            app_state=self.app_state,
            loader=_load_local if local else None,
            on_remove=_remove_local if local else None,
            on_open_downloads=self._on_open_downloads,
            player_bar=getattr(self, "player_bar", None),
        )
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
        plat = self._platform or (PLATFORM_ORDER[0] if PLATFORM_ORDER else None)
        if not plat:
            return
        if plat == LOCAL_LIBRARY_LABEL:
            self._refresh_local()
            return
        self._add_folder_btn.visible = False
        self._empty_title.value = "Sin playlists cacheadas"
        self._empty_sub.value = "Elige una fuente para sincronizar"
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
        self._grid.visible = True
        self._empty.visible = not snap.items and state != LibraryLoadState.LOADING
        # if empty
        if not snap.items and state not in (LibraryLoadState.LOADING,):
            self._empty.visible = True
            self._grid.visible = False
        try:
            self.update()
        except Exception:
            pass

    def _refresh_local(self) -> None:
        """Fuente Local: una tarjeta por carpeta agregada (sin sincronización)."""
        folders = self.library_state.local_summaries()
        self._add_folder_btn.visible = True
        self._loading_bar.visible = False
        self._status_text.value = f"{len(folders)} carpetas locales"
        self._empty_title.value = "Sin carpetas locales"
        self._empty_sub.value = "Añade una carpeta con audio o indexa tu carpeta de Descargas"
        self._grid.controls = [
            PlaylistCard(summ, state=LibraryLoadState.READY, on_tap=self._on_open_playlist)
            for summ in folders
        ]
        self._grid.visible = bool(folders)
        self._empty.visible = not folders
        try:
            self.update()
        except Exception:
            pass
