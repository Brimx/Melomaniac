"""
ui/playlist_detail_view.py — Detalle playlist Biblioteca: header + Ver en plataforma + lista tracks bajo demanda

Para playlists de plataforma ofrece «Descargar» (cola yt-dlp de ``AppState``).
Para carpetas locales (``loader``) ofrece abrir la carpeta y quitarla de la
Biblioteca.
"""

from __future__ import annotations

import asyncio
import os
import random
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from core.config import AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT, LOCAL_LIBRARY_LABEL
from core.library_models import PlaylistSummary
from core.models import Track, TransferState
from engine.downloader import open_in_file_manager
from services import settings_store
from ui.downloads_view import candidate_to_recovery, search_youtube_alternatives
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for, mono_family
from ui.icons import PIcons, app_icon, app_icon_button
from ui.song_row import ITEM_H, LibrarySkeletonRow, _thumb_content_for
from ui.tokens import (
    ACCENT, BG_HOVER, BG_LIST, BG_SURFACE, BORDER_LIGHT, BORDER_MUTED, BORDER_ROW,
    RADIUS_INPUT, SKELETON_DARK, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
    MOTION_HOVER_MS,
)
from ui.transfer_report import AlternativeSearchPanel, RecoveryCandidate
from ui.widgets import (
    ContextMenuAction,
    _ghost_btn,
    _primary_btn,
    app_dialog,
    app_text,
    build_context_menu_button,
    dialog_action,
    notify,
    open_external,
    organize_dropdown,
    wrap_with_context_menu,
)

_INITIAL_ROW_BATCH = 40
_ROW_CHUNK_SIZE = 40


