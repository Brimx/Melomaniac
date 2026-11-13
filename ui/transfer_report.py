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

import inspect
import webbrowser
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Awaitable, Callable, Sequence

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
from ui.icons import PIcons, app_icon, app_icon_button
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


@dataclass
class RecoveryCandidate:
    """Candidato listo para renderizarse en ``AlternativeSearchPanel``."""

    track: Track
    score: float | None = None
    score_suffix: str = "%"
    note: str = ""
    used: bool = False
    payload: Any = None


def default_search_query(name: str, artist: str) -> str:
    """Construye la consulta inicial limpia para buscar alternativas."""
    clean_title, clean_artist = clean_metadata(str(name or ""), str(artist or ""))
    query = build_search_query(clean_title, clean_artist).strip()
    if query:
        return query
    return " ".join(part for part in (str(artist or "").strip(), str(name or "").strip()) if part)


def compute_match_score(original: Track, candidate: Track) -> float:
    """Calcula el puntaje fuzzy (0-100) entre la pista original y un candidato."""
    try:
        score, _, _ = _fuzzy_scores_triple(
            original.name, original.artist, candidate.name, candidate.artist,
        )
        return float(score)
    except Exception:  # pylint: disable=broad-exception-caught
        return 0.0


def track_from_entry(entry: dict | None) -> Track:
    """Reconstruye una copia fresca de ``Track`` desde una entrada de resultados."""
    data = entry if isinstance(entry, dict) else {}
    base = data.get("_track")
    if isinstance(base, Track):
        return replace(base)
    return Track(
        id=str(data.get("id") or ""),
        name=str(data.get("name") or ""),
        artist=str(data.get("artist") or ""),
        album=str(data.get("album") or ""),
        duration="",
        img_url="",
        platform=str(data.get("platform") or ""),
        duration_ms=int(data.get("duration_ms") or 0),
        isrc=data.get("isrc") or None,
        source_url=str(data.get("src_url") or data.get("source_url") or ""),
        source_path=str(data.get("path") or ""),
    )


def build_original_track_card(name: str, artist: str, reason: str = "") -> ft.Container:
    """Tarjeta resumen de la pista faltante/fallida antes de la búsqueda manual."""
    title_line = f"{name} — {artist}" if artist else (name or "Pista sin título")
    items: list[ft.Control] = [
        _section_label("ORIGINAL"),
        app_text(
            title_line,
            size=11, color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_SEMI,
            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
        ),
    ]
    if reason:
        items.append(app_text(
            f"Motivo: {reason}",
            size=9, color=WARNING, font_family=FONT_TEXT,
            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
        ))
    return ft.Container(
        content=ft.Column(items, spacing=2, tight=True),
        padding=8,
        bgcolor=BG_INPUT,
        border_radius=8,
    )


