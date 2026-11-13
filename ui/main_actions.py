"""ui/main_actions.py — Acciones del usuario: cargar, pegar, importar, transferir, exportar y buscar.

Mixin de `PlaylistManagerUI` (ui/main_ui.py): los métodos se mezclan en la clase
y comparten su `self`.
"""

from __future__ import annotations

import asyncio
import flet as ft
import os

from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    _section_label,
    app_dialog,
    app_text_field,
    dialog_action,
    organize_dropdown,
    safe_update,
)
from ui.dialogs.export_dialog import open_export_dialog
from ui.dialogs.merge_dialog import open_merge_dialog
from ui.dialogs.transfer_dialog import open_transfer_dialog
from ui.dialogs.transfer_helpers import (
    add_tracks_to_existing_destination,
    get_synced_playlists_for,
    resolve_tracks_for_destination,
    run_dialog_preflight,
)
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.theme.tokens import (
    ACCENT,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    ERROR_COL,
    RADIUS_INPUT,
    SKELETON_DARK,
    SUCCESS,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
)
from core.models import LoadState, Track, TransferState
from engine.parsers import build_local_tracks, parse_local_playlist_with_paths
from ui.theme.icons import PIcons, app_icon
from ui.components.song_row import _thumb_content_for
from core.state import AppState


