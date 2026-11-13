"""ui/dialogs/recovery_panel.py — Panel reutilizable de búsqueda de alternativas."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Sequence

import flet as ft

from core.models import Track
from engine.match import _fuzzy_scores_triple
from engine.normalizer import build_search_query, clean_metadata
from ui.components.song_row import build_preview_row
from ui.components.widgets import (
    _primary_btn,
    _section_label,
    app_text,
    app_text_field,
)
from ui.theme.fonts import FONT_HEADLINE_SEMI, FONT_TEXT, mono_family
from ui.theme.icons import PIcons
from ui.theme.tokens import (
    ACCENT,
    BG_INPUT,
    BG_PANEL,
    BORDER_MUTED,
    ERROR_COL,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
)


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

    Usado por ``TransferReportDialog``, ``DownloadsView`` y ``PlaylistDetailView``.
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


__all__ = [
    "AlternativeSearchPanel",
    "RecoveryCandidate",
    "build_original_track_card",
    "compute_match_score",
    "default_search_query",
    "track_from_entry",
]
