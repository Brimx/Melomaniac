"""ui/shell/catalog_panel.py — Panel y controlador de búsqueda en catálogo (Canciones/Álbumes/Artistas)."""

from __future__ import annotations

import asyncio

import flet as ft

from core.models import Track, TransferState
from core.state import AppState
from ui.components.song_row import _thumb_content_for
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    _section_label,
    app_text_field,
    organize_dropdown,
    safe_update,
)
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_LIST,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    RADIUS_INPUT,
    SKELETON_DARK,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def _catalog_thumb(img_url: str, *, box: int = 44, radius: int = 8, icon_size: int = 18) -> ft.Container:
    return ft.Container(
        width=box,
        height=box,
        border_radius=radius,
        bgcolor=SKELETON_DARK,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=_thumb_content_for(img_url, box=box, icon_size=icon_size),
    )


class CatalogController:
    """Controlador de búsqueda en catálogo para ``PlaylistManagerUI``."""

    def __init__(self, ui) -> None:
        self._ui = ui

    def build_catalog_panel(self) -> ft.Container:
        """Panel de búsqueda en catálogo con pestañas Canciones/Álbumes/Artistas (2.11)."""
        ui = self._ui
        default_plat = (
            ui.state.source
            if ui.state.source in AppState.PLATFORMS
            else AppState.PLATFORMS[0]
        )
        ui._catalog_platform_dd = organize_dropdown(
            [(p, p) for p in AppState.PLATFORMS],
            default_plat,
            "Plataforma",
            width=170,
            height=38,
            on_select=self._on_catalog_platform_change,
        )
        ui._catalog_query_field = app_text_field(
            hint_text="Buscar canciones, álbumes o artistas…",
            prefix_icon=PIcons.MAGNIFYING_GLASS,
            expand=True,
            height=38,
            on_submit=lambda _: asyncio.create_task(self._on_catalog_search_submit()),
        )
        ui._catalog_run_btn = _primary_btn(
            "Buscar",
            PIcons.MAGNIFYING_GLASS,
            lambda _: asyncio.create_task(self._on_catalog_search_submit()),
            height=38,
        )
        ui._catalog_close_btn = app_icon_button(
            glyph=PIcons.X,
            icon_color=TEXT_MUTED,
            hover_color_icon=TEXT_PRIMARY,
            size=16,
            tooltip="Volver a la tabla de playlist",
            on_click=lambda _: ui._toggle_catalog_panel(False),
            width=32,
            height=32,
        )
        try:
            ui._catalog_tabs = ft.SegmentedButton(
                selected=["songs"],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="songs", label=ft.Text("Canciones", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="albums", label=ft.Text("Álbumes", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="artists", label=ft.Text("Artistas", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=self._on_catalog_tab_change,
            )
        except Exception:
            ui._catalog_tabs = organize_dropdown(
                [("songs", "Canciones"), ("albums", "Álbumes"), ("artists", "Artistas")],
                "songs",
                "Tipo",
                width=160,
                height=38,
                on_select=self._on_catalog_tab_change,
            )

        ui._catalog_detail_title = ft.Text(
            "",
            size=13,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            expand=True,
        )
        ui._catalog_detail_load_btn = _primary_btn(
            "Cargar en Gestión",
            PIcons.PLAYLIST,
            lambda _: ui._on_catalog_detail_load(),
            height=32,
        )
        ui._catalog_detail_dl_btn = _ghost_btn(
            "Descargar",
            PIcons.DOWNLOAD_SIMPLE,
            lambda _: self._on_catalog_detail_download(),
            height=32,
        )
        ui._catalog_detail_add_btn = _ghost_btn(
            "Añadir a playlist",
            PIcons.PLUS,
            lambda _: asyncio.create_task(self._on_catalog_detail_add()),
            height=32,
        )
        ui._catalog_detail_bar = ft.Container(
            visible=False,
            bgcolor=CHIP_BG,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            content=ft.Row(
                [
                    _ghost_btn("Volver", PIcons.ARROW_LEFT, lambda _: self._on_catalog_back(), height=32),
                    ui._catalog_detail_title,
                    ui._catalog_detail_load_btn,
                    ui._catalog_detail_dl_btn,
                    ui._catalog_detail_add_btn,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        ui._catalog_status = ft.Text(
            "Elige una pestaña y escribe una búsqueda para explorar el catálogo.",
            size=11,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
        )
        ui._catalog_results_lv = ft.ListView(
            spacing=6,
            expand=True,
            padding=ft.Padding.only(bottom=12),
        )
        return ft.Container(
            visible=False,
            expand=True,
            bgcolor=BG_LIST,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.all(14),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ui._catalog_platform_dd,
                            ui._catalog_query_field,
                            ui._catalog_run_btn,
                            ui._catalog_close_btn,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [ui._catalog_tabs, ft.Container(expand=True), ui._catalog_status],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ui._catalog_detail_bar,
                    ui._catalog_results_lv,
                ],
                spacing=10,
                expand=True,
            ),
        )

    def _on_toggle_catalog_search(self, _=None) -> None:
        self._ui._toggle_catalog_panel(not getattr(self._ui, "_catalog_open", False))

    def _toggle_catalog_panel(self, open_panel: bool | None = None) -> None:
        ui = self._ui
        target = (
            not getattr(ui, "_catalog_open", False)
            if open_panel is None
            else bool(open_panel)
        )
        ui._catalog_open = target
        if hasattr(ui, "_catalog_panel"):
            ui._catalog_panel.visible = target
        if hasattr(ui, "_col_headers"):
            ui._col_headers.visible = not target
        if hasattr(ui, "_list_area"):
            ui._list_area.visible = not target
        if hasattr(ui, "_mode_bar"):
            has_transform = bool(
                getattr(ui.state, "segments", {})
                or getattr(ui.state, "show_dual", False)
                or getattr(ui.state, "dual_mode", "lista") in ("doble", "preview")
            )
            ui._mode_bar.visible = bool(has_transform and not target)
        try:
            content = getattr(ui, "_content", None)
            if not safe_update(content):
                ui.page.update()
        except Exception:
            pass

    def _on_catalog_platform_change(self, _e=None) -> None:
        query = (getattr(self._ui._catalog_query_field, "value", "") or "").strip()
        if query:
            asyncio.create_task(self._on_catalog_search_submit())

    def _on_catalog_tab_change(self, e) -> None:
        ui = self._ui
        try:
            val = (
                list(e.control.selected)[0]
                if hasattr(e.control, "selected") and e.control.selected
                else e.control.value
            )
        except Exception:
            val = getattr(e.control, "value", "songs")
        if val not in ("songs", "albums", "artists"):
            return
        ui._catalog_tab = val
        ui._catalog_detail_stack.clear()
        if hasattr(ui, "_catalog_detail_bar"):
            ui._catalog_detail_bar.visible = False
        query = (getattr(ui._catalog_query_field, "value", "") or "").strip()
        if query:
            asyncio.create_task(self._on_catalog_search_submit())
        else:
            ui._catalog_results_lv.controls.clear()
            ui._catalog_status.value = "Escribe un término y pulsa Buscar."
            safe_update(ui._catalog_panel)

    async def _on_catalog_search_submit(self, _=None) -> None:
        ui = self._ui
        query = (getattr(ui._catalog_query_field, "value", "") or "").strip()
        if not query:
            ui._catalog_status.value = "Introduce un texto para buscar en el catálogo."
            safe_update(ui._catalog_status)
            return
        platform = str(getattr(ui._catalog_platform_dd, "value", "") or AppState.PLATFORMS[0])
        kind = getattr(ui, "_catalog_tab", "songs")
        await ui._run_catalog_search(query, kind=kind, platform=platform)

    async def _run_catalog_search(
        self, query: str, *, kind: str = "songs", platform: str = ""
    ) -> None:
        ui = self._ui
        plat = platform or AppState.PLATFORMS[0]
        service = getattr(ui.state, "service", None)
        ui._catalog_detail_stack.clear()
        if hasattr(ui, "_catalog_detail_bar"):
            ui._catalog_detail_bar.visible = False
        ui._catalog_status.value = f"Buscando {kind} en {plat}…"
        safe_update(ui._catalog_status)

        if service is None:
            ui._catalog_status.value = "Servicio no disponible."
            safe_update(ui._catalog_status)
            return

        results: list[dict] = []
        search_catalog = getattr(service, "search_catalog", None)
        try:
            if callable(search_catalog):
                results = list(await search_catalog(plat, query, kind=kind, limit=20) or [])
            elif kind == "songs" and callable(getattr(service, "search_candidates", None)):
                cands = await service.search_candidates(plat, query, limit=20)
                results = [
                    {
                        "id": tr.id,
                        "name": tr.name,
                        "subtitle": tr.artist,
                        "artist": tr.artist,
                        "img_url": tr.img_url,
                        "kind": "songs",
                        "external_url": tr.source_url,
                        "track": tr,
                    }
                    for tr in (cands or [])
                ]
            else:
                ui._catalog_results_lv.controls.clear()
                ui._catalog_status.value = "Búsqueda de catálogo no disponible en este servicio."
                safe_update(ui._catalog_panel)
                return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            ui._catalog_status.value = f"Error al buscar en {plat}: {exc}"
            safe_update(ui._catalog_status)
            return

        ui._catalog_results = results
        self._render_catalog_results(results, kind=kind, platform=plat)

    def _render_catalog_results(
        self, results: list[dict], *, kind: str, platform: str
    ) -> None:
        ui = self._ui
        lv = ui._catalog_results_lv
        lv.controls.clear()
        if not results:
            ui._catalog_status.value = f"Sin resultados en {platform}."
            safe_update(ui._catalog_panel)
            return
        ui._catalog_status.value = f"{len(results)} resultados en {platform}"
        for item in results:
            item_id = str(item.get("id") or "")
            name = str(item.get("name") or "Sin título")
            subtitle = str(item.get("subtitle") or item.get("artist") or "")
            img_url = str(item.get("img_url") or "")
            thumb = _catalog_thumb(img_url, box=44, radius=8, icon_size=18)
            info = ft.Column(
                [
                    ft.Text(
                        name,
                        size=13,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(
                        subtitle,
                        size=11,
                        color=TEXT_MUTED,
                        font_family=FONT_TEXT,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=2,
                expand=True,
            )
            actions: list[ft.Control] = []
            if kind == "songs":
                tr = item.get("track")
                if not isinstance(tr, Track):
                    tr = Track(
                        id=item_id,
                        name=name,
                        artist=str(item.get("artist") or subtitle),
                        album="",
                        duration="",
                        platform=platform,
                        img_url=img_url,
                        source_url=str(item.get("external_url") or ""),
                    )
                actions = [
                    _ghost_btn(
                        "Cargar",
                        PIcons.PLAYLIST,
                        lambda _, track=tr, p=platform: self._load_catalog_tracks_into_gestion(
                            [track], track.name, platform=p
                        ),
                        height=30,
                    ),
                    _ghost_btn(
                        "Descargar",
                        PIcons.DOWNLOAD_SIMPLE,
                        lambda _, track=tr: self._download_catalog_tracks([track], track.name),
                        height=30,
                    ),
                    _ghost_btn(
                        "Añadir",
                        PIcons.PLUS,
                        lambda _, track=tr: asyncio.create_task(
                            ui._open_add_to_playlist_dialog([track])
                        ),
                        height=30,
                    ),
                ]
            elif kind == "albums":
                actions = [
                    _ghost_btn(
                        "Ver álbum",
                        PIcons.DISC,
                        lambda _, aid=item_id, aname=name, p=platform: asyncio.create_task(
                            ui._open_catalog_album(p, aid, aname)
                        ),
                        height=30,
                    ),
                ]
            elif kind == "artists":
                actions = [
                    _ghost_btn(
                        "Ver artista",
                        PIcons.MUSIC_NOTES,
                        lambda _, arid=item_id, arname=name, p=platform: asyncio.create_task(
                            ui._open_catalog_artist(p, arid, arname)
                        ),
                        height=30,
                    ),
                ]

            lv.controls.append(
                ft.Container(
                    bgcolor=BG_SURFACE,
                    border=ft.Border.all(0.6, BORDER_MUTED),
                    border_radius=RADIUS_INPUT,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                    content=ft.Row(
                        [thumb, info, *actions],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )
        safe_update(ui._catalog_panel)

    async def _open_catalog_album(
        self, platform: str, album_id: str, fallback_name: str = ""
    ) -> None:
        ui = self._ui
        service = getattr(ui.state, "service", None)
        fetch_fn = getattr(service, "fetch_album_tracks", None) if service is not None else None
        if not callable(fetch_fn):
            ui._snack("Detalle de álbum no disponible en este servicio", error=True)
            return
        ui._catalog_status.value = f"Cargando álbum «{fallback_name or album_id}»…"
        safe_update(ui._catalog_status)
        try:
            meta, tracks = await fetch_fn(platform, album_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            ui._snack(f"No se pudo cargar el álbum: {exc}", error=True)
            return
        title = str(getattr(meta, "name", "") or fallback_name or "Álbum")
        ui._catalog_detail_stack.append(
            {
                "kind": "results",
                "tab": ui._catalog_tab,
                "platform": platform,
                "results": list(ui._catalog_results),
            }
        )
        self._render_catalog_tracks_detail(
            title=title, platform=platform, tracks=list(tracks or [])
        )

    async def _open_catalog_artist(
        self, platform: str, artist_id: str, fallback_name: str = ""
    ) -> None:
        ui = self._ui
        service = getattr(ui.state, "service", None)
        fetch_fn = getattr(service, "fetch_artist_overview", None) if service is not None else None
        if not callable(fetch_fn):
            ui._snack("Detalle de artista no disponible en este servicio", error=True)
            return
        ui._catalog_status.value = f"Cargando artista «{fallback_name or artist_id}»…"
        safe_update(ui._catalog_status)
        try:
            overview = await fetch_fn(platform, artist_id) or {}
        except Exception as exc:  # pylint: disable=broad-exception-caught
            ui._snack(f"No se pudo cargar el artista: {exc}", error=True)
            return
        artist_info = overview.get("artist")
        if isinstance(artist_info, dict):
            artist_name = str(artist_info.get("name") or fallback_name or "Artista")
        else:
            artist_name = str(artist_info or fallback_name or "Artista")
        top_tracks = list(overview.get("top_tracks") or [])
        albums = list(overview.get("albums") or [])

        ui._catalog_detail_stack.append(
            {
                "kind": "results",
                "tab": ui._catalog_tab,
                "platform": platform,
                "results": list(ui._catalog_results),
            }
        )
        ui._catalog_detail_tracks = top_tracks
        ui._catalog_detail_title_str = f"Top · {artist_name}"
        ui._catalog_detail_platform = platform
        ui._catalog_detail_title.value = f"{artist_name} · {len(top_tracks)} top / {len(albums)} álbumes"
        ui._catalog_detail_bar.visible = True
        lv = ui._catalog_results_lv
        lv.controls.clear()
        if top_tracks:
            lv.controls.append(_section_label("TOP CANCIONES"))
            for idx, tr in enumerate(top_tracks, 1):
                lv.controls.append(self._build_catalog_track_row(tr, idx, platform))
        if albums:
            lv.controls.append(_section_label("ÁLBUMES"))
            for alb in albums:
                aid = str(alb.get("id") or "")
                aname = str(alb.get("name") or "Álbum")
                asub = str(alb.get("subtitle") or alb.get("artist") or "")
                lv.controls.append(
                    ft.Container(
                        bgcolor=BG_SURFACE,
                        border=ft.Border.all(0.6, BORDER_MUTED),
                        border_radius=RADIUS_INPUT,
                        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                        content=ft.Row(
                            [
                                _catalog_thumb(str(alb.get("img_url") or ""), box=40, radius=8, icon_size=16),
                                ft.Column(
                                    [
                                        ft.Text(aname, size=12, color=TEXT_PRIMARY, font_family=FONT_HEADLINE),
                                        ft.Text(asub, size=10, color=TEXT_MUTED, font_family=FONT_TEXT),
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                                _ghost_btn(
                                    "Abrir álbum",
                                    PIcons.DISC,
                                    lambda _, a_id=aid, a_name=aname, p=platform: asyncio.create_task(
                                        ui._open_catalog_album(p, a_id, a_name)
                                    ),
                                    height=30,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )
        ui._catalog_status.value = f"{artist_name} · {len(top_tracks)} canciones"
        safe_update(ui._catalog_panel)

    def _build_catalog_track_row(self, tr: Track, idx: int, platform: str) -> ft.Container:
        ui = self._ui
        return ft.Container(
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.6, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            content=ft.Row(
                [
                    ft.Text(str(idx), size=11, color=TEXT_DIM, font_family=mono_family("light"), width=24),
                    _catalog_thumb(tr.img_url, box=38, radius=6, icon_size=16),
                    ft.Column(
                        [
                            ft.Text(
                                tr.name,
                                size=12,
                                color=TEXT_PRIMARY,
                                font_family=FONT_HEADLINE,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                tr.artist,
                                size=10,
                                color=TEXT_MUTED,
                                font_family=FONT_TEXT,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=1,
                        expand=True,
                    ),
                    ft.Text(tr.duration or "", size=10, color=TEXT_DIM, font_family=mono_family("light")),
                    _ghost_btn(
                        "Descargar",
                        PIcons.DOWNLOAD_SIMPLE,
                        lambda _, track=tr: self._download_catalog_tracks([track], track.name),
                        height=28,
                    ),
                    _ghost_btn(
                        "Añadir",
                        PIcons.PLUS,
                        lambda _, track=tr: asyncio.create_task(
                            ui._open_add_to_playlist_dialog([track])
                        ),
                        height=28,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _render_catalog_tracks_detail(
        self, *, title: str, platform: str, tracks: list[Track]
    ) -> None:
        ui = self._ui
        ui._catalog_detail_tracks = list(tracks)
        ui._catalog_detail_title_str = title
        ui._catalog_detail_platform = platform
        ui._catalog_detail_title.value = f"{title} · {len(tracks)} canciones"
        ui._catalog_detail_bar.visible = True
        lv = ui._catalog_results_lv
        lv.controls.clear()
        for idx, tr in enumerate(tracks, 1):
            lv.controls.append(self._build_catalog_track_row(tr, idx, platform))
        ui._catalog_status.value = f"{len(tracks)} canciones"
        safe_update(ui._catalog_panel)

    def _on_catalog_back(self) -> None:
        ui = self._ui
        if not ui._catalog_detail_stack:
            ui._catalog_detail_bar.visible = False
            safe_update(ui._catalog_panel)
            return
        prev = ui._catalog_detail_stack.pop()
        ui._catalog_detail_bar.visible = False
        self._render_catalog_results(
            prev.get("results") or [],
            kind=str(prev.get("tab") or ui._catalog_tab),
            platform=str(prev.get("platform") or AppState.PLATFORMS[0]),
        )

    def _on_catalog_detail_load(self) -> None:
        ui = self._ui
        tracks = list(getattr(ui, "_catalog_detail_tracks", []) or [])
        if not tracks:
            ui._snack("No hay canciones para cargar", error=True)
            return
        self._load_catalog_tracks_into_gestion(
            tracks,
            ui._catalog_detail_title_str or "Catálogo",
            platform=ui._catalog_detail_platform,
        )

    def _on_catalog_detail_download(self) -> None:
        ui = self._ui
        tracks = list(getattr(ui, "_catalog_detail_tracks", []) or [])
        if not tracks:
            ui._snack("No hay canciones para descargar", error=True)
            return
        self._download_catalog_tracks(tracks, ui._catalog_detail_title_str or "Catálogo")

    async def _on_catalog_detail_add(self) -> None:
        ui = self._ui
        tracks = list(getattr(ui, "_catalog_detail_tracks", []) or [])
        if not tracks:
            ui._snack("No hay canciones para añadir", error=True)
            return
        await ui._open_add_to_playlist_dialog(tracks)

    def _load_catalog_tracks_into_gestion(
        self, tracks: list[Track], title: str, *, platform: str = ""
    ) -> None:
        ui = self._ui
        ui._completion_snack_shown = False
        ui._pm_cleared_for_load = True
        if hasattr(ui, "_telemetry"):
            ui._telemetry.clear_postmortem()
        ui.state.load_local_tracks(list(tracks), playlist_name=title or "Catálogo")
        ui._toggle_catalog_panel(False)
        ui._snack(f"{len(tracks)} canciones cargadas en Gestión desde {platform or 'catálogo'}")

    def _download_catalog_tracks(self, tracks: list[Track], title: str) -> None:
        ui = self._ui
        if ui.state.download_state == TransferState.RUNNING:
            ui._snack("Descarga en curso", error=True)
            return
        from services import settings_store

        platforms = {t.platform for t in tracks if getattr(t, "platform", "")}
        ui.state.download_tracks(
            list(tracks),
            destination_dir=settings_store.get_download_dir(),
            playlist_name=title or "",
            platform=platforms.pop() if len(platforms) == 1 else "",
        )
        ui._snack(f"Descargando {len(tracks)} canciones…")
