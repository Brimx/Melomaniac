"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.6.0                                 ║
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
Versión: 4.6.0
"""

from __future__ import annotations

import os
import re
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
    "resolve_download_dir",
    "sanitize_filename",
    "build_audio_opts",
    "search_video_id",
    "download_audio",
]

# Margen de duración aceptable frente a la pista original. Un video con
# diferencia mayor suele ser otra versión (live, remix, extended).
DURATION_MARGIN_S: int = 20
# Puntaje mínimo del título frente a la pista original para aceptar el
# primer resultado de búsqueda. Por debajo se marca como no encontrado.
MIN_TITLE_SCORE: int = 55

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
        duration_s: Duración del video en segundos, si se conoce.
    """

    ok: bool = False
    path: str = ""
    title: str = ""
    video_id: str = ""
    error: str = ""
    duration_s: int = 0


# ──────────────────────────────────────────────────────────────────────
# Rutas y nombres
# ──────────────────────────────────────────────────────────────────────

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
    """Nombre base ``artista - título`` para el archivo final."""
    title = sanitize_filename(getattr(track, "name", ""), "")
    artist = sanitize_filename(getattr(track, "artist", ""), "")
    if artist and title:
        return f"{artist} - {title}"
    return title or artist or "audio"


# ──────────────────────────────────────────────────────────────────────
# Cookies de la sesión existente
# ──────────────────────────────────────────────────────────────────────

