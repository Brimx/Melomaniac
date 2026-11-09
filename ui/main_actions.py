"""ui/main_actions.py — Acciones del usuario: cargar, pegar, importar, transferir, exportar y buscar.

Mixin de `PlaylistManagerUI` (ui/main_ui.py): los métodos se mezclan en la clase
y comparten su `self`.
"""

from __future__ import annotations

import asyncio
import flet as ft
import os

from ui.widgets import _ghost_btn, app_dialog, app_text_field, dialog_action, input_border
from ui.fonts import FONT_HEADLINE, FONT_TEXT, mono_family
from ui.tokens import BG_INPUT, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY, WARNING
from core.models import TransferState
from core.config import EXPORT_DEST_LABEL, EXPORT_FORMATS
from engine.parsers import build_local_tracks, parse_local_playlist_with_paths
from ui.icons import PIcons
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

    def _on_load(self, _) -> None:
        src = self.state.source
        if src in AppState.LOCAL_SOURCES and not self.state.destination_confirmed:
            self._snack("⚠ Selecciona primero una plataforma de Destino", error=True)
            self._dst_dd.border = input_border(WARNING, WARNING)
            self._dst_dd._last_border_key = (WARNING, WARNING)
            self._dst_dd.update()
            return
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

    async def _ensure_destination_session(self) -> bool:
        """Preflight del destino antes de transferir.

        ``auth_session_ok`` puede estar en ``None`` (sin validar) o desactualizado.
        Aquí se confirma contra la plataforma real; si falla, se bloquea la
        transferencia con un mensaje accionable en vez de arrancar y fallar pista
        por pista.
        """
        dest = self.state.destination
        if dest not in AppState.PLATFORMS:
            return True
        am = getattr(self, "auth_manager", None)
        if am is None:
            return True
        self._snack(f"Comprobando sesión de {dest}…")
        try:
            results = await am.check_sessions([dest])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.state.log(f"[ERROR] Preflight de {dest}: {exc}")
            return True  # no bloquear por un fallo del propio preflight
        am.ingest_preflight_results(results)
        result = next((r for r in results if r.platform == dest), None)
        if result is None or result.ok:
            return True
        self.state.log(f"[ERROR] Transferencia bloqueada: sesión de {dest} no válida")
        self._snack(f"Sesión de {dest} no válida: {result.error or 'revisa Configuración'}", error=True)
        if getattr(result, "expired", False):
            am.open_wizard(dest)
        return False

    async def _on_transfer(self, _) -> None:
        # Ruta export local como destino — no va por MusicApiService
        if self.state.destination == EXPORT_DEST_LABEL:
            await self._open_export_dialog()
            return
        # Misma plataforma permitida si hay Organizar/Dividir (has_transform) §11 — antes bloqueado
        has_transform = bool(self.state.segments or getattr(self.state, 'show_dual', False) or (getattr(self.state, 'source_tracks', None) and self.state.tracks != self.state.source_tracks))
        if self.state.source == self.state.destination and not has_transform:
            self._snack("Origen y destino no pueden ser iguales (solo permitido tras Organizar/Dividir)", error=True)
            return
        if self.state.source in AppState.LOCAL_SOURCES and not self.state.destination_confirmed:
            self._snack("⚠ Selecciona una plataforma de destino antes de transferir", error=True)
            self._dst_dd.border = input_border(WARNING, WARNING)
            self._dst_dd._last_border_key = (WARNING, WARNING)
            self._dst_dd.update()
            return
        # División: si hay divisiones, se transfiere por división; si no, selección global
        has_divisions = bool(getattr(self.state, 'segments', {}))
        if has_divisions:
            # El flujo por divisiones crea varias playlists y no genera un
            # reporte único; evita que un reporte anterior se reabra.
            self._report_dialog_shown = True
            # para divisiones, cuenta seleccionadas para transferir = divisiones seleccionadas
            sel_divs = self.state.active_segment_keys if self.state.active_segment_keys is not None else set(self.state.segments.keys())
            if not sel_divs:
                self._snack("Selecciona al menos una división para transferir", error=True)
                return
        else:
            if self.state.selected_count == 0:
                self._snack("Selecciona al menos una canción", error=True)
                return
        if not await self._ensure_destination_session():
            return
        from ui.playlist_meta_dialog import PlaylistMetaDialog
        src_requires_auth = (
            self.state.source not in AppState.LOCAL_SOURCES
            and self.state.auth_session_ok.get(self.state.source) is True
        )
        divisions_for_dialog = list(sel_divs) if has_divisions else None
        meta = await PlaylistMetaDialog(
            self.page,
            default_title=self.state.playlist_name,
            default_description=self.state.playlist_description,
            source_requires_auth=src_requires_auth,
            divisions=divisions_for_dialog,
            global_destination=self.state.destination,
        ).show()
        if not meta.confirmed:
            return
        if not meta.title.strip():
            self._snack("El nombre de la playlist no puede estar vacío", error=True)
            return
        # Flujo Dividir (múltiples playlists) vs Organizar/single §10-12 — per-división destino variable solo Dividir
        if has_divisions:
            sel_divs = self.state.active_segment_keys if self.state.active_segment_keys is not None else set(self.state.segments.keys())
            per_div_dests = getattr(meta, 'per_division_destinations', None) or {}
            successes = 0
            for div in sorted(sel_divs, key=lambda k: list(self.state.segments.keys()).index(k) if k in self.state.segments else 9999):
                div_tracks = self.state.segments.get(div, [])
                div_selected = [t for t in div_tracks if t.selected] if any(t.selected for t in div_tracks) else div_tracks
                if not div_selected:
                    continue
                dest = per_div_dests.get(div, "Mantener")
                if dest == "Mantener" or not dest:
                    dest = self.state.destination
                try:
                    ok, msg, confirmed, rejected = await self.state.service.create_playlist(
                        dest, f"{meta.title.strip()} — {div}", [t.id for t in div_selected if t.id], meta.description
                    )
                    if ok:
                        successes += 1
                        self.state.log(f"[SUCCESS] División '{div}' → {dest}: {confirmed} canciones")
                    else:
                        self.state.log(f"[ERROR] División '{div}' fallo: {msg}")
                except Exception as exc:
                    self.state.log(f"[ERROR] División '{div}' excepción: {exc}")
            # mensaje sin referencia a punto cumplido
            self._snack(f"{successes}/{len(sel_divs)} divisiones transferidas")
            if per_div_dests:
                self.state.division_destinations.update(per_div_dests)
        else:
            await self.state.transfer_playlist(
                title_override=meta.title,
                description_override=meta.description,
            )
        # Reemplazar/eliminar original si marcado (§11)
        if meta.delete_original and self.state.playlist_id and self.state.source not in AppState.LOCAL_SOURCES:
            try:
                ok, msg = await self.state.service.delete_playlist(self.state.source, self.state.playlist_id)
                if ok:
                    self._snack(f"Original eliminada ({self.state.source})")
                    self.state.log(f"[INFO] Original {self.state.playlist_id} eliminada tras reemplazo")
                else:
                    self._snack(f"No se pudo eliminar original: {msg}", error=True)
            except Exception as exc:
                self._snack(f"Error eliminando original: {exc}", error=True)

    async def _open_export_dialog(self) -> None:
        """Diálogo de export local: formato + orden + ruta (pathlib + FilePicker)."""
        if not self.state.tracks:
            self._snack("No hay playlist cargada para exportar", error=True)
            return
        # Tracks a exportar: seleccionadas si las hay, si no display
        src_tracks = [t for t in self.state.tracks if t.selected]
        if not src_tracks:
            src_tracks = list(self.state.display_tracks) if hasattr(self.state, "display_tracks") else list(self.state.tracks)
        if not src_tracks:
            self._snack("No hay canciones para exportar", error=True)
            return

        # Estado local export (persiste en AppState)
        fmt_val = getattr(self.state, "local_export_format", "txt")
        order_val = getattr(self.state, "local_export_order", "artist-title")

        def _on_fmt_change(e):
            self.state.local_export_format = e.control.value
        def _on_order_change_exp(e):
            try:
                val = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                self.state.local_export_order = val

        fmt_dd = ft.Dropdown(
            label="Formato", value=fmt_val,
            width=160, height=38,
            bgcolor=BG_INPUT, border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            options=[ft.dropdown.Option(k, k.upper()) for k in EXPORT_FORMATS],
            on_select=_on_fmt_change,
        )
        order_seg = self._make_order_segmented(order_val, on_change=_on_order_change_exp)

        default_path = str(default_export_path(self.state.playlist_name, fmt_val))  # type: ignore
        path_field = app_text_field(
            value=default_path, label="Ruta destino", hint_text=str(default_path),
            prefix_icon=PIcons.FOLDER, expand=True,
        )

        async def _pick_save(_):
            # usa FilePicker.save_file (Flet 0.86) con fallback a pick
            try:
                res = await self._save_picker.save_file(
                    dialog_title="Guardar playlist",
                    file_name=path_field.value.split("/")[-1].split("\\")[-1] or "playlist.txt",
                    allowed_extensions=list(EXPORT_FORMATS),
                )
                # save_file retorna FilePickerFile o None según versión
                if res and getattr(res, "path", None):
                    path_field.value = res.path
                    path_field.update()
                elif isinstance(res, str) and res:
                    path_field.value = res
                    path_field.update()
            except Exception:
                # fallback: usa path por defecto
                pass

        pick_btn = _ghost_btn("Elegir…", PIcons.FOLDER_OPEN, lambda e: self.page.run_task(_pick_save(e)), width=90, height=38)

        # preview count
        info_txt = ft.Text(f"{len(src_tracks)} canciones · {self.state.playlist_name[:30]}", size=10, color=TEXT_MUTED, font_family=mono_family("light"))

        # necesitamos capturar dlg en closure
        dlg_ref: dict = {}

        def _do_export(_):
            fmt = getattr(self.state, "local_export_format", "txt")
            order = getattr(self.state, "local_export_order", "artist-title")
            path = (path_field.value or "").strip() or str(default_export_path(self.state.playlist_name, fmt))  # type: ignore
            # asegura extensión coincide con fmt
            from pathlib import Path as _P
            p = _P(path).expanduser()
            if not p.suffix:
                p = _P(str(p) + f".{fmt.replace('m3u','m3u8')}")
            try:
                out = export_tracks(src_tracks, p, fmt=fmt, order=order, playlist_name=self.state.playlist_name)  # type: ignore
                self._close_dlg(dlg_ref["dlg"])
                self._snack(f"Exportado {len(src_tracks)} canciones → {out}")
                self.state.log(f"[INFO] Export local · {len(src_tracks)} → {out} ({fmt}, {order})")
            except Exception as exc:
                self._snack(f"Error al exportar: {exc}", error=True)
                self.state.log(f"[ERROR] Export falló: {exc}")

        dlg = app_dialog(
            "Exportar playlist",
            ft.Column([
                ft.Text("Guarda la selección actual con el mismo bloque que lee TXT/CSV/M3U/XSPF. Elige orden para round-trip con TuneMyMusic.",
                        size=10, color=TEXT_MUTED, font_family=FONT_TEXT),
                info_txt,
                ft.Row([fmt_dd, ft.Container(expand=True), order_seg], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([path_field, pick_btn], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=12, tight=True),
            [
                dialog_action("Cancelar", lambda _: self._close_dlg(dlg), kind="muted"),
                dialog_action("Exportar", _do_export, kind="primary", icon=PIcons.EXPORT),
            ],
            width=560,
            icon=PIcons.EXPORT,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)

    async def _on_search_change(self, e: ft.ControlEvent) -> None:
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()
        query = e.control.value
        self._search_task = asyncio.create_task(self._do_search(query))

    async def _do_search(self, query: str) -> None:
        await asyncio.sleep(0.30)
        self.state.apply_search(query)
