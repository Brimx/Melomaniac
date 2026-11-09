"""
ui/downloads_view.py — Módulo Descargas: cola de audio con yt-dlp
════════════════════════════════════════════════════════════════════
Vista pura sobre ``AppState``: elige formato y carpeta de destino, lanza la
descarga de las pistas seleccionadas y muestra progreso por pista. Sin
descargas activas muestra el historial de archivos de la carpeta y permite
indexarla en la Biblioteca.

Cada pista se resuelve como coincidencia en YouTube; nunca se descarga
desde la plataforma de origen y solo se conserva el audio.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import flet as ft

from core.config import AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT
from core.models import TransferState
from engine.downloader import ffmpeg_available, open_in_file_manager, resolve_download_dir
from engine.parsers import list_audio_files
from services import settings_store
from ui.fonts import (
    FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_PHOSPHOR, FONT_PHOSPHOR_FILL, FONT_TEXT,
    mono_family,
)
from ui.icons import PIcons, app_icon, app_icon_button
from ui.tokens import (
    ACCENT, BG_LIST, BG_SURFACE, BORDER_MUTED, ERROR_COL, RADIUS_INPUT,
    SUCCESS, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import (
    _ghost_btn, _primary_btn, app_dialog, app_text, dialog_action, fmt_ms, notify,
    organize_dropdown, section_label, status_banner,
)

_HISTORY_LIMIT = 500


def _fmt_size(num: int) -> str:
    return f"{num / (1024 * 1024):.1f} MB"


def _fmt_dur(ms: int | None) -> str:
    """Duración de una fila; «—» cuando la fuente no la trae."""
    return fmt_ms(ms) if ms else "—"


def _entry_title(entry: dict) -> str:
    """``Artista — Título`` de una entrada del panel; nunca vacío."""
    artist = str(entry.get("artist") or "").strip()
    name = str(entry.get("name") or "").strip() or "Pista sin título"
    return f"{artist} — {name}" if artist else name


def _entry_meta(entry: dict) -> str:
    """Formato y duración de una entrada: ``MP3 · 3:42``."""
    fmt = str(entry.get("format") or "").upper() or "—"
    return f"{fmt} · {_fmt_dur(entry.get('duration_ms'))}"


def _fmt_path(path: str) -> str:
    """Ruta acortada al nombre de archivo y su carpeta inmediata."""
    if not path:
        return ""
    p = Path(path)
    return f"{p.parent.name}/{p.name}" if p.parent.name else p.name


class DownloadsView(ft.Container):
    """Cola de descargas de audio con destino configurable e historial.

    Args:
        page: Página Flet.
        app_state: ``AppState`` con ``download_*`` y ``download_selected()``.
        library_state: ``LibraryState`` opcional; habilita «Indexar a Biblioteca».
    """

    def __init__(self, page: ft.Page, app_state, library_state=None, player_bar=None) -> None:
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self.app_state = app_state
        self.library_state = library_state
        self.player_bar = player_bar
        self._cancel_requested = False
        self._was_running = False
        self._items_key: int = -1
        self._item_widgets: list[tuple[dict, ft.ProgressBar, ft.Text, ft.Icon]] = []
        self._history_dir = ""
        self._history_paths: list[str] = []
        self._results_sig: tuple[int, int, int, int] = (-1, -1, -1, -1)
        try:
            self._default_dir = str(resolve_download_dir(create=False))
        except Exception:
            self._default_dir = ""
        self._custom_dir = settings_store.get_download_dir()
        self._picker = ft.FilePicker()
        page.services.append(self._picker)
        self._build()
        self.app_state.subscribe(self._refresh)
        self._refresh()

    @property
    def _dir(self) -> str:
        """Carpeta efectiva: la elegida por el usuario o la por defecto."""
        return self._custom_dir or self._default_dir

    # ── construcción ───────────────────────────────────────────────────

    def _build(self) -> None:
        self._format_dd = organize_dropdown(
            [(fmt, fmt.upper()) for fmt in AUDIO_FORMATS],
            settings_store.get_download_format(),
            "Formato",
            width=140,
        )
        self._format_dd.on_change = self._on_format_change
        self._dir_text = app_text("", size=11, color=TEXT_PRIMARY, font_family=mono_family("light"),
                                  max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self._count_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)

        self._start_btn = _primary_btn(
            "Descargar seleccionadas", PIcons.DOWNLOAD_SIMPLE, self._on_start, width=220
        )
        self._start_btn.disabled = True
        self._pause_btn = _ghost_btn(
            "Pausar", PIcons.PAUSE, self._on_pause,
            width=110, disabled=True,
        )
        self._cancel_btn = _ghost_btn(
            "Cancelar", PIcons.X, self._on_cancel,
            width=120, disabled=True,
        )

        self._open_dir_btn = app_icon_button(
            PIcons.FOLDER_OPEN, icon_color=TEXT_MUTED, hover_color_icon=TEXT_PRIMARY, size=18,
            tooltip="Abrir carpeta en el explorador", on_click=self._on_open_dir, width=32, height=32,
        )
        self._pick_dir_btn = app_icon_button(
            PIcons.PENCIL_SIMPLE, icon_color=TEXT_MUTED, hover_color_icon=TEXT_PRIMARY, size=18,
            tooltip="Cambiar carpeta de descargas",
            on_click=lambda e: self._page_ref.run_task(self._pick_dir), width=32, height=32,
        )
        self._reset_dir_btn = app_icon_button(
            PIcons.ARROWS_CLOCKWISE, icon_color=TEXT_MUTED, hover_color_icon=TEXT_PRIMARY, size=18,
            tooltip="Restablecer carpeta por defecto", on_click=self._on_reset_dir, width=32, height=32,
        )
        destination = ft.Container(
            bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT, padding=ft.Padding.symmetric(horizontal=12, vertical=4),
            content=ft.Row(controls=[
                app_icon(PIcons.FOLDER, size=18, color=TEXT_MUTED),
                ft.Column(controls=[section_label("DESTINO"), self._dir_text], spacing=1, expand=True),
                self._open_dir_btn, self._pick_dir_btn, self._reset_dir_btn,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

        # progreso global (solo mientras hay cola)
        self._progress = ft.ProgressBar(
            value=0, color=ACCENT, bgcolor=BG_SURFACE, height=6, border_radius=3,
        )
        self._progress_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._current_text = app_text("", size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT,
                                      max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                      expand=True)
        self._progress_area = ft.Column(controls=[
            self._progress,
            ft.Row(controls=[self._progress_text, self._current_text], spacing=10),
        ], spacing=6, visible=False)

        # estado A: cola activa (una fila por pista)
        self._queue_list = ft.ListView(expand=True, spacing=6, padding=0)
        self._queue_section = ft.Column(
            controls=[section_label("EN PROCESO"), self._queue_list],
            spacing=8, expand=True, visible=False,
        )

        # estado B: historial de la carpeta
        self._play_all_btn = _ghost_btn(
            "Reproducir descargadas (0)",
            PIcons.PLAY,
            self._on_play_downloaded,
            height=36,
            disabled=True,
        )
        self._index_btn = _ghost_btn(
            "Indexar a Biblioteca", PIcons.PLUS, self._on_index, height=36,
        )
        self._index_btn.visible = self.library_state is not None
        self._history_list = ft.ListView(expand=True, spacing=0, padding=0)
        self._history_empty = ft.Container(
            expand=True, alignment=ft.Alignment.CENTER,
            content=ft.Column(controls=[
                app_icon(PIcons.DOWNLOAD_SIMPLE, size=48, color=TEXT_DIM),
                app_text("Sin descargas todavía", size=13, color=TEXT_PRIMARY,
                         font_family=FONT_HEADLINE_BOLD),
                app_text("Selecciona pistas en Gestión o descarga una playlist desde Biblioteca",
                         size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
                         text_align=ft.TextAlign.CENTER),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
        )
        self._done_col = ft.Column(spacing=6)
        self._failed_col = ft.Column(spacing=6)
        self._missing_col = ft.Column(spacing=6)
        self._result_counts = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._results_panel = ft.Column(controls=[
            ft.Row(controls=[
                section_label("RESULTADO DE LA ÚLTIMA TANDA"),
                ft.Container(expand=True),
                self._result_counts,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self._done_col,
            self._failed_col,
            self._missing_col,
        ], spacing=6, visible=False)
        self._history_stack = ft.Stack(
            controls=[self._history_list, self._history_empty], expand=True,
            fit=ft.StackFit.EXPAND,
        )
        self._history_section = ft.Column(controls=[
            ft.Row(controls=[
                section_label("HISTORIAL EN LA CARPETA"),
                ft.Container(expand=True),
                self._play_all_btn,
                self._index_btn,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self._results_panel,
            self._history_stack,
        ], spacing=8, expand=True)

        header = ft.Column(controls=[
            app_text("Descargas", size=18, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            app_text(
                "yt-dlp resuelve cada pista en YouTube y conserva solo el audio.",
                size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
            ),
        ], spacing=2)

        # Condición persistente que el usuario debe resolver; no un toast.
        self._ffmpeg_banner = status_banner(
            "FFmpeg no está disponible. Instálalo y añádelo al PATH para descargar audio.",
            icon=PIcons.WARNING,
            kind="warning",
        )

        controls = ft.Row(controls=[
            self._format_dd,
            ft.Container(content=self._count_text, expand=True, padding=ft.Padding.only(left=6)),
            self._pause_btn,
            self._cancel_btn,
            self._start_btn,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        self.content = ft.Container(
            content=ft.Column(controls=[
                header,
                ft.Divider(height=1, color=BORDER_MUTED),
                self._ffmpeg_banner,
                controls,
                destination,
                self._progress_area,
                self._queue_section,
                self._history_section,
            ], spacing=12, expand=True),
            padding=ft.Padding.all(16), expand=True,
        )

    # ── acciones ───────────────────────────────────────────────────────

    def _on_format_change(self, e=None) -> None:
        """Persiste el formato elegido para que Config y la próxima cola lo usen."""
        settings_store.set_download_format(
            str(getattr(e.control, "value", "") or "")
        )

    def _on_start(self, e=None) -> None:
        fmt = (self._format_dd.value or DEFAULT_AUDIO_FORMAT).lower()
        self._cancel_requested = False
        try:
            self.app_state.download_selected(audio_format=fmt, destination_dir=self._custom_dir)
        except Exception as exc:  # noqa: BLE001 - la UI no debe caerse
            self.app_state.log(f"[ERROR] No se pudo iniciar la descarga: {exc}")
        self._refresh()

    def _on_pause(self, e=None) -> None:
        """Pausa la cola; si ya estaba pausada, la reanuda."""
        if getattr(self.app_state, "download_paused", False):
            try:
                self.app_state.resume_downloads()
            except Exception as exc:  # noqa: BLE001 - la UI no debe caerse
                self.app_state.log(f"[ERROR] No se pudo reanudar: {exc}")
        else:
            try:
                self.app_state.pause_downloads()
            except Exception as exc:  # noqa: BLE001
                self.app_state.log(f"[ERROR] No se pudo pausar: {exc}")
        self._refresh()

    def _on_cancel(self, e=None) -> None:
        self._cancel_requested = True
        try:
            self.app_state.cancel_downloads()
        except Exception as exc:  # noqa: BLE001
            self.app_state.log(f"[ERROR] No se pudo cancelar la descarga: {exc}")
        self._refresh()

    def _on_open_dir(self, e=None) -> None:
        target = resolve_download_dir(self._custom_dir or None)
        if not open_in_file_manager(target):
            notify(self._page_ref, "No se pudo abrir la carpeta", kind="error")

    async def _pick_dir(self) -> None:
        try:
            initial = self._dir if os.path.isdir(self._dir) else None
            path = await self._picker.get_directory_path(
                dialog_title="Carpeta de descargas", initial_directory=initial,
            )
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo abrir el selector: {exc}", kind="error")
            return
        if path:
            self._set_dir(path)

    def _on_reset_dir(self, e=None) -> None:
        self._set_dir("")

    def _set_dir(self, path: str) -> None:
        settings_store.set_download_dir(path)
        self._custom_dir = settings_store.get_download_dir()
        self._history_dir = ""  # fuerza recarga del historial
        self._refresh()

    def _on_index(self, e=None) -> None:
        if self.library_state is None:
            return
        target = str(resolve_download_dir(self._custom_dir or None))
        if self.library_state.add_local_folder(target):
            notify(self._page_ref, "Carpeta indexada en Biblioteca → Local")
        self._refresh()

    def _play_path(self, path: str) -> None:
        if not path or not os.path.isfile(path):
            notify(self._page_ref, "El archivo no está disponible en disco", kind="error")
            return
        if self.player_bar is not None and hasattr(self.player_bar, "play_file"):
            self.player_bar.play_file(path)

    def _on_play_downloaded(self, _e=None) -> None:
        valid = [p for p in self._history_paths if p and os.path.isfile(p)]
        if not valid:
            notify(self._page_ref, "No hay archivos descargados para reproducir", kind="error")
            return
        if self.player_bar is not None and hasattr(self.player_bar, "play_files"):
            self.player_bar.play_files(valid, start_index=0)

    # ── historial ──────────────────────────────────────────────────────

    async def _load_history(self) -> None:
        folder = self._dir
        self._history_dir = folder

        def _scan() -> list[tuple[str, str, str, int, float]]:
            rows = []
            for path in list_audio_files(folder, limit=_HISTORY_LIMIT):
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                name, ext = os.path.splitext(os.path.basename(path))
                rows.append((path, name, ext.lstrip(".").upper(), st.st_size, st.st_mtime))
            return rows

        rows = await asyncio.to_thread(_scan)
        if folder != self._dir:
            return  # el usuario cambió de carpeta mientras se escaneaba
        self._history_paths = [r[0] for r in rows]
        self._play_all_btn.content = ft.Text(
            f"Reproducir descargadas ({len(self._history_paths)})",
            opacity=1.0,
            font_family=FONT_HEADLINE,
        )
        self._play_all_btn.disabled = not self._history_paths
        self._history_list.controls = [self._history_row(*r) for r in rows]
        self._history_empty.visible = not rows
        self._history_list.visible = bool(rows)
        try:
            self.update()
        except Exception:
            pass

    def _history_row(self, path: str, name: str, ext: str, size: int, mtime: float) -> ft.Container:
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_MUTED)),
            content=ft.Row(controls=[
                app_icon_button(
                    PIcons.PLAY,
                    size=16,
                    icon_color=ACCENT,
                    hover_color_icon=ACCENT,
                    tooltip="Reproducir archivo",
                    on_click=lambda _e, p=path: self._play_path(p),
                    width=28,
                    height=28,
                ),
                app_text(name, size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT,
                         max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                ft.Container(width=56, content=app_text(
                    ext, size=10, color=TEXT_MUTED, font_family=mono_family("light"))),
                ft.Container(width=72, content=app_text(
                    _fmt_size(size), size=10, color=TEXT_MUTED, font_family=mono_family("light"))),
                ft.Container(width=110, content=app_text(
                    datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                    size=10, color=TEXT_DIM, font_family=mono_family("light"))),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    # ── cola activa ────────────────────────────────────────────────────

    def _rebuild_queue(self, items: list[dict]) -> None:
        self._item_widgets = []
        rows: list[ft.Control] = []
        for item in items:
            bar = ft.ProgressBar(value=0, color=ACCENT, bgcolor=BG_LIST, height=4, border_radius=2)
            pct = app_text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"))
            icon = app_icon(PIcons.CLOCK, size=18, color=TEXT_DIM)
            rows.append(ft.Container(
                bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_MUTED),
                border_radius=RADIUS_INPUT,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                content=ft.Row(controls=[
                    icon,
                    ft.Column(controls=[
                        app_text(item["title"], size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT,
                                 max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        bar,
                    ], spacing=6, expand=True),
                    ft.Container(width=48, alignment=ft.Alignment.CENTER_RIGHT, content=pct),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ))
            self._item_widgets.append((item, bar, pct, icon))
        self._queue_list.controls = rows

    def _sync_queue(self) -> None:
        for item, bar, pct, icon in self._item_widgets:
            status = item["status"]
            if status == "done":
                bar.value, bar.color = 1, SUCCESS
                pct.value = "100%"
                icon.value, icon.color = PIcons.CHECK_CIRCLE, SUCCESS
                icon.font_family = FONT_PHOSPHOR_FILL
            elif status == "failed":
                bar.value, bar.color = 0, ERROR_COL
                pct.value = item["error"][:12] if item["error"] == "Cancelada" else "Error"
                icon.value, icon.color = PIcons.WARNING_CIRCLE, ERROR_COL
                icon.font_family = FONT_PHOSPHOR_FILL
            elif status == "skipped":
                # Ya existía en disco; no se reescribió.
                bar.value, bar.color = 1, TEXT_MUTED
                pct.value = "Omitida"
                icon.value, icon.color = PIcons.SKIP_FORWARD, TEXT_MUTED
                icon.font_family = FONT_PHOSPHOR
            elif status == "running":
                bar.color = ACCENT
                # indeterminado hasta que yt-dlp reporte bytes
                bar.value = (item["percent"] / 100) if item["percent"] > 0 else None
                pct.value = f"{item['percent']:.0f}%" if item["percent"] > 0 else "…"
                icon.value, icon.color = PIcons.DOWNLOAD_SIMPLE, ACCENT
                icon.font_family = FONT_PHOSPHOR
            else:
                bar.value, pct.value = 0, ""
                icon.value, icon.color = PIcons.CLOCK, TEXT_DIM
                icon.font_family = FONT_PHOSPHOR

    # ── refresco ───────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Recarga preferencias externas (formato por defecto desde Config)."""
        self._format_dd.value = settings_store.get_download_format()
        self._refresh()

    def _refresh(self) -> None:
        st = self.app_state
        state = st.download_state
        running = state == TransferState.RUNNING
        total = st.download_total
        progress = st.download_progress
        done = st.download_done
        failed = st.download_failed

        ffmpeg_ok = ffmpeg_available()
        self._ffmpeg_banner.visible = not ffmpeg_ok
        self._format_dd.disabled = running
        self._start_btn.disabled = running or st.selected_count == 0 or not ffmpeg_ok
        self._start_btn.tooltip = (
            None if ffmpeg_ok else "Instala FFmpeg y añádelo al PATH para descargar"
        )
        paused = bool(getattr(st, "download_paused", False))
        self._pause_btn.disabled = not running and not paused
        self._pause_btn.tooltip = (
            "Reanudar las pistas pendientes" if paused else "Pausar la cola y la pista en curso"
        )
        try:
            self._pause_btn.content.controls[1].value = "Reanudar" if paused else "Pausar"
        except Exception:
            pass
        self._cancel_btn.disabled = not running or self._cancel_requested
        self._pick_dir_btn.disabled = running
        self._reset_dir_btn.disabled = running
        self._reset_dir_btn.visible = bool(self._custom_dir)
        self._count_text.value = f"{st.selected_count} pistas seleccionadas"
        self._dir_text.value = self._dir

        indexed = (
            self.library_state is not None
            and str(Path(self._dir).expanduser()) in self.library_state.local_folders
        )
        indexed_text = "Indexada en Biblioteca" if indexed else "Indexar a Biblioteca"
        indexed_glyph = PIcons.CHECK if indexed else PIcons.PLUS
        self._index_btn.content = ft.Row(
            [app_icon(indexed_glyph, size=18, color=TEXT_MUTED if not indexed else SUCCESS),
             ft.Text(indexed_text, opacity=1.0, font_family=FONT_HEADLINE)],
            alignment=ft.MainAxisAlignment.CENTER,
            tight=True,
            spacing=8,
        )
        self._index_btn.disabled = indexed or running

        self._progress_area.visible = running or (state != TransferState.IDLE and total > 0)
        self._progress.value = (progress / total) if total else 0
        self._progress_text.value = f"{progress} / {total}" if total else ""

        if paused and not running:
            pending = len(getattr(st, "_download_pending_tracks", []) or [])
            self._current_text.value = (
                f"En pausa · {pending} pendientes" if pending else "En pausa"
            )
        elif running and self._cancel_requested:
            self._current_text.value = "Cancelando…"
        elif running:
            self._current_text.value = (
                f"Descargando: {st.download_current}" if st.download_current else "Preparando…"
            )
        elif state == TransferState.DONE:
            cancelled = total > 0 and progress < total
            omitted = len(getattr(st, "download_skipped", []) or [])
            tail = (
                f" · OK {len(done)} · Fallidas {len(failed)}"
                + (f" · Omitidas {omitted}" if omitted else "")
            )
            self._current_text.value = ("Cancelado" if cancelled else "Completado") + tail
        elif state == TransferState.ERROR:
            self._current_text.value = "Descargas interrumpidas"
        else:
            self._current_text.value = ""
        if not running:
            self._cancel_requested = False

        # estado A (cola) vs estado B (historial)
        self._queue_section.visible = running
        self._history_section.visible = not running
        items = st.download_items
        if running:
            if id(items) != self._items_key:
                self._items_key = id(items)
                self._rebuild_queue(items)
            self._sync_queue()

        # Panel de resultados de la última tanda: exitosas, omitidas, fallidas
        # y faltantes, cada una con formato, duración, ruta y motivo.
        skipped = list(getattr(st, "download_skipped", []) or [])
        missing = list(getattr(st, "download_missing", []) or [])
        signature = (len(done), len(skipped), len(failed), len(missing))
        if not running and signature != self._results_sig:
            self._results_sig = signature
            self._rebuild_results(done, skipped, failed, missing)

        # recarga el historial al terminar una cola o al cambiar de carpeta
        if (self._was_running and not running) or (not running and self._history_dir != self._dir):
            try:
                self._page_ref.run_task(self._load_history)
            except Exception:
                pass
        self._was_running = running

        try:
            self.update()
        except Exception:
            pass

    # ── panel de resultados ────────────────────────────────────────────

    def _rebuild_results(
        self,
        done: list[dict],
        skipped: list[dict],
        failed: list[dict],
        missing: list[dict],
    ) -> None:
        """Repinta el panel de la última tanda con sus cuatro grupos.

        Cada grupo lleva su encabezado solo si tiene entradas: una cola de 40
        pistas correctas no debe pagar el ruido de tres títulos vacíos.
        """
        self._results_panel.visible = bool(done or skipped or failed or missing)
        if not self._results_panel.visible:
            return

        counts = [f"{len(done)} descargadas"]
        if skipped:
            counts.append(f"{len(skipped)} omitidas")
        if failed:
            counts.append(f"{len(failed)} fallidas")
        if missing:
            counts.append(f"{len(missing)} faltantes")
        self._result_counts.value = " · ".join(counts)

        # Omitidas primero: son las que el usuario ya tenía y no se tocaron.
        finished = [(d, "skipped") for d in skipped] + [(d, "done") for d in done]
        self._done_col.controls = (
            [self._group_label("DESCARGADAS", len(finished))] if finished else []
        ) + [
            self._result_row(
                title=_entry_title(d),
                meta=_entry_meta(d),
                detail=_fmt_path(str(d.get("path", "") or "")),
                kind=kind,
                on_play=(
                    (lambda _e, p=str(d.get("path", "")): self._play_path(p))
                    if d.get("path") else None
                ),
            )
            for d, kind in finished
        ]

        self._failed_col.controls = (
            [self._group_label("FALLIDAS", len(failed))] if failed else []
        ) + [
            self._result_row(
                title=_entry_title(f),
                meta=_entry_meta(f),
                detail=str(f.get("error") or "Error desconocido"),
                kind="failed",
                entry=f,
            )
            for f in failed
        ]

        self._missing_col.controls = (
            [self._group_label("FALTANTES", len(missing))] if missing else []
        ) + [
            self._result_row(
                title=_entry_title(m),
                meta=_entry_meta(m),
                detail=str(m.get("error") or "No se intentó"),
                kind="missing",
                entry=m,
            )
            for m in missing
        ]

    @staticmethod
    def _group_label(text: str, count: int) -> ft.Control:
        return ft.Row(controls=[
            section_label(text),
            app_text(str(count), size=9, color=TEXT_MUTED, font_family=mono_family("light")),
        ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _result_row(
        self,
        *,
        title: str,
        meta: str,
        detail: str,
        kind: str = "done",
        on_play=None,
        entry: dict | None = None,
    ) -> ft.Container:
        """Fila del panel de resultados.

        ``kind`` es ``done``, ``skipped``, ``failed`` o ``missing``. Las dos
        últimas muestran acciones para rebuscar la pista o reintentar la
        descarga con otro formato.
        """
        skipped = kind == "skipped"
        ok = kind in ("done", "skipped")
        glyph = (
            PIcons.SKIP_FORWARD if skipped
            else PIcons.CHECK_CIRCLE if ok
            else PIcons.WARNING_CIRCLE
        )
        lead_color = TEXT_MUTED if skipped else SUCCESS if ok else ERROR_COL
        lead = (
            app_icon_button(
                PIcons.PLAY,
                size=16,
                icon_color=ACCENT,
                hover_color_icon=ACCENT,
                tooltip="Reproducir archivo descargado",
                on_click=on_play,
                width=28,
                height=28,
            )
            if ok and on_play is not None
            else app_icon(glyph, size=18, color=lead_color, fill=ok)
        )
        actions: list[ft.Control] = []
        if entry is not None:
            actions.append(app_icon_button(
                PIcons.MAGNIFYING_GLASS, icon_color=TEXT_MUTED,
                hover_color_icon=ACCENT, size=16, tooltip="Rebuscar en YouTube",
                on_click=lambda _e, e=entry: self._page_ref.run_task(self._research, e),
                width=28, height=28,
            ))
            # Un solo botón: reintenta igual o prueba otro contenedor.
            actions.append(ft.PopupMenuButton(
                content=app_icon(PIcons.ARROWS_CLOCKWISE, size=16, color=TEXT_MUTED),
                tooltip="Reintentar · probar otro formato",
                items=[
                    ft.PopupMenuItem(
                        content=ft.Text("Reintentar igual", font_family=FONT_TEXT, size=12),
                        on_click=lambda _e, e=entry: self._retry([e]),
                    ),
                    *[
                        ft.PopupMenuItem(
                            content=ft.Text(f"Como {f.upper()}", font_family=FONT_TEXT,
                                            size=12),
                            on_click=lambda _e, e=entry, fmt=f: self._retry([e], audio_format=fmt),
                        )
                        for f in self._alt_formats(entry)
                    ],
                ],
            ))
        return ft.Container(
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            content=ft.Row(controls=[
                lead,
                ft.Column(controls=[
                    app_text(title, size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT,
                             max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    app_text(detail, size=10, color=TEXT_MUTED, font_family=FONT_TEXT,
                             max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=1, expand=True),
                ft.Container(width=118, content=app_text(
                    meta, size=10, color=TEXT_DIM, font_family=mono_family("light"),
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)),
                *actions,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    @staticmethod
    def _alt_formats(entry: dict) -> list[str]:
        """Formatos alternativos al de la entrada, sin repetirlo."""
        current = str(entry.get("format") or "").lower()
        return [f for f in AUDIO_FORMATS if f != current]

    def _retry(self, entries: list[dict], audio_format: str = "") -> None:
        """Reintenta las entradas con el formato actual o uno alternativo."""
        try:
            task = self.app_state.retry_downloads(entries, audio_format=audio_format)
        except Exception as exc:  # noqa: BLE001 - la UI no debe caerse
            notify(self._page_ref, f"No se pudo reintentar: {exc}", kind="error")
            return
        if task is None:
            notify(self._page_ref, "No hay pistas para reintentar", kind="error")
            return
        self._refresh()

    async def _research(self, entry: dict) -> None:
        """Rebusca la pista y muestra los candidatos con su duración."""
        try:
            found = await self.app_state.research_download(entry)
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo rebuscar: {exc}", kind="error")
            return
        await self._show_candidates(entry, found)

    async def _show_candidates(self, entry: dict, found: dict) -> None:
        """Diálogo con los candidatos encontrados y descarga del elegido."""
        import asyncio

        candidates = found.get("candidates") or []
        fmt = found.get("format") or ""
        title = _entry_title(entry)

        if not candidates:
            await self._ask(
                "Sin coincidencias",
                f"{title}\n\n{found.get('error') or 'YouTube no devolvió resultados'}",
                detail=f"Consulta: {found.get('query') or '—'}",
            )
            return

        dlg = app_dialog(
            "Rebuscar en YouTube",
            ft.Column(controls=[], spacing=10, tight=True),
            [],
            width=560, icon=PIcons.MAGNIFYING_GLASS,
        )
        body = dlg.content.content

        def _close() -> None:
            dlg.open = False
            try:
                self._page_ref.update()
            except Exception:
                pass

        def _pick(video_id: str, chosen_fmt: str) -> None:
            track = found.get("track")
            try:
                self.app_state.download_candidate(track, video_id, audio_format=chosen_fmt)
            except Exception as exc:  # noqa: BLE001
                notify(self._page_ref, f"No se pudo descargar: {exc}", kind="error")
            _close()
            self._refresh()

        rows = ft.Column(spacing=6)
        for cand in candidates[:5]:
            used = str(cand.get("video_id") or "") == str(entry.get("video_id") or "")
            rows.controls.append(ft.Container(
                bgcolor=BG_SURFACE,
                border=ft.Border.all(0.8, BORDER_MUTED),
                border_radius=RADIUS_INPUT,
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                content=ft.Row(controls=[
                    ft.Column(controls=[
                        app_text(cand.get("title", ""), size=12, color=TEXT_PRIMARY,
                                 font_family=FONT_TEXT, max_lines=1,
                                 overflow=ft.TextOverflow.ELLIPSIS),
                        app_text(
                            f"{cand.get('channel') or '—'} · "
                            f"{_fmt_dur(int(cand.get('duration_s') or 0) * 1000)} · "
                            f"{cand.get('score')} pts"
                            + (" · ya se intentó" if used else ""),
                            size=10,
                            color=ERROR_COL if used else TEXT_MUTED,
                            font_family=mono_family("light"),
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ], spacing=1, expand=True),
                    _ghost_btn(
                        "Usar", PIcons.DOWNLOAD_SIMPLE,
                        lambda _e, v=str(cand.get("video_id") or ""), f=fmt: _pick(v, f),
                        width=88, height=30,
                    ),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ))

        body.controls = [
            app_text(title, size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT),
            app_text(
                f"Consulta: {found.get('query') or '—'} · formato {fmt.upper() or 'actual'}",
                size=10, color=TEXT_MUTED, font_family=mono_family("light"),
            ),
            rows,
        ]
        dlg.actions = [dialog_action("Cerrar", lambda _e: _close(), kind="muted")]
        self._page_ref.show_dialog(dlg)
        await asyncio.sleep(0)

    async def _ask(self, title: str, message: str, *, detail: str = "") -> None:
        """Diálogo informativo de una sola acción."""
        import asyncio

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def _close(_e=None) -> None:
            if not future.done():
                future.set_result(True)
            dlg.open = False
            try:
                self._page_ref.update()
            except Exception:
                pass

        controls: list[ft.Control] = [
            app_text(message, size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT),
        ]
        if detail:
            controls.append(app_text(detail, size=10, color=TEXT_MUTED,
                                     font_family=mono_family("light")))
        dlg = app_dialog(
            title, ft.Column(controls=controls, spacing=6, tight=True),
            [dialog_action("Aceptar", _close, kind="primary")],
            width=420, icon=PIcons.INFO,
        )
        self._page_ref.show_dialog(dlg)
        await future