class AlternativeSearchPanel(ft.Column):
    """Panel reutilizable para buscar alternativas y recuperar pistas faltantes.

    Usado tanto por ``TransferReportDialog`` (transferencias entre plataformas)
    como por ``DownloadsView`` (recuperación de descargas fallidas/faltantes/omitidas).
    """

    def __init__(
        self,
        page: ft.Page,
        original: Track,
        *,
        reason: str = "",
        search_fn: Callable[
            [str],
            Awaitable[Sequence[Track | RecoveryCandidate] | tuple[Sequence[Track | RecoveryCandidate], str]],
        ],
        on_select: Callable[[RecoveryCandidate], Awaitable[None] | None],
        search_label: str = "Buscar",
        hint_text: str = "Título, artista o variante con feat",
        action_label: str = "Insertar",
        action_icon: str = PIcons.PLUS,
        empty_message: str = "Sin resultados. Prueba con el título solo o sin el feat.",
        initial_query: str | None = None,
        header_controls: Sequence[ft.Control] = (),
        query_trailing: Sequence[ft.Control] = (),
        action_trailing: Sequence[ft.Control] = (),
        on_refresh: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(spacing=10, tight=True)
        self._page_ref = page
        self.original = original
        self.reason = reason
        self._search_fn = search_fn
        self._on_select = on_select
        self._action_label = action_label
        self._action_icon = action_icon
        self._empty_message = empty_message
        self._on_refresh = on_refresh
        self.searching = False
        self.last_query = (
            initial_query if initial_query is not None
            else default_search_query(original.name, original.artist)
        )

        self.query_field = app_text_field(
            value=self.last_query,
            label=search_label,
            hint_text=hint_text,
            on_submit=lambda _e: self._page_ref.run_task(self.run_search),
            autofocus=True,
            expand=bool(query_trailing),
        )
        self.search_btn = _primary_btn(
            "Buscar", PIcons.MAGNIFYING_GLASS,
            lambda _e: self._page_ref.run_task(self.run_search),
            height=38,
        )
        self.spinner = ft.ProgressRing(
            width=16, height=16, stroke_width=2, color=ACCENT, visible=False,
        )
        self.status_text = app_text("", size=10, color=TEXT_MUTED, font_family=FONT_TEXT)
        self.results_column = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)

        query_ctrl: ft.Control = (
            ft.Row(
                [self.query_field, *query_trailing],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            if query_trailing
            else self.query_field
        )
        action_row = ft.Row(
            [
                self.search_btn,
                self.spinner,
                *action_trailing,
                ft.Container(expand=True),
                self.status_text,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.controls = [
            *header_controls,
            build_original_track_card(original.name, original.artist, reason),
            query_ctrl,
            action_row,
            ft.Divider(height=1, color=BORDER_MUTED),
            self.results_column,
        ]

    def set_status(self, message: str, *, kind: str = "muted") -> None:
        color = ERROR_COL if kind == "error" else WARNING if kind == "warning" else TEXT_MUTED
        self.status_text.value = str(message or "")[:160]
        self.status_text.color = color
        self.refresh()

    async def run_search(self) -> None:
        if self.searching:
            return
        query = (self.query_field.value or "").strip()
        if not query:
            self.set_status("Escribe una búsqueda.", kind="warning")
            return
        self.searching = True
        self.search_btn.disabled = True
        self.spinner.visible = True
        self.status_text.value = "Buscando…"
        self.status_text.color = TEXT_MUTED
        self.refresh()
        try:
            raw = await self._search_fn(query)
            if isinstance(raw, tuple) and len(raw) == 2:
                items, empty_msg = raw
            else:
                items, empty_msg = raw, ""
            self.render_results(items, query, empty_message=empty_msg)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.results_column.controls.clear()
            self.status_text.value = f"Error: {exc}"[:160]
            self.status_text.color = ERROR_COL
        finally:
            self.searching = False
            self.search_btn.disabled = False
            self.spinner.visible = False
            self.refresh()

    def render_results(
        self,
        raw_candidates: Sequence[Track | RecoveryCandidate],
        query: str,
        *,
        empty_message: str = "",
    ) -> None:
        self.results_column.controls.clear()
        self.last_query = query
        candidates = [
            item if isinstance(item, RecoveryCandidate) else RecoveryCandidate(track=item)
            for item in (raw_candidates or ())
        ]
        if not candidates:
            self.status_text.value = (empty_message or self._empty_message)[:160]
            self.status_text.color = TEXT_MUTED
            return
        self.status_text.value = f"{len(candidates)} resultado(s)"
        self.status_text.color = TEXT_MUTED
        for index, cand in enumerate(candidates, 1):
            score = (
                cand.score if cand.score is not None
                else compute_match_score(self.original, cand.track)
            )
            preview = build_preview_row(cand.track, index)
            preview.bgcolor = BG_PANEL
            action_btn = _primary_btn(
                self._action_label,
                self._action_icon,
                lambda _e, c=cand: self._trigger_select(c),
                width=124,
                height=36,
            )
            row_items: list[ft.Control] = [
                ft.Container(content=preview, expand=True),
            ]
            if cand.used:
                row_items.append(
                    app_text("Ya se intentó", size=9, color=ERROR_COL, font_family=FONT_TEXT)
                )
            elif cand.note:
                row_items.append(
                    app_text(cand.note, size=9, color=TEXT_MUTED, font_family=FONT_TEXT)
                )
            row_items.extend([
                app_text(
                    f"{int(score)}{cand.score_suffix}",
                    size=10,
                    color=ACCENT if score >= 70 else TEXT_MUTED,
                    font_family=mono_family(),
                ),
                action_btn,
            ])
            self.results_column.controls.append(ft.Row(
                row_items,
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ))

    def _trigger_select(self, cand: RecoveryCandidate) -> None:
        if inspect.iscoroutinefunction(self._on_select):
            self._page_ref.run_task(self._invoke_select_async, cand)
            return
        res = self._on_select(cand)
        if inspect.isawaitable(res):
            async def _await_res() -> None:
                await res
            self._page_ref.run_task(_await_res)

    async def _invoke_select_async(self, cand: RecoveryCandidate) -> None:
        self.search_btn.disabled = True
        self.refresh()
        try:
            await self._on_select(cand)  # type: ignore[misc]
        finally:
            self.search_btn.disabled = False
            self.refresh()

    def refresh(self) -> None:
        try:
            self.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        if self._on_refresh is not None:
            try:
                self._on_refresh()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        try:
            self._page_ref.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


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
        self._search_panel: AlternativeSearchPanel | None = None
        self._last_query = ""

    # ── API pública ────────────────────────────────────────────────

    def show(self) -> None:
        self._render_report()
        actions: list[ft.Control] = [
            dialog_action(
                "Exportar", self._on_export, kind="muted",
                icon=PIcons.DOWNLOAD_SIMPLE,
            ),
        ]
        if self.report.playlist_id:
            actions.append(dialog_action(
                "Abrir playlist", self._on_open_playlist, kind="muted",
                icon=PIcons.ARROW_SQUARE_OUT,
            ))
        actions.append(dialog_action("Cerrar", self._on_close, kind="primary"))
        self._dlg = app_dialog(
            "Reporte de transferencia",
            ft.Container(
                content=self._body,
                min_width=360,
                max_width=680,
                min_height=300,
                max_height=540,
            ),
            actions,
            width=None,
            icon=PIcons.CHECK_CIRCLE,
        )
        self.page.show_dialog(self._dlg)

    # ── Vistas ─────────────────────────────────────────────────────

    def _render_report(self) -> None:
        self._view = "report"
        self._active_issue = None
        self._search_panel = None
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
        reason = issue.reason or KIND_DEFAULT_REASON.get(issue.kind, "")
        back_bar = ft.Row([
            app_icon_button(
                PIcons.ARROW_LEFT,
                size=16,
                icon_color=TEXT_MUTED,
                tooltip="Volver",
                on_click=lambda _e: self._render_report(),
                width=28,
                height=28,
            ),
            app_text("Volver al reporte", size=11, color=TEXT_MUTED),
        ], spacing=2)

        async def _search(query: str) -> list[Track]:
            return await self.state.search_candidates(
                self.report.destination, query, 5
            )

        async def _pick(cand: RecoveryCandidate) -> None:
            if self._search_panel is not None:
                self._last_query = self._search_panel.last_query
            await self._insert(cand.track)

        self._search_panel = AlternativeSearchPanel(
            self.page,
            track,
            reason=reason,
            search_fn=_search,
            on_select=_pick,
            search_label=f"Buscar en {self.report.destination}",
            hint_text="Título, artista o variante con feat",
            action_label="Insertar",
            action_icon=PIcons.PLUS,
            header_controls=[back_bar],
            on_refresh=self._refresh,
        )
        self._last_query = self._search_panel.last_query
        self._body.controls = [self._search_panel]
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
                app_icon(PIcons.CHECK_CIRCLE, color=SUCCESS, size=18, fill=True),
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
                app_icon(PIcons.WARNING_CIRCLE, color=WARNING, size=18, fill=True),
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
                    icon=PIcons.MAGNIFYING_GLASS,
                ),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            border=ft.Border.only(bottom=ft.BorderSide(0.4, BORDER_ROW)),
        )

    # ── Inserción ──────────────────────────────────────────────────

    async def _insert(self, track: Track) -> None:
        issue = self._active_issue
        if issue is None or not self.report.playlist_id:
            return
        if self._search_panel is not None:
            self._search_panel.set_status(f"Insertando {track.name[:40]}…")
        try:
            ok, message = await self.state.add_track_to_remote(
                self.report.destination, self.report.playlist_id, track.id
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            ok, message = False, str(exc)
        if not ok:
            if self._search_panel is not None:
                self._search_panel.set_status(
                    f"No se pudo insertar: {message}", kind="error"
                )
            return
        issue.resolved = True
        self.report.confirmed = min(
            self.report.confirmed + 1, self.report.detected
        )
        try:
            self.state.remember_resolution(
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

    def _refresh(self) -> None:
        try:
            self._body.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        try:
            self.page.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
