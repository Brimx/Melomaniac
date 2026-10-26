"""
ui/downloads_view.py — Módulo Descargas: cola de audio con yt-dlp
════════════════════════════════════════════════════════════════════
Vista pura sobre ``AppState``: elige formato, lanza la descarga de las
pistas seleccionadas y muestra progreso, cancelación y resultados.

Cada pista se resuelve como coincidencia en YouTube; nunca se descarga
desde la plataforma de origen y solo se conserva el audio.
"""

from __future__ import annotations

from pathlib import Path

import flet as ft

from core.config import AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT
from core.models import TransferState
from engine.downloader import resolve_download_dir
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT
from ui.tokens import (
    ACCENT, BG_LIST, BG_SURFACE, BORDER_MUTED, ERROR_COL, RADIUS_INPUT,
    SUCCESS, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, _primary_btn, app_text, organize_dropdown, section_label


class DownloadsView(ft.Container):
    """Cola de descargas de audio (fase 1 de Descargas).

    Args:
        page: Página Flet.
        app_state: ``AppState`` con ``download_*`` y ``download_selected()``.
    """

    def __init__(self, page: ft.Page, app_state) -> None:
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self.app_state = app_state
        self._rendered_results: tuple[int, int] = (-1, -1)
        self._cancel_requested = False
        try:
            self._default_dir = str(resolve_download_dir(create=False))
        except Exception:
            self._default_dir = ""
        self._build()
        self.app_state.subscribe(self._refresh)
        self._refresh()

    # ── construcción ───────────────────────────────────────────────────

    def _build(self) -> None:
        self._format_dd = organize_dropdown(
            [(fmt, fmt.upper()) for fmt in AUDIO_FORMATS],
            DEFAULT_AUDIO_FORMAT,
            "Formato",
            width=140,
        )
        self._dir_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
                                  max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self._count_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)

        self._start_btn = _primary_btn(
            "Descargar seleccionadas", ft.Icons.DOWNLOAD, self._on_start, width=220
        )
        self._cancel_btn = _ghost_btn(
            "Cancelar", ft.Icons.STOP_CIRCLE_OUTLINED, self._on_cancel,
            width=120, disabled=True,
        )

        self._progress = ft.ProgressBar(
            value=0, color=ACCENT, bgcolor=BG_SURFACE, height=6, border_radius=3,
        )
        self._progress_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._current_text = app_text("", size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT,
                                      max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                      expand=True)

        self._results = ft.ListView(expand=True, spacing=6, padding=0)
        self._results_empty = ft.Container(
            expand=True, alignment=ft.Alignment.CENTER,
            content=ft.Column(controls=[
                ft.Icon(ft.Icons.DOWNLOAD_OUTLINED, size=48, color=TEXT_DIM),
                app_text("Sin descargas todavía", size=13, color=TEXT_PRIMARY,
                         font_family=FONT_HEADLINE_BOLD),
                app_text("Selecciona pistas en Gestión y elige un formato",
                         size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
                         text_align=ft.TextAlign.CENTER),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
        )
        self._results_stack = ft.Stack(
            controls=[self._results, self._results_empty], expand=True,
            fit=ft.StackFit.EXPAND,
        )

        header = ft.Column(controls=[
            app_text("Descargas", size=18, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            app_text(
                "yt-dlp resuelve cada pista en YouTube y conserva solo el audio.",
                size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
            ),
        ], spacing=2)

        controls = ft.Row(controls=[
            self._format_dd,
            ft.Column(controls=[
                self._count_text,
                self._dir_text,
            ], spacing=2, expand=True),
            self._cancel_btn,
            self._start_btn,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        progress_area = ft.Column(controls=[
            self._progress,
            ft.Row(controls=[self._progress_text, self._current_text], spacing=10),
        ], spacing=6)

        self.content = ft.Container(
            content=ft.Column(controls=[
                header,
                ft.Divider(height=1, color=BORDER_MUTED),
                controls,
                progress_area,
                section_label("RESULTADOS"),
                self._results_stack,
            ], spacing=12, expand=True),
            padding=ft.Padding.all(16), expand=True,
        )

    # ── acciones ───────────────────────────────────────────────────────

    def _on_start(self, e=None) -> None:
        fmt = (self._format_dd.value or DEFAULT_AUDIO_FORMAT).lower()
        self._cancel_requested = False
        try:
            self.app_state.download_selected(audio_format=fmt)
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
        self._count_text.value = f"{st.selected_count} pistas seleccionadas"
        self._dir_text.value = f"Destino: {st.download_dir or self._default_dir}"

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

        signature = (len(done), len(failed))
        if signature != self._rendered_results:
            self._rendered_results = signature
            self._render_results(done, failed)
        self._results_empty.visible = not done and not failed

        try:
            self.update()
        except Exception:
            pass

    def _render_results(self, done: list[dict], failed: list[dict]) -> None:
        self._results.controls.clear()
        for item in done:
            name = Path(item.get("path") or "").name
            self._results.controls.append(
                self._result_row(
                    ok=True,
                    title=f"{item.get('artist', '')} — {item.get('name', '')}",
                    detail=name,
                )
            )
        for item in failed:
            self._results.controls.append(
                self._result_row(
                    ok=False,
                    title=f"{item.get('artist', '')} — {item.get('name', '')}",
                    detail=item.get("error", "") or "Error desconocido",
                )
            )

    @staticmethod
    def _result_row(*, ok: bool, title: str, detail: str) -> ft.Container:
        return ft.Container(
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            content=ft.Row(controls=[
                ft.Icon(
                    ft.Icons.CHECK_CIRCLE_OUTLINE if ok else ft.Icons.ERROR_OUTLINE,
                    size=18, color=SUCCESS if ok else ERROR_COL,
                ),
                ft.Column(controls=[
                    app_text(title, size=12, color=TEXT_PRIMARY, font_family=FONT_TEXT,
                             max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    app_text(detail, size=10, color=TEXT_MUTED, font_family=FONT_TEXT,
                             max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=1, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )
