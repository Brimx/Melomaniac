"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.8.2                                 ║
║              Motor de Descargas de Audio (yt-dlp)                    ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: engine/downloader.py
Descripción: Implementa la descarga de audio de pistas de playlist usando
            `yt-dlp` como librería Python. Para cada pista se busca la
            coincidencia en YouTube y se descarga solo audio, nunca video.

Estrategia de Resolución:
    Las playlists de origen (Apple Music, Spotify, YouTube Music) no se
    descargan desde sus propias fuentes. Cada pista se resuelve buscando
    una coincidencia equivalente en YouTube con `ytsearch`, igual que hacen
    los clientes de referencia: se transfiere el *contenido*, no el archivo
    original de la plataforma.

    1. Si la pista ya es de YouTube Music y tiene `source_url`, se usa ese
       video directamente: es la misma grabación, sin búsqueda.
    2. Si no, se construye una consulta "artista título" y se pide un solo
       resultado (`ytsearch1:`) con `extract_flat` para no pagar la
       extracción completa dos veces.
    3. Se descarta el resultado si el título no guarda relación con la pista
       (filtro de similitud) o si la duración se desvía demasiado.

Estrategia de Descarga:
    - Solo audio: `FFmpegExtractAudio` convierte el contenedor descargado al
      formato pedido. El cliente `android` entrega un contenedor con audio
      porque la sesión SABR oculta los formatos de audio puro.
    - Metadatos: los tags se escriben con `write_audio_metadata` (mutagen)
      usando los datos reales de la playlist. No se usa `FFmpegMetadata`
      porque vuelca la descripción completa del video en los tags.
    - Portada: se descarga el thumbnail y se incrusta con mutagen cuando el
      contenedor lo admite (mp3/m4a/flac); la imagen temporal se borra.
    - Renombrado atómico: se descarga a `<destino>.temp` y solo se renombra
      al nombre final si supera DOWNLOAD_MIN_BYTES. Nunca queda un archivo
      corrupto visible con nombre definitivo.

Concurrencia:
    `yt_dlp` es bloqueante. Todas las llamadas se ejecutan con
    `asyncio.to_thread` desde la capa de estado, nunca en el event loop de
    la UI. Este módulo es síncrono a propósito: expone funciones puras que
    el caller despacha a un hilo.

