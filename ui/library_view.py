"""
ui/library_view.py — Contenedor Biblioteca: selector plataforma + grid + detail
"""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import LOCAL_LIBRARY_LABEL, PLATFORM_ORDER
from core.library_models import LibraryLoadState
from services.library_state import LibraryState
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT
from ui.playlist_card import PlaylistCard
from ui.playlist_detail_view import PlaylistDetailView
from ui.tokens import (
    ACCENT, ACCENT_HALO, BG_HOVER, BG_LIST, BG_SURFACE, BORDER_LIGHT,
    RADIUS_BUTTON, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.icons import PIcons, app_icon, app_icon_button
from ui.widgets import _ghost_btn, app_text, notify, section_label


PLATFORM_ICONS = {
    "YouTube Music": PIcons.YOUTUBE_LOGO,
    "Apple Music": PIcons.APPLE_LOGO,
    "Spotify": PIcons.SPOTIFY_LOGO,
    LOCAL_LIBRARY_LABEL: PIcons.FOLDER,
}
SOURCES = (*PLATFORM_ORDER, LOCAL_LIBRARY_LABEL)

# Etiquetas cortas para el control segmentado (una sola fila, 4 segmentos).
_SHORT_LABELS = {
    "YouTube Music": "YouTube",
    "Apple Music": "Apple",
    "Spotify": "Spotify",
    LOCAL_LIBRARY_LABEL: "Local",
}


class LibraryView(ft.Container):
    def __init__(self, page: ft.Page, library_state: LibraryState, service,
                 app_state=None, on_open_downloads=None, on_open_gestion=None,
                 player_bar=None):
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._on_open_gestion = on_open_gestion
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
        self._platform_row = ft.Row(spacing=8, wrap=False)
        self._rebuild_platform_selector()

        self._status_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._refresh_btn = _ghost_btn("Actualizar", PIcons.ARROWS_CLOCKWISE, lambda e: self._on_refresh(), width=140, height=36)
        self._add_folder_btn = _ghost_btn("Añadir carpeta", PIcons.FOLDER_PLUS,
                                          lambda e: self._page_ref.run_task(self._pick_folder), height=36)
        self._add_folder_btn.visible = self._platform == LOCAL_LIBRARY_LABEL

        self._grid_row = ft.Row(wrap=True, spacing=12, run_spacing=12)
        self._grid = ft.Column(
            controls=[self._grid_row],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            visible=True,
        )
        self._empty_title = app_text("Sin playlists cacheadas", size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD)
        self._empty_sub = app_text("Elige una fuente para sincronizar", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._empty = ft.Container(expand=True, visible=False, alignment=ft.Alignment.CENTER, content=ft.Column(controls=[
            app_icon(PIcons.BOOKS, size=48, color=TEXT_DIM),
            self._empty_title,
            self._empty_sub,
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8))
        self._loading_bar = ft.ProgressBar(visible=False, color=ACCENT, bgcolor=BG_SURFACE)

        self._list_stack = ft.Stack(controls=[self._grid, self._empty], expand=True, fit=ft.StackFit.EXPAND)

        body = ft.Column(controls=[
            section_label("FUENTE"),
            self._platform_row,
            ft.Row(controls=[self._status_text, ft.Container(expand=True), self._add_folder_btn, self._refresh_btn], spacing=8),
            self._loading_bar,
            ft.Container(expand=True, content=self._list_stack),
        ], spacing=10, expand=True)

        self.content = ft.Container(content=body, padding=ft.Padding.all(12), expand=True)

    def _rebuild_platform_selector(self) -> None:
        """Control segmentado de una sola fila: icono + etiqueta corta."""
        self._platform_row.controls.clear()
        for plat in SOURCES:
            selected = plat == self._platform
            icon = PLATFORM_ICONS.get(plat, PIcons.MUSIC_NOTE)
            ico_ctrl = app_icon(icon, size=18, color=ACCENT if selected else TEXT_DIM, fill=selected)
            btn = ft.Container(
                content=ft.Column(
                    controls=[
                        ico_ctrl,
                        ft.Text(
                            _SHORT_LABELS.get(plat, plat),
                            size=10,
                            color=TEXT_PRIMARY if selected else TEXT_MUTED,
                            font_family=FONT_HEADLINE,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                    spacing=3,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                bgcolor=ACCENT_HALO if selected else BG_SURFACE,
                border=ft.Border.all(0.8, ACCENT if selected else BORDER_LIGHT),
                border_radius=RADIUS_BUTTON,
                padding=ft.Padding.symmetric(horizontal=6, vertical=7),
                alignment=ft.Alignment.CENTER,
                ink=True,
                tooltip=plat,
                expand=True,
                data=plat,
                on_click=self._on_platform_click,
            )
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
            # Con índice, solo se lee Mutagen de lo nuevo o modificado; las
            # pistas ya conocidas vuelven con sus tags y su portada.
            from services.local_index import scan_folder_async

            tracks = await scan_folder_async(summary.id)
            return None, tracks

        def _remove_local() -> None:
            self.library_state.remove_local_folder(summary.id)
            self._on_back_from_detail()

        self._detail = PlaylistDetailView(
            self._page_ref, summary, self.service, on_back=self._on_back_from_detail,
            app_state=self.app_state,
            loader=_load_local if local else None,
            on_remove=_remove_local if local else None,
            on_open_downloads=self._on_open_downloads,
            on_open_gestion=self._on_open_gestion,
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

    def _wrap_card(self, summary, state: LibraryLoadState) -> ft.Container:
        """Envuelve la tarjeta con ancho fijo (160 px) para reflow sin resize."""
        return ft.Container(
            width=160,
            alignment=ft.Alignment.TOP_LEFT,
            content=PlaylistCard(summary, state=state, on_tap=self._on_open_playlist),
        )

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
        # wrap de ancho fijo (160 px): al empujar la cola lateral las tarjetas
        # se reacomodan por línea sin recalcular su ancho cuadro a cuadro.
        self._grid_row.controls = [self._wrap_card(summ, state) for summ in snap.items]
        show_empty = not snap.items and state != LibraryLoadState.LOADING
        self._grid.visible = not show_empty
        self._empty.visible = show_empty
        try:
            self.update()
        except Exception:
            pass

    def _refresh_local(self) -> None:
        """Fuente Local: una tarjeta por carpeta agregada (sin sincronización).

        ``local_summaries`` solo recorre hasta ``SYNC_SCAN_LIMIT`` archivos y
        reutiliza portadas del índice, así que cabe en el hilo de la UI; el
        escaneo completo de una carpeta grande ocurre al abrir el detalle, en
        un worker.
        """
        folders = self.library_state.local_summaries()
        self._add_folder_btn.visible = True
        self._loading_bar.visible = False
        self._status_text.value = f"{len(folders)} carpetas locales"
        self._empty_title.value = "Sin carpetas locales"
        self._empty_sub.value = "Añade una carpeta con audio o indexa tu carpeta de Descargas"
        self._grid_row.controls = [
            self._wrap_card(summ, LibraryLoadState.READY) for summ in folders
        ]
        self._grid.visible = bool(folders)
        self._empty.visible = not folders
        try:
            self.update()
        except Exception:
            pass
