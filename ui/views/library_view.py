"""
ui/library_view.py — Contenedor Biblioteca: selector plataforma + grid + detail
"""

from __future__ import annotations

import asyncio
from datetime import date

import flet as ft

from core.config import LOCAL_LIBRARY_LABEL, PLATFORM_ORDER
from core.library_models import LibraryLoadState
from core.models import PlaylistMeta
from engine.local_manifests import detect_platform_from_url
from services import settings_store
from services.library_state import LibraryState
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.components.playlist_card import PlaylistCard
from ui.views.playlist_detail_view import PlaylistDetailView
from ui.theme.tokens import (
    ACCENT, ACCENT_HALO, BG_HOVER, BG_LIST, BG_PANEL, BG_SURFACE,
    BORDER_LIGHT, BORDER_MUTED, ERROR_COL, FS_META, FS_PAGE, MOTION_HOVER_MS,
    RADIUS_PILL, RADIUS_SHARP, RADIUS_SM, SPACE_0, SPACE_1, SPACE_2, SPACE_3,
    SPACE_5, SUCCESS, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.components.widgets import (
    _ghost_btn,
    app_dialog,
    app_text,
    app_text_field,
    close_dialog,
    dialog_action,
    empty_state,
    kicker,
    notify,
    organize_dropdown,
    section_label,
    show_dialog,
    tab_underline,
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


_FILTER_DEFS = (
    ("any", "Con canciones"),
    ("big", "+100 pistas"),
    ("today", "Sincronizada hoy"),
)
_SORT_LABELS = ("Recientes", "A a Z", "Z a A")


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
        self._search_query: str = ""
        self._active_filters: set[str] = set()
        self._sort_mode: int = 0
        self._local_tab: str = "carpetas"
        self._recent_opened_ids: list[tuple[str, str]] = []
        self._show_obi: bool = self._read_show_obi()
        self._build()
        self.library_state.subscribe(lambda: self._refresh())
        # inicial: selecciona primera plataforma si hay
        if PLATFORM_ORDER:
            self._platform = PLATFORM_ORDER[0]
            self.library_state.select_platform(self._platform)

    def _build(self) -> None:
        # selector plataforma (tab_underline compacto)
        self._platform_btns = {}
        self._platform_row = ft.Row(spacing=SPACE_5, tight=True)
        self._rebuild_platform_selector()

        self._status_text = app_text("", size=11, color=TEXT_MUTED, font_family=mono_family("regular"))
        self._refresh_btn = _ghost_btn("Actualizar", PIcons.ARROWS_CLOCKWISE, lambda e: self._on_refresh(), width=128, height=34)
        self._import_manifest_btn = _ghost_btn(
            "Importar playlist",
            PIcons.PLAYLIST,
            lambda e: self._open_import_dialog(),
            height=34,
        )
        self._import_manifest_btn.visible = self._platform == LOCAL_LIBRARY_LABEL
        self._add_folder_btn = _ghost_btn("Añadir carpeta", PIcons.FOLDER_PLUS,
                                          lambda e: self._page_ref.run_task(self._pick_folder), height=34)
        self._add_folder_btn.visible = self._platform == LOCAL_LIBRARY_LABEL

        # Barra 3.2: sub-pestañas Local (3.3), búsqueda, filtros acumulativos y orden 3 estados
        self._local_tab_btns: dict[str, ft.Container] = {}
        for tab_key, tab_lbl in (("carpetas", "Carpetas"), ("playlists", "Playlists")):
            btn = ft.Container(
                content=app_text(tab_lbl, size=11, color=TEXT_PRIMARY, font_family=FONT_HEADLINE),
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                border_radius=RADIUS_SHARP,
                bgcolor=ACCENT_HALO if self._local_tab == tab_key else BG_SURFACE,
                border=ft.Border.all(0.8, ACCENT if self._local_tab == tab_key else BORDER_MUTED),
                ink=True,
                on_click=lambda _e, k=tab_key: self._set_local_tab(k),
            )
            self._local_tab_btns[tab_key] = btn
        self._local_seg = ft.Row(
            controls=list(self._local_tab_btns.values()),
            spacing=SPACE_2,
            visible=self._platform == LOCAL_LIBRARY_LABEL,
        )

        self._search_field = app_text_field(
            value=self._search_query,
            hint_text="Buscar playlist o carpeta...",
            width=230,
            height=34,
            prefix_icon=PIcons.MAGNIFYING_GLASS,
            on_change=self._on_search_change,
        )

        self._filter_chip_btns: dict[str, ft.Container] = {}
        for fkey, flbl in _FILTER_DEFS:
            active = fkey in self._active_filters
            chip = ft.Container(
                content=app_text(flbl, size=11, color=TEXT_PRIMARY if active else TEXT_MUTED, font_family=FONT_HEADLINE),
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                border_radius=RADIUS_PILL,
                bgcolor=ACCENT_HALO if active else BG_SURFACE,
                border=ft.Border.all(0.8, ACCENT if active else BORDER_MUTED),
                ink=True,
                on_click=lambda _e, k=fkey: self._toggle_filter(k),
            )
            self._filter_chip_btns[fkey] = chip

        self._result_count_text = app_text("", size=11, color=TEXT_MUTED, font_family=mono_family("regular"))
        self._sort_btn = _ghost_btn(
            _SORT_LABELS[self._sort_mode],
            PIcons.SLIDERS_HORIZONTAL,
            lambda _e: self._cycle_sort_mode(),
            height=34,
        )
        self._lib_bar = ft.Row(
            controls=[
                self._local_seg,
                self._search_field,
                *self._filter_chip_btns.values(),
                ft.Container(expand=True),
                self._result_count_text,
                self._sort_btn,
            ],
            spacing=SPACE_2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._local_legend = ft.Text(
            "Descargada: carpeta con playlist.json · Importada: manifiesto sin audio",
            size=11,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            visible=self._platform == LOCAL_LIBRARY_LABEL and self._local_tab == "playlists",
        )

        self._playlists_row = ft.Row(wrap=True, spacing=SPACE_3, run_spacing=SPACE_3)
        self._grid_row = ft.Row(wrap=True, spacing=SPACE_3, run_spacing=SPACE_3)
        self._grid = ft.Column(
            controls=[self._grid_row],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=SPACE_3,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            visible=True,
        )
        empty_col = empty_state(
            PIcons.BOOKS,
            "Sin playlists cacheadas",
            "Elige una fuente para sincronizar",
        )
        self._empty_title = empty_col.controls[1]
        self._empty_sub = empty_col.controls[2]
        self._empty = ft.Container(
            expand=True,
            visible=False,
            alignment=ft.Alignment.CENTER_LEFT,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_3),
            content=empty_col,
        )
        self._filter_empty = ft.Container(
            expand=True,
            visible=False,
            alignment=ft.Alignment.CENTER_LEFT,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_3),
            content=empty_state(
                PIcons.MAGNIFYING_GLASS,
                "Nada coincide",
                "Prueba quitando algún filtro.",
                action_label="Limpiar filtros",
                on_action=lambda _e: self._clear_filters(),
            ),
        )
        self._loading_bar = ft.ProgressBar(visible=False, color=ACCENT, bgcolor=BG_SURFACE)

        self._list_stack = ft.Column(
            controls=[self._grid, self._empty, self._filter_empty],
            expand=True,
            spacing=SPACE_0,
        )

        header_row = ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        kicker("BIBLIOTECA"),
                        app_text("Estantes", size=FS_PAGE, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
                    ],
                    spacing=SPACE_0,
                    tight=True,
                ),
                self._status_text,
                ft.Container(expand=True),
                self._import_manifest_btn,
                self._add_folder_btn,
                self._refresh_btn,
            ],
            spacing=SPACE_3,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        body = ft.Column(controls=[
            header_row,
            self._platform_row,
            self._lib_bar,
            self._local_legend,
            self._loading_bar,
            ft.Container(expand=True, content=self._list_stack),
        ], spacing=SPACE_2, expand=True)

        self.content = ft.Container(content=body, padding=ft.Padding.all(12), expand=True)

    def _platform_count(self, plat: str) -> int:
        if plat == LOCAL_LIBRARY_LABEL:
            if hasattr(self.library_state, "local_folder_summaries"):
                return (
                    len(self.library_state.local_folder_summaries())
                    + len(self.library_state.downloaded_playlist_summaries())
                    + len(self.library_state.imported_manifest_summaries())
                )
            return len(self.library_state.local_summaries())
        snap = self.library_state.get_snapshot(plat)
        return len(snap.items) if snap and snap.items else 0

    def _rebuild_platform_selector(self) -> None:
        """Pestañas subrayadas compactas con nombre y contador cuando hay elementos."""
        active_key = self._platform or (SOURCES[0] if SOURCES else "")
        items: list[tuple[str, str]] = []
        for plat in SOURCES:
            short = _SHORT_LABELS.get(plat, plat)
            count = self._platform_count(plat)
            label = f"{short}  {count}" if count > 0 else short
            items.append((plat, label))
        tabs = tab_underline(items, active_key, self._on_platform_select)
        self._platform_row.controls[:] = tabs.controls
        self._platform_row.spacing = tabs.spacing
        self._platform_row.tight = tabs.tight
        self._platform_btns = {plat: ctrl for plat, ctrl in zip(SOURCES, self._platform_row.controls)}

    def _on_platform_select(self, plat: str) -> None:
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

    def _on_platform_click(self, e) -> None:
        self._on_platform_select(str(e.control.data))

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

    def _set_local_tab(self, tab_key: str) -> None:
        if tab_key not in ("carpetas", "playlists") or self._local_tab == tab_key:
            return
        self._local_tab = tab_key
        for k, btn in self._local_tab_btns.items():
            active = k == tab_key
            btn.bgcolor = ACCENT_HALO if active else BG_SURFACE
            btn.border = ft.Border.all(0.8, ACCENT if active else BORDER_MUTED)
        self._refresh()

    def _on_search_change(self, e) -> None:
        self._search_query = str(getattr(e.control, "value", "") or "")
        self._refresh()

    def _toggle_filter(self, key: str) -> None:
        if key in self._active_filters:
            self._active_filters.remove(key)
        else:
            self._active_filters.add(key)
        for k, chip in self._filter_chip_btns.items():
            active = k in self._active_filters
            chip.bgcolor = ACCENT_HALO if active else BG_SURFACE
            chip.border = ft.Border.all(0.8, ACCENT if active else BORDER_MUTED)
            if isinstance(chip.content, ft.Text):
                chip.content.color = TEXT_PRIMARY if active else TEXT_MUTED
        self._refresh()

    def _cycle_sort_mode(self) -> None:
        self._sort_mode = (self._sort_mode + 1) % len(_SORT_LABELS)
        from ui.components.widgets import set_button_text
        set_button_text(self._sort_btn, _SORT_LABELS[self._sort_mode])
        self._refresh()

    def _clear_filters(self) -> None:
        self._search_query = ""
        self._active_filters.clear()
        if hasattr(self, "_search_field"):
            self._search_field.value = ""
        for chip in self._filter_chip_btns.values():
            chip.bgcolor = BG_SURFACE
            chip.border = ft.Border.all(0.8, BORDER_MUTED)
            if isinstance(chip.content, ft.Text):
                chip.content.color = TEXT_MUTED
        self._refresh()

    def _read_show_obi(self) -> bool:
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

    def _filter_and_sort_items(self, items: list, *, is_local: bool) -> list:
        out = list(items)
        q = self._search_query.strip().casefold()
        if q:
            out = [s for s in out if q in str(getattr(s, "name", "") or "").casefold()]
        if "any" in self._active_filters:
            out = [s for s in out if (getattr(s, "track_count", None) or 0) > 0]
        if "big" in self._active_filters:
            out = [s for s in out if (getattr(s, "track_count", None) or 0) > 100]
        if "today" in self._active_filters and not is_local:
            today = date.today().isoformat()
            out = [
                s for s in out
                if str(getattr(s, "synced_at", "") or "")[:10] == today
            ]
        if self._sort_mode == 0:
            recent_rank = {k: i for i, k in enumerate(self._recent_opened_ids)}
            out.sort(key=lambda s: str(getattr(s, "name", "") or "").casefold())
            out.sort(key=lambda s: str(getattr(s, "synced_at", "") or ""), reverse=True)
            out.sort(
                key=lambda s: (
                    0 if (getattr(s, "platform", ""), getattr(s, "id", "")) in recent_rank else 1,
                    recent_rank.get((getattr(s, "platform", ""), getattr(s, "id", "")), 0),
                )
            )
        elif self._sort_mode == 1:
            out.sort(key=lambda s: str(getattr(s, "name", "") or "").casefold())
        elif self._sort_mode == 2:
            out.sort(key=lambda s: str(getattr(s, "name", "") or "").casefold(), reverse=True)
        return out

    def _on_open_playlist(self, summary) -> None:
        # registrar en Recientes (máx 6)
        key = (summary.platform, summary.id)
        self._recent_opened_ids = [key] + [k for k in self._recent_opened_ids if k != key]
        self._recent_opened_ids = self._recent_opened_ids[:6]
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
        obi_label: str | None = None,
        subtitle_line2: str | None = None,
        on_save_manifest=None,
        on_remove_manifest=None,
    ) -> ft.Container:
        """Envuelve la tarjeta con ancho fijo (160 px) para reflow sin resize."""
        card = PlaylistCard(
            summary,
            state=state,
            on_tap=self._on_open_playlist,
            obi_label=obi_label,
            subtitle_line2=subtitle_line2,
            show_obi=self._show_obi,
        )
        overlays: list[ft.Control] = []

        if badge_text:
            overlays.append(
                ft.Container(
                    content=ft.Text(
                        badge_text,
                        size=FS_META,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_BOLD,
                    ),
                    bgcolor=badge_bgcolor,
                    border=ft.Border.all(0.8, badge_border),
                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                    border_radius=RADIUS_SM,
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
                        border_radius=RADIUS_PILL,
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
                        border_radius=RADIUS_PILL,
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
        dlg_body = ft.Column(controls=controls, spacing=SPACE_3, tight=True)

        dlg: ft.AlertDialog | None = None
        busy = False

        def _close(_e=None) -> None:
            close_dialog(self._page_ref, dlg)

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
                close_dialog(self._page_ref, dlg)
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
        show_dialog(self._page_ref, dlg)

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
        self._rebuild_platform_selector()
        self._add_folder_btn.visible = False
        self._import_manifest_btn.visible = False
        self._local_seg.visible = False
        self._local_legend.visible = False
        if "today" in self._filter_chip_btns:
            self._filter_chip_btns["today"].visible = True
        self._show_obi = self._read_show_obi()
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

        filtered = self._filter_and_sort_items(snap.items, is_local=False)
        self._result_count_text.value = (
            f"{len(filtered)} resultado" if len(filtered) == 1 else f"{len(filtered)} resultados"
        )
        can_save = hasattr(self.library_state, "import_summary_as_manifest")
        self._grid_row.controls = [
            self._wrap_card(
                summ,
                state,
                on_save_manifest=(lambda _e, s=summ: self._save_remote_as_manifest(s)) if can_save else None,
            )
            for summ in filtered
        ]

        sections: list[ft.Control] = []
        if (
            self._recent_opened_ids
            and not self._search_query.strip()
            and not self._active_filters
            and self._sort_mode == 0
        ):
            by_id = {(s.platform, s.id): s for s in snap.items}
            recents = [by_id[k] for k in self._recent_opened_ids if k in by_id]
            if recents:
                sections.append(section_label("RECIENTES"))
                sections.append(
                    ft.Row(
                        controls=[
                            self._wrap_card(
                                summ,
                                state,
                                on_save_manifest=(lambda _e, s=summ: self._save_remote_as_manifest(s)) if can_save else None,
                            )
                            for summ in recents
                        ],
                        scroll=ft.ScrollMode.AUTO,
                        spacing=SPACE_3,
                    )
                )
                sections.append(section_label("TODAS LAS PLAYLISTS"))
        sections.append(self._grid_row)
        self._grid.controls = sections

        has_source_items = bool(snap.items)
        show_empty = not has_source_items and state != LibraryLoadState.LOADING
        show_filter_empty = has_source_items and not filtered and state != LibraryLoadState.LOADING
        self._grid.visible = not show_empty and not show_filter_empty
        self._empty.visible = show_empty
        self._filter_empty.visible = show_filter_empty
        try:
            self.update()
        except Exception:
            pass

    def _refresh_local(self) -> None:
        """Fuente Local: separa Carpetas (3.3) y Playlists locales (descargadas + importadas)."""
        self._rebuild_platform_selector()
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
        self._local_seg.visible = True
        self._local_legend.visible = self._local_tab == "playlists"
        if "today" in self._filter_chip_btns:
            self._filter_chip_btns["today"].visible = False
        self._show_obi = self._read_show_obi()
        self._loading_bar.visible = False

        for k, btn in self._local_tab_btns.items():
            active = k == self._local_tab
            btn.bgcolor = ACCENT_HALO if active else BG_SURFACE
            btn.border = ft.Border.all(0.8, ACCENT if active else BORDER_MUTED)

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

        f_imp = self._filter_and_sort_items(imported, is_local=True)
        f_dl = self._filter_and_sort_items(downloaded, is_local=True)
        f_folders = self._filter_and_sort_items(folders, is_local=True)

        playlist_cards: list[ft.Control] = [
            self._wrap_card(
                summ,
                LibraryLoadState.READY,
                obi_label="IMPORTADA",
                subtitle_line2="Importada · sin audio",
                on_remove_manifest=lambda _e, s=summ: self._remove_manifest(s),
            )
            for summ in f_imp
        ] + [
            self._wrap_card(
                summ,
                LibraryLoadState.READY,
                obi_label="DESCARGADA",
                subtitle_line2="Descargada",
                on_remove_manifest=(
                    (lambda _e, s=summ: (
                        self.library_state.remove_downloaded_playlist(s.id)
                        and self._refresh()
                    ))
                    if hasattr(self.library_state, "remove_downloaded_playlist")
                    else None
                ),
            )
            for summ in f_dl
        ]
        self._playlists_row.controls = playlist_cards
        self._grid_row.controls = [
            self._wrap_card(
                summ,
                LibraryLoadState.READY,
                obi_label="LOCAL",
                subtitle_line2="Carpeta local",
            )
            for summ in f_folders
        ]

        if self._local_tab == "playlists":
            total_src = len(imported) + len(downloaded)
            total_shown = len(f_imp) + len(f_dl)
            self._grid.controls = (
                [section_label("PLAYLISTS LOCALES"), self._playlists_row]
                if playlist_cards
                else [self._playlists_row]
            )
        else:
            total_src = len(folders)
            total_shown = len(f_folders)
            self._grid.controls = (
                [section_label("CARPETAS LOCALES"), self._grid_row]
                if f_folders
                else [self._grid_row]
            )
        self._result_count_text.value = (
            f"{total_shown} resultado" if total_shown == 1 else f"{total_shown} resultados"
        )

        has_any_overall = bool(folders or downloaded or imported)
        show_empty = not has_any_overall or (total_src == 0)
        show_filter_empty = total_src > 0 and total_shown == 0
        self._grid.visible = not show_empty and not show_filter_empty
        self._empty.visible = show_empty
        self._filter_empty.visible = show_filter_empty
        try:
            self.update()
        except Exception:
            pass
