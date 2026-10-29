"""Credential storage and platform pre-flight checks.

This module owns the runtime configuration contract.  Credentials and the
search cache live exclusively under the project-level ``config/`` directory;
no legacy root-level paths are consulted.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key

from core.config import PLATFORM_ORDER

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
ENV_FILE = CONFIG_DIR / ".env"
BROWSER_JSON = CONFIG_DIR / "browser.json"
SPOTIFY_COOKIES_JSON = CONFIG_DIR / "spotify_cookies.json"
SEARCH_CACHE_JSON = CONFIG_DIR / "search_cache.json"
# Preferencias del usuario (carpeta de descargas, carpetas locales de Biblioteca).
SETTINGS_JSON = CONFIG_DIR / "settings.json"
# Historial append-only de transferencias y resoluciones manuales (JSONL).
TRANSFER_HISTORY_JSONL = CONFIG_DIR / "transfer_history.jsonl"
TRANSFER_REPORTS_DIR = CONFIG_DIR / "transfer_reports"

BROWSER_JSON_FIXED: dict[str, str] = {
    "Accept": "*/*",
    "Content-Type": "application/json",
    "X-Goog-AuthUser": "0",
    "x-origin": "https://music.youtube.com",
}

ENV_KEYS_APPLE = [
    "APPLE_AUTH_BEARER",
    "APPLE_MUSIC_USER_TOKEN",
]
ENV_KEYS_ALL = ENV_KEYS_APPLE


def ensure_config_dir() -> Path:
    """Create and return the runtime configuration directory."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def load_runtime_env(*, override: bool = False) -> None:
    """Load only ``config/.env`` into the current process environment."""
    ensure_config_dir()
    load_dotenv(str(ENV_FILE), override=override)


def _recalc_sapisidhash_from_cookie(raw_cookie: str) -> str:
    """Recalcula ``SAPISIDHASH`` desde ``SAPISID`` o ``__Secure-3PAPISID`` en memoria."""
    cookies: dict[str, str] = {}
    for chunk in str(raw_cookie or "").split(";"):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        k, _, v = piece.partition("=")
        cookies[k.strip()] = v.strip()
    sapisid = cookies.get("SAPISID") or cookies.get("__Secure-3PAPISID") or ""
    if not sapisid:
        return ""
    ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} https://music.youtube.com".encode("utf-8")).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def read_browser_json() -> dict:
    """Return parsed YouTube headers, recalculating SAPISIDHASH from Cookie and ignoring legacy saved Authorization."""
    if not BROWSER_JSON.exists():
        return {}
    try:
        data = json.loads(BROWSER_JSON.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        data.pop("Authorization", None)
        raw_cookie = str(data.get("Cookie", "") or "").strip()
        if raw_cookie:
            recalc = _recalc_sapisidhash_from_cookie(raw_cookie)
            if recalc:
                data["Authorization"] = recalc
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def write_browser_json(authorization: str = "", cookie: str = "") -> None:
    """Write the YouTube browser headers file without persisting SAPISIDHASH."""
    ensure_config_dir()
    data = {
        "Accept": BROWSER_JSON_FIXED["Accept"],
        "Content-Type": BROWSER_JSON_FIXED["Content-Type"],
        "X-Goog-AuthUser": BROWSER_JSON_FIXED["X-Goog-AuthUser"],
        "x-origin": BROWSER_JSON_FIXED["x-origin"],
        "Cookie": cookie.strip(),
    }
    BROWSER_JSON.write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
    )


def read_env_values() -> dict[str, str]:
    """Return the configured Apple Music values."""
    raw = dotenv_values(str(ENV_FILE)) if ENV_FILE.exists() else {}
    return {key: raw.get(key, "") or "" for key in ENV_KEYS_ALL}


def write_env_values(values: dict[str, str]) -> None:
    """Upsert the supported Apple Music values in ``config/.env``."""
    ensure_config_dir()
    if not ENV_FILE.exists():
        ENV_FILE.write_text(
            "# APPLE MUSIC\n"
            + "\n".join(f'{key}=""' for key in ENV_KEYS_APPLE)
            + "\n",
            encoding="utf-8",
        )
    for key, value in values.items():
        if key in ENV_KEYS_ALL:
            set_key(str(ENV_FILE), key, value, quote_mode="always")
    load_runtime_env(override=True)


def read_spotify_cookies() -> dict:
    """Return parsed Spotify cookies, or an empty mapping if unavailable."""
    if not SPOTIFY_COOKIES_JSON.exists():
        return {}
    try:
        return json.loads(SPOTIFY_COOKIES_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_spotify_cookies(identifier: str, sp_dc: str, sp_key: str) -> None:
    """Write the cookie dump expected by ``spotapi.Login.from_cookies``."""
    ensure_config_dir()
    data = {
        "identifier": identifier.strip(),
        "cookies": {"sp_dc": sp_dc.strip(), "sp_key": sp_key.strip()},
    }
    SPOTIFY_COOKIES_JSON.write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
    )


class AuthFailureCode:
    """Stable authentication failure codes consumed by the UI."""

    NO_CREDENTIALS = "NO_CREDENTIALS"
    YT_EXPIRED = "YT_EXPIRED"
    YT_FORBIDDEN = "YT_FORBIDDEN"
    YT_RATE_LIMITED = "YT_RATE_LIMITED"
    YT_CHECK_FAILED = "YT_CHECK_FAILED"
    APPLE_EXPIRED = "APPLE_EXPIRED"
    SPOTIFY_EXPIRED = "SPOTIFY_EXPIRED"


