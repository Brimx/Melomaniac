"""engine/download_search.py — Búsqueda de la coincidencia en YouTube para una pista.

Elige entre los mejores resultados de `ytsearch` por título, artista y duración,
descartando directos y videos demasiado largos. Síncrono: el caller lo despacha
a un hilo.
"""

from __future__ import annotations

import re
import yt_dlp
from typing import Any

from engine.download_cookies import _load_cookie_jar
from engine.match import _fuzzy_scores_triple
from engine.normalizer import clean_metadata
from core.config import YOUTUBE_PLAYER_CLIENT


# Margen de duración aceptable frente a la pista original. Un video con
# diferencia mayor suele ser otra versión (live, remix, extended).
DURATION_MARGIN_S: int = 20
# Puntaje mínimo del título frente a la pista original para aceptar el
# primer resultado de búsqueda. Por debajo se marca como no encontrado.
MIN_TITLE_SCORE: int = 55
# Resultados que se piden por búsqueda. Con uno solo se aceptaba el primer
# hit de YouTube, que suele ser el vídeo oficial o un live.
SEARCH_RESULT_COUNT: int = 5
# Tope de duración: por encima de esto ya no es una canción.
MAX_VIDEO_DURATION_S: int = 20 * 60


# ──────────────────────────────────────────────────────────────────────
# Búsqueda de coincidencia
# ──────────────────────────────────────────────────────────────────────

def _youtube_video_id(track: Any) -> str:
    """Extrae el ID de video de ``source_url`` si la pista es de YouTube."""
    url = str(getattr(track, "source_url", "") or "")
    if "youtube.com/watch" in url or "youtu.be/" in url:
        match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
        if match:
            return match.group(1)
    return ""


def _search_query(track: Any) -> str:
    """Consulta de búsqueda ``artista título`` sin ruido de metadatos."""
    title, artist = clean_metadata(
        str(getattr(track, "name", "") or ""),
        str(getattr(track, "artist", "") or ""),
    )
    query = f"{artist} {title}".strip()
    return query or str(getattr(track, "name", "") or "")


def _accept_result(track: Any, title: str, duration_s: int) -> bool:
    """Decide si el resultado de búsqueda corresponde a la pista original.

    Usa el mismo motor fuzzy que la transferencia entre plataformas y añade
    un filtro de duración. Evita bajar covers, lives o versiones distintas.
    """
    original_title = str(getattr(track, "name", "") or "")
    original_artist = str(getattr(track, "artist", "") or "")
    combined, title_score, _ = _fuzzy_scores_triple(
        original_title, original_artist, title, original_artist
    )
    if title_score < MIN_TITLE_SCORE and combined < MIN_TITLE_SCORE:
        return False

    original_s = 0
    duration_ms = int(getattr(track, "duration_ms", 0) or 0)
    if duration_ms:
        original_s = duration_ms // 1000
    if original_s and duration_s:
        if abs(original_s - duration_s) > DURATION_MARGIN_S:
            return False
    return True


# Marcadores de video en directo o de duración anómala. Un live no tiene
# duración fija y descargar sus horas de metraje es peor que no descargar.
_LIVE_MARKERS = ("is_live", "isLive", "live_status")


def _reject_live(entry: dict[str, Any]) -> bool:
    """``True`` si el resultado es un directo o un video demasiado largo."""
    if entry.get("is_live") or entry.get("was_live"):
        return True
    if str(entry.get("live_status") or "").lower() in ("is_live", "is_upcoming"):
        return True
    duration = int(entry.get("duration") or 0)
    return bool(duration and duration > MAX_VIDEO_DURATION_S)


def _score_entry(track: Any, entry: dict[str, Any]) -> float:
    """Puntúa un resultado de búsqueda contra la pista original.

    Combina similitud de título/artista con cercanía de duración: dos vídeos
    del mismo tema con títulos parecidos se distinguen por durar lo que la
    pista. Un resultado sin duración pierde frente a uno que coincide.
    """
    title = str(entry.get("title") or "")
    channel = str(entry.get("channel") or entry.get("uploader") or "")
    combined, title_score, _ = _fuzzy_scores_triple(
        str(getattr(track, "name", "") or ""),
        str(getattr(track, "artist", "") or ""),
        title,
        channel,
    )
    score = max(float(combined), float(title_score))

    duration_ms = int(getattr(track, "duration_ms", 0) or 0)
    original_s = duration_ms // 1000
    duration_s = int(entry.get("duration") or 0)
    if original_s and duration_s:
        delta = abs(original_s - duration_s)
        # Hasta 5 s es lo mismo; más allá, penalización proporcional.
        score += 15.0 if delta <= 5 else max(-30.0, 15.0 - delta)
    return score


def _search_entries(track: Any, timeout_s: int) -> tuple[list[dict[str, Any]], str]:
    """Consulta ``ytsearchN:`` y devuelve ``(entradas, error)``.

    Compartida por la resolución automática y por la búsqueda manual del
    panel de resultados: ambas deben ver exactamente los mismos candidatos.
    """
    query = _search_query(track)
    if not query:
        return [], "Metadatos vacíos"

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
                f"ytsearch{SEARCH_RESULT_COUNT}:{query}", download=False
            )
    except Exception as exc:  # yt-dlp lanza DownloadError y variantes
        return [], f"Búsqueda fallida: {exc}"

    entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
    if not entries:
        return [], "Sin resultados en YouTube"
    return entries, ""


def search_candidates(track: Any, timeout_s: int = 30) -> dict[str, Any]:
    """Lista los mejores resultados de YouTube para una pista, sin descargar.

    Devuelve todos los candidatos que pasan el filtro con su puntuación, en
    orden de mejor a peor. Es la vista que el panel de resultados usa para
    corregir una elección equivocada: muestra qué encontró el extractor y
    permite descargar otro resultado.

    Returns:
        ``{"query", "candidates": [{video_id,title,channel,duration_s,score,
        url}], "error"}``.
    """
    query = _search_query(track)
    entries, error = _search_entries(track, timeout_s)
    if error:
        return {"query": query, "candidates": [], "error": error}

    candidates: list[dict[str, Any]] = []
    rejected_live = 0
    for entry in entries:
        video_id = str(entry.get("id") or "")
        if not video_id:
            continue
        if _reject_live(entry):
            rejected_live += 1
            continue
        title = str(entry.get("title") or "")
        duration_s = int(entry.get("duration") or 0)
        if not _accept_result(track, title, duration_s):
            continue
        candidates.append({
            "video_id": video_id,
            "title": title,
            "channel": str(entry.get("channel") or entry.get("uploader") or ""),
            "duration_s": duration_s,
            "score": round(_score_entry(track, entry), 1),
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    if not candidates:
        hint = " (se descartaron directos o vídeos demasiado largos)" if rejected_live else ""
        return {
            "query": query, "candidates": [],
            "error": f"Ningún resultado coincide con la pista{hint}",
        }
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"query": query, "candidates": candidates, "error": ""}