class ActionsMixin:
    """Acciones del usuario: cargar, pegar, importar, transferir, exportar y buscar."""

    def _playlist_download_context(self, tracks: list) -> dict[str, str]:
        from services import settings_store

        platforms = {getattr(t, "platform", "") for t in tracks if getattr(t, "platform", "")}
        if len(platforms) == 1:
            platform = next(iter(platforms))
        elif getattr(self.state, "source", "") not in ("", "Archivo Local", "Pegar Texto"):
            platform = str(self.state.source)
        else:
            platform = ""
        return {
            "destination_dir": settings_store.get_download_dir(),
            "playlist_name": self.state.playlist_name or "",
            "platform": platform,
        }

    def _on_row_download(self, track_id: str) -> None:
        if self.state.download_state == TransferState.RUNNING:
            self._snack("Descarga en curso", error=True)
            return
        target = next((t for t in self.state.tracks if t.id == track_id), None)
        if target is None:
            target = next((t for t in self.state.source_tracks if t.id == track_id), None)
        if target is None:
            return
        self._auto_play_download_ids.add(target.id)
        try:
            self.state.download_tracks(
                [target],
                **self._playlist_download_context([target]),
            )
            self._snack(f"Descargando: {target.artist} — {target.name}")
        except Exception as exc:  # noqa: BLE001
            self._auto_play_download_ids.discard(target.id)
            self._snack(f"No se pudo iniciar la descarga: {exc}", error=True)

    def _on_batch_download(self) -> None:
        """Descarga en lote las pistas seleccionadas que no sean archivos locales."""
        if self.state.download_state == TransferState.RUNNING:
            self._snack("Descarga en curso", error=True)
            return
        selected = [t for t in self.state.tracks if t.selected]
        if not selected:
            self._snack("Selecciona al menos una canción", error=True)
            return
        to_download = [t for t in selected if not getattr(t, "source_path", "")]
        if not to_download:
            self._snack("Las canciones seleccionadas ya son archivos locales en disco", error=True)
            return
        try:
            self.state.download_tracks(
                to_download,
                **self._playlist_download_context(to_download),
            )
            self._snack(f"Descargando {len(to_download)} canciones…")
        except Exception as exc:  # noqa: BLE001
            self._snack(f"No se pudo iniciar la descarga: {exc}", error=True)

    def _on_load(self, _) -> None:
        src = self.state.source
        if src == "Archivo Local":
            asyncio.create_task(self._do_local_pick())
        elif src == "Pegar Texto":
            self._open_paste_dialog()
        else:
            asyncio.create_task(self._do_cloud_load())

    async def _do_cloud_load(self, _=None) -> None:
        # El selector manda cuando está visible: elegir una playlist de la
        # biblioteca es el camino normal y el campo de ID solo aparece como
        # alternativa avanzada.
        pid = ""
        if not self._url_mode:
            pid = str(self._playlist_pick.value or "")
        if not pid:
            pid = (self._id_field.value or "").strip()
        if not pid:
            self._snack("Elige una playlist o introduce un ID o una URL")
            return
        self._completion_snack_shown = False
        await self.state.load_playlist(pid)

    def _on_clear_id(self, _) -> None:
        """Limpia solo el campo de ID pegado."""
        self._id_field.value = ""
        self._id_clear_btn.visible = False
        self._id_field.update()

    def _on_clear_session(self, _) -> None:
        """Limpia el ID, la lista cargada y el estado de transferencia."""
        self._id_field.value = ""
        self._id_clear_btn.visible = False
        self._search_field.value = ""
        self.state.reset_session()
        self._snack("Sesión limpiada")

    def _open_folder_in_gestion(self, name: str, tracks: list) -> None:
        """Carga una carpeta local de la Biblioteca en Gestión como playlist.

        Deja el origen en «Archivo Local» para que Transferir pida destino en
        vez de tratar la lista como una playlist remota, y alinea el selector
        de origen: ``AppState.source`` no se sincroniza solo con el widget.
        """
        if not tracks:
            self._snack("La carpeta no tiene pistas", error=True)
            return
        self._completion_snack_shown = False
        self._pm_cleared_for_load = True
        self._telemetry.clear_postmortem()
        self.state.load_local_tracks(list(tracks), playlist_name=name or "Playlist Local")
        self.state.set_source("Archivo Local")
        self._src_dd.value = "Archivo Local"
        self._select_module(0)
        self._snack(f"{len(tracks)} pistas de «{name}» cargadas en Gestión")

    def _open_paste_dialog(self) -> None:
        self._paste_field.value = ""

        def _on_order_change(e):
            # SegmentedButton -> set, Dropdown -> value
            try:
                val = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                self.state.local_parse_order = val

        order_seg = self._make_order_segmented(self.state.local_parse_order, on_change=_on_order_change)

        def _close_paste():
            self._close_dlg(paste_dlg)

        def _process(_):
            text = self._paste_field.value or ""
            _close_paste()
            if not text.strip():
                self._snack("El campo de texto está vacío", error=True)
                return
            import datetime as _dt
            default_ts = _dt.datetime.now().strftime("%H:%M")
            self._ask_playlist_name_then_ingest(text=text, filename="",
                                                suggested_name=f"Local_Import_{default_ts}")

        paste_dlg = app_dialog(
            "Pegar Texto",
            ft.Column([
                ft.Container(content=self._paste_field, width=480, height=220),
                ft.Row([
                    ft.Text("Orden:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                    order_seg
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("Elige cómo está escrito cada línea (TuneMyMusic usa Artista - Título).",
                        size=9, color=TEXT_DIM, font_family=FONT_HEADLINE),
            ], spacing=10, tight=True),
            [
                dialog_action("Procesar", _process, kind="primary", icon=PIcons.PLAY),
                dialog_action("Cancelar", lambda _: _close_paste(), kind="muted"),
            ],
            width=520,
        )
        self.page.show_dialog(paste_dlg)

    def _ask_playlist_name_then_ingest(self, text: str, filename: str, suggested_name: str) -> None:
        import datetime as _dt
        name_field = app_text_field(
            value=suggested_name, hint_text=f"Ej. {suggested_name}",
            label="Nombre de la Playlist",
            autofocus=True, on_submit=lambda _: _confirm(None),
        )

        def _on_order_change2(e):
            try:
                val = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                self.state.local_parse_order = val

        order_seg2 = self._make_order_segmented(self.state.local_parse_order, on_change=_on_order_change2)

        def _close():
            self._close_dlg(name_dlg)

        def _confirm(_):
            raw        = (name_field.value or "").strip()
            final_name = raw if raw else (suggested_name or f"Local_Import_{_dt.datetime.now().strftime('%H:%M')}")
            _close()
            self._ingest_text(text, label=final_name, filename=filename)

        name_dlg = app_dialog(
            "Nombra esta playlist",
            ft.Column([
                ft.Text("Asigna un nombre antes de importar. Si lo dejas vacío se usará el nombre sugerido.",
                        size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                name_field,
                ft.Row([
                    ft.Text("Orden:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                    order_seg2
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("Confirma el orden de cada línea antes de importar.",
                        size=9, color=TEXT_DIM, font_family=FONT_HEADLINE),
            ], spacing=10, tight=True),
            [
                dialog_action("Importar", _confirm, kind="primary", icon=PIcons.CHECK),
                dialog_action("Cancelar", lambda _: _close(), kind="muted"),
            ],
            width=460,
            icon=PIcons.PENCIL_SIMPLE,
        )
        self.page.show_dialog(name_dlg)

    async def _do_local_pick(self) -> None:
        files = await self._file_picker.pick_files(
            dialog_title="Seleccionar playlist",
            allowed_extensions=[
                "txt", "csv", "m3u", "m3u8", "pls", "wpl", "xspf", "xml",
                "mp3", "flac", "aac", "ogg", "wav", "m4a", "wma", "opus", "aiff", "aif",
            ],
        )
        if not files:
            return
        f = files[0]
        if os.path.splitext(f.name)[1].lower() in {
            ".mp3", ".flac", ".aac", ".ogg", ".wav", ".m4a", ".wma", ".opus", ".aiff", ".aif",
        }:
            self._ingest_audio_file(f.path, os.path.splitext(os.path.basename(f.name))[0])
            return
        try:
            with open(f.path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            self.state.log(f"[ERROR] No se pudo leer '{f.name}': {exc}")
            self._snack(f"Error leyendo archivo: {exc}", error=True)
            return
        base_name = os.path.splitext(os.path.basename(f.name))[0] or "Playlist Local"
        self._ask_playlist_name_then_ingest(text=text, filename=f.path, suggested_name=base_name)

    def _ingest_text(self, text: str, label: str = "", filename: str = "") -> None:
        try:
            pairs = parse_local_playlist_with_paths(text, filename=filename or label, order=self.state.local_parse_order)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.state.log(f"[ERROR] Parser ingesta: {exc}")
            self._snack(f"Error en el parser: {exc}", error=True)
            return
        if not pairs:
            self.state.log(f"[WARN] No se encontraron pistas en '{label or 'texto'}'")
            self._snack("No se reconocieron pistas en el archivo", error=True)
            return
        tracks = build_local_tracks(pairs)
        name   = label.strip() if label and label.strip() else "Playlist Local"
        self._completion_snack_shown = False
        self._pm_cleared_for_load    = True
        self._telemetry.clear_postmortem()
        self.state.load_local_tracks(tracks, playlist_name=name)
        self._snack(f"{len(tracks)} canciones importadas de '{name}'")
        self.state.log(f"[INFO] Ingesta completa · {len(tracks)} pistas desde '{label}'")

    def _ingest_audio_file(self, path: str, label: str) -> None:
        """Importa un archivo de audio leyendo sus tags con Mutagen."""
        try:
            tracks = build_local_tracks([("", os.path.basename(path), path)])
        except Exception as exc:  # Mutagen no debe tumbar la UI
            self.state.log(f"[ERROR] Metadatos de audio: {exc}")
            self._snack(f"Error leyendo metadatos: {exc}", error=True)
            return
        if not tracks:
            self._snack("No se pudieron leer los metadatos del audio", error=True)
            return
        name = label.strip() or "Archivo de audio"
        self._completion_snack_shown = False
        self._pm_cleared_for_load = True
        self._telemetry.clear_postmortem()
        self.state.load_local_tracks(tracks, playlist_name=name)
        self._snack(f"Audio importado: '{name}'")
        self.state.log(f"[INFO] Audio local importado · {path}")

    def _get_synced_playlists_for(self, dest: str) -> list:
        """Devuelve playlists sincronizadas en Biblioteca para ``dest``."""
        return get_synced_playlists_for(self, dest)

    async def _run_dialog_preflight(self, dest: str) -> tuple[bool, str, bool]:
        """Ejecuta comprobación en vivo de sesión para ``dest``."""
        return await run_dialog_preflight(self, dest)

    async def _resolve_tracks_for_destination(
        self, dest: str, tracks: list[Track]
    ) -> list[Track]:
        """Resuelve pistas que no tengan ID en ``dest`` usando el servicio."""
        return await resolve_tracks_for_destination(self, dest, tracks)

    async def _add_tracks_to_existing_destination(
        self, dest: str, playlist_id: str, playlist_name: str, tracks: list[Track]
    ) -> int:
        """Inserta ``tracks`` en una playlist existente usando el servicio disponible."""
        return await add_tracks_to_existing_destination(
            self, dest, playlist_id, playlist_name, tracks
        )

    async def _open_transfer_dialog(self, *, has_divisions: bool = False) -> None:
        """Abre el diálogo de transferencia delegando a ``ui.dialogs.transfer_dialog``."""
        await open_transfer_dialog(self, has_divisions=has_divisions)

    async def _ensure_destination_session(self) -> bool:
        """Preflight del destino antes de transferir."""
        dest = self.state.destination
        if dest not in AppState.PLATFORMS:
            return True
        am = getattr(self, "auth_manager", None)
        if am is None:
            return True
        self._snack(f"Comprobando sesión de {dest}…")
        ok, err, expired = await self._run_dialog_preflight(dest)
        if ok:
            return True
        self.state.log(f"[ERROR] Transferencia bloqueada: sesión de {dest} no válida")
        self._snack(f"Sesión de {dest} no válida: {err or 'revisa Configuración'}", error=True)
        if expired and hasattr(am, "open_wizard"):
            am.open_wizard(dest)
        return False

    @staticmethod
    def _safe_ctrl_update(ctrl) -> None:
        safe_update(ctrl)

    async def _on_transfer(self, _=None) -> None:
        """Abre el diálogo unificado de destino + preflight en vivo (2.1 & 2.9)."""
        if not self.state.tracks:
            self._snack("Carga una playlist primero", error=True)
            return
        has_divisions = bool(getattr(self.state, "segments", {}))
        if has_divisions:
            self._report_dialog_shown = True
            sel_divs = (
                self.state.active_segment_keys
                if self.state.active_segment_keys is not None
                else set(self.state.segments.keys())
            )
            if not sel_divs:
                self._snack("Selecciona al menos una división para transferir", error=True)
                return
        elif self.state.selected_count == 0:
            self._snack("Selecciona al menos una canción", error=True)
            return
        await open_transfer_dialog(self, has_divisions=has_divisions)

    async def _open_add_to_playlist_dialog(self, tracks: list[Track] | None = None) -> None:
        """Abre el diálogo de exportación directamente en modo «Añadir a playlist»."""
        await self._open_export_dialog(initial_mode="playlist", custom_tracks=tracks)

    async def _open_export_dialog(
        self,
        *,
        initial_mode: str = "file",
        custom_tracks: list[Track] | None = None,
    ) -> None:
        """Diálogo de exportación local o inserción en una playlist destino (2.4/2.5)."""
        await open_export_dialog(
            self,
            initial_mode=initial_mode,
            custom_tracks=custom_tracks,
        )

    def _open_merge_dialog(self) -> None:
        """Diálogo de fusión de playlists locales/remotas con deduplicación (2.2)."""
        open_merge_dialog(self)

    # ── Búsqueda en catálogo 2.11 (Canciones / Álbumes / Artistas) ────

    def _on_toggle_catalog_search(self, _=None) -> None:
        self._toggle_catalog_panel(not getattr(self, "_catalog_open", False))

    def _toggle_catalog_panel(self, open_panel: bool | None = None) -> None:
        target = (
            not getattr(self, "_catalog_open", False)
            if open_panel is None
            else bool(open_panel)
        )
        self._catalog_open = target
        if hasattr(self, "_catalog_panel"):
            self._catalog_panel.visible = target
        if hasattr(self, "_col_headers"):
            self._col_headers.visible = not target
        if hasattr(self, "_list_area"):
            self._list_area.visible = not target
        if hasattr(self, "_mode_bar"):
            has_transform = bool(
                getattr(self.state, "segments", {})
                or getattr(self.state, "show_dual", False)
                or getattr(self.state, "dual_mode", "lista") in ("doble", "preview")
            )
            self._mode_bar.visible = bool(has_transform and not target)
        try:
            content = getattr(self, "_content", None)
            if not safe_update(content):
                self.page.update()
        except Exception:
            pass

    def _on_catalog_platform_change(self, _e=None) -> None:
        query = (getattr(self._catalog_query_field, "value", "") or "").strip()
        if query:
            asyncio.create_task(self._on_catalog_search_submit())

    def _on_catalog_tab_change(self, e) -> None:
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
        self._catalog_tab = val
        self._catalog_detail_stack.clear()
        if hasattr(self, "_catalog_detail_bar"):
            self._catalog_detail_bar.visible = False
        query = (getattr(self._catalog_query_field, "value", "") or "").strip()
        if query:
            asyncio.create_task(self._on_catalog_search_submit())
        else:
            self._catalog_results_lv.controls.clear()
            self._catalog_status.value = "Escribe un término y pulsa Buscar."
            self._safe_ctrl_update(self._catalog_panel)

    async def _on_catalog_search_submit(self, _=None) -> None:
        query = (getattr(self._catalog_query_field, "value", "") or "").strip()
        if not query:
            self._catalog_status.value = "Introduce un texto para buscar en el catálogo."
            self._safe_ctrl_update(self._catalog_status)
            return
        platform = str(getattr(self._catalog_platform_dd, "value", "") or AppState.PLATFORMS[0])
        kind = getattr(self, "_catalog_tab", "songs")
        await self._run_catalog_search(query, kind=kind, platform=platform)

    async def _run_catalog_search(
        self, query: str, *, kind: str = "songs", platform: str = ""
    ) -> None:
        plat = platform or AppState.PLATFORMS[0]
        service = getattr(self.state, "service", None)
        self._catalog_detail_stack.clear()
        if hasattr(self, "_catalog_detail_bar"):
            self._catalog_detail_bar.visible = False
        self._catalog_status.value = f"Buscando {kind} en {plat}…"
        self._safe_ctrl_update(self._catalog_status)

        if service is None:
            self._catalog_status.value = "Servicio no disponible."
            self._safe_ctrl_update(self._catalog_status)
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
                self._catalog_results_lv.controls.clear()
                self._catalog_status.value = "Búsqueda de catálogo no disponible en este servicio."
                self._safe_ctrl_update(self._catalog_panel)
                return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._catalog_status.value = f"Error al buscar en {plat}: {exc}"
            self._safe_ctrl_update(self._catalog_status)
            return

        self._catalog_results = results
        self._render_catalog_results(results, kind=kind, platform=plat)

    def _render_catalog_results(
        self, results: list[dict], *, kind: str, platform: str
    ) -> None:
        lv = self._catalog_results_lv
        lv.controls.clear()
        if not results:
            self._catalog_status.value = f"Sin resultados en {platform}."
            self._safe_ctrl_update(self._catalog_panel)
            return
        self._catalog_status.value = f"{len(results)} resultados en {platform}"
        for item in results:
            item_id = str(item.get("id") or "")
            name = str(item.get("name") or "Sin título")
            subtitle = str(item.get("subtitle") or item.get("artist") or "")
            img_url = str(item.get("img_url") or "")
            thumb = ft.Container(
                width=44,
                height=44,
                border_radius=8,
                bgcolor=SKELETON_DARK,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=_thumb_content_for(img_url, box=44, icon_size=18),
            )
            info = ft.Column(
                [
                    ft.Text(name, size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(subtitle, size=11, color=TEXT_MUTED, font_family=FONT_TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
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
                        lambda _, track=tr, p=platform: self._load_catalog_tracks_into_gestion([track], track.name, platform=p),
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
                        lambda _, track=tr: asyncio.create_task(self._open_add_to_playlist_dialog([track])),
                        height=30,
                    ),
                ]
            elif kind == "albums":
                actions = [
                    _ghost_btn(
                        "Ver álbum",
                        PIcons.DISC,
                        lambda _, aid=item_id, aname=name, p=platform: asyncio.create_task(
                            self._open_catalog_album(p, aid, aname)
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
                            self._open_catalog_artist(p, arid, arname)
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
        self._safe_ctrl_update(self._catalog_panel)

    async def _open_catalog_album(
        self, platform: str, album_id: str, fallback_name: str = ""
    ) -> None:
        service = getattr(self.state, "service", None)
        fetch_fn = getattr(service, "fetch_album_tracks", None) if service is not None else None
        if not callable(fetch_fn):
            self._snack("Detalle de álbum no disponible en este servicio", error=True)
            return
        self._catalog_status.value = f"Cargando álbum «{fallback_name or album_id}»…"
        self._safe_ctrl_update(self._catalog_status)
        try:
            meta, tracks = await fetch_fn(platform, album_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._snack(f"No se pudo cargar el álbum: {exc}", error=True)
            return
        title = str(getattr(meta, "name", "") or fallback_name or "Álbum")
        self._catalog_detail_stack.append(
            {
                "kind": "results",
                "tab": self._catalog_tab,
                "platform": platform,
                "results": list(self._catalog_results),
            }
        )
        self._render_catalog_tracks_detail(
            title=title, platform=platform, tracks=list(tracks or [])
        )

    async def _open_catalog_artist(
        self, platform: str, artist_id: str, fallback_name: str = ""
    ) -> None:
        service = getattr(self.state, "service", None)
        fetch_fn = getattr(service, "fetch_artist_overview", None) if service is not None else None
        if not callable(fetch_fn):
            self._snack("Detalle de artista no disponible en este servicio", error=True)
            return
        self._catalog_status.value = f"Cargando artista «{fallback_name or artist_id}»…"
        self._safe_ctrl_update(self._catalog_status)
        try:
            overview = await fetch_fn(platform, artist_id) or {}
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._snack(f"No se pudo cargar el artista: {exc}", error=True)
            return
        artist_info = overview.get("artist")
        if isinstance(artist_info, dict):
            artist_name = str(artist_info.get("name") or fallback_name or "Artista")
        else:
            artist_name = str(artist_info or fallback_name or "Artista")
        top_tracks = list(overview.get("top_tracks") or [])
        albums = list(overview.get("albums") or [])

        self._catalog_detail_stack.append(
            {
                "kind": "results",
                "tab": self._catalog_tab,
                "platform": platform,
                "results": list(self._catalog_results),
            }
        )
        self._catalog_detail_tracks = top_tracks
        self._catalog_detail_title_str = f"Top · {artist_name}"
        self._catalog_detail_platform = platform
        self._catalog_detail_title.value = f"{artist_name} · {len(top_tracks)} top / {len(albums)} álbumes"
        self._catalog_detail_bar.visible = True
        lv = self._catalog_results_lv
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
                                ft.Container(
                                    width=40,
                                    height=40,
                                    border_radius=8,
                                    bgcolor=SKELETON_DARK,
                                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                    content=_thumb_content_for(str(alb.get("img_url") or ""), box=40, icon_size=16),
                                ),
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
                                        self._open_catalog_album(p, a_id, a_name)
                                    ),
                                    height=30,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )
        self._catalog_status.value = f"{artist_name} · {len(top_tracks)} canciones"
        self._safe_ctrl_update(self._catalog_panel)

    def _build_catalog_track_row(self, tr: Track, idx: int, platform: str) -> ft.Container:
        return ft.Container(
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.6, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            content=ft.Row(
                [
                    ft.Text(str(idx), size=11, color=TEXT_DIM, font_family=mono_family("light"), width=24),
                    ft.Container(
                        width=38,
                        height=38,
                        border_radius=6,
                        bgcolor=SKELETON_DARK,
                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                        content=_thumb_content_for(tr.img_url, box=38, icon_size=16),
                    ),
                    ft.Column(
                        [
                            ft.Text(tr.name, size=12, color=TEXT_PRIMARY, font_family=FONT_HEADLINE, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(tr.artist, size=10, color=TEXT_MUTED, font_family=FONT_TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
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
                        lambda _, track=tr: asyncio.create_task(self._open_add_to_playlist_dialog([track])),
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
        self._catalog_detail_tracks = list(tracks)
        self._catalog_detail_title_str = title
        self._catalog_detail_platform = platform
        self._catalog_detail_title.value = f"{title} · {len(tracks)} canciones"
        self._catalog_detail_bar.visible = True
        lv = self._catalog_results_lv
        lv.controls.clear()
        for idx, tr in enumerate(tracks, 1):
            lv.controls.append(self._build_catalog_track_row(tr, idx, platform))
        self._catalog_status.value = f"{len(tracks)} canciones"
        self._safe_ctrl_update(self._catalog_panel)

    def _on_catalog_back(self) -> None:
        if not self._catalog_detail_stack:
            self._catalog_detail_bar.visible = False
            self._safe_ctrl_update(self._catalog_panel)
            return
        prev = self._catalog_detail_stack.pop()
        self._catalog_detail_bar.visible = False
        self._render_catalog_results(
            prev.get("results") or [],
            kind=str(prev.get("tab") or self._catalog_tab),
            platform=str(prev.get("platform") or AppState.PLATFORMS[0]),
        )

    def _on_catalog_detail_load(self) -> None:
        tracks = list(getattr(self, "_catalog_detail_tracks", []) or [])
        if not tracks:
            self._snack("No hay canciones para cargar", error=True)
            return
        self._load_catalog_tracks_into_gestion(
            tracks,
            self._catalog_detail_title_str or "Catálogo",
            platform=self._catalog_detail_platform,
        )

    def _on_catalog_detail_download(self) -> None:
        tracks = list(getattr(self, "_catalog_detail_tracks", []) or [])
        if not tracks:
            self._snack("No hay canciones para descargar", error=True)
            return
        self._download_catalog_tracks(tracks, self._catalog_detail_title_str or "Catálogo")

    async def _on_catalog_detail_add(self) -> None:
        tracks = list(getattr(self, "_catalog_detail_tracks", []) or [])
        if not tracks:
            self._snack("No hay canciones para añadir", error=True)
            return
        await self._open_add_to_playlist_dialog(tracks)

    def _load_catalog_tracks_into_gestion(
        self, tracks: list[Track], title: str, *, platform: str = ""
    ) -> None:
        self._completion_snack_shown = False
        self._pm_cleared_for_load = True
        if hasattr(self, "_telemetry"):
            self._telemetry.clear_postmortem()
        self.state.load_local_tracks(list(tracks), playlist_name=title or "Catálogo")
        self._toggle_catalog_panel(False)
        self._snack(f"{len(tracks)} canciones cargadas en Gestión desde {platform or 'catálogo'}")

    def _download_catalog_tracks(self, tracks: list[Track], title: str) -> None:
        if self.state.download_state == TransferState.RUNNING:
            self._snack("Descarga en curso", error=True)
            return
        from services import settings_store
        platforms = {t.platform for t in tracks if getattr(t, "platform", "")}
        self.state.download_tracks(
            list(tracks),
            destination_dir=settings_store.get_download_dir(),
            playlist_name=title or "",
            platform=platforms.pop() if len(platforms) == 1 else "",
        )
        self._snack(f"Descargando {len(tracks)} canciones…")

    async def _on_search_change(self, e: ft.ControlEvent) -> None:
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()
        query = e.control.value
        self._search_task = asyncio.create_task(self._do_search(query))

    async def _do_search(self, query: str) -> None:
        await asyncio.sleep(0.30)
        self.state.apply_search(query)
