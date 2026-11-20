"""engine/download_search.py — Búsqueda de la coincidencia en YouTube para una pista.

Elige entre los mejores resultados de `ytsearch` por título, artista y duración,
descartando directos y videos demasiado largos. Síncrono: el caller lo despacha
a un hilo.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import yt_dlp

from engine.download_cookies import _load_cookie_jar
from engine.match import _fuzzy_scores_triple
from engine.normalizer import clean_metadata
from core.config import (
    YOUTUBE_PLAYER_CLIENT,
    YTDLP_STREAM_FORMAT_SELECTOR,
    YTDLP_STREAM_PLAYER_CLIENTS,
)


_STREAM_CACHE_TTL_S: float = 30 * 60.0
_STREAM_CACHE_MAX: int = 200
_STREAM_TMP_MAX: int = 16
_STREAM_CACHE_LOCK = threading.Lock()
_STREAM_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_STREAM_INFLIGHT: dict[str, threading.Event] = {}


def _stream_temp_dir() -> Path:
    return Path(tempfile.gettempdir()) / "melomaniac_stream_cache"


def cleanup_stream_temp_cache() -> None:
    """Elimina los archivos temporales efímeros de streaming al cerrar la app."""
    tmp_dir = _stream_temp_dir()
    try:
        if tmp_dir.is_dir():
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


def _prune_stream_temp_dir(tmp_dir: Path) -> None:
    try:
        files = sorted(
            (p for p in tmp_dir.iterdir() if p.is_file() and not p.name.endswith(".tmp")),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(files) - _STREAM_TMP_MAX
        for old in files[: max(0, excess)]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def _remux_audio_only(stream_url: str, cache_key: str, *, timeout_s: int = 20) -> str:
    """Extrae solo la pista de audio sin recodificar (``-vn -c:a copy``) en caché efímero.

    Evita que GStreamer/audioplayers abra una ventana de video cuando YouTube
    solo ofrece contenedores con video (como ``itag=18``).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return ""
    tmp_dir = _stream_temp_dir()
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    digest = hashlib.sha1(cache_key.encode("utf-8", errors="ignore")).hexdigest()[:16]
    dst = tmp_dir / f"{digest}.m4a"
    if dst.is_file() and dst.stat().st_size > 0:
        try:
            os.utime(dst, None)
        except OSError:
            pass
        return str(dst)
    part = tmp_dir / f"{digest}.{os.getpid()}.tmp.m4a"
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "timeout": max(10, int(timeout_s)),
    }
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", stream_url, "-vn", "-c:a", "copy", str(part)],
            **kwargs,
        )
        if proc.returncode == 0 and part.is_file() and part.stat().st_size > 0:
            os.replace(part, dst)
            _prune_stream_temp_dir(tmp_dir)
            return str(dst)
    except Exception:
        pass
    try:
        part.unlink(missing_ok=True)
    except OSError:
        pass
    return ""


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


def _probe_video_info(video_id: str, timeout_s: int = 30) -> dict[str, Any] | None:
    """Extrae metadatos mínimos de un vídeo directo para validar directos y duración."""
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    jar = _load_cookie_jar()
    attempts: list[Any] = [None]
    if jar is not None:
        attempts.append(jar)

    for cookie_jar in attempts:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": timeout_s,
            "extractor_args": {"youtube": {"player_client": list(YTDLP_STREAM_PLAYER_CLIENTS)}},
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                if cookie_jar is not None:
                    ydl.cookiejar = cookie_jar
                info = ydl.extract_info(watch_url, download=False)
            if isinstance(info, dict):
                return info
        except Exception:
            continue
    return None


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


def clear_stream_cache() -> None:
    """Limpia la caché en memoria de URLs de streaming resueltas."""
    with _STREAM_CACHE_LOCK:
        _STREAM_CACHE.clear()


def invalidate_stream_cache(track: Any) -> None:
    """Invalida la entrada de caché de streaming para ``track``."""
    key = _stream_cache_key(track, _youtube_video_id(track))
    with _STREAM_CACHE_LOCK:
        _STREAM_CACHE.pop(key, None)


def _stream_cache_key(track: Any, vid: str) -> str:
    if vid:
        return f"yt:{vid}"
    dur_s = int(getattr(track, "duration_ms", 0) or 0) // 1000
    return f"q:{_search_query(track).lower()}|{dur_s}"


def _stream_expiry_deadline(url: str, now: float) -> float:
    deadline = now + _STREAM_CACHE_TTL_S
    try:
        raw_exp = (parse_qs(urlparse(url).query).get("expire") or [None])[0]
        if raw_exp:
            deadline = min(deadline, float(raw_exp) - 120.0)
    except (TypeError, ValueError):
        pass
    return deadline