Autor: Melomaniac Team
Versión: 4.8.2
"""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote

import yt_dlp

from core.config import (
    AUDIO_BITRATES,
    DEFAULT_AUDIO_FORMAT,
    DOWNLOAD_MIN_BYTES,
    YOUTUBE_PLAYER_CLIENT,
    YTDLP_FORMAT_SELECTOR,
)
from engine.match import _fuzzy_scores_triple
from engine.normalizer import clean_metadata

__all__ = [
    "DownloadResult",
    "DownloadError",
    "DOWNLOAD_ERROR_NO_FFMPEG",
    "DOWNLOAD_SKIPPED",
    "ffmpeg_available",
    "resolve_download_dir",
    "sanitize_filename",
    "build_audio_opts",
    "search_video_id",
    "download_audio",
]

# Código de error reutilizable por la UI para distinguir la falta de FFmpeg
# de un fallo de red. ``DownloadResult.error`` lleva el texto legible.
DOWNLOAD_ERROR_NO_FFMPEG = "NO_FFMPEG"
# Pausa solicitada por el usuario. El ``.part`` se conserva para reanudar.
DOWNLOAD_PAUSED = "PAUSED"


class DownloadPaused(Exception):
    """Se lanza desde el progress hook para abortar sin borrar el ``.part``.

    ``yt-dlp`` no expone una pausa: la única forma de detener la pista en
    curso es abortar. Al no limpiar los temporales, la siguiente llamada con
    ``continuedl`` reanuda desde el byte donde se quedó.
    """
# Código de éxito con matiz: el archivo ya existía y se conservó tal cual.
DOWNLOAD_SKIPPED = "SKIPPED"

_ffmpeg_path_cache: str | None | bool = False


def ffmpeg_available() -> bool:
    """Indica si ``ffmpeg`` está en ``PATH``. El resultado se cachea.

    ``yt-dlp`` resuelve el binario una sola vez por proceso, así que
    comprobarlo en cada pista cuesta poco, pero cachearlo evita llamadas
    repetidas a ``shutil.which`` en una cola de cientos de pistas. La
    caché es por proceso: no cambia si el usuario instala FFmpeg sin
    reiniciar.
    """
    global _ffmpeg_path_cache
    if _ffmpeg_path_cache is False:
        _ffmpeg_path_cache = shutil.which("ffmpeg")
    return bool(_ffmpeg_path_cache)

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

_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


class DownloadError(RuntimeError):
    """Fallo de descarga con un motivo legible por la UI."""


@dataclass
class DownloadResult:
    """Resultado de intentar descargar una pista.

    Attributes:
        ok: True solo si el archivo final existe y supera el tamaño mínimo.
        path: Ruta absoluta del archivo de audio descargado.
        title: Título del video elegido en YouTube.
        video_id: ID del video usado como fuente.
        error: Motivo legible cuando ``ok`` es False.
        code: Código estable del fallo (``NO_FFMPEG``) para decisiones de UI;
            vacío cuando no hay uno específico.
        duration_s: Duración del video en segundos, si se conoce.
    """

    ok: bool = False
    path: str = ""
    title: str = ""
    video_id: str = ""
    error: str = ""
    code: str = ""
    duration_s: int = 0
    skipped: bool = False


# ──────────────────────────────────────────────────────────────────────
# Rutas y nombres
# ──────────────────────────────────────────────────────────────────────

PLAYLIST_MANIFEST_NAME = "playlist.json"
"""Manifiesto que conserva el orden original de una playlist descargada."""


def playlist_folder_name(playlist_name: str, platform: str = "") -> str:
    """Nombre de subcarpeta por playlist.

    Una carpeta plana mezcla pistas de listas distintas y todos los ``cover.jpg``
    del mismo álbum. La subcarpeta conserva la unidad de la playlist; el nombre
    se sanea igual que un archivo para no romper el sistema de archivos.
    """
    base = sanitize_filename(playlist_name, "") or "Playlist"
    plat = sanitize_filename(platform, "")
    return f"{base} [{plat}]" if plat else base


def resolve_playlist_dir(
    base_dir: Path | str | None,
    playlist_name: str,
    platform: str = "",
    *,
    create: bool = True,
) -> Path:
    """Carpeta de destino de una playlist dentro de la carpeta configurada."""
    base = resolve_download_dir(base_dir, create=create)
    target = base / playlist_folder_name(playlist_name, platform)
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def write_playlist_manifest(
    folder: Path | str,
    *,
    name: str,
    platform: str = "",
    entries: list[dict[str, Any]],
) -> str:
    """Escribe ``playlist.json`` con el orden original de las pistas.

    ``entries`` es la lista de pistas en el orden de la playlist, cada una con
    al menos ``name``/``artist``/``path``. Se escribe de forma atómica para no
    dejar un manifiesto a medias si la app se cierra durante la descarga.
    """
    import json

    target = Path(folder) / PLAYLIST_MANIFEST_NAME
    payload = {
        "name": name,
        "platform": platform,
        "count": len(entries),
        "tracks": entries,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return str(target)
    except (OSError, TypeError, ValueError):
        return ""


def read_playlist_manifest(folder: Path | str) -> dict[str, Any]:
    """Lee ``playlist.json`` de ``folder``; ``{}`` si no existe o está roto."""
    import json

    target = Path(folder) / PLAYLIST_MANIFEST_NAME
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_download_dir(preferred: Path | str | None = None, *, create: bool = True) -> Path:
    """Devuelve el directorio de caché de audio, creándolo si no existe.

    Sigue la misma resolución que ``engine.exporters``: prefiere
    ``~/Documentos/Melomaniac/Descargas`` y cae a ``./downloads`` cuando
    ``~/Documentos`` no existe (entornos portables).

    Args:
        preferred: Ruta preferida (directorio o archivo). Vacío usa el default.
        create: Si False solo calcula la ruta; no toca el disco. La UI lo usa
            para mostrar el destino sin crear carpetas vacías al arrancar.
    """
    if preferred:
        target = Path(preferred).expanduser()
        if target.suffix:
            # Es un archivo: el directorio es su padre.
            target = target.parent
    else:
        preferred_dir = Path.home() / "Documentos" / "Melomaniac" / "Descargas"
        target = (
            preferred_dir
            if preferred_dir.parent.exists()
            else Path.cwd() / "downloads"
        )
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def open_in_file_manager(path: Path | str) -> bool:
    """Abre una carpeta en el explorador del sistema. Devuelve False si falla."""
    import subprocess
    import sys

    target = Path(path).expanduser()
    if target.is_file():
        target = target.parent
    try:
        if not target.is_dir():
            return False
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except OSError:
        return False


def sanitize_filename(name: str, fallback: str = "audio") -> str:
    """Convierte un texto en nombre de archivo seguro y estable.

    Normaliza a NFC para no romper diacríticos, sustituye caracteres
    ilegales y recorta a 120 caracteres. Mantiene el estilo de
    ``engine.exporters._sane_filename`` para que ambos flujos generen
    nombres comparables.
    """
    value = unicodedata.normalize("NFC", str(name or "")).strip()
    value = _ILLEGAL_CHARS.sub("_", value)
    value = " ".join(value.split())
    return value[:120] or fallback


def _target_stem(track: Any) -> str:
    """Nombre base ``artista - título`` para el archivo final.

    Si la pista no trae ``artist`` se usa ``album_artist``; el nombre del
    archivo y el tag deben coincidir.
    """
    title = sanitize_filename(getattr(track, "name", ""), "")
    artist = sanitize_filename(
        str(getattr(track, "artist", "") or getattr(track, "album_artist", "") or ""), ""
    )
    if artist and title:
        return f"{artist} - {title}"
    return title or artist or "audio"


# ──────────────────────────────────────────────────────────────────────
# Cookies de la sesión existente
# ──────────────────────────────────────────────────────────────────────

def _load_cookie_jar() -> Optional[Any]:
    """Construye un cookie jar en memoria desde ``browser_session.youtube_cookies()``.

    La decisión de Descargas prohíbe introducir un ``cookies.txt`` en disco.
    Se reutilizan las cookies de YouTube resueltas desde el navegador o desde
    ``config/browser.json`` como fallback. Si no hay cookies, devuelve ``None``.

    El jar se mantiene en memoria: nunca se serializa a disco.
    """
    try:
        from services.browser_session import youtube_cookies
        from yt_dlp.cookies import YoutubeDLCookieJar
    except ImportError:
        return None

    cookies = youtube_cookies()
    if not cookies:
        return None

    jar = YoutubeDLCookieJar()
    http_cookiejar = yt_dlp.cookies.http.cookiejar
    for name, value in cookies.items():
        if not name or not value:
            continue
        for domain in (".youtube.com", ".google.com"):
            try:
                jar.set_cookie(
                    http_cookiejar.Cookie(
                        version=0,
                        name=name.strip(),
                        value=unquote(str(value).strip()),
                        port=None,
                        port_specified=False,
                        domain=domain,
                        domain_specified=True,
                        domain_initial_dot=True,
                        path="/",
                        path_specified=True,
                        secure=True,
                        expires=None,
                        discard=False,
                        comment=None,
                        comment_url=None,
                        rest={},
                        rfc2109=False,
                    )
                )
            except Exception:
                continue
    return jar if len(list(jar)) else None


# ──────────────────────────────────────────────────────────────────────
# Opciones de yt-dlp
# ──────────────────────────────────────────────────────────────────────

def build_audio_opts(
    out_stem: str,
    audio_format: str = DEFAULT_AUDIO_FORMAT,
    *,
    progress_hook: Optional[Callable[[dict], None]] = None,
    cookies: bool = True,
) -> dict[str, Any]:
    """Arma el diccionario de opciones de ``YoutubeDL`` para una pista.

    Args:
        out_stem: Nombre base sin extensión del archivo destino.
        audio_format: ``mp3``, ``m4a``, ``opus``, ``flac`` o ``wav``.
        progress_hook: Callback opcional con el dict de progreso de yt-dlp.
        cookies: Si es True intenta cargar la sesión de ``browser.json``.

    Returns:
        Opciones listas para ``YoutubeDL(opts)``. El ``outtmpl`` usa un
        sufijo ``.temp`` para que el renombrado final sea atómico.
    """
    fmt = (audio_format or DEFAULT_AUDIO_FORMAT).lower().lstrip(".")
    if fmt not in AUDIO_BITRATES:
        fmt = DEFAULT_AUDIO_FORMAT

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "format": YTDLP_FORMAT_SELECTOR,
        "outtmpl": f"{out_stem}.temp",
        "extractor_args": {"youtube": {"player_client": [YOUTUBE_PLAYER_CLIENT]}},
        # Solo se extrae el audio. Los tags se escriben después con mutagen
        # usando los metadatos reales de la playlist: FFmpegMetadata vuelca la
        # descripción completa del video en `description`/`synopsis`, ruido
        # que no corresponde a una pista musical.
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": fmt,
                "preferredquality": AUDIO_BITRATES.get(fmt, "192"),
            },
        ],
        "overwrites": True,
        # Reanudar desde el ``.part`` es lo que hace útil la pausa. El nombre
        # temporal es estable por pista (incluye el PID), así que una pista
        # pausada y reanudada sigue escribiendo el mismo archivo.
        "continuedl": True,
    }
    if progress_hook is not None:
        opts["progress_hooks"] = [progress_hook]
    if cookies:
        jar = _load_cookie_jar()
        if jar is not None:
            opts["_cookie_jar"] = jar
    return opts


def _apply_cookie_jar(ydl: "yt_dlp.YoutubeDL", opts: dict[str, Any]) -> None:
    """Inyecta el cookie jar privado en la instancia de ``YoutubeDL``."""
    jar = opts.pop("_cookie_jar", None)
    if jar is not None:
        ydl.cookiejar = jar


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


def search_video_id(track: Any, timeout_s: int = 30) -> DownloadResult:
    """Resuelve el video de YouTube para una pista, sin descargar.

    Si la pista viene de YouTube Music y trae ``source_url``, se reutiliza
    ese ID *solo si su duración encaja*: es la misma grabación y no hace
    falta buscar. En cualquier otro caso se consulta ``ytsearch5:`` con
    ``extract_flat`` y se elige el mejor resultado, no el primero.

    Elegir el primero era el fallo de origen: el primer hit de YouTube suele
    ser el vídeo oficial, el lyric video o un live, y cualquiera de los tres
    es peor que la pista del álbum.
    """
    direct_id = _youtube_video_id(track)
    if direct_id:
        return DownloadResult(
            ok=True, video_id=direct_id, title=str(getattr(track, "name", "") or "")
        )

    found = search_candidates(track, timeout_s)
    if found["error"]:
        return DownloadResult(ok=False, error=found["error"])

    best = found["candidates"][0]
    return DownloadResult(
        ok=True,
        video_id=str(best.get("video_id") or ""),
        title=str(best.get("title") or ""),
        duration_s=int(best.get("duration_s") or 0),
    )


# ──────────────────────────────────────────────────────────────────────
# Descarga
# ──────────────────────────────────────────────────────────────────────

def _finalize_temp_file(temp_path: Path, final_path: Path) -> bool:
    """Renombra ``.temp`` a la extensión final solo si el archivo es válido."""
    try:
        size = temp_path.stat().st_size
    except OSError:
        return False
    if size < DOWNLOAD_MIN_BYTES:
        try:
            temp_path.unlink()
        except OSError:
            pass
        return False
    try:
        if final_path.exists():
            final_path.unlink()
        os.replace(temp_path, final_path)
        return True
    except OSError:
        return False


def _attempt_download(
    track: Any,
    url: str,
    temp_stem: Path,
    final_path: Path,
    fmt: str,
    *,
    use_cookies: bool,
    progress_hook: Optional[Callable[[dict], None]],
    embed_thumbnail: bool,
    timeout_s: int,
    artwork_fetcher: Optional[Callable[[Any], bytes | None]] = None,
    pause_event: Optional[Any] = None,
) -> DownloadResult:
    """Ejecuta un intento de descarga con o sin sesión autenticada.

    La sesión de YouTube Music tiene activo el experimento SABR, que oculta
    los formatos de audio puro. Por eso el intento sin cookies es el que
    entrega el contenedor 18 con audio; el intento con cookies sirve como
    respaldo cuando el anónimo es rechazado por límite de peticiones.

    Si ``pause_event`` está activo al llegar un progreso, se lanza
    ``DownloadPaused`` y los temporales se conservan para reanudar.
    """
    opts = build_audio_opts(
        str(temp_stem), fmt, progress_hook=progress_hook, cookies=use_cookies
    )
    opts["socket_timeout"] = timeout_s
    # ``embed_thumbnail`` y ``convert_thumbnails`` no son claves de YoutubeDL
    # (solo se lee ``writethumbnail``); la incrustación real la hace mutagen.
    if embed_thumbnail:
        opts["writethumbnail"] = True

    def _pause_hook(info: dict) -> None:
        # Corre dentro del hilo de yt-dlp. Abortar desde aquí es lo único que
        # detiene la pista en curso sin esperar a que termine.
        if pause_event is not None and pause_event.is_set():
            raise DownloadPaused()
        if progress_hook is not None:
            progress_hook(info)

    if pause_event is not None:
        opts["progress_hooks"] = [_pause_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            _apply_cookie_jar(ydl, opts)
            info = ydl.extract_info(url, download=True)
    except DownloadPaused:
        # No se limpian los temporales: el ``.part`` es el progreso guardado.
        return DownloadResult(ok=False, error="Pausada por el usuario", code=DOWNLOAD_PAUSED)
    except Exception as exc:  # yt-dlp lanza DownloadError y variantes
        if pause_event is not None and pause_event.is_set():
            # yt-dlp envuelve la excepción del hook; el evento manda.
            return DownloadResult(ok=False, error="Pausada por el usuario", code=DOWNLOAD_PAUSED)
        _cleanup_temp_files(temp_stem)
        return DownloadResult(ok=False, error=f"Descarga fallida: {exc}")

    produced_files = _list_produced_files(temp_stem)
    produced = _find_produced_file(temp_stem)
    if produced is None:
        _cleanup_temp_files(temp_stem)
        return DownloadResult(ok=False, error="yt-dlp no generó archivo de audio")

    artwork = (
        _resolve_artwork(track, produced_files, fetcher=artwork_fetcher)
        if embed_thumbnail
        else None
    )

    if not _finalize_temp_file(produced, final_path):
        _cleanup_temp_files(temp_stem)
        return DownloadResult(ok=False, error="Archivo descargado inválido o vacío")

    # Los tags del video no son los de la playlist: se corrigen aquí con los
    # datos originales. Si falla, el archivo ya está guardado y sirve igual.
    _apply_playlist_metadata(track, final_path, artwork)
    _write_sidecar_if_needed(final_path, artwork)
    _cleanup_temp_files(temp_stem)

    title = str((info or {}).get("title") or "")
    return DownloadResult(
        ok=True,
        path=str(final_path),
        title=title,
        duration_s=int((info or {}).get("duration") or 0),
    )


def download_audio(
    track: Any,
    *,
    destination_dir: Path | str | None = None,
    audio_format: str = DEFAULT_AUDIO_FORMAT,
    video_id: str = "",
    progress_hook: Optional[Callable[[dict], None]] = None,
    embed_thumbnail: bool = True,
    timeout_s: int = 60,
    artwork_fetcher: Optional[Callable[[Any], bytes | None]] = None,
    skip_existing: bool = True,
    pause_event: Optional[Any] = None,
) -> DownloadResult:
    """Descarga el audio de una pista y devuelve la ruta final.

    Es síncrona y bloqueante: el caller debe despacharla con
    ``asyncio.to_thread``. Nunca llama a la UI.

    Cascada de intentos:
        1. Sin cookies. Es el camino que funciona con la sesión SABR activa,
           porque el cliente ``android`` anónimo sí expone el formato 18.
        2. Con cookies de ``config/browser.json`` en memoria, solo si el
           primero falló. Sirve cuando el anónimo topa con un límite.

    Args:
        track: ``Track`` con ``name``, ``artist`` y opcionalmente
            ``source_url``/``duration_ms``.
        destination_dir: Directorio destino. Por defecto usa
            ``resolve_download_dir()``.
        audio_format: Formato de salida entre ``AUDIO_BITRATES``.
        video_id: ID ya resuelto; si está vacío se busca con
            ``search_video_id``.
        progress_hook: Callback de progreso de yt-dlp.
        embed_thumbnail: Incrusta la portada cuando el contenedor lo admite.
        timeout_s: Timeout de red por petición.
        artwork_fetcher: Callable ``(track) -> bytes | None`` que resuelve la
            portada por la cadena de proveedores. Se inyecta desde
            ``core/state`` porque ``engine`` no puede importar ``services``.
            Si es ``None`` o devuelve vacío, se usa la cadena local.

    Returns:
        ``DownloadResult`` con ``ok=True`` y la ruta final, o ``ok=False``
        con un motivo legible. Nunca deja archivos ``.temp`` visibles.
    """
    fmt = (audio_format or DEFAULT_AUDIO_FORMAT).lower().lstrip(".")
    if fmt not in AUDIO_BITRATES:
        fmt = DEFAULT_AUDIO_FORMAT

    # FFmpegExtractAudio lo necesita para cualquier formato. Comprobarlo antes
    # de buscar el vídeo evita gastar una búsqueda en YouTube para fallar luego.
    if not ffmpeg_available():
        return DownloadResult(
            ok=False,
            error="FFmpeg no está instalado o no está en PATH",
            code=DOWNLOAD_ERROR_NO_FFMPEG,
        )

    target_dir = resolve_download_dir(destination_dir)
    stem = _target_stem(track)
    final_path = target_dir / f"{stem}.{fmt}"

    # «Omitir existentes»: sin esto la cola vuelve a descargar, busca en
    # YouTube y sobrescribe en silencio un archivo que ya estaba bien.
    if skip_existing and final_path.is_file():
        try:
            size = final_path.stat().st_size
        except OSError:
            size = 0
        if size >= DOWNLOAD_MIN_BYTES:
            return DownloadResult(
                ok=True,
                path=str(final_path),
                title=str(getattr(track, "name", "") or ""),
                code=DOWNLOAD_SKIPPED,
                skipped=True,
            )

    temp_stem = target_dir / f".{stem}.{os.getpid()}"

    resolved_id = video_id or search_video_id(track).video_id
    if not resolved_id:
        return DownloadResult(ok=False, error="Sin coincidencia en YouTube")

    url = f"https://www.youtube.com/watch?v={resolved_id}"

    result = _attempt_download(
        track, url, temp_stem, final_path, fmt,
        use_cookies=False,
        progress_hook=progress_hook,
        embed_thumbnail=embed_thumbnail,
        timeout_s=timeout_s,
        artwork_fetcher=artwork_fetcher,
        pause_event=pause_event,
    )
    if not result.ok and result.code == DOWNLOAD_PAUSED:
        # Reintentar con cookies reiniciaría la descarga: la pausa manda.
        result.video_id = resolved_id
        return result
    if not result.ok and _load_cookie_jar() is not None:
        # El primer error puede ser un límite anónimo; con sesión cambia el
        # conjunto de formatos disponibles y a veces resuelve.
        retry = _attempt_download(
            track, url, temp_stem, final_path, fmt,
            use_cookies=True,
            progress_hook=progress_hook,
            embed_thumbnail=embed_thumbnail,
            timeout_s=timeout_s,
            artwork_fetcher=artwork_fetcher,
            pause_event=pause_event,
        )
        if retry.ok:
            result = retry

    result.video_id = resolved_id
    return result


def _artifact_artist_from_filename(audio_path: Path) -> str:
    """Deriva un artista plausible de ``"Artista - Título.ext"``.

    Solo se usa cuando el ``Track`` no trae artista ni ``album_artist``. Si
    el nombre no sigue el patrón, devuelve cadena vacía y el tag del vídeo
    se deja como esté en vez de escribirse vacío.
    """
    stem = audio_path.stem.strip()
    if " - " not in stem:
        return ""
    return stem.split(" - ", 1)[0].strip()


def _resolve_artist(track: Any, audio_path: Path) -> tuple[str, str]:
    """Devuelve ``(artist, album_artist)`` con el mejor dato disponible.

    Prioriza los campos del ``Track``, cae al nombre del archivo y por
    último deja vacío para que ``write_audio_metadata`` no sobrescriba un
    tag existente con una cadena vacía.
    """
    artist = str(getattr(track, "artist", "") or "").strip()
    album_artist = str(getattr(track, "album_artist", "") or "").strip()
    if not artist:
        artist = album_artist or _artifact_artist_from_filename(audio_path)
    if not album_artist:
        album_artist = artist
    return artist, album_artist


def _apply_playlist_metadata(track: Any, audio_path: Path, artwork: bytes | None) -> bool:
    """Corrige los tags con los datos reales de la playlist.

    ``yt-dlp`` escribe el título del video ("... Official Video (4K
    Remaster)"), que no es el nombre de la pista. Aquí se sobrescribe con
    los campos del ``Track`` original usando ``write_audio_metadata`` de
    ``engine.audio_metadata`` (mutagen). Es la misma función que el proyecto
    ya expone y hasta ahora no tenía llamadas.
    """
    try:
        from engine.audio_metadata import write_audio_metadata
    except ImportError:
        return False
    artist, album_artist = _resolve_artist(track, audio_path)
    metadata = {
        "title": str(getattr(track, "name", "") or ""),
        "artist": artist,
        "album": str(getattr(track, "album", "") or ""),
        "album_artist": album_artist,
        "track_number": getattr(track, "track_number", "") or "",
        "date": str(getattr(track, "release_date", "") or ""),
        "isrc": str(getattr(track, "isrc", "") or ""),
    }
    try:
        return write_audio_metadata(str(audio_path), metadata, artwork=artwork)
    except Exception:
        return False


def _write_sidecar_if_needed(audio_path: Path, artwork: bytes | None) -> None:
    """Deja ``cover.jpg`` junto al audio en los formatos que no la incrustan.

    Opus no admite ``add_picture`` en mutagen y muchos reproductores ignoran
    el APIC de WAV; el sidecar es la única forma fiable de que la vean.
    """
    if not artwork:
        return
    fmt = audio_path.suffix.lower().lstrip(".")
    try:
        from engine.audio_metadata import SIDECAR_COVER_FORMATS, write_cover_sidecar
    except ImportError:
        return
    if fmt not in SIDECAR_COVER_FORMATS:
        return
    try:
        write_cover_sidecar(str(audio_path), artwork)
    except Exception:
        pass


def _read_artwork(produced_files: list[Path]) -> bytes | None:
    """Devuelve la portada descargada, si ``yt-dlp`` dejó una imagen."""
    for entry in produced_files:
        if entry.suffix.lower().lstrip(".") in ("webp", "jpg", "jpeg", "png"):
            try:
                return entry.read_bytes()
            except OSError:
                return None
    return None


def _fetch_url_bytes(url: str, timeout_s: int = 10) -> bytes | None:
    """Descarga una imagen remota en memoria; ``None`` si falla o no es imagen."""
    if not url or not url.startswith("http"):
        return None
    try:
        import requests
    except ImportError:
        return None
    try:
        resp = requests.get(url, timeout=timeout_s, stream=True)
        if resp.status_code != 200:
            return None
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if content_type and not content_type.startswith("image/"):
            return None
        data = resp.content
    except Exception:
        return None
    return data or None


def _resolve_artwork(
    track: Any,
    produced_files: list[Path],
    *,
    fetcher: Optional[Callable[[Any], bytes | None]] = None,
) -> bytes | None:
    """Elige y normaliza la portada a incrustar.

    Prioridad:
        1. ``fetcher``: cadena de proveedores inyectada desde ``core/state``
           (plataforma, ISRC, iTunes, Deezer). Devuelve ya un JPEG cuadrado.
        2. ``track.img_url``: artwork de la plataforma de origen.
        3. Thumbnail de YouTube dejado por ``yt-dlp``: solo como respaldo,
           porque suele ser una miniatura de vídeo 16:9.

    Todo sale normalizado a JPEG cuadrado. Antes se pasaban los bytes crudos
    (a menudo WebP) y ``write_audio_metadata`` los etiquetaba como
    ``image/jpeg``, generando portadas corruptas.
    """
    from engine.covers import (
        is_acceptable_cover, is_video_thumbnail_url, normalize_cover, probe_cover,
        upscale_cover_url,
    )

    if fetcher is not None:
        try:
            resolved = fetcher(track)
        except Exception:
            resolved = None
        if resolved:
            return resolved

    candidates: list[bytes] = []

    # Un thumbnail de vídeo no compite con una carátula: si la plataforma solo
    # ofrece eso, se prefiere el respaldo de ``yt-dlp`` y, en último término,
    # el relleno; incrustar el fotograma de un vídeo es peor que un cuadrado
    # neutro.
    raw_img = str(getattr(track, "img_url", "") or "")
    if not is_video_thumbnail_url(raw_img):
        platform_cover = _fetch_url_bytes(upscale_cover_url(raw_img))
        if platform_cover and is_acceptable_cover(probe_cover(platform_cover)):
            candidates.append(platform_cover)

    youtube_cover = _read_artwork(produced_files)
    if youtube_cover:
        candidates.append(youtube_cover)

    for raw in candidates:
        normalized = normalize_cover(raw)
        if normalized:
            return normalized
    return None


def _list_produced_files(temp_stem: Path) -> list[Path]:
    """Lista los archivos generados a partir del prefijo ``.temp``."""
    parent = temp_stem.parent
    prefix = temp_stem.name
    try:
        return [
            entry
            for entry in parent.iterdir()
            if entry.is_file() and entry.name.startswith(prefix)
        ]
    except OSError:
        return []


def _find_produced_file(temp_stem: Path) -> Optional[Path]:
    """Localiza el archivo de audio generado a partir del ``.temp``.

    FFmpeg renombra a ``<stem>.temp.<ext>`` tras la extracción. Se busca
    primero la coincidencia exacta y luego cualquier variante con la
    extensión de audio pedida.
    """
    candidates = _list_produced_files(temp_stem)
    if not candidates:
        return None
    audio_like = [
        c for c in candidates
        if c.suffix.lower().lstrip(".") in AUDIO_BITRATES
    ]
    pool = audio_like or candidates
    return max(pool, key=lambda p: p.stat().st_size)


def _cleanup_temp_files(temp_stem: Path) -> None:
    """Borra el ``.temp`` y sus derivados para no dejar basura."""
    parent = temp_stem.parent
    prefix = temp_stem.name
    try:
        for entry in parent.iterdir():
            if entry.is_file() and entry.name.startswith(prefix):
                try:
                    entry.unlink()
                except OSError:
                    pass
    except OSError:
        pass
