"""
services/lyrics.py — Obtención, caché, parseo LRC y lectura local de letras.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Sequence

import requests

from engine.audio_metadata import read_lyrics as read_embedded_lyrics
from services.authentication import CONFIG_DIR

CACHE_DIR = CONFIG_DIR / "lyrics"
LRCLIB_GET_URL = "https://lrclib.net/api/get"
_USER_AGENT = "Melomaniac/4.9 (https://github.com/santireyes/melomaniac)"
_NEGATIVE_CACHE_TTL_S = 86400
_DEFAULT_RETRY_AFTER_S = 60.0

_TS_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_retry_after_until: float = 0.0
_retry_lock = threading.Lock()
_last_error_local = threading.local()


class LyricsFetchError(RuntimeError):
    """Error transitorio o de red/servidor al consultar el proveedor de letras."""


def _set_last_error(msg: str) -> None:
    _last_error_local.error = msg


def get_last_error() -> str:
    """Devuelve el último error de red/proveedor en el hilo actual (vacío si fue 404 o éxito)."""
    return str(getattr(_last_error_local, "error", "") or "")


@dataclass(frozen=True)
class LyricLine:
    time_ms: int
    text: str


@dataclass
class LyricsResult:
    lines: list[LyricLine] = field(default_factory=list)
    plain_text: str = ""
    synced: bool = False
    instrumental: bool = False
    source: str = ""  # "local_lrc" | "embedded" | "cache" | "lrclib" | ""

    def to_lrc(self) -> str:
        """Serializa las líneas sincronizadas a formato .lrc."""
        return serialize_lrc(self.lines) if self.lines else ""


def active_line_index(lines: Sequence[LyricLine], position_ms: int) -> int:
    """Índice de la línea activa para position_ms (-1 si aún no empieza o está vacío)."""
    if not lines or position_ms < lines[0].time_ms:
        return -1
    idx = bisect_right([line.time_ms for line in lines], position_ms) - 1
    return idx if idx >= 0 else -1


def parse_lrc(raw: str) -> list[LyricLine]:
    """Parsea texto .lrc en líneas ordenadas por ``time_ms``."""
    if not raw:
        return []
    parsed: list[LyricLine] = []
    for raw_line in raw.splitlines():
        matches = list(_TS_RE.finditer(raw_line))
        if not matches:
            continue
        text = _TS_RE.sub("", raw_line).strip()
        for match in matches:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            frac = match.group(3) or ""
            if len(frac) == 1:
                ms = int(frac) * 100
            elif len(frac) == 2:
                ms = int(frac) * 10
            elif len(frac) == 3:
                ms = int(frac)
            else:
                ms = 0
            total_ms = (minutes * 60 + seconds) * 1000 + ms
            parsed.append(LyricLine(time_ms=total_ms, text=text))
    parsed.sort(key=lambda item: item.time_ms)
    return parsed


def _format_ts(ms: int) -> str:
    ms = max(0, int(ms))
    minutes, rem = divmod(ms, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"[{minutes:02d}:{seconds:02d}.{millis // 10:02d}]"


def serialize_lrc(
    lines: Sequence[LyricLine],
    *,
    title: str = "",
    artist: str = "",
    album: str = "",
    duration_ms: int = 0,
) -> str:
    """Serializa ``LyricLine``s a formato .lrc estándar."""
    out: list[str] = []
    if title:
        out.append(f"[ti:{title}]")
    if artist:
        out.append(f"[ar:{artist}]")
    if album:
        out.append(f"[al:{album}]")
    if duration_ms > 0:
        mins, secs = divmod(int(duration_ms) // 1000, 60)
        out.append(f"[length:{mins:02d}:{secs:02d}]")
    for line in sorted(lines, key=lambda item: item.time_ms):
        out.append(f"{_format_ts(line.time_ms)}{line.text}")
    return "\n".join(out) + ("\n" if out else "")


def lrc_path_for_audio(audio_path: str | Path) -> Path:
    """Devuelve la ruta sidecar ``.lrc`` junto al archivo de audio."""
    return Path(audio_path).with_suffix(".lrc")


def load_local_lyrics(audio_path: str | Path) -> LyricsResult | None:
    """Lee letras desde ``.lrc`` sidecar y, si no existe, desde tags embebidos."""
    if not audio_path:
        return None
    path = Path(audio_path)
    lrc_file = lrc_path_for_audio(path)
    if lrc_file.is_file():
        try:
            raw = lrc_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        lines = parse_lrc(raw)
        if lines:
            plain = "\n".join(line.text for line in lines if line.text)
            return LyricsResult(lines=lines, plain_text=plain, synced=True, source="local_lrc")
        stripped = raw.strip()
        if stripped:
            return LyricsResult(plain_text=stripped, synced=False, source="local_lrc")

    if not path.is_file():
        return None
    embedded = read_embedded_lyrics(str(path))
    if not embedded:
        return None

    raw_synced = embedded.get("synced") or []
    if raw_synced:
        lines = sorted(
            (LyricLine(time_ms=int(ms), text=str(txt)) for ms, txt in raw_synced),
            key=lambda item: item.time_ms,
        )
        plain = str(embedded.get("plain") or "").strip() or "\n".join(
            line.text for line in lines if line.text
        )
        return LyricsResult(lines=lines, plain_text=plain, synced=True, source="embedded")

    raw_lrc = str(embedded.get("lrc") or "").strip()
    if raw_lrc:
        lines = parse_lrc(raw_lrc)
        if lines:
            plain = str(embedded.get("plain") or "").strip() or "\n".join(
                line.text for line in lines if line.text
            )
            return LyricsResult(lines=lines, plain_text=plain, synced=True, source="embedded")
        return LyricsResult(plain_text=raw_lrc, synced=False, source="embedded")

    plain = str(embedded.get("plain") or "").strip()
    if plain:
        return LyricsResult(plain_text=plain, synced=False, source="embedded")
    return None


def save_lrc_file(audio_path: str | Path, lyrics: LyricsResult) -> Path | None:
    """Guarda ``lyrics`` de forma atómica como ``.lrc`` junto al audio."""
    if not audio_path or lyrics is None:
        return None
    content = lyrics.to_lrc() if lyrics.lines else (lyrics.plain_text or "")
    if not content.strip():
        return None
    target = lrc_path_for_audio(audio_path)
    tmp = target.with_suffix(".lrc.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
        return target
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return None


def _extract_query(
    track: Any,
    title: str,
    artist: str,
    album: str,
    duration_ms: int,
) -> tuple[str, str, str, int]:
    if track is not None:
        title = title or str(getattr(track, "name", "") or getattr(track, "title", "") or "")
        artist = artist or str(getattr(track, "artist", "") or "")
        album = album or str(getattr(track, "album", "") or "")
        if not duration_ms:
            duration_ms = int(getattr(track, "duration_ms", 0) or 0)
    return title.strip(), artist.strip(), album.strip(), max(0, int(duration_ms or 0))


def _cache_key(title: str, artist: str, album: str, duration_ms: int) -> str:
    raw = f"{artist.lower()}|{title.lower()}|{album.lower()}|{duration_ms // 1000}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _read_cache(key: str) -> tuple[bool, LyricsResult | None]:
    """Devuelve ``(hit, result)``. ``hit=True, result=None`` indica negative cache vigente."""
    path = CACHE_DIR / f"{key}.json"
    if not path.is_file():
        return False, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    if data.get("not_found"):
        cached_at = float(data.get("cached_at") or 0.0)
        if time.time() - cached_at < _NEGATIVE_CACHE_TTL_S:
            return True, None
        return False, None
    synced_raw = str(data.get("synced_lyrics") or "")
    plain = str(data.get("plain_lyrics") or "").strip()
    instrumental = bool(data.get("instrumental", False))
    lines = parse_lrc(synced_raw)
    if not lines and not plain and not instrumental:
        return False, None
    if lines and not plain:
        plain = "\n".join(line.text for line in lines if line.text)
    return True, LyricsResult(
        lines=lines,
        plain_text=plain,
        synced=bool(lines),
        instrumental=instrumental,
        source="cache",
    )


def _write_cache(key: str, payload: dict[str, Any]) -> None:
    tmp = CACHE_DIR / f"{key}.json.tmp"
    target = CACHE_DIR / f"{key}.json"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _parse_retry_after(header_value: str | None) -> float:
    if not header_value:
        return _DEFAULT_RETRY_AFTER_S
    value = header_value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_AFTER_S


def reset_rate_limit() -> None:
    """Limpia el cooldown de Retry-After (útil en tests)."""
    global _retry_after_until
    with _retry_lock:
        _retry_after_until = 0.0


def fetch_lyrics(
    track: Any = None,
    *,
    title: str = "",
    artist: str = "",
    album: str = "",
    duration_ms: int = 0,
    use_cache: bool = True,
    allow_network: bool = True,
    timeout_s: float = 10.0,
    raise_on_error: bool = False,
) -> LyricsResult | None:
    """Consulta caché en ``config/lyrics/`` y luego LRCLIB respetando ``Retry-After``."""
    global _retry_after_until
    _set_last_error("")
    q_title, q_artist, q_album, q_duration_ms = _extract_query(
        track, title, artist, album, duration_ms
    )
    if not q_title or not q_artist:
        return None

    key = _cache_key(q_title, q_artist, q_album, q_duration_ms)
    if use_cache:
        hit, cached = _read_cache(key)
        if hit:
            return cached

    if not allow_network:
        return None

    with _retry_lock:
        cooldown_until = _retry_after_until
    if time.monotonic() < cooldown_until:
        msg = "Proveedor de letras en espera por límite de peticiones."
        _set_last_error(msg)
        if raise_on_error:
            raise LyricsFetchError(msg)
        return None

    params: dict[str, Any] = {
        "track_name": q_title,
        "artist_name": q_artist,
    }
    if q_album:
        params["album_name"] = q_album
    if q_duration_ms > 0:
        params["duration"] = round(q_duration_ms / 1000)

    try:
        resp = requests.get(
            LRCLIB_GET_URL,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout_s,
        )
    except requests.RequestException as exc:
        msg = f"Error de red al consultar letras: {exc}"
        _set_last_error(msg)
        if raise_on_error:
            raise LyricsFetchError(msg) from exc
        return None

    if resp.status_code in (429, 503):
        delay = _parse_retry_after(resp.headers.get("Retry-After"))
        with _retry_lock:
            _retry_after_until = time.monotonic() + delay
        msg = f"Proveedor de letras saturado (HTTP {resp.status_code})."
        _set_last_error(msg)
        if raise_on_error:
            raise LyricsFetchError(msg)
        return None

    if resp.status_code == 404:
        if use_cache:
            _write_cache(key, {"not_found": True, "cached_at": time.time()})
        return None

    if resp.status_code != 200:
        msg = f"Error HTTP {resp.status_code} al consultar letras."
        _set_last_error(msg)
        if raise_on_error:
            raise LyricsFetchError(msg)
        return None

    try:
        body = resp.json()
    except ValueError as exc:
        msg = "Respuesta inválida del proveedor de letras."
        _set_last_error(msg)
        if raise_on_error:
            raise LyricsFetchError(msg) from exc
        return None
    if not isinstance(body, dict):
        return None

    synced_raw = str(body.get("syncedLyrics") or "")
    plain = str(body.get("plainLyrics") or "").strip()
    instrumental = bool(body.get("instrumental", False))
    lines = parse_lrc(synced_raw)

    if not lines and not plain and not instrumental:
        return None

    if lines and not plain:
        plain = "\n".join(line.text for line in lines if line.text)

    if use_cache:
        _write_cache(
            key,
            {
                "synced_lyrics": synced_raw,
                "plain_lyrics": plain,
                "instrumental": instrumental,
                "cached_at": time.time(),
            },
        )

    return LyricsResult(
        lines=lines,
        plain_text=plain,
        synced=bool(lines),
        instrumental=instrumental,
        source="lrclib",
    )


def get_lyrics(
    track: Any = None,
    *,
    audio_path: str | Path = "",
    title: str = "",
    artist: str = "",
    album: str = "",
    duration_ms: int = 0,
    use_cache: bool = True,
    allow_network: bool = True,
    raise_on_error: bool = False,
) -> LyricsResult | None:
    """Orden: ``.lrc`` local -> tags embebidos -> caché ``config/lyrics/`` -> LRCLIB."""
    _set_last_error("")
    if audio_path:
        local = load_local_lyrics(audio_path)
        if local is not None:
            return local
    return fetch_lyrics(
        track,
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        use_cache=use_cache,
        allow_network=allow_network,
        raise_on_error=raise_on_error,
    )