def _get_cached_stream(key: str) -> dict[str, Any] | None:
    now = time.time()
    with _STREAM_CACHE_LOCK:
        entry = _STREAM_CACHE.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if now >= expires_at:
            _STREAM_CACHE.pop(key, None)
            return None
        cached_url = str(payload.get("url") or "")
        if cached_url and not cached_url.startswith(("http://", "https://")) and not os.path.isfile(cached_url):
            _STREAM_CACHE.pop(key, None)
            return None
        return dict(payload)


def _put_cached_stream(key: str, payload: dict[str, Any]) -> None:
    url = str(payload.get("url") or "")
    if not url:
        return
    now = time.time()
    deadline = _stream_expiry_deadline(url, now)
    if deadline <= now:
        return
    with _STREAM_CACHE_LOCK:
        if len(_STREAM_CACHE) >= _STREAM_CACHE_MAX and key not in _STREAM_CACHE:
            _STREAM_CACHE.pop(next(iter(_STREAM_CACHE)), None)
        _STREAM_CACHE[key] = (deadline, dict(payload))


def _verify_stream_url(
    url: str,
    timeout_s: float = 4.0,
) -> bool:
    """Comprueba con GET directo (sin Range, igual que GStreamer/audioplayers) que responde 200/206."""
    if not url.startswith(("http://", "https://")):
        return False
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_s) as client:
            with client.stream("GET", url) as resp:
                return resp.status_code in (200, 206)
    except Exception:
        return False


