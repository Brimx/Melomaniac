"""ui/shell/main_actions.py — Controlador de acciones de carga, descarga, transferencia y búsqueda."""

from __future__ import annotations

import asyncio
import datetime as _dt
import os

import flet as ft

from core.models import Track, TransferState
from core.state import AppState
from engine.parsers import build_local_tracks, parse_local_playlist_with_paths
from ui.components.widgets import (
    app_dialog,
    app_text_field,
    dialog_action,
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
from ui.theme.fonts import FONT_HEADLINE
from ui.theme.icons import PIcons
from ui.theme.tokens import TEXT_DIM, TEXT_MUTED


class ActionsController:
    """Acciones del usuario para ``PlaylistManagerUI``."""

    def __init__(self, ui) -> None:
        self._ui = ui

    def _playlist_download_context(self, tracks: list) -> dict[str, str]:
        from services import settings_store

        ui = self._ui
        platforms = {getattr(t, "platform", "") for t in tracks if getattr(t, "platform", "")}
        if len(platforms) == 1:
            platform = next(iter(platforms))
        elif getattr(ui.state, "source", "") not in ("", "Archivo Local", "Pegar Texto"):
            platform = str(ui.state.source)
        else:
            platform = ""
        return {
            "destination_dir": settings_store.get_download_dir(),
            "playlist_name": ui.state.playlist_name or "",
            "platform": platform,
        }

    def _on_row_download(self, track_id: str) -> None:
        ui = self._ui
        if ui.state.download_state == TransferState.RUNNING:
            ui._snack("Descarga en curso", error=True)
            return
        target = next((t for t in ui.state.tracks if t.id == track_id), None)
        if target is None:
            target = next((t for t in ui.state.source_tracks if t.id == track_id), None)
        if target is None:
            return
        ui._auto_play_download_ids.add(target.id)
        try:
            ui.state.download_tracks(
                [target],
                **self._playlist_download_context([target]),
            )
            ui._snack(f"Descargando: {target.artist} — {target.name}")
        except Exception as exc:  # noqa: BLE001
            ui._auto_play_download_ids.discard(target.id)
            ui._snack(f"No se pudo iniciar la descarga: {exc}", error=True)

    def _on_batch_download(self) -> None:
        """Descarga en lote las pistas seleccionadas que no sean archivos locales."""
        ui = self._ui
        if ui.state.download_state == TransferState.RUNNING:
            ui._snack("Descarga en curso", error=True)
            return
        selected = [t for t in ui.state.tracks if t.selected]
        if not selected:
            ui._snack("Selecciona al menos una canción", error=True)
            return
        to_download = [t for t in selected if not getattr(t, "source_path", "")]
        if not to_download:
            ui._snack("Las canciones seleccionadas ya son archivos locales en disco", error=True)
            return
        try:
            ui.state.download_tracks(
                to_download,
                **self._playlist_download_context(to_download),
            )
            ui._snack(f"Descargando {len(to_download)} canciones…")
        except Exception as exc:  # noqa: BLE001
            ui._snack(f"No se pudo iniciar la descarga: {exc}", error=True)

    def _on_load(self, _) -> None:
        ui = self._ui
        src = ui.state.source
        if src == "Archivo Local":
            asyncio.create_task(ui._do_local_pick())
        elif src == "Pegar Texto":
            ui._open_paste_dialog()
        else:
            asyncio.create_task(ui._do_cloud_load())

    async def _do_cloud_load(self, _=None) -> None:
        ui = self._ui
        pid = ""
        if not ui._url_mode:
            pid = str(ui._playlist_pick.value or "")
        if not pid:
            pid = (ui._id_field.value or "").strip()
        if not pid:
            ui._snack("Elige una playlist o introduce un ID o una URL")
            return
        ui._completion_snack_shown = False
        await ui.state.load_playlist(pid)

    def _on_clear_id(self, _) -> None:
        """Limpia solo el campo de ID pegado."""
        ui = self._ui
        ui._id_field.value = ""
        ui._id_clear_btn.visible = False
        ui._id_field.update()

    def _on_clear_session(self, _) -> None:
        """Limpia el ID, la lista cargada y el estado de transferencia."""
        ui = self._ui
        ui._id_field.value = ""
        ui._id_clear_btn.visible = False
        ui._search_field.value = ""
        ui.state.reset_session()
        ui._snack("Sesión limpiada")

    def _open_folder_in_gestion(self, name: str, tracks: list) -> None:
        """Carga una carpeta local de la Biblioteca en Gestión como playlist."""
        ui = self._ui
        if not tracks:
            ui._snack("La carpeta no tiene pistas", error=True)
            return
        ui._completion_snack_shown = False
        ui._pm_cleared_for_load = True
        ui._telemetry.clear_postmortem()
        ui.state.load_local_tracks(list(tracks), playlist_name=name or "Playlist Local")
        ui.state.set_source("Archivo Local")
        ui._src_dd.value = "Archivo Local"
        ui._select_module(0)
        ui._snack(f"{len(tracks)} pistas de «{name}» cargadas en Gestión")

    def _open_paste_dialog(self) -> None:
        ui = self._ui
        ui._paste_field.value = ""

        def _on_order_change(e):
            try:
                val = (
                    list(e.control.selected)[0]
                    if hasattr(e.control, "selected") and e.control.selected
                    else e.control.value
                )
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                ui.state.local_parse_order = val

        order_seg = ui._make_order_segmented(ui.state.local_parse_order, on_change=_on_order_change)

        def _close_paste():
            ui._close_dlg(paste_dlg)

        def _process(_):
            text = ui._paste_field.value or ""
            _close_paste()
            if not text.strip():
                ui._snack("El campo de texto está vacío", error=True)
                return
            default_ts = _dt.datetime.now().strftime("%H:%M")
            ui._ask_playlist_name_then_ingest(
                text=text,
                filename="",
                suggested_name=f"Local_Import_{default_ts}",
            )

        paste_dlg = app_dialog(
            "Pegar Texto",
            ft.Column(
                [
                    ft.Container(content=ui._paste_field, width=480, height=220),
                    ft.Row(
                        [
                            ft.Text("Orden:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                            order_seg,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        "Elige cómo está escrito cada línea (TuneMyMusic usa Artista - Título).",
                        size=9,
                        color=TEXT_DIM,
                        font_family=FONT_HEADLINE,
                    ),
                ],
                spacing=10,
                tight=True,
            ),
            [
                dialog_action("Procesar", _process, kind="primary", icon=PIcons.PLAY),
                dialog_action("Cancelar", lambda _: _close_paste(), kind="muted"),
            ],
            width=520,
        )
        ui.page.show_dialog(paste_dlg)

    def _ask_playlist_name_then_ingest(self, text: str, filename: str, suggested_name: str) -> None:
        ui = self._ui
        name_field = app_text_field(
            value=suggested_name,
            hint_text=f"Ej. {suggested_name}",
            label="Nombre de la Playlist",
            autofocus=True,
            on_submit=lambda _: _confirm(None),
        )

        def _on_order_change2(e):
            try:
                val = (
                    list(e.control.selected)[0]
                    if hasattr(e.control, "selected") and e.control.selected
                    else e.control.value
                )
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                ui.state.local_parse_order = val

        order_seg2 = ui._make_order_segmented(ui.state.local_parse_order, on_change=_on_order_change2)

        def _close():
            ui._close_dlg(name_dlg)

        def _confirm(_):
            raw = (name_field.value or "").strip()
            final_name = (
                raw
                if raw
                else (suggested_name or f"Local_Import_{_dt.datetime.now().strftime('%H:%M')}")
            )
            _close()
            ui._ingest_text(text, label=final_name, filename=filename)

        name_dlg = app_dialog(
            "Nombra esta playlist",
            ft.Column(
                [
                    ft.Text(
                        "Asigna un nombre antes de importar. Si lo dejas vacío se usará el nombre sugerido.",
                        size=11,
                        color=TEXT_MUTED,
                        font_family=FONT_HEADLINE,
                    ),
                    name_field,
                    ft.Row(
                        [
                            ft.Text("Orden:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                            order_seg2,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        "Confirma el orden de cada línea antes de importar.",
                        size=9,
                        color=TEXT_DIM,
                        font_family=FONT_HEADLINE,
                    ),
                ],
                spacing=10,
                tight=True,
            ),
            [
                dialog_action("Importar", _confirm, kind="primary", icon=PIcons.CHECK),
                dialog_action("Cancelar", lambda _: _close(), kind="muted"),
            ],
            width=460,
            icon=PIcons.PENCIL_SIMPLE,
        )
        ui.page.show_dialog(name_dlg)

    async def _do_local_pick(self) -> None:
        ui = self._ui
        files = await ui._file_picker.pick_files(
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
            ui._ingest_audio_file(f.path, os.path.splitext(os.path.basename(f.name))[0])
            return
        try:
            with open(f.path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            ui.state.log(f"[ERROR] No se pudo leer '{f.name}': {exc}")
            ui._snack(f"Error leyendo archivo: {exc}", error=True)
            return
        base_name = os.path.splitext(os.path.basename(f.name))[0] or "Playlist Local"
        ui._ask_playlist_name_then_ingest(text=text, filename=f.path, suggested_name=base_name)

    def _ingest_text(self, text: str, label: str = "", filename: str = "") -> None:
        ui = self._ui
        try:
            pairs = parse_local_playlist_with_paths(
                text,
                filename=filename or label,
                order=ui.state.local_parse_order,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            ui.state.log(f"[ERROR] Parser ingesta: {exc}")
            ui._snack(f"Error en el parser: {exc}", error=True)
            return
        if not pairs:
            ui.state.log(f"[WARN] No se encontraron pistas en '{label or 'texto'}'")
            ui._snack("No se reconocieron pistas en el archivo", error=True)
            return
        tracks = build_local_tracks(pairs)
        name = label.strip() if label and label.strip() else "Playlist Local"
        ui._completion_snack_shown = False
        ui._pm_cleared_for_load = True
        ui._telemetry.clear_postmortem()
        ui.state.load_local_tracks(tracks, playlist_name=name)
        ui._snack(f"{len(tracks)} canciones importadas de '{name}'")
        ui.state.log(f"[INFO] Ingesta completa · {len(tracks)} pistas desde '{label}'")

    def _ingest_audio_file(self, path: str, label: str) -> None:
        """Importa un archivo de audio leyendo sus tags con Mutagen."""
        ui = self._ui
        try:
            tracks = build_local_tracks([("", os.path.basename(path), path)])
        except Exception as exc:
            ui.state.log(f"[ERROR] Metadatos de audio: {exc}")
            ui._snack(f"Error leyendo metadatos: {exc}", error=True)
            return
        if not tracks:
            ui._snack("No se pudieron leer los metadatos del audio", error=True)
            return
        name = label.strip() or "Archivo de audio"
        ui._completion_snack_shown = False
        ui._pm_cleared_for_load = True
        ui._telemetry.clear_postmortem()
        ui.state.load_local_tracks(tracks, playlist_name=name)
        ui._snack(f"Audio importado: '{name}'")
        ui.state.log(f"[INFO] Audio local importado · {path}")

    def _get_synced_playlists_for(self, dest: str) -> list:
        """Devuelve playlists sincronizadas en Biblioteca para ``dest``."""
        return get_synced_playlists_for(self._ui, dest)

    async def _run_dialog_preflight(self, dest: str) -> tuple[bool, str, bool]:
        """Ejecuta comprobación en vivo de sesión para ``dest``."""
        return await run_dialog_preflight(self._ui, dest)

    async def _resolve_tracks_for_destination(
        self, dest: str, tracks: list[Track]
    ) -> list[Track]:
        """Resuelve pistas que no tengan ID en ``dest`` usando el servicio."""
        return await resolve_tracks_for_destination(self._ui, dest, tracks)

    async def _add_tracks_to_existing_destination(
        self, dest: str, playlist_id: str, playlist_name: str, tracks: list[Track]
    ) -> int:
        """Inserta ``tracks`` en una playlist existente usando el servicio disponible."""
        return await add_tracks_to_existing_destination(
            self._ui, dest, playlist_id, playlist_name, tracks
        )

    async def _open_transfer_dialog(self, *, has_divisions: bool = False) -> None:
        """Abre el diálogo de transferencia delegando a ``ui.dialogs.transfer_dialog``."""
        await open_transfer_dialog(self._ui, has_divisions=has_divisions)

    async def _ensure_destination_session(self) -> bool:
        """Preflight del destino antes de transferir."""
        ui = self._ui
        dest = ui.state.destination
        if dest not in AppState.PLATFORMS:
            return True
        am = getattr(ui, "auth_manager", None)
        if am is None:
            return True
        ui._snack(f"Comprobando sesión de {dest}…")
        ok, err, expired = await ui._run_dialog_preflight(dest)
        if ok:
            return True
        ui.state.log(f"[ERROR] Transferencia bloqueada: sesión de {dest} no válida")
        ui._snack(f"Sesión de {dest} no válida: {err or 'revisa Configuración'}", error=True)
        if expired and hasattr(am, "open_wizard"):
            am.open_wizard(dest)
        return False

    @staticmethod
    def _safe_ctrl_update(ctrl) -> None:
        safe_update(ctrl)

    async def _on_transfer(self, _=None) -> None:
        """Abre el diálogo unificado de destino + preflight en vivo (2.1 & 2.9)."""
        ui = self._ui
        if not ui.state.tracks:
            ui._snack("Carga una playlist primero", error=True)
            return
        has_divisions = bool(getattr(ui.state, "segments", {}))
        if has_divisions:
            ui._report_dialog_shown = True
            sel_divs = (
                ui.state.active_segment_keys
                if ui.state.active_segment_keys is not None
                else set(ui.state.segments.keys())
            )
            if not sel_divs:
                ui._snack("Selecciona al menos una división para transferir", error=True)
                return
        elif ui.state.selected_count == 0:
            ui._snack("Selecciona al menos una canción", error=True)
            return
        await ui._open_transfer_dialog(has_divisions=has_divisions)

    async def _open_add_to_playlist_dialog(self, tracks: list[Track] | None = None) -> None:
        """Abre el diálogo de exportación directamente en modo «Añadir a playlist»."""
        await self._ui._open_export_dialog(initial_mode="playlist", custom_tracks=tracks)

    async def _open_export_dialog(
        self,
        *,
        initial_mode: str = "file",
        custom_tracks: list[Track] | None = None,
    ) -> None:
        """Diálogo de exportación local o inserción en una playlist destino (2.4/2.5)."""
        await open_export_dialog(
            self._ui,
            initial_mode=initial_mode,
            custom_tracks=custom_tracks,
        )

    def _open_merge_dialog(self) -> None:
        """Diálogo de fusión de playlists locales/remotas con deduplicación (2.2)."""
        open_merge_dialog(self._ui)

    async def _on_search_change(self, e: ft.ControlEvent) -> None:
        ui = self._ui
        if ui._search_task and not ui._search_task.done():
            ui._search_task.cancel()
        query = e.control.value
        ui._search_task = asyncio.create_task(self._do_search(query))

    async def _do_search(self, query: str) -> None:
        await asyncio.sleep(0.30)
        self._ui.state.apply_search(query)