class PlaylistDetailView(ft.Container):
    def __init__(self, page: ft.Page, summary: PlaylistSummary, service, on_back: Callable[[], None],
                 app_state=None, loader: Optional[Callable] = None,
                 on_remove: Optional[Callable[[], None]] = None,
                 on_open_downloads: Optional[Callable[[], None]] = None,
                 on_open_gestion: Optional[Callable[[str, list], None]] = None,
                 player_bar=None):
        super().__init__(expand=True, bgcolor=BG_LIST)
        self.app_state = app_state
        self.player_bar = player_bar
        self._loader = loader
        self._on_open_downloads = on_open_downloads
        self._on_open_gestion = on_open_gestion
        self._is_local = summary.platform == LOCAL_LIBRARY_LABEL
        self._page_ref = page
        self.summary = summary
        self.service = service
        self.on_back = on_back
        self._tracks: list[Track] = []
        self._loading = True
        self._error = ""
        self._load_gen: int = 0
        self._unmounted: bool = False
        self._batch_task: Optional[asyncio.Task] = None
        self._init_reload_task: Optional[asyncio.Task] = None
        self._has_file_by_id: dict[int, bool] = {}
        self._selection_mode: bool = False
        self._selected_ids: set[int] = set()
        self._row_checkboxes: dict[int, ft.Checkbox] = {}

        # header
        cover_url = summary.cover_urls[0] if summary.cover_urls else ""
        if cover_url:
            cover = ft.Container(width=120, height=120, border_radius=10, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=ft.Image(src=cover_url, fit=ft.BoxFit.COVER, error_content=app_icon(PIcons.MUSIC_NOTE, color=TEXT_DIM)))
        else:
            cover = ft.Container(width=120, height=120, border_radius=10, bgcolor=SKELETON_DARK, content=app_icon(PIcons.MUSIC_NOTE, color=TEXT_DIM, size=32), alignment=ft.Alignment.CENTER)

        self._title = app_text(summary.name, size=16, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD)
        self._desc = app_text(summary.description or "", size=11, color=TEXT_MUTED, font_family=FONT_TEXT, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS) if summary.description else ft.Container()
        count_label = f"{summary.track_count} canciones" if summary.track_count is not None else "Conteo al abrir"
        self._count = app_text(f"{count_label} • {summary.platform}", size=11, color=TEXT_DIM, font_family=mono_family("light"))

        self._open_btn = _ghost_btn("Ver playlist en plataforma", PIcons.ARROW_SQUARE_OUT, lambda e: self._launch(summary.external_url), width=260, height=40) if summary.external_url else ft.Container()
        self._shuffle_btn = _ghost_btn("Aleatorio", PIcons.SHUFFLE, self._on_shuffle_play, height=40)
        self._shuffle_btn.disabled = True
        self._select_mode_btn = _ghost_btn(
            "Seleccionar",
            PIcons.CHECK_CIRCLE,
            lambda _e: self._toggle_selection_mode(),
            height=40,
            disabled=True,
        )

        if self._is_local:
            self._play_all_btn = _primary_btn("Reproducir", PIcons.PLAY, self._on_play_all, height=40)
            self._play_all_btn.disabled = True
            self._create_btn = _ghost_btn(
                "Crear playlist", PIcons.PLAYLIST,
                lambda e: self._on_create_playlist(), height=40,
            )
            self._create_btn.disabled = True
            actions = [
                self._play_all_btn,
                self._shuffle_btn,
                self._select_mode_btn,
                self._create_btn,
                _ghost_btn("Abrir carpeta", PIcons.FOLDER_OPEN, lambda e: self._open_folder(), height=40),
                _ghost_btn("Quitar de la Biblioteca", PIcons.TRASH, lambda e: on_remove() if on_remove else None, height=40),
            ]
            self._download_btn = None
        else:
            self._create_btn = None
            self._play_all_btn = _primary_btn("Reproducir", PIcons.PLAY, self._on_play_all, height=40)
            self._play_all_btn.visible = False
            self._shuffle_btn.visible = False
            self._download_btn = _primary_btn("Descargar playlist", PIcons.DOWNLOAD_SIMPLE, self._on_download, height=40)
            self._download_btn.disabled = True
            actions = [self._play_all_btn, self._shuffle_btn, self._download_btn, self._select_mode_btn]
        if summary.external_url:
            actions.append(self._open_btn)
        actions_row = ft.Row(controls=actions, spacing=8, wrap=True)

        header = ft.Container(
            bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_LIGHT), border_radius=12, padding=ft.Padding.all(12),
            content=ft.Row(controls=[cover, ft.Column(controls=[self._title, self._count, self._desc, actions_row], spacing=6, expand=True, alignment=ft.MainAxisAlignment.CENTER)], spacing=12),
        )

        # Barra de acciones por lote (modo selección múltiple)
        self._select_all_chk = ft.Checkbox(
            value=False,
            fill_color={
                ft.ControlState.SELECTED: ACCENT,
                ft.ControlState.DEFAULT: BG_SURFACE,
            },
            check_color=TEXT_PRIMARY,
            border_side=ft.BorderSide(1.5, TEXT_DIM),
            on_change=self._on_select_all_change,
        )
        self._selected_count_label = app_text(
            "0 seleccionadas", size=12, color=TEXT_PRIMARY, font_family=FONT_HEADLINE,
        )
        self._batch_play_btn = _primary_btn("Reproducir", PIcons.PLAY, self._on_batch_play, height=34)
        self._batch_play_btn.disabled = True
        self._batch_queue_btn = _ghost_btn("A la cola", PIcons.QUEUE, self._on_batch_enqueue, height=34, disabled=True)
        self._batch_download_btn = _ghost_btn("Descargar", PIcons.DOWNLOAD_SIMPLE, self._on_batch_download, height=34, disabled=True)
        self._batch_cancel_btn = app_icon_button(
            PIcons.X,
            size=16,
            icon_color=TEXT_MUTED,
            tooltip="Salir de selección",
            width=30,
            height=30,
            on_click=lambda _e: self.set_selection_mode(False),
        )
        self._batch_bar = ft.Container(
            visible=False,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, ACCENT),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            content=ft.Row(
                controls=[
                    self._select_all_chk,
                    app_text("Todo", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                    self._selected_count_label,
                    ft.Container(expand=True),
                    self._batch_play_btn,
                    self._batch_queue_btn,
                    self._batch_download_btn,
                    self._batch_cancel_btn,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
            ),
        )

        # list area — skeletons dinámicos según altura disponible (paridad Gestión)
        self._skeletons: list[LibrarySkeletonRow] = []
        self._skeleton_tasks: list[asyncio.Task] = []
        self._press_tasks: set[asyncio.Task] = set()
        self._manifest_cache: dict[str, dict] = {}
        self._skeleton_list = ft.ListView(expand=True, spacing=0)
        self._skeleton_wrap = ft.Container(content=self._skeleton_list, expand=True, visible=True)
        self._sync_skeleton_count()
        self._list_view = ft.ListView(expand=True, spacing=0, visible=False)
        self._error_box = ft.Container(visible=False, bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_MUTED), border_radius=10, padding=ft.Padding.all(12), content=ft.Column(controls=[
            app_text("No se pudo cargar", size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
            _ghost_btn("Reintentar", PIcons.ARROWS_CLOCKWISE, lambda e: self.reload()),
        ], spacing=8))

        body = ft.Column(controls=[
            ft.Row(controls=[app_icon_button(PIcons.ARROW_LEFT, size=16, icon_color=TEXT_MUTED, on_click=lambda e: on_back(), width=28, height=28), app_text("Volver", size=12, color=TEXT_MUTED)], spacing=4),
            header,
            self._batch_bar,
            ft.Divider(height=1, color=BORDER_MUTED),
            ft.Stack(controls=[self._skeleton_wrap, self._list_view, self._error_box], expand=True),
        ], spacing=10, expand=True)

        self.content = ft.Container(content=body, padding=ft.Padding.all(12), expand=True)
        # kick load
        try:
            self._init_reload_task = asyncio.create_task(self.reload())
        except Exception:
            pass

    def _open_folder(self) -> None:
        if not open_in_file_manager(self.summary.id):
            notify(self._page_ref, "No se pudo abrir la carpeta", kind="error")

    def _on_create_playlist(self) -> None:
        """Manda la carpeta local a Gestión como playlist con origen preparado.

        Gestión es la única vista que tiene destino, Organizar/Dividir y el
        diálogo de nombre; duplicar ese flujo aquí dejaría dos caminos para la
        misma operación. Por eso cede los datos al callback en vez de tocar
        ``AppState`` directamente: allí también hay que alinear el selector de
        origen.
        """
        if not self._tracks or self._on_open_gestion is None:
            notify(self._page_ref, "No hay pistas cargadas para crear la playlist", kind="error")
            return
        self._on_open_gestion(self.summary.name, list(self._tracks))

    def _on_download(self, _e=None) -> None:
        """Encola todas las pistas de la playlist en la cola de Descargas."""
        st = self.app_state
        if st is None or not self._tracks:
            return
        if st.download_state == TransferState.RUNNING:
            notify(self._page_ref, "Ya hay una descarga en curso", kind="error")
            return
        try:
            st.download_tracks(self._tracks, destination_dir=settings_store.get_download_dir())
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo iniciar la descarga: {exc}", kind="error")
            return
        cb = self._on_open_downloads
        notify(
            self._page_ref, f"Descargando {len(self._tracks)} pistas",
            action="Ver" if cb else None,
            on_action=(lambda e: cb()) if cb else None,
        )

    def _launch(self, url: str) -> None:
        """Delega en ``ui.widgets.open_external``."""
        open_external(getattr(self, "_page_ref", None), url)

    def _calc_detail_skeleton_count(self) -> int:
        """Cuántos LibrarySkeletonRow caben — espejo de main_ui._calc_skeleton_count.

        Resta breadcrumb + header (~150) + divider + padding para overfill.
        """
        try:
            page = getattr(self, "_page_ref", None)
            h = (page.height if page else None) or (page.window.height if page and getattr(page, "window", None) else None) or 900
            if not h or h < 600:
                h = 900
        except Exception:
            h = 900
        header_h = 200  # breadcrumb 36 + header card ~150 + divider/pad
        avail = max(180, h - header_h - 48)
        return max(8, min(20, int(avail // ITEM_H)))

    def _sync_skeleton_count(self) -> None:
        """Recrea skeletons si cambió el count (on_resize) — paridad Gestión."""
        try:
            new_cnt = self._calc_detail_skeleton_count()
            if new_cnt != len(self._skeletons):
                self._stop_skeleton_pulse()
                self._skeletons = [LibrarySkeletonRow(i) for i in range(new_cnt)]
                self._skeleton_list.controls = self._skeletons
                if self._skeleton_wrap.visible:
                    self._ensure_skeletons_pulsing()
                try:
                    self._skeleton_list.update()
                except Exception:
                    pass
        except Exception:
            pass

    def _ensure_skeletons_pulsing(self) -> None:
        try:
            for sk in self._skeletons:
                try:
                    self._skeleton_tasks.append(asyncio.create_task(sk.start_pulse()))
                except Exception:
                    pass
        except Exception:
            pass

    def _stop_skeleton_pulse(self) -> None:
        for sk in self._skeletons:
            try:
                sk.stop_pulse()
            except Exception:
                pass
        for t in self._skeleton_tasks:
            try:
                if not t.done():
                    t.cancel()
            except Exception:
                pass
        self._skeleton_tasks = []

    def did_mount(self) -> None:
        self._unmounted = False

    def will_unmount(self) -> None:
        self._unmounted = True
        self._load_gen += 1
        if self._init_reload_task is not None and not self._init_reload_task.done():
            try:
                self._init_reload_task.cancel()
            except Exception:
                pass
        self._init_reload_task = None
        self._cancel_batch_task()
        self._stop_skeleton_pulse()
        for task in list(self._press_tasks):
            if not task.done():
                try:
                    task.cancel()
                except Exception:
                    pass

    def _cancel_batch_task(self) -> None:
        task = self._batch_task
        self._batch_task = None
        if task is not None and not task.done():
            try:
                task.cancel()
            except Exception:
                pass

    async def reload(self) -> None:
        current = asyncio.current_task()
        if (
            self._init_reload_task is not None
            and self._init_reload_task is not current
            and not self._init_reload_task.done()
        ):
            try:
                self._init_reload_task.cancel()
            except Exception:
                pass
        if self._init_reload_task is current:
            self._init_reload_task = None
        self._load_gen += 1
        gen = self._load_gen
        self._cancel_batch_task()
        self._reset_selection_state()
        self._loading = True
        self._error = ""
        self._sync_skeleton_count()
        self._skeleton_wrap.visible = True
        self._list_view.visible = False
        self._error_box.visible = False
        self._ensure_skeletons_pulsing()
        try:
            self.update()
        except Exception:
            pass
        try:
            if self._loader is not None:
                res = await self._loader()
                if isinstance(res, tuple) and len(res) == 2:
                    _meta, tracks = res
                else:
                    _meta, tracks = None, res
            else:
                _meta, tracks = await self.service.fetch_playlist(self.summary.platform, self.summary.id, None)
            if gen != self._load_gen or self._unmounted:
                return
            self._tracks = self._apply_manifest_order(tracks)
            tracks = self._tracks

            # Mapear pistas que ya existen en descargas o carpetas locales
            # (las carpetas locales ya traen source_path desde el escaneo).
            self._has_file_by_id = {}
            if not self._is_local:
                await asyncio.to_thread(self._match_local_files)
                if len(self._has_file_by_id) != len(tracks):
                    self._has_file_by_id = await asyncio.to_thread(
                        self._compute_has_file_map, tracks
                    )
            else:
                self._has_file_by_id = await asyncio.to_thread(
                    self._compute_has_file_map, tracks
                )
            if gen != self._load_gen or self._unmounted:
                return

            if self._download_btn is not None:
                self._download_btn.disabled = not tracks
            self._update_header_play_count(self._has_file_by_id)
            self._loading = False
            self.summary.track_count = len(tracks)
            # header count real tras cargar (fix Apple count 0 en summary)
            try:
                self._count.value = f"{len(tracks)} canciones • {self.summary.platform}"
            except Exception:
                pass
            initial_tracks = tracks[:_INITIAL_ROW_BATCH]
            remaining_tracks = tracks[_INITIAL_ROW_BATCH:]
            has_file_snapshot = dict(self._has_file_by_id)
            self._list_view.controls = [
                self._build_track_row(tr, i, has_file=has_file_snapshot.get(id(tr), False))
                for i, tr in enumerate(initial_tracks, 1)
            ]
            self._stop_skeleton_pulse()
            self._skeleton_wrap.visible = False
            self._list_view.visible = True
            if remaining_tracks and gen == self._load_gen and not self._unmounted:
                self._batch_task = asyncio.create_task(
                    self._append_remaining_rows(
                        remaining_tracks,
                        start_index=_INITIAL_ROW_BATCH + 1,
                        gen=gen,
                        has_file_map=has_file_snapshot,
                    )
                )
        except Exception as exc:
            if gen != self._load_gen or self._unmounted:
                return
            auth_manager = getattr(self.service, "auth_manager", None)
            if auth_manager is not None and self._loader is None:
                auth_manager.record_operation_failure(self.summary.platform, exc)
            self._loading = False
            self._error = str(exc)[:400]
            self._stop_skeleton_pulse()
            self._skeleton_wrap.visible = False
            self._error_box.visible = True
            try:
                self._error_box.content.controls[1].value = self._error  # type: ignore
            except Exception:
                pass
        if gen != self._load_gen or self._unmounted:
            return
        try:
            self.update()
        except Exception:
            pass

    async def _append_remaining_rows(
        self,
        tracks: list[Track],
        *,
        start_index: int,
        gen: int,
        has_file_map: dict[int, bool],
    ) -> None:
        current_task = asyncio.current_task()
        try:
            idx = start_index
            for offset in range(0, len(tracks), _ROW_CHUNK_SIZE):
                await asyncio.sleep(0)
                if gen != self._load_gen or self._unmounted:
                    return
                chunk = tracks[offset:offset + _ROW_CHUNK_SIZE]
                new_rows = [
                    self._build_track_row(tr, i, has_file=has_file_map.get(id(tr), False))
                    for i, tr in enumerate(chunk, idx)
                ]
                idx += len(chunk)
                if gen != self._load_gen or self._unmounted:
                    return
                self._list_view.controls.extend(new_rows)
                try:
                    self._list_view.update()
                except Exception:
                    pass
        except asyncio.CancelledError:
            return
        finally:
            if self._batch_task is current_task:
                self._batch_task = None

    def _apply_manifest_order(self, tracks: list[Track]) -> list[Track]:
        """Ordena una carpeta descargada según el ``playlist.json``.

        ``list_audio_files`` devuelve los archivos por fecha de modificación,
        así que sin manifiesto una playlist descargada se ve desordenada. Si
        no hay manifiesto o no se puede emparejar nada, se respeta el orden
        del sistema de archivos.
        """
        from engine.downloader import read_playlist_manifest

        variant = self.summary.id
        if variant not in self._manifest_cache:
            self._manifest_cache[variant] = read_playlist_manifest(variant)
        manifest = self._manifest_cache[variant]
        entries = manifest.get("tracks") if isinstance(manifest, dict) else None
        if not isinstance(entries, list) or not entries:
            return tracks

        by_path = {
            os.path.normpath(str(t.source_path)): t
            for t in tracks if t.source_path
        }
        ordered: list[Track] = []
        used: set[int] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "")
            track = by_path.get(os.path.normpath(path)) if path else None
            if track is not None and id(track) not in used:
                ordered.append(track)
                used.add(id(track))
        # Lo que el manifiesto no cubre (archivos añadidos después) va al final
        # en su orden original, sin duplicar.
        ordered.extend(t for t in tracks if id(t) not in used)
        return ordered

    @staticmethod
    def _compute_has_file_map(tracks: list[Track]) -> dict[int, bool]:
        return {
            id(tr): bool(tr.source_path and os.path.isfile(tr.source_path))
            for tr in tracks
        }

    def _match_local_files(self) -> int:
        """Asocia source_path a las pistas que ya existen en el directorio de descargas o carpetas locales."""
        try:
            from engine.downloader import _target_stem, resolve_download_dir
            from engine.parsers import _LOCAL_AUDIO_EXTENSIONS
            candidate_dirs = [resolve_download_dir(settings_store.get_download_dir())]
            for extra in settings_store.get_local_folders():
                if extra and os.path.isdir(extra):
                    candidate_dirs.append(Path(extra))

            disk_files: dict[str, str] = {}
            for d in candidate_dirs:
                try:
                    for fname in os.listdir(d):
                        lower = fname.lower()
                        if lower not in disk_files:
                            disk_files[lower] = os.path.join(str(d), fname)
                except OSError:
                    continue

            matched = 0
            has_file_map: dict[int, bool] = {}
            for tr in list(self._tracks):
                if tr.source_path and os.path.isfile(tr.source_path):
                    matched += 1
                    has_file_map[id(tr)] = True
                    continue
                stem = _target_stem(tr).lower()
                found = False
                for ext in _LOCAL_AUDIO_EXTENSIONS:
                    candidate_name = f"{stem}{ext}".lower()
                    if candidate_name in disk_files:
                        candidate_path = disk_files[candidate_name]
                        if os.path.isfile(candidate_path):
                            tr.source_path = candidate_path
                            matched += 1
                            found = True
                            break
                has_file_map[id(tr)] = found
            self._has_file_by_id = has_file_map
            return matched
        except Exception:
            self._has_file_by_id = {}
            return 0

    def _update_header_play_count(self, has_file_map: Optional[dict[int, bool]] = None) -> None:
        """Actualiza el botón Reproducir y Aleatorio en la cabecera según las pistas disponibles en disco."""
        if self._play_all_btn is None:
            return
        file_map = has_file_map if has_file_map is not None else self._has_file_by_id
        downloaded = sum(1 for t in self._tracks if file_map.get(id(t), False))
        total = len(self._tracks)
        if downloaded > 0:
            self._play_all_btn.visible = True
            self._play_all_btn.disabled = False
            self._shuffle_btn.visible = True
            self._shuffle_btn.disabled = False
            try:
                txt = "Reproducir" if downloaded == total or self._is_local else f"Reproducir ({downloaded})"
                if hasattr(self._play_all_btn.content, "value"):
                    self._play_all_btn.content.value = txt
                elif hasattr(self._play_all_btn, "text"):
                    self._play_all_btn.text = txt
            except Exception:
                pass
        else:
            if not self._is_local:
                self._play_all_btn.visible = False
                self._shuffle_btn.visible = False
            else:
                self._play_all_btn.disabled = True
                self._shuffle_btn.disabled = True
        if self._create_btn is not None:
            self._create_btn.disabled = total == 0
        self._select_mode_btn.disabled = total == 0

    def _download_recovered_track(
        self,
        track: Track,
        action_container: ft.Container | None = None,
        *,
        video_id: str = "",
        candidate_url: str = "",
        audio_format: str = "",
        dlg: ft.AlertDialog | None = None,
    ) -> None:
        """Reencola la descarga de una pista de Biblioteca con alternativa o formato nuevo."""
        st = self.app_state
        if st is None:
            notify(self._page_ref, "Estado de aplicación no disponible", kind="error")
            return
        if st.download_state == TransferState.RUNNING:
            notify(self._page_ref, "Ya hay una descarga en curso", kind="error")
            return
        if dlg is not None:
            dlg.open = False
            try:
                self._page_ref.update()
            except Exception:
                pass

        fmt = str(audio_format or settings_store.get_download_format() or DEFAULT_AUDIO_FORMAT).lower().lstrip(".")
        if fmt not in AUDIO_FORMATS:
            fmt = DEFAULT_AUDIO_FORMAT
        vid = str(video_id or "").strip()
        url = str(candidate_url or "").strip()
        if not url and vid:
            url = f"https://www.youtube.com/watch?v={vid}"
        target = Track(
            id=vid or track.id,
            name=track.name,
            artist=track.artist,
            album=track.album,
            duration=track.duration,
            duration_ms=track.duration_ms,
            img_url=track.img_url,
            source_url=url or track.source_url,
            platform=track.platform or getattr(self.summary, "platform", "") or "",
        )

        async def _run_recovery_download() -> None:
            if action_container is not None:
                action_container.content = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT)
                action_container.tooltip = "Descargando..."
                try:
                    action_container.update()
                except Exception:
                    pass
            notify(self._page_ref, f"Descargando «{track.name}» ({fmt.upper()})...")
            try:
                await st.download_tracks(
                    [target],
                    destination_dir=settings_store.get_download_dir(),
                    output_format=fmt,
                )
            except Exception as exc:  # noqa: BLE001
                notify(self._page_ref, f"Error al descargar: {exc}", kind="error")
                if action_container is not None:
                    action_container.content = app_icon(PIcons.DOWNLOAD_SIMPLE, size=16, color=TEXT_MUTED)
                    action_container.tooltip = "Descargar canción"
                    try:
                        action_container.update()
                    except Exception:
                        pass
                return

            if target.source_path and os.path.isfile(target.source_path):
                track.source_path = target.source_path
                self._has_file_by_id[id(track)] = True
                if action_container is not None:
                    action_container.content = app_icon(PIcons.PLAY, size=16, color=ACCENT, fill=True)
                    action_container.tooltip = "Reproducir"
                    try:
                        action_container.update()
                    except Exception:
                        pass
                self._update_header_play_count()
                try:
                    self.update()
                except Exception:
                    pass
                notify(
                    self._page_ref,
                    f"Descarga lista: {track.artist} — {track.name}",
                    action="Reproducir",
                    on_action=lambda _e: self._play_single_track(track, 1),
                )
            elif action_container is not None:
                action_container.content = app_icon(PIcons.DOWNLOAD_SIMPLE, size=16, color=TEXT_MUTED)
                action_container.tooltip = "Descargar canción"
                try:
                    action_container.update()
                except Exception:
                    pass

        self._page_ref.run_task(_run_recovery_download)

    async def _open_track_recovery(
        self,
        track: Track,
        action_container: ft.Container | None = None,
    ) -> AlternativeSearchPanel:
        """Abre el diálogo 2.6 de recuperación para una pista faltante en Biblioteca."""
        raw_fmt = str(settings_store.get_download_format() or DEFAULT_AUDIO_FORMAT).lower().lstrip(".")
        initial_fmt = raw_fmt if raw_fmt in AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT
        selected_fmt = {"value": initial_fmt}

        format_dd = organize_dropdown(
            [(f, f.upper()) for f in AUDIO_FORMATS],
            initial_fmt,
            "Formato",
            width=124,
        )

        def _on_fmt_select(e) -> None:
            val = str(getattr(e.control, "value", "") or initial_fmt).lower().lstrip(".")
            selected_fmt["value"] = val if val in AUDIO_FORMATS else initial_fmt

        format_dd.on_select = _on_fmt_select

        dlg = app_dialog(
            "Recuperar pista",
            ft.Container(
                content=ft.Column(controls=[], spacing=8, tight=True),
                min_width=360,
                max_width=660,
                max_height=520,
            ),
            [],
            width=None,
            icon=PIcons.MAGNIFYING_GLASS,
        )

        def _close(_e=None) -> None:
            dlg.open = False
            try:
                self._page_ref.update()
            except Exception:
                pass

        async def _search(query: str) -> tuple[list[RecoveryCandidate], str]:
            found = await search_youtube_alternatives(track, query, app_state=self.app_state)
            candidates = [
                candidate_to_recovery(c)
                for c in (found.get("candidates") or [])[:5]
            ]
            err = str(found.get("error") or "")
            return candidates, err

        def _pick(cand: RecoveryCandidate) -> None:
            payload = cand.payload if isinstance(cand.payload, dict) else {}
            vid = str(payload.get("video_id") or cand.track.id or "")
            url = str(payload.get("url") or cand.track.source_url or "")
            fmt = str(format_dd.value or selected_fmt["value"] or initial_fmt).lower()
            self._download_recovered_track(
                track,
                action_container,
                video_id=vid,
                candidate_url=url,
                audio_format=fmt,
                dlg=dlg,
            )

        retry_btn = _ghost_btn(
            "Reintentar con formato",
            PIcons.ARROWS_CLOCKWISE,
            lambda _e: self._download_recovered_track(
                track,
                action_container,
                audio_format=str(format_dd.value or selected_fmt["value"] or initial_fmt).lower(),
                dlg=dlg,
            ),
            height=38,
        )

        panel = AlternativeSearchPanel(
            self._page_ref,
            track,
            reason="Archivo no disponible en disco",
            search_fn=_search,
            on_select=_pick,
            search_label="Buscar en YouTube",
            hint_text="Título, artista, URL de YouTube o variante",
            action_label="Descargar",
            action_icon=PIcons.DOWNLOAD_SIMPLE,
            empty_message="Sin coincidencias. Edita la consulta o pega una URL de YouTube.",
            query_trailing=[format_dd],
            action_trailing=[retry_btn],
        )
        dlg.content.content = panel
        dlg.actions = [dialog_action("Cerrar", _close, kind="muted")]
        self._recovery_dialog = dlg
        self._recovery_panel = panel
        self._page_ref.show_dialog(dlg)
        await panel.run_search()
        return panel

    def _download_single_track(self, track: Track, action_container: ft.Container) -> None:
        """Descarga una pista individual y al completarse habilita su reproducción inmediata."""
        st = self.app_state
        if st is None:
            notify(self._page_ref, "Estado de aplicación no disponible", kind="error")
            return
        if st.download_state == TransferState.RUNNING:
            notify(self._page_ref, "Ya hay una descarga en curso", kind="error")
            return

        async def _do_download() -> None:
            action_container.content = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT)
            action_container.tooltip = "Descargando..."
            try:
                action_container.update()
            except Exception:
                pass

            notify(self._page_ref, f"Descargando «{track.name}»...")
            try:
                task = st.download_tracks([track], destination_dir=settings_store.get_download_dir())
                await task
            except Exception as exc:  # noqa: BLE001
                notify(self._page_ref, f"Error al descargar: {exc}", kind="error")
                action_container.content = app_icon(PIcons.DOWNLOAD_SIMPLE, size=16, color=TEXT_MUTED)
                action_container.tooltip = "Descargar canción"
                try:
                    action_container.update()
                except Exception:
                    pass
                return

            if track.source_path and os.path.isfile(track.source_path):
                self._has_file_by_id[id(track)] = True
                notify(
                    self._page_ref,
                    f"Descarga lista: {track.artist} — {track.name}",
                    action="Reproducir",
                    on_action=lambda e: self._play_single_track(track, 1),
                )
                action_container.content = app_icon(PIcons.PLAY, size=16, color=ACCENT, fill=True)
                action_container.tooltip = "Reproducir"
                try:
                    action_container.update()
                except Exception:
                    pass
                self._update_header_play_count()
                try:
                    self.update()
                except Exception:
                    pass
            else:
                notify(self._page_ref, f"No se pudo descargar «{track.name}»", kind="error")
                action_container.content = app_icon(PIcons.DOWNLOAD_SIMPLE, size=16, color=TEXT_MUTED)
                action_container.tooltip = "Descargar canción"
                try:
                    action_container.update()
                except Exception:
                    pass

        try:
            asyncio.create_task(_do_download())
        except Exception:
            pass

    def _on_play_all(self, _e=None) -> None:
        """Reproduce todas las pistas locales disponibles en el reproductor."""
        items = [
            (t.source_path, t)
            for t in self._tracks
            if t.source_path and os.path.isfile(t.source_path)
        ]
        if not items:
            notify(self._page_ref, "No hay archivos disponibles en disco", kind="error")
            return
        if self.player_bar is not None and hasattr(self.player_bar, "play_files"):
            self.player_bar.play_files(items, start_index=0)
            notify(self._page_ref, f"Reproduciendo {len(items)} canciones")
        elif self.player_bar is not None and hasattr(self.player_bar, "play_file"):
            self.player_bar.play_file(items[0][0])

    def _on_shuffle_play(self, _e=None) -> None:
        """Activa el modo aleatorio y reproduce desde una pista al azar."""
        playable_tracks = [
            t for t in self._tracks if t.source_path and os.path.isfile(t.source_path)
        ]
        if not playable_tracks:
            notify(self._page_ref, "No hay archivos disponibles en disco", kind="error")
            return
        pb = self.player_bar
        if pb is None:
            return
        q = getattr(pb, "queue", None)
        start_idx: int | None = None
        used_context_shuffle = False
        if q is not None and hasattr(q, "shuffle_from_context"):
            picked = q.shuffle_from_context(playable_tracks)
            picked_track = getattr(picked, "track", None)
            if isinstance(picked_track, Track):
                for idx, track in enumerate(playable_tracks):
                    if track is picked_track or track == picked_track:
                        start_idx = idx
                        used_context_shuffle = True
                        break
        if not used_context_shuffle and q is not None and hasattr(q, "set_shuffle"):
            if getattr(q, "shuffle", False) is not True:
                q.set_shuffle(True)
        setter = getattr(settings_store, "set_shuffle", None)
        if callable(setter):
            try:
                setter(True)
            except Exception:
                pass
        items = [(t.source_path, t) for t in playable_tracks]
        if start_idx is None:
            start_idx = random.randrange(len(items))
        if hasattr(pb, "play_files"):
            pb.play_files(items, start_index=start_idx)
            notify(self._page_ref, f"Reproduciendo {len(items)} canciones en aleatorio")
        elif hasattr(pb, "play_file"):
            pb.play_file(items[start_idx][0], track=items[start_idx][1])
            notify(self._page_ref, f"Reproduciendo {len(items)} canciones en aleatorio")
        elif used_context_shuffle and hasattr(pb, "_load_and_play_current"):
            pb._load_and_play_current()
            notify(self._page_ref, f"Reproduciendo {len(items)} canciones en aleatorio")

    # ── Selección múltiple y acciones por lote ─────────────────────────

    def _reset_selection_state(self) -> None:
        self._selection_mode = False
        self._selected_ids.clear()
        self._row_checkboxes.clear()
        if hasattr(self, "_batch_bar"):
            self._batch_bar.visible = False
        if hasattr(self, "_select_all_chk"):
            self._select_all_chk.value = False
        if hasattr(self, "_select_mode_btn"):
            self._select_mode_btn.disabled = True
        self._sync_selection_ui(notify_ui=False)

    def selected_tracks(self) -> list[Track]:
        """Devuelve las pistas seleccionadas preservando su orden en la lista."""
        return [t for t in self._tracks if id(t) in self._selected_ids]

    def set_selection_mode(self, enabled: bool) -> None:
        """Activa o desactiva el modo de selección múltiple."""
        target = bool(enabled) and bool(self._tracks)
        self._selection_mode = target
        if not target:
            self._selected_ids.clear()
        for tid, chk in self._row_checkboxes.items():
            chk.visible = target
            chk.value = tid in self._selected_ids if target else False
        self._batch_bar.visible = target
        self._sync_selection_ui(notify_ui=True)

    def _toggle_selection_mode(self) -> None:
        self.set_selection_mode(not self._selection_mode)

    def toggle_track_selection(self, track: Track) -> None:
        """Alterna la selección de una pista individual."""
        tid = id(track)
        if tid in self._selected_ids:
            self._selected_ids.remove(tid)
        else:
            self._selected_ids.add(tid)
        chk = self._row_checkboxes.get(tid)
        if chk is not None:
            chk.value = tid in self._selected_ids
        self._sync_selection_ui(notify_ui=True)

    def _start_selection_with(self, track: Track) -> None:
        if not self._selection_mode:
            self.set_selection_mode(True)
        if id(track) not in self._selected_ids:
            self.toggle_track_selection(track)

    def select_all(self, selected: bool = True) -> None:
        """Selecciona o deselecciona todas las pistas cargadas."""
        if selected:
            self._selected_ids = {id(t) for t in self._tracks}
        else:
            self._selected_ids.clear()
        for tid, chk in self._row_checkboxes.items():
            chk.value = tid in self._selected_ids
        self._sync_selection_ui(notify_ui=True)

    def _on_select_all_change(self, e) -> None:
        self.select_all(bool(getattr(e, "control", None) and e.control.value))

    def _sync_selection_ui(self, *, notify_ui: bool = False) -> None:
        if not hasattr(self, "_selected_count_label"):
            return
        selected = self.selected_tracks()
        count = len(selected)
        total = len(self._tracks)
        self._selected_count_label.value = (
            "1 seleccionada" if count == 1 else f"{count} seleccionadas"
        )
        self._select_all_chk.value = total > 0 and count == total
        has_playable = any(
            t.source_path and os.path.isfile(t.source_path) for t in selected
        )
        self._batch_play_btn.disabled = not has_playable
        self._batch_queue_btn.disabled = not has_playable
        self._batch_download_btn.disabled = count == 0 or self.app_state is None
        if notify_ui:
            try:
                self.update()
            except Exception:
                pass

    def _on_batch_play(self, _e=None) -> None:
        items = [
            (t.source_path, t)
            for t in self.selected_tracks()
            if t.source_path and os.path.isfile(t.source_path)
        ]
        if not items:
            notify(self._page_ref, "Las pistas seleccionadas no están en disco", kind="error")
            return
        if self.player_bar is not None and hasattr(self.player_bar, "play_files"):
            self.player_bar.play_files(items, start_index=0)
            notify(self._page_ref, f"Reproduciendo {len(items)} seleccionadas")
        elif self.player_bar is not None and hasattr(self.player_bar, "play_file"):
            self.player_bar.play_file(items[0][0], track=items[0][1])

    def _on_batch_enqueue(self, _e=None) -> None:
        items = [
            (t.source_path, t)
            for t in self.selected_tracks()
            if t.source_path and os.path.isfile(t.source_path)
        ]
        if not items:
            notify(self._page_ref, "Las pistas seleccionadas no están en disco", kind="error")
            return
        pb = self.player_bar
        if pb is None or not hasattr(pb, "enqueue_file"):
            return
        added = 0
        for path, tr in items:
            if pb.enqueue_file(path, track=tr, play_now=False):
                added += 1
        if added:
            notify(self._page_ref, f"{added} canciones añadidas a la cola")

    def _on_batch_download(self, _e=None) -> None:
        st = self.app_state
        selected = self.selected_tracks()
        if st is None or not selected:
            return
        if st.download_state == TransferState.RUNNING:
            notify(self._page_ref, "Ya hay una descarga en curso", kind="error")
            return
        try:
            st.download_tracks(selected, destination_dir=settings_store.get_download_dir())
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo iniciar la descarga: {exc}", kind="error")
            return
        cb = self._on_open_downloads
        notify(
            self._page_ref,
            f"Descargando {len(selected)} pistas seleccionadas",
            action="Ver" if cb else None,
            on_action=(lambda _e: cb()) if cb else None,
        )

    # ── Acciones contextuales por pista ────────────────────────────────

    def _enqueue_single_track(self, track: Track) -> None:
        if not track.source_path or not os.path.isfile(track.source_path):
            notify(self._page_ref, "Archivo no encontrado en disco", kind="error")
            return
        pb = self.player_bar
        if pb is not None and hasattr(pb, "enqueue_file"):
            if pb.enqueue_file(track.source_path, track=track, play_now=False):
                notify(self._page_ref, f"Añadida a la cola: {track.name}")

    def _play_next_track(self, track: Track) -> None:
        if not track.source_path or not os.path.isfile(track.source_path):
            notify(self._page_ref, "Archivo no encontrado en disco", kind="error")
            return
        pb = self.player_bar
        if pb is None or not hasattr(pb, "enqueue_file"):
            return
        q = getattr(pb, "queue", None)
        had_current = q is not None and getattr(q, "current", None) is not None
        if not pb.enqueue_file(track.source_path, track=track, play_now=False):
            return
        if had_current and q is not None and hasattr(pb, "move_queue_item"):
            abs_target = os.path.abspath(track.source_path)
            ordered = q.ordered_tracks()
            idx = next(
                (i for i, item in enumerate(ordered) if item.path == abs_target or item.track is track),
                -1,
            )
            cur_pos = getattr(q, "pos", -1)
            if idx >= 0 and cur_pos >= 0 and idx != cur_pos and idx != cur_pos + 1:
                dest = cur_pos + 1 if idx > cur_pos else cur_pos
                pb.move_queue_item(idx, dest)
        notify(self._page_ref, f"Siguiente en cola: {track.name}")

    def _track_context_actions(self, track: Track, index: int, action_icon: ft.Container | None = None) -> list[ContextMenuAction]:
        has_disk = bool(track.source_path and os.path.isfile(track.source_path))
        actions: list[ContextMenuAction] = []
        if has_disk:
            actions.extend([
                ContextMenuAction(
                    "Reproducir",
                    icon=PIcons.PLAY,
                    on_click=lambda: self._play_single_track(track, index),
                ),
                ContextMenuAction(
                    "Reproducir siguiente",
                    icon=PIcons.SKIP_FORWARD,
                    on_click=lambda: self._play_next_track(track),
                ),
                ContextMenuAction(
                    "Añadir a la cola",
                    icon=PIcons.QUEUE,
                    on_click=lambda: self._enqueue_single_track(track),
                ),
                ContextMenuAction(
                    "Mostrar en carpeta",
                    icon=PIcons.FOLDER_OPEN,
                    on_click=lambda: open_in_file_manager(track.source_path),
                ),
            ])
        else:
            actions.extend([
                ContextMenuAction(
                    "Descargar canción",
                    icon=PIcons.DOWNLOAD_SIMPLE,
                    on_click=(lambda: self._download_single_track(track, action_icon)) if action_icon is not None else None,
                    disabled=self.app_state is None or action_icon is None,
                ),
                ContextMenuAction(
                    "Buscar alternativa...",
                    icon=PIcons.MAGNIFYING_GLASS,
                    on_click=lambda: self._page_ref.run_task(self._open_track_recovery, track, action_icon),
                    disabled=self.app_state is None,
                ),
            ])
        if track.source_url:
            actions.append(
                ContextMenuAction(
                    "Abrir en plataforma",
                    icon=PIcons.ARROW_SQUARE_OUT,
                    on_click=lambda: self._launch(track.source_url or ""),
                )
            )
        if self._selection_mode:
            is_sel = id(track) in self._selected_ids
            actions.append(
                ContextMenuAction(
                    "Deseleccionar" if is_sel else "Seleccionar",
                    icon=PIcons.CIRCLE if is_sel else PIcons.CHECK_CIRCLE,
                    on_click=lambda: self.toggle_track_selection(track),
                    divider_before=bool(actions),
                )
            )
        else:
            actions.append(
                ContextMenuAction(
                    "Seleccionar varias...",
                    icon=PIcons.CHECK_CIRCLE,
                    on_click=lambda: self._start_selection_with(track),
                    divider_before=bool(actions),
                )
            )
        return actions

    def _play_single_track(self, track: Track, index: int) -> None:
        """Reproduce una sola pista local en el reproductor encolando la lista."""
        if not track.source_path or not os.path.isfile(track.source_path):
            notify(self._page_ref, "Archivo no encontrado en disco", kind="error")
            return
        items = [(t.source_path, t) for t in self._tracks if t.source_path and os.path.isfile(t.source_path)]
        if self.player_bar is not None and hasattr(self.player_bar, "play_files") and items:
            try:
                start_idx = next(i for i, (p, _t) in enumerate(items) if p == track.source_path)
            except StopIteration:
                start_idx = max(0, min(len(items) - 1, index - 1))
            self.player_bar.play_files(items, start_index=start_idx)
        elif self.player_bar is not None and hasattr(self.player_bar, "play_file"):
            self.player_bar.play_file(track.source_path)

    def _open_track(self, track: Track) -> None:
        """Pista local → reproducir si hay archivo; si no, abrir explorador. Plataforma → su URL."""
        if self._is_local and track.source_path and os.path.isfile(track.source_path):
            self._play_single_track(track, 1)
        elif self._is_local and track.source_path:
            open_in_file_manager(track.source_path)
        else:
            self._launch(track.source_url or "")

    def _build_track_row(self, track: Track, index: int, *, has_file: Optional[bool] = None) -> ft.Container:
        """Fila Biblioteca: [chk?][#][thumb48][título/artista expand3][álbum expand2][dur48][open/play 32][⋮ 28].

        Misma geometría que SongRow Gestión (spacing 16, ITEM_H) + columna
        álbum. Hover: BG_HOVER + scale 1.01. Click: press scale 0.99 +
        opacidad 0.85 antes de abrir URL o reproducir.
        """
        if has_file is None:
            has_file = self._has_file_by_id.get(id(track), False)

        thumb = ft.Container(
            width=48, height=48, border_radius=6, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=_thumb_content_for(track.img_url, box=48, icon_size=16),
        )
        title = ft.Text(track.name, size=13, color=TEXT_PRIMARY, font_family=font_family_for(track.name, "semibold"), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        artist = ft.Text(track.artist, size=11, color=TEXT_MUTED, font_family=font_family_for(track.artist), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        album = ft.Text(track.album or "—", size=11, color=TEXT_MUTED, font_family=font_family_for(track.album or ""), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        dur = ft.Text(track.duration, size=11, color=TEXT_DIM, font_family=mono_family("light"))

        chk = ft.Checkbox(
            value=id(track) in self._selected_ids,
            visible=self._selection_mode,
            fill_color={
                ft.ControlState.SELECTED: ACCENT,
                ft.ControlState.DEFAULT: BG_SURFACE,
            },
            check_color=TEXT_PRIMARY,
            border_side=ft.BorderSide(1.5, TEXT_DIM),
            on_change=lambda _e, tr=track: self.toggle_track_selection(tr),
        )
        self._row_checkboxes[id(track)] = chk

        action_icon = ft.Container(
            content=app_icon(
                PIcons.PLAY if has_file else PIcons.DOWNLOAD_SIMPLE,
                size=16,
                color=ACCENT if has_file else TEXT_MUTED,
                fill=has_file,
            ),
            width=32,
            height=32,
            border_radius=16,
            alignment=ft.Alignment.CENTER,
            tooltip="Reproducir" if has_file else "Descargar canción",
            ink=True,
        )

        def _on_action_click(_e) -> None:
            if track.source_path and os.path.isfile(track.source_path):
                self._play_single_track(track, index)
            else:
                self._download_single_track(track, action_icon)

        action_icon.on_click = _on_action_click

        menu_btn = build_context_menu_button(
            lambda tr=track, idx=index, ic=action_icon: self._track_context_actions(tr, idx, ic),
            tooltip="Opciones de pista",
            width=28,
            height=28,
            icon_size=16,
        )

        row_inner = ft.Row(controls=[
            chk,
            ft.Container(content=ft.Text(str(index), size=11, color=TEXT_MUTED, font_family=mono_family("light")), width=28, alignment=ft.Alignment.CENTER),
            thumb,
            ft.Column(controls=[title, artist], spacing=1, expand=3, tight=True, alignment=ft.MainAxisAlignment.CENTER),
            ft.Container(content=album, expand=2, alignment=ft.Alignment.CENTER_LEFT),
            ft.Container(content=dur, width=48, alignment=ft.Alignment.CENTER),
            action_icon,
            menu_btn,
        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        row = ft.Container(
            height=ITEM_H, bgcolor=BG_LIST, border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)), padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            ink=True,
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            animate_opacity=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            content=wrap_with_context_menu(
                row_inner,
                lambda tr=track, idx=index, ic=action_icon: self._track_context_actions(tr, idx, ic),
                title=track.name,
                get_page=lambda: self._page_ref,
            ),
        )

        def _on_hover(e: ft.HoverEvent) -> None:
            hovering = e.data == "true"
            row.bgcolor = BG_HOVER if hovering else BG_LIST
            row.scale = 1.01 if hovering else 1.0
            try:
                row.update()
            except Exception:
                pass

        def _on_click(_e) -> None:
            if self._selection_mode:
                self.toggle_track_selection(track)
                return

            async def _press_and_act() -> None:
                try:
                    row.scale = 0.99
                    row.opacity = 0.85
                    row.update()
                    try:
                        await asyncio.sleep(0.08)
                    except asyncio.CancelledError:
                        raise
                finally:
                    # El estado visual se restaura siempre, también si la tarea
                    # se cancela al desmontar la vista: la fila nunca queda
                    # encogida ni translúcida.
                    try:
                        row.scale = 1.0
                        row.opacity = 1.0
                        row.update()
                    except Exception:
                        pass
                if track.source_path and os.path.isfile(track.source_path):
                    self._play_single_track(track, index)
                else:
                    self._download_single_track(track, action_icon)

            try:
                task = asyncio.create_task(_press_and_act())
                self._press_tasks.add(task)
                task.add_done_callback(self._press_tasks.discard)
            except Exception:
                try:
                    row.scale = 1.0
                    row.opacity = 1.0
                except Exception:
                    pass
                if track.source_path and os.path.isfile(track.source_path):
                    self._play_single_track(track, index)
                else:
                    self._download_single_track(track, action_icon)

        row.on_hover = _on_hover
        row.on_click = _on_click
        return row