class PreFlightResult:
    """Outcome of one platform pre-flight check."""

    def __init__(self, platform: str):
        self.platform = platform
        self.ok = False
        self.error = ""
        self.expired = False
        self.code = ""
        self.source = ""

    def __repr__(self) -> str:
        status = "OK" if self.ok else f"FAIL({'EXPIRED' if self.expired else self.error[:30]})"
        return f"<PreFlight {self.platform}: {status}>"


def youtube_http_status(error: BaseException | str) -> int | None:
    """Extract the HTTP status exposed by ytmusicapi or requests errors."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    message = str(error)
    match = re.search(r"(?:http(?:/\d(?:\.\d)?)?\s+|status code:\s*)(\d{3})", message, re.I)
    if match is None:
        match = re.search(r"\b(401|403|429)\b", message)
    return int(match.group(1)) if match else None


def preflight_result(platform: str, ok: bool, error: str = "", source: str = "") -> PreFlightResult:
    """Map a platform probe result preserving 401/403/429 and AuthFailureCode."""
    result = PreFlightResult(platform)
    result.ok = ok
    result.source = source
    if ok:
        return result

    raw_error = str(error or "").strip()
    lower = raw_error.lower()

    if any(
        term in lower
        for term in (
            "missing credentials",
            "no credentials",
            "missing config/",
            "sin sesión en navegador",
            "missing",
            "falta sp_dc",
            "falta identifier",
            "falta authorization",
        )
    ):
        result.code = AuthFailureCode.NO_CREDENTIALS
        result.expired = True
        result.error = raw_error or f"Sin credenciales para {platform}"
        return result

    status = youtube_http_status(raw_error)

    if platform == "YouTube Music":
        result.error = raw_error or "No se pudo validar YouTube Music"
        if status == 401 or (
            status is None
            and any(word in lower for word in ("unauthorized", "sign in", "login"))
        ):
            result.code = AuthFailureCode.YT_EXPIRED
            result.expired = True
            result.error = (
                "401 — sesión de YouTube Music rechazada"
                if status == 401
                else "YouTube Music pide iniciar sesión de nuevo"
            )
        elif status == 403:
            result.code = AuthFailureCode.YT_FORBIDDEN
            result.error = f"403 — YouTube Music rechazó la solicitud: {raw_error[:160]}"
        elif status == 429:
            result.code = AuthFailureCode.YT_RATE_LIMITED
            result.error = f"429 — límite de solicitudes de YouTube Music: {raw_error[:160]}"
        elif any(
            term in lower
            for term in (
                "no comienza con",
                "cookie is missing",
                "cookie missing",
                "invalid cookie",
            )
        ):
            result.code = AuthFailureCode.YT_EXPIRED
            result.expired = True
        else:
            result.code = AuthFailureCode.YT_CHECK_FAILED
        return result

    if platform == "Apple Music":
        result.error = raw_error or "No se pudo validar Apple Music"
        if status in (401, 403) or any(
            w in lower for w in ("401", "403", "expired", "token", "rechazada", "bloquea")
        ):
            result.code = AuthFailureCode.APPLE_EXPIRED
            result.expired = True
        return result

    if platform == "Spotify":
        result.error = raw_error or "No se pudo validar Spotify"
        if status in (401, 403) or any(
            w in lower for w in ("401", "403", "unauthorized", "invalid", "cookie", "login", "libraryv3", "falló")
        ):
            result.code = AuthFailureCode.SPOTIFY_EXPIRED
            result.expired = True
        return result

    result.error = raw_error or f"No se pudo validar {platform}"
    return result


def youtube_preflight_result(ok: bool, error: str = "") -> PreFlightResult:
    """Alias compatible que delega en ``preflight_result('YouTube Music', ...)``."""
    return preflight_result("YouTube Music", ok, error)


def auth_failure_tooltip(result: PreFlightResult) -> str:
    """Build the concise authentication hint displayed by the UI."""
    if result.ok:
        return ""
    if result.platform == "YouTube Music" and not result.expired:
        tag = f"[{result.code}] " if result.code else ""
        return f"{tag}YouTube Music · {result.error}"[:500]
    hints = {
        "YouTube Music": "Inicia sesión en el navegador o revisa config/browser.json",
        "Apple Music": "Inicia sesión en el navegador o revisa config/.env",
        "Spotify": "Inicia sesión en el navegador o revisa config/spotify_cookies.json",
    }
    tag = f"[{result.code}] " if result.code else ""
    return f"{tag}{hints.get(result.platform, result.platform)} · {result.error}"[:500]


__all__ = [
    "CONFIG_DIR",
    "ENV_FILE",
    "BROWSER_JSON",
    "SPOTIFY_COOKIES_JSON",
    "SEARCH_CACHE_JSON",
    "SETTINGS_JSON",
    "TRANSFER_HISTORY_JSONL",
    "TRANSFER_REPORTS_DIR",
    "BROWSER_JSON_FIXED",
    "ENV_KEYS_APPLE",
    "ENV_KEYS_ALL",
    "PLATFORM_ORDER",
    "AuthFailureCode",
    "PreFlightResult",
    "auth_failure_tooltip",
    "ensure_config_dir",
    "load_runtime_env",
    "preflight_result",
    "read_browser_json",
    "write_browser_json",
    "read_env_values",
    "write_env_values",
    "read_spotify_cookies",
    "write_spotify_cookies",
    "youtube_http_status",
    "youtube_preflight_result",
]
