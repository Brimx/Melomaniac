"""ui/main_actions.py — Acciones del usuario: cargar, pegar, importar, transferir, exportar y buscar.

Mixin de `PlaylistManagerUI` (ui/main_ui.py): los métodos se mezclan en la clase
y comparten su `self`.
"""

from __future__ import annotations

import asyncio
import flet as ft
import os

from ui.widgets import (
    _ghost_btn,
    _primary_btn,
    _section_label,
    app_dialog,
    app_text_field,
    debounce,
    dialog_action,
    input_border,
    limit_label,
    organize_dropdown,
)
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.tokens import (
    ACCENT,
    BG_INPUT,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    DIALOG_OPTION_LIMIT,
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
from core.config import EXPORT_DEST_LABEL, EXPORT_FORMATS
from engine.parsers import build_local_tracks, parse_local_playlist_with_paths
from ui.icons import PIcons, app_icon
from ui.song_row import _thumb_content_for
from core.state import AppState
from engine.exporters import default_export_path, export_tracks


class ActionsMixin:
    """Acciones del usuario: cargar, pegar, importar, transferir, exportar y buscar."""

    def _on_row_download(self, track_id: str) -> None:
        if self.state.download_state == TransferState.RUNNING:
            self._snack("Descarga en curso", error=True)
            return
        target = next((t for t in self.state.tracks if t.id == track_id), None)
        if target is None:
            target = next((t for t in self.state.source_tracks if t.id == track_id), None)
        if target is None:
            return
        from services import settings_store
        self._auto_play_download_ids.add(target.id)
        try:
            self.state.download_tracks(
                [target],
                destination_dir=settings_store.get_download_dir(),
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
        from services import settings_store
        platforms = {t.platform for t in to_download if getattr(t, "platform", "")}
        try:
            self.state.download_tracks(
                to_download,
                destination_dir=settings_store.get_download_dir(),
                playlist_name=self.state.playlist_name or "",
                platform=platforms.pop() if len(platforms) == 1 else "",
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
        lib = getattr(self, "library_state", None)
        if lib is None or dest not in AppState.PLATFORMS:
            return []
        try:
            return list(lib.get_playlists(dest) or [])
        except Exception:
            return []

    async def _run_dialog_preflight(self, dest: str) -> tuple[bool, str, bool]:
        """Ejecuta comprobación en vivo de sesión para ``dest``.

        Retorna ``(ok, error_msg, expired)``.
        """
        if dest == EXPORT_DEST_LABEL:
            return True, "Exportación local sin sesión requerida", False
        if dest not in AppState.PLATFORMS:
            return True, "", False
        am = getattr(self, "auth_manager", None)
        if am is None:
            cached = self.state.auth_session_ok.get(dest)
            if cached is False:
                hint = self.state.auth_session_hint.get(dest) or "revisa Configuración"
                return False, hint, True
            return True, "", False
        try:
            results = await am.check_sessions([dest])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.state.log(f"[ERROR] Preflight de {dest}: {exc}")
            return False, str(exc), False
        if hasattr(am, "ingest_preflight_results"):
            am.ingest_preflight_results(results)
        result = next((r for r in results if getattr(r, "platform", "") == dest), None)
        if result is None:
            return True, "", False
        return (
            bool(result.ok),
            str(getattr(result, "error", "") or ""),
            bool(getattr(result, "expired", False)),
        )

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

    def _make_preflight_status_row(self, on_status_change=None):
        """Widget reutilizable de preflight en vivo dentro de diálogos."""
        icon_ctrl = app_icon(PIcons.INFO, size=15, color=TEXT_MUTED)
        status_txt = ft.Text(
            "Comprobando sesión…",
            size=11,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            expand=True,
        )
        state = {"dest": "", "ok": None, "seq": 0}
        config_btn = _ghost_btn("Configurar", PIcons.GEAR, lambda _: None, height=30)
        config_btn.visible = False

        async def _run(dest: str) -> bool:
            state["dest"] = dest
            state["seq"] += 1
            seq = state["seq"]
            if dest == EXPORT_DEST_LABEL:
                state["ok"] = True
                icon_ctrl.value = PIcons.CHECK_CIRCLE
                icon_ctrl.color = SUCCESS
                status_txt.value = "Exportación local no requiere sesión."
                status_txt.color = SUCCESS
                config_btn.visible = False
                if on_status_change:
                    on_status_change(True)
                self._safe_ctrl_update(banner)
                return True
            state["ok"] = None
            icon_ctrl.value = PIcons.CLOCK
            icon_ctrl.color = TEXT_MUTED
            status_txt.value = f"Comprobando sesión de {dest}…"
            status_txt.color = TEXT_MUTED
            config_btn.visible = False
            if on_status_change:
                on_status_change(None)
            self._safe_ctrl_update(banner)
            ok, err, _expired = await self._run_dialog_preflight(dest)
            if seq != state["seq"]:
                return ok
            state["ok"] = ok
            if ok:
                icon_ctrl.value = PIcons.CHECK_CIRCLE
                icon_ctrl.color = SUCCESS
                status_txt.value = f"Sesión activa en {dest}"
                status_txt.color = SUCCESS
                config_btn.visible = False
            else:
                icon_ctrl.value = PIcons.WARNING_CIRCLE
                icon_ctrl.color = ERROR_COL
                detail = f": {err[:90]}" if err else " (revisa credenciales)"
                status_txt.value = f"Sesión no válida en {dest}{detail}"
                status_txt.color = ERROR_COL
                am = getattr(self, "auth_manager", None)
                config_btn.visible = am is not None
                if am is not None:
                    config_btn.on_click = lambda _, p=dest: am.open_wizard(p)
            if on_status_change:
                on_status_change(ok)
            self._safe_ctrl_update(banner)
            return ok

        recheck_btn = _ghost_btn(
            "Comprobar",
            PIcons.ARROWS_CLOCKWISE,
            lambda _: asyncio.create_task(_run(state["dest"])),
            height=30,
        )
        banner = ft.Container(
            bgcolor=CHIP_BG,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            content=ft.Row(
                [icon_ctrl, status_txt, config_btn, recheck_btn],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        return banner, state, _run

    @staticmethod
    def _safe_ctrl_update(ctrl) -> None:
        try:
            if getattr(ctrl, "page", None) is not None:
                ctrl.update()
        except Exception:
            pass

    async def _resolve_tracks_for_destination(
        self, dest: str, tracks: list[Track]
    ) -> list[Track]:
        """Resuelve pistas que no tengan ID en ``dest`` usando el servicio."""
        service = getattr(self.state, "service", None)
        resolved: list[Track] = []
        pending: list[Track] = []
        for tr in tracks:
            if getattr(tr, "platform", "") == dest and getattr(tr, "id", ""):
                resolved.append(tr)
            else:
                pending.append(tr)
        if not pending or service is None or not hasattr(service, "search_with_fallback"):
            return resolved

        async def _resolve_one(tr: Track) -> Track | None:
            try:
                res = await service.search_with_fallback(dest, tr)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.state.log(f"[WARN] Sin coincidencia para '{tr.name}' en {dest}: {exc}")
                return None
            if res and getattr(res, "track_id", ""):
                return Track(
                    id=str(res.track_id),
                    name=tr.name,
                    artist=tr.artist,
                    album=tr.album,
                    duration=tr.duration,
                    platform=dest,
                    duration_ms=tr.duration_ms,
                    is_explicit=tr.is_explicit,
                    isrc=getattr(res, "isrc", "") or tr.isrc,
                    img_url=tr.img_url,
                )
            return None

        if dest == "Apple Music":
            # Apple Music siempre secuencial por AppleRequestLimiter (AGENTS.md).
            for tr in pending:
                item = await _resolve_one(tr)
                if item is not None:
                    resolved.append(item)
        else:
            sem = asyncio.Semaphore(3)

            async def _bounded(tr: Track) -> Track | None:
                async with sem:
                    return await _resolve_one(tr)

            results = await asyncio.gather(*(_bounded(t) for t in pending), return_exceptions=True)
            for item in results:
                if isinstance(item, Track):
                    resolved.append(item)
        return resolved

    async def _add_tracks_to_existing_destination(
        self, dest: str, playlist_id: str, playlist_name: str, tracks: list[Track]
    ) -> int:
        """Inserta ``tracks`` en una playlist existente usando el servicio disponible."""
        service = getattr(self.state, "service", None)
        if service is None:
            self._snack("Servicio de música no disponible", error=True)
            return 0
        if not playlist_id:
            self._snack("Selecciona una playlist destino válida", error=True)
            return 0
        init_map = {
            "YouTube Music": "init_youtube",
            "Apple Music": "init_apple",
            "Spotify": "init_spotify",
        }
        init_name = init_map.get(dest)
        if init_name and callable(getattr(service, init_name, None)):
            ok_auth = await getattr(service, init_name)()
            if not ok_auth:
                self._snack(f"No se pudo autenticar en {dest}", error=True)
                return 0

        self._snack(f"Preparando {len(tracks)} canciones para «{playlist_name}»…")
        resolved = await self._resolve_tracks_for_destination(dest, tracks)
        if not resolved:
            self._snack(f"No se encontraron coincidencias válidas en {dest}", error=True)
            return 0

        added = 0
        batch_fn = getattr(service, "add_tracks_to_playlist", None)
        if callable(batch_fn):
            try:
                added = int(await batch_fn(dest, playlist_id, resolved))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.state.log(f"[ERROR] add_tracks_to_playlist({dest}): {exc}")
                self._snack(f"Error al añadir pistas: {exc}", error=True)
                return 0
        elif callable(getattr(service, "add_track", None)):
            for tr in resolved:
                try:
                    ok, _msg = await service.add_track(dest, playlist_id, tr.id)
                    if ok:
                        added += 1
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.state.log(f"[WARN] add_track({tr.name}): {exc}")
        else:
            self._snack("El servicio actual no soporta añadir pistas a playlists", error=True)
            return 0

        self.state.log(
            f"[SUCCESS] {added}/{len(resolved)} pistas añadidas a «{playlist_name}» ({dest})"
        )
        self._snack(f"{added} canciones añadidas a «{playlist_name}» ({dest})")
        return added

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
        await self._open_transfer_dialog(has_divisions=has_divisions)

    async def _open_transfer_dialog(self, *, has_divisions: bool = False) -> None:
        """Diálogo de ejecución de transferencia: destino, playlist existente o nueva y preflight."""
        s = self.state
        has_transform = bool(
            s.segments
            or getattr(s, "show_dual", False)
            or (getattr(s, "source_tracks", None) and s.tracks != s.source_tracks)
        )
        is_local_or_merged = (
            s.source in AppState.LOCAL_SOURCES
            or str(getattr(s, "playlist_id", "")).startswith(("local_", "merged_"))
        )
        default_dest = s.destination if s.destination in (*AppState.PLATFORMS, EXPORT_DEST_LABEL) else AppState.PLATFORMS[0]
        if default_dest == s.source and not has_transform and not is_local_or_merged:
            other = [p for p in AppState.PLATFORMS if p != s.source]
            if other:
                default_dest = other[0]

        dest_opts = [*AppState.PLATFORMS, EXPORT_DEST_LABEL]
        mode_state = {"mode": "new"}  # "new" | "existing"
        per_div_override: dict[str, str] = {}

        title_field = app_text_field(
            value=s.playlist_name,
            hint_text="Nombre de la playlist",
            label="Nombre de la playlist",
            autofocus=True,
        )
        desc_field = app_text_field(
            value=s.playlist_description,
            hint_text="Descripción (opcional)",
            label="Descripción",
            multiline=True,
            min_lines=2,
            max_lines=3,
        )
        src_requires_auth = (
            s.source not in AppState.LOCAL_SOURCES
            and s.auth_session_ok.get(s.source) is True
        )
        delete_chk = ft.Checkbox(
            label=(
                "Eliminar original (avanzado, requiere sesión activa)"
                if src_requires_auth
                else "Eliminar original (no disponible para local)"
            ),
            value=False,
            disabled=not src_requires_auth,
            fill_color={ft.ControlState.SELECTED: ACCENT},
            check_color=TEXT_PRIMARY,
            label_style=ft.TextStyle(color=TEXT_MUTED, size=11, font_family=FONT_TEXT),
            border_side=ft.BorderSide(1.2, TEXT_DIM),
        )
        existing_dd = organize_dropdown(
            [],
            "",
            "Playlist destino sincronizada",
            width=460,
        )
        existing_help = ft.Text(
            "",
            size=10,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            visible=False,
        )
        new_box = ft.Column([title_field, desc_field, delete_chk], spacing=8, tight=True)
        existing_box = ft.Column([existing_dd, existing_help], spacing=6, tight=True, visible=False)

        confirm_btn = dialog_action("Transferir", lambda _: None, kind="primary", icon=PIcons.ARROWS_LEFT_RIGHT)

        def _on_preflight_status(ok: bool | None) -> None:
            confirm_btn.disabled = ok is False
            self._safe_ctrl_update(confirm_btn)

        preflight_banner, preflight_state, run_preflight = self._make_preflight_status_row(
            on_status_change=_on_preflight_status
        )

        def _refresh_existing_options(dest: str) -> None:
            items = self._get_synced_playlists_for(dest)
            existing_dd.options = [
                ft.dropdown.Option(key=str(p.id), text=f"{p.name or p.id} ({p.track_count})")
                for p in items
            ]
            existing_dd.value = str(items[0].id) if items else ""
            existing_help.visible = not items
            if not items:
                existing_help.value = (
                    f"Sin playlists sincronizadas de {dest} en Biblioteca. "
                    "Sincroniza esa plataforma en Biblioteca o crea una playlist nueva."
                )

        def _sync_mode_visibility(dest: str) -> None:
            is_export = dest == EXPORT_DEST_LABEL
            is_existing = mode_state["mode"] == "existing" and not is_export
            mode_row.visible = not is_export and not has_divisions
            new_box.visible = not is_export and not is_existing
            existing_box.visible = is_existing
            if is_export:
                confirm_btn.disabled = False
            self._safe_ctrl_update(mode_row)
            self._safe_ctrl_update(new_box)
            self._safe_ctrl_update(existing_box)
            self._safe_ctrl_update(confirm_btn)

        def _on_dest_select(e) -> None:
            dest = str(e.control.value or default_dest)
            _refresh_existing_options(dest)
            _sync_mode_visibility(dest)
            asyncio.create_task(run_preflight(dest))

        dest_dd = organize_dropdown(
            [(p, p) for p in dest_opts],
            default_dest,
            "Plataforma destino",
            width=460,
            on_select=_on_dest_select,
        )

        def _on_mode_change(e) -> None:
            try:
                val = (
                    list(e.control.selected)[0]
                    if hasattr(e.control, "selected") and e.control.selected
                    else e.control.value
                )
            except Exception:
                val = getattr(e.control, "value", "new")
            if val in ("new", "existing"):
                mode_state["mode"] = val
            _sync_mode_visibility(str(dest_dd.value or default_dest))

        try:
            mode_seg: ft.Control = ft.SegmentedButton(
                selected=["new"],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="new", label=ft.Text("Nueva playlist", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="existing", label=ft.Text("Playlist existente", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=_on_mode_change,
            )
        except Exception:
            mode_seg = organize_dropdown(
                [("new", "Nueva playlist"), ("existing", "Playlist existente")],
                "new",
                "Destino",
                width=220,
                on_select=_on_mode_change,
            )

        mode_row = ft.Row(
            [ft.Text("Modo:", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE), mode_seg],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=not has_divisions,
        )

        dlg_ref: dict = {}

        async def _confirm_transfer(_e=None) -> None:
            dest = str(dest_dd.value or default_dest)
            if dest == EXPORT_DEST_LABEL:
                self._close_dlg(dlg_ref["dlg"])
                s.set_destination(dest)
                await self._open_export_dialog()
                return
            if preflight_state["ok"] is None:
                ok = await run_preflight(dest)
            else:
                ok = bool(preflight_state["ok"])
            if not ok:
                self._snack(
                    f"Sesión de {dest} no válida; configura o revalida antes de transferir",
                    error=True,
                )
                return

            mode = "new" if has_divisions else mode_state["mode"]
            if mode == "existing":
                target_pid = str(existing_dd.value or "").strip()
                if not target_pid:
                    self._snack(
                        f"Elige una playlist sincronizada de {dest} o cambia a «Nueva playlist»",
                        error=True,
                    )
                    return
                items = self._get_synced_playlists_for(dest)
                match = next((p for p in items if str(p.id) == target_pid), None)
                target_name = str(getattr(match, "name", "") or target_pid)
                selected_tracks = [t for t in s.tracks if t.selected]
                self._close_dlg(dlg_ref["dlg"])
                s.set_destination(dest)
                await self._add_tracks_to_existing_destination(
                    dest, target_pid, target_name, selected_tracks
                )
                return

            if s.source == dest and not has_transform and not is_local_or_merged:
                self._snack(
                    "Origen y destino no pueden ser iguales (solo permitido tras Organizar/Dividir)",
                    error=True,
                )
                return
            clean_title = (title_field.value or "").strip()
            if not clean_title:
                self._snack("El nombre de la playlist no puede estar vacío", error=True)
                return
            clean_desc = (desc_field.value or "").strip()
            delete_orig = bool(delete_chk.value) and src_requires_auth
            self._close_dlg(dlg_ref["dlg"])
            s.set_destination(dest)
            await self._execute_new_playlist_transfer(
                dest=dest,
                title=clean_title,
                description=clean_desc,
                delete_original=delete_orig,
                has_divisions=has_divisions,
                per_div_dests=per_div_override,
            )

        confirm_btn.on_click = lambda e: asyncio.create_task(_confirm_transfer(e))

        _refresh_existing_options(default_dest)
        _sync_mode_visibility(default_dest)

        dlg = app_dialog(
            "Transferir playlist",
            ft.Column(
                [
                    _section_label("DESTINO Y SESIÓN"),
                    dest_dd,
                    preflight_banner,
                    mode_row,
                    new_box,
                    existing_box,
                ],
                spacing=10,
                tight=True,
            ),
            [
                dialog_action("Cancelar", lambda _: self._close_dlg(dlg), kind="muted"),
                confirm_btn,
            ],
            width=500,
            icon=PIcons.ARROWS_LEFT_RIGHT,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)
        asyncio.create_task(run_preflight(default_dest))

    async def _execute_new_playlist_transfer(
        self,
        *,
        dest: str,
        title: str,
        description: str,
        delete_original: bool,
        has_divisions: bool,
        per_div_dests: dict[str, str] | None = None,
    ) -> None:
        """Ejecuta la creación de una o varias playlists según divisiones activas."""
        s = self.state
        if has_divisions:
            sel_divs = (
                s.active_segment_keys
                if s.active_segment_keys is not None
                else set(s.segments.keys())
            )
            overrides = per_div_dests or {}
            successes = 0
            seg_keys = list(s.segments.keys())
            for div in sorted(sel_divs, key=lambda k: seg_keys.index(k) if k in s.segments else 9999):
                div_tracks = s.segments.get(div, [])
                div_selected = (
                    [t for t in div_tracks if t.selected]
                    if any(t.selected for t in div_tracks)
                    else div_tracks
                )
                if not div_selected:
                    continue
                div_dest = overrides.get(div, "Mantener")
                if not div_dest or div_dest == "Mantener":
                    div_dest = dest
                try:
                    ok, msg, confirmed, _rejected = await s.create_remote_playlist(
                        div_dest,
                        f"{title} — {div}",
                        [t.id for t in div_selected if t.id],
                        description,
                    )
                    if ok:
                        successes += 1
                        s.log(f"[SUCCESS] División '{div}' → {div_dest}: {confirmed} canciones")
                    else:
                        s.log(f"[ERROR] División '{div}' fallo: {msg}")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    s.log(f"[ERROR] División '{div}' excepción: {exc}")
            self._snack(f"{successes}/{len(sel_divs)} divisiones transferidas")
            if overrides:
                s.division_destinations.update(overrides)
        else:
            await s.transfer_playlist(
                title_override=title,
                description_override=description,
            )

        if delete_original and s.playlist_id and s.source not in AppState.LOCAL_SOURCES:
            try:
                ok, msg = await s.delete_remote_playlist(s.source, s.playlist_id)
                if ok:
                    self._snack(f"Original eliminada ({s.source})")
                    s.log(f"[INFO] Original {s.playlist_id} eliminada tras reemplazo")
                else:
                    self._snack(f"No se pudo eliminar original: {msg}", error=True)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._snack(f"Error eliminando original: {exc}", error=True)

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
        if custom_tracks is not None:
            src_tracks = list(custom_tracks)
        else:
            if not self.state.tracks:
                self._snack("No hay playlist cargada para exportar", error=True)
                return
            src_tracks = [t for t in self.state.tracks if t.selected]
            if not src_tracks:
                src_tracks = (
                    list(self.state.display_tracks)
                    if hasattr(self.state, "display_tracks")
                    else list(self.state.tracks)
                )
        if not src_tracks:
            self._snack("No hay canciones para exportar", error=True)
            return

        mode_state = {"mode": "playlist" if initial_mode == "playlist" else "file"}
        fmt_val = getattr(self.state, "local_export_format", "txt")
        order_val = getattr(self.state, "local_export_order", "artist-title")

        def _on_fmt_change(e):
            self.state.local_export_format = e.control.value

        def _on_order_change_exp(e):
            try:
                val = (
                    list(e.control.selected)[0]
                    if hasattr(e.control, "selected") and e.control.selected
                    else e.control.value
                )
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                self.state.local_export_order = val

        fmt_dd = ft.Dropdown(
            label="Formato",
            value=fmt_val,
            width=160,
            height=38,
            bgcolor=BG_INPUT,
            border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            options=[ft.dropdown.Option(k, k.upper()) for k in EXPORT_FORMATS],
            on_select=_on_fmt_change,
        )
        order_seg = self._make_order_segmented(order_val, on_change=_on_order_change_exp)

        default_path = str(default_export_path(self.state.playlist_name, fmt_val))
        path_field = app_text_field(
            value=default_path,
            label="Ruta destino",
            hint_text=str(default_path),
            prefix_icon=PIcons.FOLDER,
            expand=True,
        )

        async def _pick_save(_):
            try:
                res = await self._save_picker.save_file(
                    dialog_title="Guardar playlist",
                    file_name=path_field.value.split("/")[-1].split("\\")[-1] or "playlist.txt",
                    allowed_extensions=list(EXPORT_FORMATS),
                )
                if res and getattr(res, "path", None):
                    path_field.value = res.path
                    path_field.update()
                elif isinstance(res, str) and res:
                    path_field.value = res
                    path_field.update()
            except Exception:
                pass

        pick_btn = _ghost_btn(
            "Elegir…",
            PIcons.FOLDER_OPEN,
            lambda e: self.page.run_task(_pick_save(e)),
            width=90,
            height=38,
        )
        file_box = ft.Column(
            [
                ft.Text(
                    "Guarda la selección actual con el mismo bloque que lee TXT/CSV/M3U/XSPF.",
                    size=10,
                    color=TEXT_MUTED,
                    font_family=FONT_TEXT,
                ),
                ft.Row(
                    [fmt_dd, ft.Container(expand=True), order_seg],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [path_field, pick_btn],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=10,
            tight=True,
            visible=(mode_state["mode"] == "file"),
        )

        # Modo «Añadir a playlist»
        default_plat = (
            self.state.destination
            if self.state.destination in AppState.PLATFORMS
            else AppState.PLATFORMS[0]
        )
        target_playlist_dd = organize_dropdown(
            [],
            "",
            "Playlist destino",
            width=480,
        )
        target_help = ft.Text(
            "",
            size=10,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            visible=False,
        )
        new_name_field = app_text_field(
            value=self.state.playlist_name or "Playlist Exportada",
            label="O crear nueva playlist",
            hint_text="Nombre si no eliges una existente",
            visible=False,
        )

        primary_btn = dialog_action("Exportar", lambda _: None, kind="primary", icon=PIcons.EXPORT)

        def _on_pf(ok: bool | None) -> None:
            if mode_state["mode"] == "playlist":
                primary_btn.disabled = ok is False
                self._safe_ctrl_update(primary_btn)

        pf_banner, pf_state, run_pf = self._make_preflight_status_row(on_status_change=_on_pf)

        def _fill_target_playlists(plat: str) -> None:
            items = self._get_synced_playlists_for(plat)
            target_playlist_dd.options = [
                ft.dropdown.Option(key=str(p.id), text=f"{p.name or p.id} ({p.track_count})")
                for p in items
            ]
            target_playlist_dd.value = str(items[0].id) if items else ""
            target_help.visible = not items
            new_name_field.visible = not items
            if not items:
                target_help.value = (
                    f"No hay playlists sincronizadas de {plat}: introduce un nombre para crearla."
                )

        def _on_plat_select(e) -> None:
            plat = str(e.control.value or default_plat)
            _fill_target_playlists(plat)
            self._safe_ctrl_update(playlist_box)
            asyncio.create_task(run_pf(plat))

        target_plat_dd = organize_dropdown(
            [(p, p) for p in AppState.PLATFORMS],
            default_plat,
            "Plataforma destino",
            width=480,
            on_select=_on_plat_select,
        )
        _fill_target_playlists(default_plat)

        playlist_box = ft.Column(
            [
                target_plat_dd,
                pf_banner,
                target_playlist_dd,
                target_help,
                new_name_field,
            ],
            spacing=8,
            tight=True,
            visible=(mode_state["mode"] == "playlist"),
        )

        def _sync_exp_mode(new_mode: str) -> None:
            mode_state["mode"] = new_mode
            file_box.visible = new_mode == "file"
            playlist_box.visible = new_mode == "playlist"
            if new_mode == "playlist":
                primary_btn.disabled = pf_state["ok"] is False
                if pf_state["dest"] != str(target_plat_dd.value or default_plat):
                    asyncio.create_task(run_pf(str(target_plat_dd.value or default_plat)))
            else:
                primary_btn.disabled = False
            self._safe_ctrl_update(file_box)
            self._safe_ctrl_update(playlist_box)
            self._safe_ctrl_update(primary_btn)

        def _on_exp_mode_change(e) -> None:
            try:
                val = (
                    list(e.control.selected)[0]
                    if hasattr(e.control, "selected") and e.control.selected
                    else e.control.value
                )
            except Exception:
                val = getattr(e.control, "value", "file")
            if val in ("file", "playlist"):
                _sync_exp_mode(val)

        try:
            exp_mode_seg: ft.Control = ft.SegmentedButton(
                selected=[mode_state["mode"]],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="file", label=ft.Text("Archivo local", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="playlist", label=ft.Text("Añadir a playlist", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=_on_exp_mode_change,
            )
        except Exception:
            exp_mode_seg = organize_dropdown(
                [("file", "Archivo local"), ("playlist", "Añadir a playlist")],
                mode_state["mode"],
                "Modo",
                width=220,
                on_select=_on_exp_mode_change,
            )

        info_txt = ft.Text(
            f"{len(src_tracks)} canciones · {(self.state.playlist_name or 'Selección')[:30]}",
            size=10,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
        )
        dlg_ref: dict = {}

        async def _submit_export(_e=None) -> None:
            if mode_state["mode"] == "file":
                fmt = getattr(self.state, "local_export_format", "txt")
                order = getattr(self.state, "local_export_order", "artist-title")
                path = (path_field.value or "").strip() or str(
                    default_export_path(self.state.playlist_name, fmt)
                )
                from pathlib import Path as _P
                p = _P(path).expanduser()
                if not p.suffix:
                    p = _P(str(p) + f".{fmt.replace('m3u', 'm3u8')}")
                try:
                    out = export_tracks(
                        src_tracks,
                        p,
                        fmt=fmt,
                        order=order,
                        playlist_name=self.state.playlist_name,
                    )
                    self._close_dlg(dlg_ref["dlg"])
                    self._snack(f"Exportado {len(src_tracks)} canciones → {out}")
                    self.state.log(f"[INFO] Export local · {len(src_tracks)} → {out} ({fmt}, {order})")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self._snack(f"Error al exportar: {exc}", error=True)
                    self.state.log(f"[ERROR] Export falló: {exc}")
                return

            dest = str(target_plat_dd.value or default_plat)
            ok = await run_pf(dest) if pf_state["ok"] is None else bool(pf_state["ok"])
            if not ok:
                self._snack(f"Sesión de {dest} no válida", error=True)
                return
            pid = str(target_playlist_dd.value or "").strip()
            if pid:
                items = self._get_synced_playlists_for(dest)
                match = next((p for p in items if str(p.id) == pid), None)
                pname = str(getattr(match, "name", "") or pid)
                self._close_dlg(dlg_ref["dlg"])
                await self._add_tracks_to_existing_destination(dest, pid, pname, src_tracks)
                return

            new_title = (new_name_field.value or "").strip()
            if not new_title:
                self._snack("Indica una playlist destino o un nombre para crearla", error=True)
                return
            self._close_dlg(dlg_ref["dlg"])
            resolved = await self._resolve_tracks_for_destination(dest, src_tracks)
            if not resolved:
                self._snack(f"No se encontraron pistas compatibles en {dest}", error=True)
                return
            ok_create, msg, confirmed, _rej = await self.state.create_remote_playlist(
                dest, new_title, [t.id for t in resolved if t.id], self.state.playlist_description
            )
            if ok_create:
                self._snack(f"Playlist «{new_title}» creada en {dest} ({confirmed} pistas)")
            else:
                self._snack(f"No se pudo crear la playlist: {msg}", error=True)

        primary_btn.on_click = lambda e: asyncio.create_task(_submit_export(e))

        dlg = app_dialog(
            "Exportar / Añadir a playlist",
            ft.Column(
                [exp_mode_seg, info_txt, file_box, playlist_box],
                spacing=10,
                tight=True,
            ),
            [
                dialog_action("Cancelar", lambda _: self._close_dlg(dlg), kind="muted"),
                primary_btn,
            ],
            width=560,
            icon=PIcons.EXPORT,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)
        if mode_state["mode"] == "playlist":
            asyncio.create_task(run_pf(default_plat))

    def _open_merge_dialog(self) -> None:
        """Diálogo de fusión de playlists locales/remotas con deduplicación (2.2)."""
        sources: list[dict] = []
        if self.state.tracks:
            sources.append(
                {
                    "key": "__current__",
                    "kind": "current",
                    "label": f"Actual: {self.state.playlist_name} ({len(self.state.tracks)})",
                    "platform": self.state.source,
                    "tracks": list(self.state.tracks),
                    "selected": True,
                }
            )
        lib = getattr(self, "library_state", None)
        if lib is not None:
            for plat in AppState.PLATFORMS:
                for p in self._get_synced_playlists_for(plat):
                    sources.append(
                        {
                            "key": f"{plat}::{p.id}",
                            "kind": "remote",
                            "label": f"{p.name or p.id} · {plat} ({p.track_count})",
                            "platform": plat,
                            "playlist_id": str(p.id),
                            "selected": False,
                        }
                    )
            try:
                local_items = list(lib.local_summaries() or [])
            except Exception:
                local_items = []
            for p in local_items:
                sources.append(
                    {
                        "key": f"local::{p.id}",
                        "kind": "local_summary",
                        "label": f"{p.name or p.id} · Local ({p.track_count})",
                        "summary_id": str(p.id),
                        "selected": False,
                    }
                )

        name_field = app_text_field(
            value="Playlist Fusionada",
            label="Nombre de la playlist fusionada",
            hint_text="Ej. Mix Verano",
        )
        filter_field = app_text_field(
            hint_text="Filtrar fuentes…",
            prefix_icon=PIcons.MAGNIFYING_GLASS,
            height=36,
        )
        limit_txt = ft.Text("", size=10, color=TEXT_DIM, font_family=mono_family("light"))
        lv = ft.ListView(spacing=4, height=230)
        dedup_chk = ft.Checkbox(
            label="Omitir canciones duplicadas (ISRC, ID o título y artista)",
            value=True,
            fill_color={ft.ControlState.SELECTED: ACCENT},
            check_color=TEXT_PRIMARY,
            label_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
        )

        def _rebuild_list(query: str = "") -> None:
            q = (query or "").strip().lower()
            matched = [it for it in sources if not q or q in it["label"].lower()]
            shown = matched[:DIALOG_OPTION_LIMIT]
            limit_txt.value = limit_label(len(shown), len(matched))
            lv.controls = [
                ft.Checkbox(
                    label=it["label"],
                    value=bool(it["selected"]),
                    fill_color={ft.ControlState.SELECTED: ACCENT},
                    check_color=TEXT_PRIMARY,
                    label_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
                    on_change=lambda e, item=it: item.__setitem__("selected", bool(e.control.value)),
                )
                for it in shown
            ]
            if not shown:
                lv.controls = [
                    ft.Text(
                        "Sin fuentes sincronizadas. Añade un archivo local o sincroniza Biblioteca.",
                        size=11,
                        color=TEXT_MUTED,
                        font_family=FONT_TEXT,
                    )
                ]
            self._safe_ctrl_update(limit_txt)
            self._safe_ctrl_update(lv)

        filter_field.on_change = lambda e: debounce(
            ("merge_filter", id(self)),
            lambda: _rebuild_list(e.control.value or ""),
        )

        async def _add_local_file(_e) -> None:
            try:
                files = await self._file_picker.pick_files(
                    dialog_title="Añadir playlist local a la fusión",
                    allowed_extensions=["txt", "csv", "m3u", "m3u8", "pls", "wpl", "xspf", "xml"],
                )
            except Exception:
                files = None
            if not files:
                return
            f = files[0]
            try:
                with open(f.path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                pairs = parse_local_playlist_with_paths(
                    text, filename=f.path, order=self.state.local_parse_order
                )
                tracks = build_local_tracks(pairs)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._snack(f"No se pudo leer '{f.name}': {exc}", error=True)
                return
            if not tracks:
                self._snack(f"'{f.name}' no contiene pistas reconocibles", error=True)
                return
            sources.append(
                {
                    "key": f"file::{f.path}",
                    "kind": "current",
                    "label": f"Archivo: {f.name} ({len(tracks)})",
                    "tracks": tracks,
                    "selected": True,
                }
            )
            _rebuild_list(filter_field.value or "")

        dlg_ref: dict = {}

        async def _execute_merge(_e=None) -> None:
            picked = [it for it in sources if it.get("selected")]
            if len(picked) < 2:
                self._snack("Selecciona al menos 2 fuentes para fusionar", error=True)
                return
            final_name = (name_field.value or "").strip() or "Playlist Fusionada"
            deduplicate = bool(dedup_chk.value)
            self._close_dlg(dlg_ref["dlg"])
            prev_load_state = self.state.load_state
            self.state.load_state = LoadState.LOADING_TRACKS
            self.state.notify()
            resolved_lists: list[list[Track]] = []
            service = getattr(self.state, "service", None)
            try:
                for it in picked:
                    kind = it["kind"]
                    if kind == "current":
                        resolved_lists.append(list(it["tracks"]))
                    elif kind == "remote":
                        if service is None or not hasattr(service, "fetch_playlist"):
                            continue
                        try:
                            _meta, tracks = await service.fetch_playlist(
                                it["platform"], it["playlist_id"], None
                            )
                            if tracks:
                                resolved_lists.append(list(tracks))
                        except Exception as exc:  # pylint: disable=broad-exception-caught
                            self.state.log(f"[WARN] Error cargando '{it['label']}': {exc}")
                    elif kind == "local_summary" and lib is not None:
                        sid = it["summary_id"]
                        try:
                            manifest = await asyncio.to_thread(lib.load_local_manifest, sid)
                            if manifest is not None and getattr(manifest, "tracks", None):
                                resolved_lists.append(list(manifest.tracks))
                                continue
                            from services.local_index import scan_folder_async
                            folder_tracks = await scan_folder_async(sid)
                            if folder_tracks:
                                resolved_lists.append(list(folder_tracks))
                        except Exception as exc:  # pylint: disable=broad-exception-caught
                            self.state.log(f"[WARN] Error leyendo local '{it['label']}': {exc}")

                if len(resolved_lists) < 2:
                    self.state.load_state = prev_load_state
                    self.state.notify()
                    self._snack("No se pudieron cargar al menos 2 fuentes con pistas", error=True)
                    return
                raw_total = sum(len(lst) for lst in resolved_lists)
                merged = self.state.merge_sources(
                    resolved_lists,
                    name=final_name,
                    deduplicate=deduplicate,
                )
                msg = (
                    f"Fusionadas {len(merged)} pistas únicas (de {raw_total})"
                    if deduplicate
                    else f"Fusionadas {len(merged)} pistas (incluyendo duplicadas)"
                )
                self._snack(msg)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.state.load_state = prev_load_state
                self.state.notify()
                self._snack(f"Error al fusionar: {exc}", error=True)

        _rebuild_list("")

        dlg = app_dialog(
            "Fusionar playlists",
            ft.Column(
                [
                    name_field,
                    ft.Row(
                        [
                            filter_field,
                            _ghost_btn(
                                "+ Archivo",
                                PIcons.FOLDER_OPEN,
                                lambda e: asyncio.create_task(_add_local_file(e)),
                                height=36,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    limit_txt,
                    ft.Container(
                        content=lv,
                        bgcolor=CHIP_BG,
                        border=ft.Border.all(0.8, BORDER_LIGHT),
                        border_radius=RADIUS_INPUT,
                        padding=ft.Padding.all(8),
                    ),
                    dedup_chk,
                ],
                spacing=10,
                tight=True,
            ),
            [
                dialog_action("Cancelar", lambda _: self._close_dlg(dlg), kind="muted"),
                dialog_action(
                    "Fusionar",
                    lambda e: asyncio.create_task(_execute_merge(e)),
                    kind="primary",
                    icon=PIcons.QUEUE,
                ),
            ],
            width=540,
            icon=PIcons.QUEUE,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)

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
