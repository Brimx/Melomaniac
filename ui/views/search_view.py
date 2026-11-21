"""
ui/views/search_view.py — Vista dedicada «Buscar» (3.11)
════════════════════════════════════════════════════════
Explora canciones, álbumes y artistas en las plataformas configuradas con
acciones completas: streaming, Reproducir siguiente, Añadir a la cola,
descargar, guardar colección en Biblioteca Local, Añadir a playlist… y
Cargar en Gestión.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

import flet as ft

from core.config import LOCAL_LIBRARY_LABEL, PLATFORM_ORDER
from core.models import Track, TransferState
from engine.local_manifests import save_local_manifest
from ui.components.context_menu import (
    ContextMenuAction,
    build_context_menu_button,
    wrap_with_context_menu,
)
from ui.components.song_row import _thumb_content_for
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    app_segmented_toggle,
    app_text,
    app_text_field,
    empty_state,
    kicker,
    notify,
    organize_dropdown,
    safe_update,
    spawn_task,
)
from ui.dialogs.add_to_playlist_dialog import show_add_to_playlist_dialog
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_LIST,
    BG_SURFACE,
    BORDER_MUTED,
    FS_META,
    RADIUS_SHARP,
    RADIUS_SM,
    SKELETON_DARK,
    SPACE_0,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def _search_thumb(img_url: str, *, box: int = 44, icon_size: int = 18) -> ft.Container:
    return ft.Container(
        width=box,
        height=box,
        border_radius=RADIUS_SM,
        bgcolor=SKELETON_DARK,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=_thumb_content_for(img_url, box=box, icon_size=icon_size),
    )


class SearchView(ft.Container):
    """Vista dedicada de búsqueda en catálogo para el rail lateral."""

    def __init__(
        self,
        page: ft.Page,
        app_state,
        *,
        library_state=None,
        player_bar=None,
        on_load_in_gestion: Optional[Callable[[list[Track], str, str], None]] = None,
        on_open_downloads: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self.app_state = app_state
        self.library_state = library_state
        self.player_bar = player_bar
        self._on_load_in_gestion = on_load_in_gestion
        self._on_open_downloads = on_open_downloads

        self._platform: str = PLATFORM_ORDER[0] if PLATFORM_ORDER else "YouTube Music"
        self._kind: str = "songs"
        self._search_task: Optional[asyncio.Task] = None
        self._detail_tracks: list[Track] = []
        self._detail_title_str: str = ""
        self._detail_platform: str = self._platform
        self._detail_cover: str = ""
        self._detail_id: str = ""

        self._platform_dd = organize_dropdown(
            [(p, p) for p in PLATFORM_ORDER],
            self._platform,
            "Plataforma",
            width=170,
            height=38,
            on_select=self._on_platform_change,
        )
        self._search_field = app_text_field(
            hint_text="Buscar canciones, álbumes o artistas…",
            prefix_icon=PIcons.MAGNIFYING_GLASS,
            expand=True,
            height=38,
            on_submit=lambda _e: self._trigger_search(),
        )
        self._run_btn = _primary_btn(
            "Buscar",
            PIcons.MAGNIFYING_GLASS,
            lambda _e: self._trigger_search(),
            height=38,
        )
        self._tabs = app_segmented_toggle(
            [("songs", "Canciones"), ("albums", "Álbumes"), ("artists", "Artistas")],
            self._kind,
            on_change=self._on_tab_change,
            height=34,
        )
        self._status_txt = app_text(
            "Escribe una consulta y pulsa Enter para explorar el catálogo.",
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
        )

        self._results_lv = ft.ListView(expand=True, spacing=SPACE_2)
        self._empty_box = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER_LEFT,
            padding=ft.Padding.symmetric(horizontal=SPACE_4, vertical=SPACE_4),
            content=empty_state(
                PIcons.MAGNIFYING_GLASS,
                "Explora el catálogo",
                "Busca canciones, álbumes o artistas para reproducir en streaming, añadir a tus playlists o descargar.",
            ),
        )

        # Detalle de álbum / artista
        self._detail_title = ft.Text(
            "",
            size=15,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
        )
        self._detail_lv = ft.ListView(expand=True, spacing=SPACE_2)
        self._detail_box = ft.Container(
            expand=True,
            visible=False,
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            app_icon_button(
                                PIcons.ARROW_LEFT,
                                size=18,
                                icon_color=TEXT_PRIMARY,
                                tooltip="Volver a resultados",
                                on_click=lambda _e: self._close_detail(),
                                width=32,
                                height=32,
                            ),
                            self._detail_title,
                        ],
                        spacing=SPACE_2,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        controls=[
                            _primary_btn(
                                "Reproducir todo",
                                PIcons.PLAY,
                                lambda _e: self._play_detail_all(),
                                height=34,
                            ),
                            _ghost_btn(
                                "Guardar en Biblioteca",
                                PIcons.BOOKS,
                                lambda _e: self._save_detail_to_library(),
                                height=34,
                            ),
                            _ghost_btn(
                                "Añadir a playlist…",
                                PIcons.PLAYLIST,
                                lambda _e: self._open_add_dialog(list(self._detail_tracks)),
                                height=34,
                            ),
                            _ghost_btn(
                                "Descargar",
                                PIcons.DOWNLOAD_SIMPLE,
                                lambda _e: self._download_tracks(
                                    list(self._detail_tracks),
                                    self._detail_title_str,
                                    self._detail_platform,
                                ),
                                height=34,
                            ),
                            _ghost_btn(
                                "Cargar en Gestión",
                                PIcons.ARROWS_LEFT_RIGHT,
                                lambda _e: self._send_to_gestion(
                                    list(self._detail_tracks),
                                    self._detail_title_str,
                                    self._detail_platform,
                                ),
                                height=34,
                            ),
                        ],
                        spacing=SPACE_2,
                        wrap=True,
                    ),
                    ft.Divider(height=1, color=BORDER_MUTED),
                    self._detail_lv,
                ],
                spacing=SPACE_2,
                expand=True,
            ),
        )

        self._results_stack = ft.Stack(
            controls=[self._results_lv, self._empty_box],
            expand=True,
            fit=ft.StackFit.EXPAND,
        )

        self.content = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=SPACE_4, vertical=SPACE_4),
            content=ft.Column(
                controls=[
                    ft.Column(
                        controls=[
                            kicker("CATÁLOGO"),
                            ft.Text(
                                "Buscar",
                                size=22,
                                color=TEXT_PRIMARY,
                                font_family=FONT_HEADLINE_BOLD,
                            ),
                        ],
                        spacing=SPACE_0,
                        tight=True,
                    ),
                    ft.Row(
                        controls=[self._platform_dd, self._search_field, self._run_btn],
                        spacing=SPACE_2,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        controls=[self._tabs, ft.Container(expand=True), self._status_txt],
                        spacing=SPACE_3,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=BORDER_MUTED),
                    ft.Stack(
                        controls=[self._results_stack, self._detail_box],
                        expand=True,
                        fit=ft.StackFit.EXPAND,
                    ),
                ],
                spacing=SPACE_3,
                expand=True,
            ),
        )

    def focus_query(self) -> None:
        if hasattr(self._search_field, "focus"):
            try:
                self._search_field.focus()
            except Exception:
                pass

    def _on_platform_change(self, e) -> None:
        val = str(getattr(e.control, "value", "") or "")
        if val in PLATFORM_ORDER:
            self._platform = val
        if str(self._search_field.value or "").strip():
            self._trigger_search()

    def _on_tab_change(self, e) -> None:
        val = str(getattr(e.control, "value", "") or "songs")
        if val in ("songs", "albums", "artists"):
            self._kind = val
        if str(self._search_field.value or "").strip():
            self._trigger_search()

    def _trigger_search(self) -> None:
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()
        self._search_task = spawn_task(self._run_search())

    def _close_detail(self) -> None:
        self._detail_box.visible = False
        self._results_stack.visible = True
        safe_update(self)

    async def _run_search(self) -> None:
        query = str(self._search_field.value or "").strip()
        if not query:
            notify(self._page_ref, "Escribe un título, álbum o artista para buscar", kind="warn")
            return
        service = getattr(self.app_state, "service", None)
        if service is None:
            notify(self._page_ref, "Servicio de búsqueda no disponible", kind="error")
            return

        plat = self._platform
        kind = self._kind
        self._close_detail()
        self._status_txt.value = f"Buscando «{query}» en {plat}…"
        self._empty_box.visible = False
        safe_update(self)

        try:
            search_catalog = getattr(service, "search_catalog", None)
            if callable(search_catalog):
                results = await search_catalog(plat, query, kind=kind, limit=25)
            elif kind == "songs" and callable(getattr(service, "search_candidates", None)):
                cands = await service.search_candidates(plat, query, limit=15)
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
                results = []
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            self._status_txt.value = f"Error al buscar en {plat}: {exc}"
            safe_update(self._status_txt)
            return

        self._render_results(results or [], kind=kind, platform=plat)

    def _render_results(self, results: list[dict], *, kind: str, platform: str) -> None:
        lv = self._results_lv
        lv.controls.clear()
        if not results:
            self._status_txt.value = f"Sin resultados en {platform}."
            self._empty_box.visible = True
            safe_update(self)
            return

        self._empty_box.visible = False
        self._status_txt.value = f"{len(results)} resultados en {platform}"
        for item in results:
            item_id = str(item.get("id") or "")
            name = str(item.get("name") or "Sin título")
            subtitle = str(item.get("subtitle") or item.get("artist") or "")
            img_url = str(item.get("img_url") or "")
            thumb = _search_thumb(img_url, box=44, icon_size=18)
            info = ft.Column(
                controls=[
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
                spacing=SPACE_0,
                expand=True,
                tight=True,
            )

            if kind == "songs":
                tr = item.get("track")
                if not isinstance(tr, Track):
                    tr = Track(
                        id=item_id,
                        name=name,
                        artist=str(item.get("artist") or subtitle),
                        album=str(item.get("album") or ""),
                        duration=str(item.get("duration") or ""),
                        platform=platform,
                        img_url=img_url,
                        source_url=str(item.get("external_url") or ""),
                    )
                lv.controls.append(self._build_song_result_row(tr, platform=platform))
                continue

            if kind == "albums":
                action_btn = _ghost_btn(
                    "Ver álbum",
                    PIcons.DISC,
                    lambda _e, aid=item_id, aname=name, p=platform, cov=img_url: spawn_task(
                        self._open_album(p, aid, aname, cov)
                    ),
                    height=32,
                )
            else:
                action_btn = _ghost_btn(
                    "Ver artista",
                    PIcons.MUSIC_NOTES,
                    lambda _e, arid=item_id, arname=name, p=platform, cov=img_url: spawn_task(
                        self._open_artist(p, arid, arname, cov)
                    ),
                    height=32,
                )

            lv.controls.append(
                ft.Container(
                    bgcolor=BG_SURFACE,
                    border=ft.Border.all(0.6, BORDER_MUTED),
                    border_radius=RADIUS_SHARP,
                    padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_2),
                    content=ft.Row(
                        controls=[thumb, info, action_btn],
                        spacing=SPACE_3,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )
        safe_update(self)

    def _build_song_result_row(self, tr: Track, *, platform: str) -> ft.Container:
        thumb = _search_thumb(tr.img_url, box=44, icon_size=18)
        info = ft.Column(
            controls=[
                ft.Text(
                    tr.name,
                    size=13,
                    color=TEXT_PRIMARY,
                    font_family=FONT_HEADLINE,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    tr.artist or platform,
                    size=11,
                    color=TEXT_MUTED,
                    font_family=FONT_TEXT,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
            spacing=SPACE_0,
            expand=True,
            tight=True,
        )

        def _ctx_actions() -> list[ContextMenuAction]:
            items: list[ContextMenuAction] = []
            if self.player_bar is not None:
                items.extend([
                    ContextMenuAction(
                        "Reproducir",
                        icon=PIcons.PLAY,
                        on_click=lambda: self._play_single_track(tr),
                    ),
                    ContextMenuAction(
                        "Reproducir siguiente",
                        icon=PIcons.SKIP_FORWARD,
                        on_click=lambda: self._play_next_track(tr),
                    ),
                    ContextMenuAction(
                        "Añadir a la cola",
                        icon=PIcons.QUEUE,
                        on_click=lambda: self._enqueue_track(tr),
                    ),
                ])
            items.extend([
                ContextMenuAction(
                    "Añadir a playlist…",
                    icon=PIcons.PLAYLIST,
                    on_click=lambda: self._open_add_dialog([tr]),
                ),
                ContextMenuAction(
                    "Descargar canción",
                    icon=PIcons.DOWNLOAD_SIMPLE,
                    on_click=lambda: self._download_tracks([tr], tr.name, platform),
                ),
                ContextMenuAction(
                    "Cargar en Gestión",
                    icon=PIcons.ARROWS_LEFT_RIGHT,
                    on_click=lambda: self._send_to_gestion([tr], tr.name, platform),
                ),
            ])
            return items

        play_btn = app_icon_button(
            PIcons.PLAY,
            size=16,
            icon_color=ACCENT,
            tooltip="Reproducir en streaming",
            on_click=lambda _e: self._play_single_track(tr),
            width=30,
            height=30,
        )
        add_btn = _ghost_btn(
            "Añadir",
            PIcons.PLUS,
            lambda _e: self._open_add_dialog([tr]),
            height=30,
        )
        menu_btn = build_context_menu_button(
            _ctx_actions,
            width=28,
            height=28,
            icon_size=16,
            title=tr.name,
            get_page=lambda: self._page_ref,
        )
        row_inner = ft.Row(
            controls=[thumb, info, play_btn, add_btn, menu_btn],
            spacing=SPACE_2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ft.Container(
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.6, BORDER_MUTED),
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_2),
            ink=True,
            on_click=lambda _e: self._play_single_track(tr),
            content=wrap_with_context_menu(
                row_inner,
                _ctx_actions,
                title=tr.name,
                get_page=lambda: self._page_ref,
            ),
        )

    async def _open_album(self, platform: str, album_id: str, fallback_name: str, cover: str) -> None:
        service = getattr(self.app_state, "service", None)
        fn = getattr(service, "fetch_album_tracks", None) if service is not None else None
        if not callable(fn):
            notify(self._page_ref, "Detalle de álbum no disponible en este servicio", kind="error")
            return
        self._status_txt.value = f"Cargando álbum «{fallback_name}»…"
        safe_update(self._status_txt)
        try:
            meta, tracks = await fn(platform, album_id)
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo cargar el álbum: {exc}", kind="error")
            return
        title = getattr(meta, "name", "") or fallback_name or "Álbum"
        self._show_detail(title, list(tracks or ()), platform=platform, cover=cover, item_id=album_id)

    async def _open_artist(self, platform: str, artist_id: str, fallback_name: str, cover: str) -> None:
        service = getattr(self.app_state, "service", None)
        fn = getattr(service, "fetch_artist_top_tracks", None) if service is not None else None
        if not callable(fn):
            notify(self._page_ref, "Detalle de artista no disponible en este servicio", kind="error")
            return
        self._status_txt.value = f"Cargando pistas de «{fallback_name}»…"
        safe_update(self._status_txt)
        try:
            meta, tracks = await fn(platform, artist_id)
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo cargar el artista: {exc}", kind="error")
            return
        title = getattr(meta, "name", "") or fallback_name or "Artista"
        self._show_detail(title, list(tracks or ()), platform=platform, cover=cover, item_id=artist_id)

    def _show_detail(
        self,
        title: str,
        tracks: list[Track],
        *,
        platform: str,
        cover: str = "",
        item_id: str = "",
    ) -> None:
        self._detail_tracks = list(tracks)
        self._detail_title_str = title
        self._detail_platform = platform
        self._detail_cover = cover
        self._detail_id = item_id
        self._detail_title.value = f"{title} · {len(tracks)} pistas"
        self._detail_lv.controls = [
            self._build_song_result_row(tr, platform=platform)
            for tr in tracks
        ]
        self._results_stack.visible = False
        self._detail_box.visible = True
        safe_update(self)

    # ── Acciones sobre pistas / colecciones ────────────────────────────

    def _play_single_track(self, tr: Track) -> None:
        pb = self.player_bar
        if pb is None:
            return
        if hasattr(pb, "play_remote_track"):
            async def _run() -> None:
                notify(self._page_ref, f"Resolviendo audio: {tr.name}…")
                await pb.play_remote_track(tr)

            try:
                self._page_ref.run_task(_run)
            except Exception:
                spawn_task(_run())

    def _play_detail_all(self) -> None:
        pb = self.player_bar
        if pb is None or not self._detail_tracks:
            return
        if hasattr(pb, "play_tracks"):
            n = pb.play_tracks(list(self._detail_tracks), start_index=0, include_remote=True)
            if n:
                notify(self._page_ref, f"Reproduciendo {n} canciones")

    def _play_next_track(self, tr: Track) -> None:
        pb = self.player_bar
        if pb is not None and hasattr(pb, "play_next"):
            if pb.play_next(tr):
                notify(self._page_ref, f"Siguiente en cola: {tr.name}")

    def _enqueue_track(self, tr: Track) -> None:
        pb = self.player_bar
        if pb is not None and hasattr(pb, "enqueue_track"):
            if pb.enqueue_track(tr):
                notify(self._page_ref, f"Añadida a la cola: {tr.name}")

    def _open_add_dialog(self, tracks: list[Track]) -> None:
        if not tracks:
            return
        show_add_to_playlist_dialog(
            self._page_ref,
            tracks,
            app_state=self.app_state,
            library_state=self.library_state,
        )

    def _save_detail_to_library(self) -> None:
        if not self._detail_tracks:
            return
        title = self._detail_title_str or "Colección"
        tracks = list(self._detail_tracks)
        cover = self._detail_cover
        pid = self._detail_id

        async def _save() -> None:
            await asyncio.to_thread(
                save_local_manifest,
                platform=LOCAL_LIBRARY_LABEL,
                playlist_id=pid,
                name=title,
                img_url=cover,
                tracks=tracks,
            )
            if self.library_state is not None and hasattr(self.library_state, "notify"):
                try:
                    self.library_state.notify()
                except Exception:
                    pass
            notify(self._page_ref, f"«{title}» guardada en Biblioteca Local")

        try:
            self._page_ref.run_task(_save)
        except Exception:
            spawn_task(_save())

    def _download_tracks(self, tracks: list[Track], title: str, platform: str) -> None:
        st = self.app_state
        if st is None or not tracks:
            return
        was_running = getattr(st, "download_state", None) == TransferState.RUNNING
        try:
            st.download_tracks(tracks, playlist_name=title, platform=platform)
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo iniciar la descarga: {exc}", kind="error")
            return
        cb = self._on_open_downloads
        msg = (
            f"Añadidas {len(tracks)} pistas a la cola de descargas"
            if was_running
            else f"Descargando {len(tracks)} pistas"
        )
        notify(
            self._page_ref,
            msg,
            action="Ver" if cb else None,
            on_action=(lambda _e: cb()) if cb else None,
        )

    def _send_to_gestion(self, tracks: list[Track], title: str, platform: str) -> None:
        if callable(self._on_load_in_gestion) and tracks:
            self._on_load_in_gestion(tracks, title, platform)