def _extract_stream_url(info: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extrae URL y metadatos del mejor flujo de audio disponible en ``info``."""
    direct_url = str(info.get("url") or "").strip()
    if direct_url:
        return direct_url, info

    req_fmts = [
        f for f in (info.get("requested_formats") or [])
        if isinstance(f, dict) and f.get("url") and (f.get("acodec") or "none") != "none"
    ]
    if req_fmts:
        chosen = req_fmts[0]
        return str(chosen.get("url") or "").strip(), chosen

    fmts = [
        f for f in (info.get("formats") or [])
        if isinstance(f, dict) and f.get("url") and (f.get("acodec") or "none") != "none"
    ]
    if not fmts:
        return "", info

    audio_only = [f for f in fmts if (f.get("vcodec") or "none") == "none"]
    http_audio = [f for f in audio_only if str(f.get("protocol") or "").lower() in ("http", "https")]
    http_any = [f for f in fmts if str(f.get("protocol") or "").lower() in ("http", "https")]
    pool = http_audio or audio_only or http_any or fmts
    chosen = max(pool, key=lambda f: float(f.get("abr") or f.get("tbr") or 0.0))
    return str(chosen.get("url") or "").strip(), chosen


def _stream_strategies(jar: Any) -> list[tuple[list[str], Any]]:
    """Estrategias ordenadas: ``android`` anónimo primero (GET 200 directo en GStreamer)."""
    strategies: list[tuple[list[str], Any]] = [
        ([YOUTUBE_PLAYER_CLIENT], None),
        (list(YTDLP_STREAM_PLAYER_CLIENTS), None),
    ]
    if jar is not None:
        strategies.append(([YOUTUBE_PLAYER_CLIENT], jar))
        strategies.append((list(YTDLP_STREAM_PLAYER_CLIENTS), jar))
    return strategies


def _resolve_watch_url(
    watch_url: str,
    track: Any,
    *,
    is_direct: bool,
    timeout_s: int,
    jar: Any,
    cache_key: str = "",
) -> tuple[dict[str, Any] | None, str, bool]:
    """Intenta resolver un ``watch_url`` probando clientes y sin cookies primero."""
    last_error = ""
    for clients, cookie_jar in _stream_strategies(jar):
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": YTDLP_STREAM_FORMAT_SELECTOR,
            "socket_timeout": timeout_s,
            "extractor_args": {"youtube": {"player_client": clients}},
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                if cookie_jar is not None:
                    ydl.cookiejar = cookie_jar
                info = ydl.extract_info(watch_url, download=False)
        except Exception as exc:  # yt-dlp lanza DownloadError y variantes
            last_error = f"No se pudo resolver el stream: {exc}"
            continue

        if not isinstance(info, dict):
            last_error = "Respuesta inválida de yt-dlp"
            continue
        if _reject_live(info):
            return None, "Directo o vídeo demasiado largo descartado", True
        if not is_direct:
            cand_title = str(info.get("title") or getattr(track, "name", "") or "")
            cand_dur = int(info.get("duration") or 0)
            if cand_dur > 0 and not _accept_result(track, cand_title, cand_dur):
                return None, "Ningún candidato verificado coincide con la pista", True

        stream_url, chosen = _extract_stream_url(info)
        if not stream_url:
            last_error = "No se encontró flujo de audio reproducible"
            continue
        if not _verify_stream_url(stream_url):
            last_error = "URL de streaming rechazada o inaccesible"
            continue

        # Si el formato elegido contiene pista de video (p. ej. itag=18),
        # GStreamer abre una ventana adicional al reproducirlo. Remuxeamos
        # solo el audio (-vn -c:a copy, ~0.3s) en caché efímero.
        vcodec = str(chosen.get("vcodec") or info.get("vcodec") or "none").strip().lower()
        final_url = stream_url
        final_ext = str(chosen.get("ext") or info.get("ext") or "mp4").lower()
        if vcodec not in ("none", ""):
            remuxed = _remux_audio_only(stream_url, cache_key or watch_url, timeout_s=timeout_s)
            if remuxed:
                final_url = remuxed
                final_ext = "m4a"

        duration_s = float(info.get("duration") or 0.0)
        duration_ms = (
            int(duration_s * 1000)
            if duration_s > 0
            else int(getattr(track, "duration_ms", 0) or 0)
        )
        abr_raw = chosen.get("abr") or info.get("abr")
        asr_raw = chosen.get("asr") or info.get("asr")
        return {
            "url": final_url,
            "ext": final_ext,
            "acodec": str(chosen.get("acodec") or info.get("acodec") or "mp4a.40.2").lower(),
            "abr": int(round(float(abr_raw))) if abr_raw else None,
            "asr": int(asr_raw) if asr_raw else None,
            "duration_ms": duration_ms,
            "webpage_url": watch_url,
            "error": "",
        }, "", False

    return None, last_error, False


def resolve_stream_info(
    track: Any,
    timeout_s: int = 20,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Resuelve una URL directa de audio en YouTube sin descargar el archivo.

    Síncrono: el caller debe invocarlo dentro de ``asyncio.to_thread``.
    """
    vid = _youtube_video_id(track)
    cache_key = _stream_cache_key(track, vid)

    if force_refresh:
        with _STREAM_CACHE_LOCK:
            _STREAM_CACHE.pop(cache_key, None)
    else:
        cached = _get_cached_stream(cache_key)
        if cached is not None:
            return cached

    # Singleflight: si otro hilo ya resuelve la misma pista, espera su resultado.
    wait_event: threading.Event | None = None
    own_event: threading.Event | None = None
    with _STREAM_CACHE_LOCK:
        wait_event = _STREAM_INFLIGHT.get(cache_key)
        if wait_event is None:
            own_event = threading.Event()
            _STREAM_INFLIGHT[cache_key] = own_event

    if wait_event is not None:
        wait_event.wait(timeout=max(5.0, float(timeout_s)))
        if not force_refresh:
            cached = _get_cached_stream(cache_key)
            if cached is not None:
                return cached
        with _STREAM_CACHE_LOCK:
            own_event = threading.Event()
            _STREAM_INFLIGHT[cache_key] = own_event

    try:
        targets: list[tuple[str, bool]] = []
        if vid:
            targets.append((f"https://www.youtube.com/watch?v={vid}", True))
        else:
            search = search_candidates(track, timeout_s=timeout_s)
            candidates = search.get("candidates") or []
            if not candidates:
                return {"url": "", "error": search.get("error") or "Sin resultados en YouTube"}
            targets = [
                (str(c.get("url") or ""), False)
                for c in candidates
                if c.get("url")
            ]
        if not targets:
            return {"url": "", "error": "URL de reproducción no disponible"}

        jar = _load_cookie_jar()
        last_error = ""
        for watch_url, is_direct in targets:
            payload, err, hard_reject = _resolve_watch_url(
                watch_url,
                track,
                is_direct=is_direct,
                timeout_s=timeout_s,
                jar=jar,
                cache_key=cache_key,
            )
            if payload is not None:
                _put_cached_stream(cache_key, payload)
                return payload
            if err:
                last_error = err
            if is_direct and hard_reject:
                break

        return {"url": "", "error": last_error or "No se encontró flujo de audio reproducible"}
    finally:
        if own_event is not None:
            with _STREAM_CACHE_LOCK:
                if _STREAM_INFLIGHT.get(cache_key) is own_event:
                    _STREAM_INFLIGHT.pop(cache_key, None)
            own_event.set()
