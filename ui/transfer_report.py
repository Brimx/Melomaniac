"""ui/transfer_report.py — Reporte de cierre y búsqueda de alternativas.

Se abre siempre al terminar una transferencia (DONE o ERROR). Reutiliza
los controles canónicos del proyecto: ``app_dialog``/``dialog_action``,
``app_text_field``, ``build_preview_row`` y ``_status_icon``.

Flujo:
    1. Cabecera con nombre, descripción, confirmadas/tiempo/faltantes.
    2. Incidencias agrupadas por motivo en ``ExpansionPanelList``.
    3. "Buscar" abre una consulta editable y muestra hasta 5 candidatos.
    4. "Insertar" añade la alternativa a la playlist recién creada y deja
       un evento ``resolution`` en el historial JSONL.
"""

from __future__ import annotations

import webbrowser
from datetime import datetime

import flet as ft

from core.models import Track
from core.transfer import (
    KIND_DEFAULT_REASON,
    KIND_LABELS,
    TransferIssue,
    TransferReport,
    append_resolution_log,
    format_elapsed,
    playlist_url,
    report_to_text,
)
from engine.match import _fuzzy_scores_triple
from engine.normalizer import build_search_query, clean_metadata
from services.authentication import TRANSFER_HISTORY_JSONL, TRANSFER_REPORTS_DIR
from ui.fonts import (
    FONT_HEADLINE,
    FONT_HEADLINE_BOLD,
    FONT_HEADLINE_SEMI,
    FONT_TEXT,
    mono_family,
)
from ui.song_row import build_preview_row
from ui.tokens import (
    ACCENT,
    BG_INPUT,
    BG_PANEL,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    BORDER_ROW,
    ERROR_COL,
    SUCCESS,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARN_BG,
    WARN_BORDER,
    WARNING,
)
from ui.widgets import (
    DialogMixin,
    _primary_btn,
    _section_label,
    _status_icon,
    app_dialog,
    app_text,
    app_text_field,
    dialog_action,
    notify,
)

_KIND_ORDER = ("not_found", "revision", "api_rejected", "error")
_KIND_ICON_STATUS = {
    "not_found": "not_found",
    "revision": "revision_necesaria",
    "api_rejected": "error",
    "error": "error",
}


