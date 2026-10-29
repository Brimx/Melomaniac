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
from engine.downloader import open_in_file_manager, resolve_download_dir
from engine.parsers import list_audio_files
from services import settings_store
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.tokens import (
    ACCENT, BG_LIST, BG_SURFACE, BORDER_MUTED, ERROR_COL, RADIUS_INPUT,
    SUCCESS, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import (
    _ghost_btn, _primary_btn, app_text, notify, organize_dropdown, section_label,
)

_HISTORY_LIMIT = 500


def _fmt_size(num: int) -> str:
    return f"{num / (1024 * 1024):.1f} MB"


class DownloadsView(ft.Container):
    """Cola de descargas de audio con destino configurable e historial.

    Args:
        page: Página Flet.
        app_state: ``AppState`` con ``download_*`` y ``download_selected()``.
        library_state: ``LibraryState`` opcional; habilita «Indexar a Biblioteca».
    """

    def __init__(self, page: ft.Page, app_state, library_state=None) -> None:
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self.app_state = app_state
        self.library_state = library_state
        self._cancel_requested = False
        self._was_running = False
        self._items_key: int = -1
        self._item_widgets: list[tuple[dict, ft.ProgressBar, ft.Text, ft.Icon]] = []
        self._history_dir = ""
        self._failed_sig = -1
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
            DEFAULT_AUDIO_FORMAT,
            "Formato",
            width=140,
        )
        self._dir_text = app_text("", size=11, color=TEXT_PRIMARY, font_family=mono_family("light"),
                                  max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self._count_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)

        self._start_btn = _primary_btn(
            "Descargar seleccionadas", ft.Icons.DOWNLOAD, self._on_start, width=220
        )
        self._start_btn.disabled = True
        self._cancel_btn = _ghost_btn(
            "Cancelar", ft.Icons.STOP_CIRCLE_OUTLINED, self._on_cancel,
            width=120, disabled=True,
        )

        self._open_dir_btn = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN, icon_color=TEXT_MUTED, icon_size=18,
            tooltip="Abrir carpeta en el explorador", on_click=self._on_open_dir,
        )
        self._pick_dir_btn = ft.IconButton(
            icon=ft.Icons.EDIT_OUTLINED, icon_color=TEXT_MUTED, icon_size=18,
            tooltip="Cambiar carpeta de descargas",
            on_click=lambda e: self._page_ref.run_task(self._pick_dir),
        )
        self._reset_dir_btn = ft.IconButton(
            icon=ft.Icons.RESTART_ALT, icon_color=TEXT_MUTED, icon_size=18,
            tooltip="Restablecer carpeta por defecto", on_click=self._on_reset_dir,
        )
        destination = ft.Container(
            bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT, padding=ft.Padding.symmetric(horizontal=12, vertical=4),
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.FOLDER_OUTLINED, size=18, color=TEXT_MUTED),
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
        self._index_btn = _ghost_btn(
            "Indexar a Biblioteca", ft.Icons.LIBRARY_ADD_OUTLINED, self._on_index, height=36,
        )
        self._index_btn.visible = self.library_state is not None
        self._history_list = ft.ListView(expand=True, spacing=0, padding=0)
        self._history_empty = ft.Container(
            expand=True, alignment=ft.Alignment.CENTER,
            content=ft.Column(controls=[
                ft.Icon(ft.Icons.DOWNLOAD_OUTLINED, size=48, color=TEXT_DIM),
                app_text("Sin descargas todavía", size=13, color=TEXT_PRIMARY,
                         font_family=FONT_HEADLINE_BOLD),
                app_text("Selecciona pistas en Gestión o descarga una playlist desde Biblioteca",
                         size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
                         text_align=ft.TextAlign.CENTER),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
        )
        self._failed_col = ft.Column(spacing=6)
        self._history_stack = ft.Stack(
            controls=[self._history_list, self._history_empty], expand=True,
            fit=ft.StackFit.EXPAND,
        )
        self._history_section = ft.Column(controls=[
            ft.Row(controls=[
                section_label("HISTORIAL EN LA CARPETA"),
                ft.Container(expand=True),
                self._index_btn,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self._failed_col,
            self._history_stack,
        ], spacing=8, expand=True)

        header = ft.Column(controls=[
            app_text("Descargas", size=18, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            app_text(
                "yt-dlp resuelve cada pista en YouTube y conserva solo el audio.",
                size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
            ),
        ], spacing=2)

        controls = ft.Row(controls=[
            self._format_dd,
            ft.Container(content=self._count_text, expand=True, padding=ft.Padding.only(left=6)),
            self._cancel_btn,
            self._start_btn,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        self.content = ft.Container(
            content=ft.Column(controls=[
                header,
                ft.Divider(height=1, color=BORDER_MUTED),
                controls,
                destination,
                self._progress_area,
                self._queue_section,
                self._history_section,
            ], spacing=12, expand=True),
            padding=ft.Padding.all(16), expand=True,
        )

    # ── acciones ───────────────────────────────────────────────────────

    def _on_start(self, e=None) -> None:
        fmt = (self._format_dd.value or DEFAULT_AUDIO_FORMAT).lower()
        self._cancel_requested = False
        try:
            self.app_state.download_selected(audio_format=fmt, destination_dir=self._custom_dir)
        except Exception as exc:  # noqa: BLE001 - la UI no debe caerse
            self.app_state.log(f"[ERROR] No se pudo iniciar la descarga: {exc}")
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

    # ── historial ──────────────────────────────────────────────────────

    async def _load_history(self) -> None:
        folder = self._dir
        self._history_dir = folder

        def _scan() -> list[tuple[str, str, int, float]]:
            rows = []
            for path in list_audio_files(folder, limit=_HISTORY_LIMIT):
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                name, ext = os.path.splitext(os.path.basename(path))
                rows.append((name, ext.lstrip(".").upper(), st.st_size, st.st_mtime))
            return rows

        rows = await asyncio.to_thread(_scan)
        if folder != self._dir:
            return  # el usuario cambió de carpeta mientras se escaneaba
        self._history_list.controls = [self._history_row(*r) for r in rows]
        self._history_empty.visible = not rows
        self._history_list.visible = bool(rows)
        try:
            self.update()
        except Exception:
            pass

    @staticmethod
    def _history_row(name: str, ext: str, size: int, mtime: float) -> ft.Container:
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_MUTED)),
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.AUDIOTRACK_OUTLINED, size=16, color=TEXT_MUTED),
                app_text(name, size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT,
                         max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                ft.Container(width=56, content=app_text(
                    ext, size=10, color=TEXT_MUTED, font_family=mono_family("light"))),
                ft.Container(width=72, content=app_text(
                    _fmt_size(size), size=10, color=TEXT_MUTED, font_family=mono_family("light"))),
                ft.Container(width=110, content=app_text(
                    datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                    size=10, color=TEXT_DIM, font_family=mono_family("light"))),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    # ── cola activa ────────────────────────────────────────────────────

    def _rebuild_queue(self, items: list[dict]) -> None:
        self._item_widgets = []
        rows: list[ft.Control] = []
        for item in items:
            bar = ft.ProgressBar(value=0, color=ACCENT, bgcolor=BG_LIST, height=4, border_radius=2)
            pct = app_text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"))
            icon = ft.Icon(ft.Icons.SCHEDULE, size=18, color=TEXT_DIM)
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
                icon.icon, icon.color = ft.Icons.CHECK_CIRCLE_OUTLINE, SUCCESS
            elif status == "failed":
                bar.value, bar.color = 0, ERROR_COL
                pct.value = item["error"][:12] if item["error"] == "Cancelada" else "Error"
                icon.icon, icon.color = ft.Icons.ERROR_OUTLINE, ERROR_COL
            elif status == "running":
                bar.color = ACCENT
                # indeterminado hasta que yt-dlp reporte bytes
                bar.value = (item["percent"] / 100) if item["percent"] > 0 else None
                pct.value = f"{item['percent']:.0f}%" if item["percent"] > 0 else "…"
                icon.icon, icon.color = ft.Icons.DOWNLOADING, ACCENT
            else:
                bar.value, pct.value = 0, ""
                icon.icon, icon.color = ft.Icons.SCHEDULE, TEXT_DIM

    # ── refresco ───────────────────────────────────────────────────────

    def _refresh(self) -> None:
        st = self.app_state
        state = st.download_state
        running = state == TransferState.RUNNING
        total = st.download_total
        progress = st.download_progress
        done = st.download_done
        failed = st.download_failed

        self._format_dd.disabled = running
        self._start_btn.disabled = running or st.selected_count == 0
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
        self._index_btn.content = ft.Text(
            "Indexada en Biblioteca" if indexed else "Indexar a Biblioteca",
            opacity=1.0, font_family=FONT_HEADLINE,
        )
        self._index_btn.icon = ft.Icons.CHECK if indexed else ft.Icons.LIBRARY_ADD_OUTLINED
        self._index_btn.disabled = indexed or running

        self._progress_area.visible = running or (state != TransferState.IDLE and total > 0)
        self._progress.value = (progress / total) if total else 0
        self._progress_text.value = f"{progress} / {total}" if total else ""

        if running and self._cancel_requested:
            self._current_text.value = "Cancelando…"
        elif running:
            self._current_text.value = (
                f"Descargando: {st.download_current}" if st.download_current else "Preparando…"
            )
        elif state == TransferState.DONE:
            cancelled = total > 0 and progress < total
            self._current_text.value = (
                f"Cancelado · OK {len(done)} · Fallidas {len(failed)}"
                if cancelled
                else f"Completado · OK {len(done)} · Fallidas {len(failed)}"
            )
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

        # fallidas de la última tanda sobre el historial
        if not running and len(failed) != self._failed_sig:
            self._failed_sig = len(failed)
            self._failed_col.controls = [
                self._result_row(
                    title=f"{f.get('artist', '')} — {f.get('name', '')}",
                    detail=f.get("error", "") or "Error desconocido",
                )
                for f in failed
            ]

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

    @staticmethod
    def _result_row(*, title: str, detail: str) -> ft.Container:
        return ft.Container(
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.ERROR_OUTLINE, size=18, color=ERROR_COL),
                ft.Column(controls=[
                    app_text(title, size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT,
                             max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    app_text(detail, size=10, color=TEXT_MUTED, font_family=FONT_TEXT,
                             max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=1, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )
