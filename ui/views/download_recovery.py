"""ui/views/download_recovery.py — Búsqueda de alternativas, filas y diálogo de recuperación."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import flet as ft

from core.config import AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT
from core.models import Track
from ui.components.widgets import (
    _ghost_btn,
    app_dialog,
    app_text,
    close_dialog,
    dialog_action,
    fmt_ms,
    organize_dropdown,
)
from ui.dialogs.recovery_panel import (
    AlternativeSearchPanel,
    RecoveryCandidate,
    default_search_query,
)
from ui.theme.fonts import FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_SURFACE,
    BORDER_MUTED,
    ERROR_COL,
    RADIUS_INPUT,
    SUCCESS,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

_YOUTUBE_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")
_URL_RE = re.compile(
    r"https?://\S+|(?:www\.)?(?:music\.)?(?:youtube\.com|youtu\.be)\S*",
    re.IGNORECASE,
)
_ORIGIN_LABEL_RE = re.compile(r"^url\s+directa(?:\s+de\s+youtube)?$", re.IGNORECASE)


def _clean_candidate_text(value: Any, *, fallback: str = "") -> str:
    """Elimina URLs y etiquetas de origen antes de mostrar texto de un candidato."""
    text = _URL_RE.sub("", str(value or "")).strip()
    text = re.sub(r"\s{2,}", " ", text)
    if not text or _ORIGIN_LABEL_RE.match(text):
        return fallback
    return text


def fmt_size(num: int) -> str:
    return f"{num / (1024 * 1024):.1f} MB"


def fmt_dur(ms: int | None) -> str:
    """Duración de una fila; «—» cuando la fuente no la trae."""
    return fmt_ms(ms) if ms else "—"


def entry_title(entry: dict) -> str:
    """``Artista — Título`` de una entrada del panel; nunca vacío."""
    artist = str(entry.get("artist") or "").strip()
    name = str(entry.get("name") or "").strip() or "Pista sin título"
    return f"{artist} — {name}" if artist else name


def entry_meta(entry: dict) -> str:
    """Formato y duración de una entrada: ``MP3 · 3:42``."""
    fmt = str(entry.get("format") or "").upper() or "—"
    return f"{fmt} · {fmt_dur(entry.get('duration_ms'))}"


def fmt_path(path: str) -> str:
    """Ruta acortada al nombre de archivo y su carpeta inmediata."""
    if not path:
        return ""
    p = Path(path)
    return f"{p.parent.name}/{p.name}" if p.parent.name else p.name


def extract_youtube_video_id(text: str) -> str:
    """Extrae el ID de vídeo de una URL o referencia directa de YouTube."""
    raw = (text or "").strip()
    match = _YOUTUBE_ID_RE.search(raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return raw
    return ""


def _search_ytdlp_query(original: Track, query: str, timeout_s: int = 30) -> dict[str, Any]:
    """Busca alternativas en YouTube por consulta libre sin filtro estricto de duración."""
    import yt_dlp

    from core.config import YOUTUBE_PLAYER_CLIENT
    from engine.download_cookies import _load_cookie_jar
    from engine.download_search import SEARCH_RESULT_COUNT, _reject_live, _score_entry

    clean_q = (query or "").strip()
    if not clean_q:
        return {"query": "", "candidates": [], "error": "Escribe una búsqueda."}

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "socket_timeout": timeout_s,
        "extractor_args": {"youtube": {"player_client": [YOUTUBE_PLAYER_CLIENT]}},
        "default_search": f"ytsearch{SEARCH_RESULT_COUNT}",
    }
    jar = _load_cookie_jar()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            if jar is not None:
                ydl.cookiejar = jar
            info = ydl.extract_info(
                f"ytsearch{SEARCH_RESULT_COUNT}:{clean_q}", download=False
            )
    except Exception as exc:  # noqa: BLE001 - yt-dlp lanza DownloadError y variantes
        return {"query": clean_q, "candidates": [], "error": f"Búsqueda fallida: {exc}"}

    entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
    if not entries:
        return {"query": clean_q, "candidates": [], "error": "Sin resultados"}

    candidates: list[dict[str, Any]] = []
    for raw in entries:
        video_id = str(raw.get("id") or "")
        if not video_id or _reject_live(raw):
            continue
        duration_s = int(raw.get("duration") or 0)
        candidates.append({
            "video_id": video_id,
            "title": str(raw.get("title") or ""),
            "channel": str(raw.get("channel") or raw.get("uploader") or ""),
            "duration_s": duration_s,
            "score": round(_score_entry(original, raw), 1),
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })
    if not candidates:
        return {
            "query": clean_q,
            "candidates": [],
            "error": "No se encontraron vídeos válidos (se descartaron directos o vídeos demasiado largos)",
        }
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"query": clean_q, "candidates": candidates, "error": ""}


async def search_youtube_alternatives(
    original: Track,
    query: str,
    *,
    entry: dict | None = None,
    app_state: Any = None,
    timeout_s: int = 30,
) -> dict[str, Any]:
    """Busca candidatos alternativos en YouTube por URL, consulta libre o pista."""
    clean_q = (query or "").strip()
    direct_id = extract_youtube_video_id(clean_q)
    if direct_id and ("youtube.com" in clean_q or "youtu.be" in clean_q or len(clean_q) == 11):
        return {
            "query": clean_q,
            "candidates": [{
                "video_id": direct_id,
                "title": original.name or "Alternativa",
                "channel": original.artist or "",
                "duration_s": int(original.duration_ms or 0) // 1000,
                "score": 100.0,
                "url": f"https://www.youtube.com/watch?v={direct_id}",
            }],
            "error": "",
        }
    default_q = default_search_query(original.name, original.artist).strip()
    if (
        clean_q == default_q
        and entry is not None
        and app_state is not None
        and hasattr(app_state, "research_download")
    ):
        found = await app_state.research_download(entry)
        if isinstance(found, dict):
            return found
    return await asyncio.to_thread(_search_ytdlp_query, original, clean_q, timeout_s)


def candidate_to_recovery(cand: dict, *, used_video_id: str = "") -> RecoveryCandidate:
    """Convierte un candidato dict a ``RecoveryCandidate`` sin exponer URL ni origen."""
    video_id = str(cand.get("video_id") or "")
    duration_s = int(cand.get("duration_s") or 0) or (
        int(cand.get("duration_ms") or 0) // 1000
    )
    artist = _clean_candidate_text(
        cand.get("artist") or cand.get("channel"),
        fallback="—",
    )
    url = str(
        cand.get("url")
        or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
    )
    used = bool(video_id and used_video_id and video_id == used_video_id)
    display_score = max(0.0, min(100.0, float(cand.get("score") or 0.0)))
    track = Track(
        id=video_id,
        name=_clean_candidate_text(cand.get("title"), fallback="Sin título"),
        artist=artist,
        album=_clean_candidate_text(cand.get("album"), fallback=""),
        duration=fmt_dur(duration_s * 1000) if duration_s else "—",
        img_url="",
        platform="",
        duration_ms=duration_s * 1000,
        source_url=url,
    )
    return RecoveryCandidate(
        track=track,
        score=display_score,
        score_suffix="%",
        note="Ya se intentó" if used else "",
        used=used,
        payload=cand,
    )


def results_signature(*groups: list[dict]) -> tuple:
    """Firma estable del panel de resultados para detectar cambios de estado o formato."""
    return tuple(
        tuple(
            (
                str(e.get("id") or ""),
                str(e.get("format") or ""),
                str(e.get("error") or ""),
                str(e.get("path") or ""),
                str(e.get("video_id") or ""),
            )
            for e in group
            if isinstance(e, dict)
        )
        for group in groups
    )


def build_history_row(
    path: str,
    name: str,
    ext: str,
    size: int,
    mtime: float,
    *,
    on_play: Callable[[str], None],
) -> ft.Container:
    """Fila del historial de descargas locales en disco."""
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_MUTED)),
        content=ft.Row(
            controls=[
                app_icon_button(
                    PIcons.PLAY,
                    size=16,
                    icon_color=ACCENT,
                    hover_color_icon=ACCENT,
                    tooltip="Reproducir archivo",
                    on_click=lambda _e, p=path: on_play(p),
                    width=28,
                    height=28,
                ),
                app_text(
                    name,
                    size=12,
                    color=TEXT_PRIMARY,
                    font_family=FONT_TEXT,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                ft.Container(
                    width=56,
                    content=app_text(
                        ext, size=10, color=TEXT_MUTED, font_family=mono_family("light")
                    ),
                ),
                ft.Container(
                    width=72,
                    content=app_text(
                        fmt_size(size),
                        size=10,
                        color=TEXT_MUTED,
                        font_family=mono_family("light"),
                    ),
                ),
                ft.Container(
                    width=110,
                    content=app_text(
                        datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                        size=10,
                        color=TEXT_DIM,
                        font_family=mono_family("light"),
                    ),
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def build_result_row(
    *,
    title: str,
    meta: str,
    detail: str,
    kind: str = "done",
    on_play=None,
    entry: dict | None = None,
    alt_formats: list[str] | None = None,
    on_open_recovery: Callable[[dict, str], None] | None = None,
    on_retry: Callable[[dict, str], None] | None = None,
) -> ft.Container:
    """Fila del panel de resultados de descarga (done, skipped, failed, missing)."""
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
        actions.append(
            app_icon_button(
                PIcons.MAGNIFYING_GLASS,
                icon_color=TEXT_MUTED,
                hover_color_icon=ACCENT,
                size=16,
                tooltip="Recuperar · buscar alternativa",
                on_click=(
                    lambda _e, e=entry, k=kind: on_open_recovery(e, k)
                    if on_open_recovery is not None
                    else None
                ),
                width=28,
                height=28,
            )
        )
        actions.append(
            ft.PopupMenuButton(
                content=app_icon(PIcons.ARROWS_CLOCKWISE, size=16, color=TEXT_MUTED),
                tooltip="Reintentar · probar otro formato",
                items=[
                    ft.PopupMenuItem(
                        content=ft.Text("Reintentar igual", font_family=FONT_TEXT, size=12),
                        on_click=(
                            lambda _e, e=entry: on_retry(e, "")
                            if on_retry is not None
                            else None
                        ),
                    ),
                    *[
                        ft.PopupMenuItem(
                            content=ft.Text(
                                f"Como {f.upper()}", font_family=FONT_TEXT, size=12
                            ),
                            on_click=(
                                lambda _e, e=entry, fmt=f: on_retry(e, fmt)
                                if on_retry is not None
                                else None
                            ),
                        )
                        for f in (alt_formats or [])
                    ],
                ],
            )
        )
    return ft.Container(
        bgcolor=BG_SURFACE,
        border=ft.Border.all(0.8, BORDER_MUTED),
        border_radius=RADIUS_INPUT,
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        content=ft.Row(
            controls=[
                lead,
                ft.Column(
                    controls=[
                        app_text(
                            title,
                            size=12,
                            color=TEXT_PRIMARY,
                            font_family=FONT_TEXT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        app_text(
                            detail,
                            size=10,
                            color=TEXT_MUTED,
                            font_family=FONT_TEXT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=1,
                    expand=True,
                ),
                ft.Container(
                    width=118,
                    content=app_text(
                        meta,
                        size=10,
                        color=TEXT_DIM,
                        font_family=mono_family("light"),
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ),
                *actions,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


async def open_youtube_recovery_dialog(
    page: ft.Page,
    original: Track,
    *,
    initial_format: str,
    reason: str,
    search_fn: Callable[[str], Awaitable[tuple[list[RecoveryCandidate], str]]],
    on_select: Callable[[RecoveryCandidate, str, ft.AlertDialog], None],
    on_retry_format: Callable[[str, ft.AlertDialog], None],
    fixed_width: int | None = None,
) -> tuple[ft.AlertDialog, AlternativeSearchPanel]:
    """Abre el diálogo reutilizable de búsqueda de alternativas en YouTube."""
    raw_fmt = str(initial_format or DEFAULT_AUDIO_FORMAT).lower().lstrip(".")
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

    sized_body = ft.Container(
        content=ft.Column(controls=[], spacing=8, tight=True),
        width=fixed_width,
        min_width=360,
        max_width=660,
        max_height=520,
    )
    dlg = app_dialog(
        "Recuperar pista",
        sized_body,
        [],
        width=None,
        icon=PIcons.MAGNIFYING_GLASS,
    )

    def _close(_e=None) -> None:
        close_dialog(page, dlg)

    def _current_fmt() -> str:
        return str(format_dd.value or selected_fmt["value"] or initial_fmt).lower()

    retry_btn = _ghost_btn(
        "Reintentar con formato",
        PIcons.ARROWS_CLOCKWISE,
        lambda _e: on_retry_format(_current_fmt(), dlg),
        height=38,
    )

    panel = AlternativeSearchPanel(
        page,
        original,
        reason=reason,
        search_fn=search_fn,
        on_select=lambda cand: on_select(cand, _current_fmt(), dlg),
        search_label="Buscar alternativa",
        hint_text="Título, artista o variante",
        action_label="Descargar",
        action_icon=PIcons.DOWNLOAD_SIMPLE,
        empty_message="Sin coincidencias. Edita la consulta para probar otra búsqueda.",
        query_trailing=[format_dd],
        action_trailing=[retry_btn],
    )
    sized_body.content = panel
    dlg.actions = [dialog_action("Cerrar", _close, kind="muted")]
    page.show_dialog(dlg)
    await panel.run_search()
    return dlg, panel


__all__ = [
    "build_history_row",
    "build_result_row",
    "candidate_to_recovery",
    "entry_meta",
    "entry_title",
    "extract_youtube_video_id",
    "fmt_dur",
    "fmt_path",
    "fmt_size",
    "open_youtube_recovery_dialog",
    "results_signature",
    "search_youtube_alternatives",
]