class TransferReportDialog(DialogMixin):
    """AlertDialog de cierre con resumen, incidencias y búsqueda manual."""

    def __init__(self, page: ft.Page, state, report: TransferReport) -> None:
        self.page = page
        self.state = state
        self.report = report
        self._dlg: ft.AlertDialog | None = None
        self._body = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
        self._view = "report"
        self._active_issue: TransferIssue | None = None
        self._query: ft.TextField | None = None
        self._search_btn: ft.Button | None = None
        self._spinner: ft.ProgressRing | None = None
        self._status: ft.Text | None = None
        self._results = ft.Column(spacing=2)
        self._searching = False
        self._last_query = ""

    # ── API pública ────────────────────────────────────────────────

    def show(self) -> None:
        self._render_report()
        actions: list[ft.Control] = [
            dialog_action(
                "Exportar", self._on_export, kind="muted",
                icon=ft.Icons.DOWNLOAD_OUTLINED,
            ),
        ]
        if self.report.playlist_id:
            actions.append(dialog_action(
                "Abrir playlist", self._on_open_playlist, kind="muted",
                icon=ft.Icons.OPEN_IN_NEW,
            ))
        actions.append(dialog_action("Cerrar", self._on_close, kind="primary"))
        self._dlg = app_dialog(
            "Reporte de transferencia",
            ft.Container(
                content=self._body,
                height=self._content_height(),
            ),
            actions,
            width=680,
            icon=ft.Icons.FACT_CHECK_OUTLINED,
        )
        self.page.show_dialog(self._dlg)

    # ── Vistas ─────────────────────────────────────────────────────

    def _render_report(self) -> None:
        self._view = "report"
        self._active_issue = None
        controls: list[ft.Control] = [
            self._build_header(),
            self._build_stats(),
        ]
        if self.report.status == "ERROR":
            controls.append(self._build_error_banner())
        missing = self.report.missing
        if missing:
            controls.append(_section_label(f"FALTANTES · {len(missing)}"))
            controls.append(self._build_sections(missing))
        elif self.report.confirmed < self.report.detected:
            controls.append(self._build_warning_banner(
                f"No se registraron incidencias, pero faltan "
                f"{self.report.detected - self.report.confirmed} confirmaciones."
            ))
        else:
            controls.append(self._build_success_banner())
        self._body.controls = controls
        self._refresh()

    def _show_search(self, issue: TransferIssue) -> None:
        if not self.report.playlist_id:
            notify(
                self.page,
                "No hay playlist de destino: la transferencia no llegó a crearla.",
                kind="error",
            )
            return
        self._view = "search"
        self._active_issue = issue
        track = issue.track
        clean_title, clean_artist = clean_metadata(track.name, track.artist)
        self._last_query = build_search_query(clean_title, clean_artist)
        self._query = app_text_field(
            value=self._last_query,
            label=f"Buscar en {self.report.destination}",
            hint_text="Título, artista o variante con feat",
            on_submit=lambda _e: self.page.run_task(self._run_search),
            autofocus=True,
        )
        self._search_btn = _primary_btn(
            "Buscar", ft.Icons.SEARCH,
            lambda _e: self.page.run_task(self._run_search),
            height=38,
        )
        self._spinner = ft.ProgressRing(
            width=16, height=16, stroke_width=2, color=ACCENT, visible=False,
        )
        self._status = app_text("", size=10, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._results = ft.Column(spacing=2)
        self._body.controls = [
            ft.Row([
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    icon_color=TEXT_MUTED,
                    on_click=lambda _e: self._render_report(),
                ),
                app_text("Volver al reporte", size=11, color=TEXT_MUTED),
            ], spacing=2),
            self._build_original_card(issue),
            self._query,
            ft.Row([
                self._search_btn,
                self._spinner,
                ft.Container(expand=True),
                self._status,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Divider(height=1, color=BORDER_MUTED),
            self._results,
        ]
        self._refresh()

    # ── Cabecera y resumen ─────────────────────────────────────────

    def _build_header(self) -> ft.Control:
        description = (self.report.description or "").strip() or "Sin descripción"
        return ft.Container(
            content=ft.Column([
                app_text(
                    self.report.title or "Playlist sin nombre",
                    size=16, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD,
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                ),
                app_text(
                    description, size=10, color=TEXT_MUTED, font_family=FONT_TEXT,
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                ),
                app_text(
                    f"{self.report.destination} · {self.report.status}",
                    size=9, color=TEXT_DIM, font_family=mono_family("light"),
                ),
            ], spacing=2, tight=True),
            padding=ft.Padding.all(10),
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.6, BORDER_LIGHT),
            border_radius=10,
        )

    def _build_stats(self) -> ft.Control:
        def _stat(label: str, value: str, color: str) -> ft.Control:
            return ft.Container(
                content=ft.Column([
                    app_text(
                        value, size=18, color=color,
                        font_family=FONT_HEADLINE_BOLD,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    app_text(
                        label, size=9, color=TEXT_MUTED,
                        font_family=FONT_HEADLINE,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ], spacing=0, tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                expand=True,
                padding=ft.Padding.symmetric(horizontal=8, vertical=8),
                bgcolor=BG_INPUT,
                border_radius=8,
            )

        missing = len(self.report.missing)
        return ft.Row([
            _stat("Confirmadas", str(self.report.confirmed), SUCCESS),
            _stat("Detectadas", str(self.report.detected), ACCENT),
            _stat("Tiempo", format_elapsed(self.report.elapsed_s), TEXT_PRIMARY),
            _stat("Faltantes", str(missing), WARNING if missing else SUCCESS),
        ], spacing=8)

    def _build_success_banner(self) -> ft.Control:
        return ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=SUCCESS, size=18),
                app_text(
                    "Todas las canciones quedaron confirmadas en la playlist.",
                    size=11, color=SUCCESS, font_family=FONT_TEXT, expand=True,
                ),
            ], spacing=8),
            padding=10,
            bgcolor=BG_SURFACE,
            border_radius=8,
        )

    def _build_warning_banner(self, message: str) -> ft.Control:
        return ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.ERROR_OUTLINE, color=WARNING, size=18),
                app_text(
                    message, size=10, color=WARNING,
                    font_family=FONT_TEXT, expand=True,
                ),
            ], spacing=8),
            padding=10,
            bgcolor=WARN_BG,
            border=ft.Border.all(0.6, WARN_BORDER),
            border_radius=8,
        )

    def _build_error_banner(self) -> ft.Control:
        return self._build_warning_banner(
            "El proceso se interrumpió. Se muestran las incidencias "
            "registradas hasta ese punto."
        )

    # ── Incidencias ────────────────────────────────────────────────

    def _build_sections(self, missing: list[TransferIssue]) -> ft.Control:
        panels: list[ft.ExpansionPanel] = []
        for kind in _KIND_ORDER:
            group = [issue for issue in missing if issue.kind == kind]
            if not group:
                continue
            header = ft.ListTile(
                leading=_status_icon(_KIND_ICON_STATUS[kind]),
                title=app_text(
                    f"{KIND_LABELS[kind]} ({len(group)})",
                    size=11, color=TEXT_PRIMARY,
                    font_family=FONT_HEADLINE_SEMI,
                ),
                dense=True,
            )
            panels.append(ft.ExpansionPanel(
                header=header,
                content=ft.Container(
                    content=ft.Column(
                        [self._build_issue_row(issue) for issue in group],
                        spacing=0, tight=True,
                    ),
                    padding=ft.Padding.only(left=6, right=6, bottom=6),
                ),
                bgcolor=BG_SURFACE,
                expanded=(kind == "not_found"),
                can_tap_header=True,
            ))
        return ft.ExpansionPanelList(
            controls=panels,
            elevation=0,
            divider_color=BORDER_LIGHT,
            expand_icon_color=TEXT_MUTED,
            spacing=6,
        )

    def _build_issue_row(self, issue: TransferIssue) -> ft.Control:
        track = issue.track
        reason = issue.reason or KIND_DEFAULT_REASON.get(issue.kind, "")
        return ft.Container(
            content=ft.Row([
                _status_icon(_KIND_ICON_STATUS[issue.kind]),
                ft.Column([
                    app_text(
                        track.name, size=12, color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_SEMI,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    app_text(
                        f"{track.artist} · {reason}",
                        size=9, color=TEXT_MUTED, font_family=FONT_TEXT,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ], spacing=0, tight=True, expand=True),
                dialog_action(
                    "Buscar",
                    lambda _e, i=issue: self._show_search(i),
                    kind="primary",
                    icon=ft.Icons.SEARCH,
                ),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            border=ft.Border.only(bottom=ft.BorderSide(0.4, BORDER_ROW)),
        )

    def _build_original_card(self, issue: TransferIssue) -> ft.Control:
        track = issue.track
        reason = issue.reason or KIND_DEFAULT_REASON.get(issue.kind, "")
        return ft.Container(
            content=ft.Column([
                _section_label("ORIGINAL"),
                app_text(
                    f"{track.name} — {track.artist}",
                    size=11, color=TEXT_PRIMARY,
                    font_family=FONT_HEADLINE_SEMI,
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                ),
                app_text(
                    f"Motivo: {reason}",
                    size=9, color=WARNING, font_family=FONT_TEXT,
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ], spacing=2, tight=True),
            padding=8,
            bgcolor=BG_INPUT,
            border_radius=8,
        )

    # ── Búsqueda e inserción ───────────────────────────────────────

    async def _run_search(self) -> None:
        if self._searching or self._active_issue is None:
            return
        assert self._query is not None
        assert self._search_btn is not None
        assert self._spinner is not None
        assert self._status is not None
        query = (self._query.value or "").strip()
        if not query:
            self._status.value = "Escribe una búsqueda."
            self._status.color = WARNING
            self._refresh()
            return
        self._searching = True
        self._search_btn.disabled = True
        self._spinner.visible = True
        self._status.value = "Buscando…"
        self._status.color = TEXT_MUTED
        self._refresh()
        try:
            tracks = await self.state.service.search_candidates(
                self.report.destination, query, 5
            )
            self._render_results(tracks, query)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._results.controls.clear()
            self._status.value = f"Error: {exc}"[:160]
            self._status.color = ERROR_COL
        finally:
            self._searching = False
            self._search_btn.disabled = False
            self._spinner.visible = False
            self._refresh()

    def _render_results(self, tracks: list[Track], query: str) -> None:
        assert self._status is not None
        assert self._active_issue is not None
        self._results.controls.clear()
        self._last_query = query
        if not tracks:
            self._status.value = (
                "Sin resultados. Prueba con el título solo o sin el feat."
            )
            self._status.color = TEXT_MUTED
            return
        self._status.value = f"{len(tracks)} resultado(s)"
        self._status.color = TEXT_MUTED
        original = self._active_issue.track
        for index, track in enumerate(tracks, 1):
            score = 0
            try:
                score, _, _ = _fuzzy_scores_triple(
                    original.name, original.artist, track.name, track.artist,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                score = 0
            preview = build_preview_row(track, index)
            preview.bgcolor = BG_PANEL
            insert = _primary_btn(
                "Insertar", ft.Icons.ADD,
                lambda _e, t=track: self.page.run_task(self._insert, t),
                width=124, height=36,
            )
            self._results.controls.append(ft.Row([
                ft.Container(content=preview, expand=True),
                app_text(
                    f"{int(score)}%", size=10,
                    color=ACCENT if score >= 70 else TEXT_MUTED,
                    font_family=mono_family(),
                ),
                insert,
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    async def _insert(self, track: Track) -> None:
        issue = self._active_issue
        if issue is None or not self.report.playlist_id:
            return
        if self._status is not None:
            self._status.value = f"Insertando {track.name[:40]}…"
            self._status.color = TEXT_MUTED
        if self._search_btn is not None:
            self._search_btn.disabled = True
        self._refresh()
        try:
            ok, message = await self.state.service.add_track(
                self.report.destination, self.report.playlist_id, track.id
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            ok, message = False, str(exc)
        finally:
            if self._search_btn is not None:
                self._search_btn.disabled = False
        if not ok:
            if self._status is not None:
                self._status.value = f"No se pudo insertar: {message}"[:160]
                self._status.color = ERROR_COL
            self._refresh()
            return
        issue.resolved = True
        self.report.confirmed = min(
            self.report.confirmed + 1, self.report.detected
        )
        try:
            self.state.service.remember_resolution(
                issue.track.name, issue.track.artist,
                self.report.destination, track.id, isrc=track.isrc,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.state.log(f"[WARN] No se pudo actualizar la caché: {exc}")
        try:
            append_resolution_log(
                self.report, issue, track, TRANSFER_HISTORY_JSONL,
                query=self._last_query,
            )
        except OSError as exc:
            self.state.log(f"[WARN] No se pudo registrar la resolución: {exc}")
        self.state.log(
            f"[SUCCESS] Alternativa añadida · "
            f"{issue.track.name[:36]} → {track.name[:36]}"
        )
        notify(self.page, f"Añadida a la playlist: {track.name}")
        self._render_report()
        try:
            self.state.notify()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # ── Acciones ───────────────────────────────────────────────────

    def _on_export(self, _e=None) -> None:
        try:
            TRANSFER_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = TRANSFER_REPORTS_DIR / f"transfer_report_{stamp}.txt"
            path.write_text(report_to_text(self.report), encoding="utf-8")
            notify(self.page, f"Reporte guardado en {path}")
        except OSError as exc:
            notify(self.page, f"No se pudo exportar: {exc}", kind="error")

    def _on_open_playlist(self, _e=None) -> None:
        url = playlist_url(self.report.destination, self.report.playlist_id)
        if not url:
            notify(self.page, "No hay URL disponible para esta playlist.", kind="error")
            return
        try:
            webbrowser.open(url)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            notify(self.page, f"No se pudo abrir el navegador: {exc}", kind="error")

    def _on_close(self, _e=None) -> None:
        self.close_dialog(self.page, self._dlg)

    # ── Utilidades ─────────────────────────────────────────────────

    def _content_height(self) -> int:
        return min(540, max(300, int((self.page.height or 720) - 260)))

    def _refresh(self) -> None:
        try:
            self._body.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        try:
            self.page.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
