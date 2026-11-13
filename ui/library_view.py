"""
ui/library_view.py — Contenedor Biblioteca: selector plataforma + grid + detail
"""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import LOCAL_LIBRARY_LABEL, PLATFORM_ORDER
from core.library_models import LibraryLoadState
from core.models import PlaylistMeta
from engine.local_manifests import detect_platform_from_url
from services.library_state import LibraryState
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT
from ui.playlist_card import PlaylistCard
from ui.playlist_detail_view import PlaylistDetailView
from ui.tokens import (
    ACCENT, ACCENT_HALO, BG_HOVER, BG_LIST, BG_PANEL, BG_SURFACE,
    BORDER_LIGHT, BORDER_MUTED, ERROR_COL, RADIUS_BUTTON, SUCCESS,
    TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.icons import PIcons, app_icon, app_icon_button
from ui.widgets import (
    DialogMixin, _ghost_btn, app_dialog, app_text, app_text_field,
    dialog_action, notify, organize_dropdown, section_label,
)


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


class LibraryView(ft.Container, DialogMixin):
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
        self._import_manifest_btn = _ghost_btn(
            "Importar playlist",
            PIcons.PLAYLIST,
            lambda e: self._open_import_dialog(),
            height=36,
        )
        self._import_manifest_btn.visible = self._platform == LOCAL_LIBRARY_LABEL
        self._add_folder_btn = _ghost_btn("Añadir carpeta", PIcons.FOLDER_PLUS,
                                          lambda e: self._page_ref.run_task(self._pick_folder), height=36)
        self._add_folder_btn.visible = self._platform == LOCAL_LIBRARY_LABEL

        self._playlists_row = ft.Row(wrap=True, spacing=12, run_spacing=12)
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
            ft.Row(
                controls=[
                    self._status_text,
                    ft.Container(expand=True),
                    self._import_manifest_btn,
                    self._add_folder_btn,
                    self._refresh_btn,
                ],
                spacing=8,
            ),
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
        is_manifest = bool(
            hasattr(self.library_state, "is_imported_manifest")
            and self.library_state.is_imported_manifest(summary)
        )
        local = summary.platform == LOCAL_LIBRARY_LABEL

        async def _load_manifest():
            manifest = await asyncio.to_thread(self.library_state.load_local_manifest, summary.id)
            if manifest is None:
                raise FileNotFoundError("El manifiesto local ya no existe")
            meta = PlaylistMeta(name=manifest.name, description=manifest.description)
            return meta, list(manifest.tracks)

        def _remove_manifest() -> None:
            self.library_state.remove_local_manifest(summary.id)
            self._on_back_from_detail()

        async def _load_local():
            # Con índice, solo se lee Mutagen de lo nuevo o modificado; las
            # pistas ya conocidas vuelven con sus tags y su portada.
            from services.local_index import scan_folder_async

            tracks = await scan_folder_async(summary.id)
            return None, tracks

        def _remove_local() -> None:
            is_downloaded_sub = bool(
                not is_manifest
                and local
                and str(summary.id) not in set(self.library_state.local_folders)
                and hasattr(self.library_state, "remove_downloaded_playlist")
            )
            if is_downloaded_sub:
                self.library_state.remove_downloaded_playlist(summary.id)
            else:
                self.library_state.remove_local_folder(summary.id)
            self._on_back_from_detail()

        loader = _load_manifest if is_manifest else (_load_local if local else None)
        on_remove = _remove_manifest if is_manifest else (_remove_local if local else None)

        self._detail = PlaylistDetailView(
            self._page_ref, summary, self.service, on_back=self._on_back_from_detail,
            app_state=self.app_state,
            loader=loader,
            on_remove=on_remove,
            on_open_downloads=self._on_open_downloads,
            on_open_gestion=self._on_open_gestion,
            player_bar=getattr(self, "player_bar", None),
            is_imported_manifest=is_manifest,
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

    def _wrap_card(
        self,
        summary,
        state: LibraryLoadState,
        *,
        badge_text: str = "",
        badge_bgcolor: str = ACCENT_HALO,
        badge_border: str = ACCENT,
        on_save_manifest=None,
        on_remove_manifest=None,
    ) -> ft.Container:
        """Envuelve la tarjeta con ancho fijo (160 px) para reflow sin resize."""
        card = PlaylistCard(summary, state=state, on_tap=self._on_open_playlist)
        overlays: list[ft.Control] = []

        if badge_text:
            overlays.append(
                ft.Container(
                    content=ft.Text(
                        badge_text,
                        size=8,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_BOLD,
                    ),
                    bgcolor=badge_bgcolor,
                    border=ft.Border.all(0.8, badge_border),
                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                    border_radius=6,
                    left=14,
                    top=14,
                )
            )

        if on_save_manifest is not None:
            overlays.append(
                ft.Container(
                    content=app_icon_button(
                        PIcons.FLOPPY_DISK,
                        size=14,
                        width=26,
                        height=26,
                        bgcolor=BG_PANEL,
                        hover_bgcolor=BG_HOVER,
                        border=ft.Border.all(0.8, BORDER_MUTED),
                        hover_border=ft.Border.all(0.8, ACCENT),
                        border_radius=13,
                        icon_color=TEXT_MUTED,
                        hover_color_icon=ACCENT,
                        tooltip="Importar a Local sin descargar",
                        on_click=on_save_manifest,
                    ),
                    left=14,
                    top=14,
                )
            )

        if on_remove_manifest is not None:
            overlays.append(
                ft.Container(
                    content=app_icon_button(
                        PIcons.TRASH,
                        size=14,
                        width=26,
                        height=26,
                        bgcolor=BG_PANEL,
                        hover_bgcolor=BG_HOVER,
                        border=ft.Border.all(0.8, BORDER_MUTED),
                        hover_border=ft.Border.all(0.8, ERROR_COL),
                        border_radius=13,
                        icon_color=TEXT_MUTED,
                        hover_color_icon=ERROR_COL,
                        tooltip="Quitar playlist importada",
                        on_click=on_remove_manifest,
                    ),
                    right=14,
                    top=14,
                )
            )

        content: ft.Control = ft.Stack(controls=[card, *overlays]) if overlays else card
        return ft.Container(
            width=160,
            alignment=ft.Alignment.TOP_LEFT,
            content=content,
        )

    def _remove_manifest(self, summary) -> None:
        """Elimina un manifiesto local importado desde la vista Local."""
        if not hasattr(self.library_state, "remove_local_manifest"):
            return
        removed = self.library_state.remove_local_manifest(summary.id)
        if removed:
            if self._detail is None:
                self._refresh()
            try:
                notify(self._page_ref, f"Manifiesto «{summary.name}» quitado")
            except Exception:
                pass
        else:
            try:
                notify(self._page_ref, "No se pudo quitar el manifiesto", kind="error")
            except Exception:
                pass

    def _save_remote_as_manifest(self, summary) -> None:
        """Guarda una playlist remota como manifiesto local sin descargar audio."""
        if not hasattr(self.library_state, "import_summary_as_manifest"):
            return
        if self.service is None:
            notify(self._page_ref, "Servicio no disponible para importar la playlist", kind="error")
            return

        async def _do_save() -> None:
            try:
                manifest = await self.library_state.import_summary_as_manifest(summary)
            except Exception as exc:  # noqa: BLE001
                notify(self._page_ref, f"No se pudo importar: {exc}", kind="error")
                return
            notify(
                self._page_ref,
                f"Guardada en Local: «{manifest.name}» ({len(manifest.tracks)} pistas, sin audio)",
            )

        try:
            self._page_ref.run_task(_do_save)
        except Exception:
            try:
                asyncio.create_task(_do_save())
            except Exception:
                pass

    def _open_import_dialog(self) -> None:
        """Abre diálogo para importar una playlist remota como manifiesto local."""
        entries_by_plat: dict[str, list[tuple[str, object]]] = {}
        cached_by_key: dict[str, object] = {}
        for plat in PLATFORM_ORDER:
            snap = self.library_state.get_snapshot(plat)
            plat_entries: list[tuple[str, object]] = []
            for summ in snap.items:
                key = f"{plat}::{summ.id}"
                plat_entries.append((key, summ))
                cached_by_key[key] = summ
            if plat_entries:
                entries_by_plat[plat] = plat_entries

        default_plat = (
            self._platform
            if getattr(self, "_platform", None) in PLATFORM_ORDER
            else (PLATFORM_ORDER[0] if PLATFORM_ORDER else "Spotify")
        )
        platform_dd = organize_dropdown(
            [(p, p) for p in PLATFORM_ORDER],
            value=default_plat,
            label="Plataforma",
            width=380,
        )
        url_input = app_text_field(
            label="URL o ID de playlist",
            hint_text="https://open.spotify.com/playlist/... o ID",
            autofocus=True,
        )
        owner_input = app_text_field(
            label="Propietario / autor (opcional)",
            hint_text="Ej. Curador, artista o usuario",
        )
        status_txt = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)

        selected_summary: dict[str, object | None] = {"item": None}

        def _options_for_platform(plat: str) -> list[tuple[str, str]]:
            plat_items = entries_by_plat.get(plat, [])
            return [("", "— Escribir URL o ID manualmente —")] + [
                (key, s.name) for key, s in plat_items
            ]

        has_any_cached = bool(cached_by_key)
        cached_dd = organize_dropdown(
            _options_for_platform(default_plat),
            value="",
            label="Playlist sincronizada",
            width=380,
        )
        cached_dd.visible = has_any_cached

        def _sync_cached_for_platform(plat: str, *, clear_pick: bool = True) -> None:
            if not has_any_cached:
                return
            plat_opts = _options_for_platform(plat)
            cached_dd.options = [
                ft.dropdown.Option(key=k, text=lbl) for k, lbl in plat_opts
            ]
            cached_dd.visible = len(plat_opts) > 1
            if clear_pick:
                cached_dd.value = ""
                selected_summary["item"] = None
            try:
                cached_dd.update()
            except Exception:
                pass

        def _on_platform_change(e) -> None:
            plat = str(getattr(e.control, "value", "") or default_plat)
            _sync_cached_for_platform(plat, clear_pick=True)

        def _on_pick_cached(e) -> None:
            chosen = cached_by_key.get(str(e.control.value or ""))
            selected_summary["item"] = chosen
            if chosen is None:
                return
            url_input.value = chosen.external_url or chosen.id
            try:
                url_input.update()
            except Exception:
                pass

        def _on_url_change(_e) -> None:
            selected_summary["item"] = None
            detected = detect_platform_from_url(url_input.value or "")
            if detected and detected in PLATFORM_ORDER and platform_dd.value != detected:
                platform_dd.value = detected
                try:
                    platform_dd.update()
                except Exception:
                    pass
                _sync_cached_for_platform(detected, clear_pick=True)

        platform_dd.on_select = _on_platform_change
        cached_dd.on_select = _on_pick_cached
        url_input.on_change = _on_url_change

        controls: list[ft.Control] = [platform_dd]
        if has_any_cached:
            controls.append(cached_dd)
        controls.extend([url_input, owner_input, status_txt])
        dlg_body = ft.Column(controls=controls, spacing=10, tight=True)

        dlg: ft.AlertDialog | None = None
        busy = False

        def _close(_e=None) -> None:
            self.close_dialog(self._page_ref, dlg)

        def _on_submit(_e=None) -> None:
            nonlocal busy
            if busy:
                return
            raw_id = (url_input.value or "").strip()
            plat = str(platform_dd.value or default_plat)
            owner = (owner_input.value or "").strip()
            if not raw_id:
                status_txt.value = "Introduce una URL o ID de playlist."
                status_txt.color = ERROR_COL
                try:
                    status_txt.update()
                except Exception:
                    pass
                return
            if self.service is None:
                status_txt.value = "Servicio no disponible; inicia sesión en una plataforma."
                status_txt.color = ERROR_COL
                try:
                    status_txt.update()
                except Exception:
                    pass
                return

            busy = True
            import_btn.disabled = True
            status_txt.value = "Importando metadatos sin descargar audio..."
            status_txt.color = TEXT_MUTED
            try:
                import_btn.update()
                status_txt.update()
            except Exception:
                pass

            async def _run_import() -> None:
                nonlocal busy
                chosen = selected_summary["item"]
                try:
                    manifest = await self.library_state.import_playlist_manifest(
                        plat,
                        raw_id,
                        owner=owner,
                        name=str(getattr(chosen, "name", "") or "") if chosen is not None else "",
                        cover_urls=list(getattr(chosen, "cover_urls", None) or []) if chosen is not None else None,
                        external_url=str(getattr(chosen, "external_url", "") or "") if chosen is not None else "",
                    )
                except Exception as exc:  # noqa: BLE001
                    busy = False
                    import_btn.disabled = False
                    status_txt.value = f"Error al importar: {exc}"
                    status_txt.color = ERROR_COL
                    try:
                        import_btn.update()
                        status_txt.update()
                    except Exception:
                        pass
                    return
                self.close_dialog(self._page_ref, dlg)
                notify(
                    self._page_ref,
                    f"Importada «{manifest.name}» ({len(manifest.tracks)} pistas) sin descargar audio",
                )

            try:
                self._page_ref.run_task(_run_import)
            except Exception:
                asyncio.create_task(_run_import())

        cancel_btn = dialog_action("Cancelar", _close, kind="muted", icon=PIcons.X)
        import_btn = dialog_action("Importar sin descargar", _on_submit, kind="primary", icon=PIcons.FLOPPY_DISK)
        dlg = app_dialog(
            "Importar playlist sin descargar",
            dlg_body,
            [cancel_btn, import_btn],
            width=400,
            icon=PIcons.PLAYLIST,
        )
        self.show_dialog(self._page_ref, dlg)

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
        self._import_manifest_btn.visible = False
        self._grid.controls = [self._grid_row]
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
        can_save = hasattr(self.library_state, "import_summary_as_manifest")
        self._grid_row.controls = [
            self._wrap_card(
                summ,
                state,
                on_save_manifest=(lambda _e, s=summ: self._save_remote_as_manifest(s)) if can_save else None,
            )
            for summ in snap.items
        ]
        show_empty = not snap.items and state != LibraryLoadState.LOADING
        self._grid.visible = not show_empty
        self._empty.visible = show_empty
        try:
            self.update()
        except Exception:
            pass

    def _refresh_local(self) -> None:
        """Fuente Local: distingue carpetas locales y playlists importadas/descargadas."""
        if hasattr(self.library_state, "local_folder_summaries"):
            folders = self.library_state.local_folder_summaries()
            downloaded = self.library_state.downloaded_playlist_summaries()
            imported = self.library_state.imported_manifest_summaries()
        else:
            folders = self.library_state.local_summaries()
            downloaded = []
            imported = []

        self._add_folder_btn.visible = True
        self._import_manifest_btn.visible = True
        self._loading_bar.visible = False

        status_parts: list[str] = []
        if imported:
            status_parts.append(f"{len(imported)} importadas (sin audio)")
        if downloaded:
            status_parts.append(f"{len(downloaded)} descargadas")
        if folders:
            status_parts.append(f"{len(folders)} carpetas locales")
        self._status_text.value = " · ".join(status_parts) if status_parts else "0 elementos locales"

        self._empty_title.value = "Sin carpetas ni playlists locales"
        self._empty_sub.value = "Añade una carpeta con audio o importa una playlist sin descargar"

        sections: list[ft.Control] = []
        playlist_cards: list[ft.Control] = [
            self._wrap_card(
                summ,
                LibraryLoadState.READY,
                badge_text="SIN AUDIO",
                badge_bgcolor=ACCENT_HALO,
                badge_border=ACCENT,
                on_remove_manifest=lambda _e, s=summ: self._remove_manifest(s),
            )
            for summ in imported
        ] + [
            self._wrap_card(
                summ,
                LibraryLoadState.READY,
                badge_text="DESCARGADA",
                badge_bgcolor=BG_PANEL,
                badge_border=SUCCESS,
                on_remove_manifest=(
                    (lambda _e, s=summ: (
                        self.library_state.remove_downloaded_playlist(s.id)
                        and self._refresh()
                    ))
                    if hasattr(self.library_state, "remove_downloaded_playlist")
                    else None
                ),
            )
            for summ in downloaded
        ]
        self._playlists_row.controls = playlist_cards
        if playlist_cards:
            sections.append(section_label("PLAYLISTS IMPORTADAS Y DESCARGADAS"))
            sections.append(self._playlists_row)

        self._grid_row.controls = [
            self._wrap_card(
                summ,
                LibraryLoadState.READY,
                badge_text="CARPETA",
                badge_bgcolor=BG_PANEL,
                badge_border=BORDER_LIGHT,
            )
            for summ in folders
        ]
        if folders:
            sections.append(section_label("CARPETAS LOCALES"))
            sections.append(self._grid_row)

        self._grid.controls = sections if sections else [self._grid_row]
        has_any = bool(folders or downloaded or imported)
        self._grid.visible = has_any
        self._empty.visible = not has_any
        try:
            self.update()
        except Exception:
            pass