def _load_cookie_jar() -> Optional[Any]:
    """Construye un cookie jar en memoria desde ``config/browser.json``.

    La decisión de Descargas prohíbe introducir un ``cookies.txt`` en disco.
    Se reutiliza la sesión de YouTube Music ya autenticada y se inyecta el
    header ``Cookie`` directamente en el jar. Si el archivo no existe o no
    tiene cookies, se devuelve ``None`` y yt-dlp trabaja sin sesión.

    El jar se mantiene en memoria: nunca se serializa a disco.
    """
    try:
        from services.authentication import read_browser_json
        from yt_dlp.cookies import YoutubeDLCookieJar
    except ImportError:
        return None

    data = read_browser_json()
    raw_cookie = str(data.get("Cookie", "") or "").strip()
    if not raw_cookie:
        return None

    jar = YoutubeDLCookieJar()
    http_cookiejar = yt_dlp.cookies.http.cookiejar
    for chunk in raw_cookie.split(";"):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        name, _, value = piece.partition("=")
        for domain in (".youtube.com", ".google.com"):
            try:
                jar.set_cookie(
                    http_cookiejar.Cookie(
                        version=0,
                        name=name.strip(),
                        value=unquote(value.strip()),
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
        "continuedl": False,
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


def search_video_id(track: Any, timeout_s: int = 30) -> DownloadResult:
    """Resuelve el video de YouTube para una pista, sin descargar.

    Si la pista viene de YouTube Music y trae ``source_url``, se reutiliza
    ese ID: es la misma grabación y no hace falta buscar. En cualquier otro
    caso se consulta ``ytsearch1:`` con ``extract_flat`` para obtener el
    primer resultado con metadatos ligeros.
    """
    direct_id = _youtube_video_id(track)
    if direct_id:
        return DownloadResult(
            ok=True, video_id=direct_id, title=str(getattr(track, "name", "") or "")
        )

    query = _search_query(track)
    if not query:
        return DownloadResult(ok=False, error="Metadatos vacíos")

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "socket_timeout": timeout_s,
        "extractor_args": {"youtube": {"player_client": [YOUTUBE_PLAYER_CLIENT]}},
        "default_search": "ytsearch1",
    }
    jar = _load_cookie_jar()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            if jar is not None:
                ydl.cookiejar = jar
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
    except Exception as exc:  # yt-dlp lanza DownloadError y variantes
        return DownloadResult(ok=False, error=f"Búsqueda fallida: {exc}")

    entries = info.get("entries") or []
    if not entries:
        return DownloadResult(ok=False, error="Sin resultados en YouTube")

    entry = entries[0]
    title = str(entry.get("title") or "")
    duration_s = int(entry.get("duration") or 0)
    video_id = str(entry.get("id") or "")
    if not video_id:
        return DownloadResult(ok=False, error="Resultado sin ID de video")

    if not _accept_result(track, title, duration_s):
        return DownloadResult(
            ok=False,
            video_id=video_id,
            title=title,
            duration_s=duration_s,
            error="La coincidencia no corresponde a la pista",
        )
    return DownloadResult(
        ok=True, video_id=video_id, title=title, duration_s=duration_s
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
) -> DownloadResult:
    """Ejecuta un intento de descarga con o sin sesión autenticada.

    La sesión de YouTube Music tiene activo el experimento SABR, que oculta
    los formatos de audio puro. Por eso el intento sin cookies es el que
    entrega el contenedor 18 con audio; el intento con cookies sirve como
    respaldo cuando el anónimo es rechazado por límite de peticiones.
    """
    opts = build_audio_opts(
        str(temp_stem), fmt, progress_hook=progress_hook, cookies=use_cookies
    )
    opts["socket_timeout"] = timeout_s
    if embed_thumbnail and fmt in ("mp3", "m4a", "flac"):
        opts["writethumbnail"] = True
        opts["embed_thumbnail"] = True
        opts["convert_thumbnails"] = "jpg"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            _apply_cookie_jar(ydl, opts)
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # yt-dlp lanza DownloadError y variantes
        _cleanup_temp_files(temp_stem)
        return DownloadResult(ok=False, error=f"Descarga fallida: {exc}")

    produced_files = _list_produced_files(temp_stem)
    produced = _find_produced_file(temp_stem)
    if produced is None:
        _cleanup_temp_files(temp_stem)
        return DownloadResult(ok=False, error="yt-dlp no generó archivo de audio")

    artwork = _read_artwork(produced_files) if embed_thumbnail else None

    if not _finalize_temp_file(produced, final_path):
        _cleanup_temp_files(temp_stem)
        return DownloadResult(ok=False, error="Archivo descargado inválido o vacío")

    # Los tags del video no son los de la playlist: se corrigen aquí con los
    # datos originales. Si falla, el archivo ya está guardado y sirve igual.
    _apply_playlist_metadata(track, final_path, artwork)
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

    Returns:
        ``DownloadResult`` con ``ok=True`` y la ruta final, o ``ok=False``
        con un motivo legible. Nunca deja archivos ``.temp`` visibles.
    """
    fmt = (audio_format or DEFAULT_AUDIO_FORMAT).lower().lstrip(".")
    if fmt not in AUDIO_BITRATES:
        fmt = DEFAULT_AUDIO_FORMAT

    target_dir = resolve_download_dir(destination_dir)
    stem = _target_stem(track)
    final_path = target_dir / f"{stem}.{fmt}"
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
    )
    if not result.ok and _load_cookie_jar() is not None:
        # El primer error puede ser un límite anónimo; con sesión cambia el
        # conjunto de formatos disponibles y a veces resuelve.
        retry = _attempt_download(
            track, url, temp_stem, final_path, fmt,
            use_cookies=True,
            progress_hook=progress_hook,
            embed_thumbnail=embed_thumbnail,
            timeout_s=timeout_s,
        )
        if retry.ok:
            result = retry

    result.video_id = resolved_id
    return result


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
    metadata = {
        "title": str(getattr(track, "name", "") or ""),
        "artist": str(getattr(track, "artist", "") or ""),
        "album": str(getattr(track, "album", "") or ""),
        "album_artist": str(getattr(track, "album_artist", "") or ""),
        "track_number": getattr(track, "track_number", "") or "",
        "date": str(getattr(track, "release_date", "") or ""),
        "isrc": str(getattr(track, "isrc", "") or ""),
    }
    try:
        return write_audio_metadata(str(audio_path), metadata, artwork=artwork)
    except Exception:
        return False


def _read_artwork(produced_files: list[Path]) -> bytes | None:
    """Devuelve la portada descargada, si ``yt-dlp`` dejó una imagen."""
    for entry in produced_files:
        if entry.suffix.lower().lstrip(".") in ("webp", "jpg", "jpeg", "png"):
            try:
                return entry.read_bytes()
            except OSError:
                return None
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
