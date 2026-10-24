"""Credential storage and platform pre-flight checks.

This module owns the runtime configuration contract.  Credentials and the
search cache live exclusively under the project-level ``config/`` directory;
no legacy root-level paths are consulted.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import requests
from dotenv import dotenv_values, load_dotenv, set_key

from core.config import APPLE_API_BASE, PLATFORM_ORDER

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
ENV_FILE = CONFIG_DIR / ".env"
BROWSER_JSON = CONFIG_DIR / "browser.json"
SPOTIFY_COOKIES_JSON = CONFIG_DIR / "spotify_cookies.json"
SEARCH_CACHE_JSON = CONFIG_DIR / "search_cache.json"
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


def read_browser_json() -> dict:
    """Return parsed YouTube headers, or an empty mapping if unavailable."""
    if not BROWSER_JSON.exists():
        return {}
    try:
        return json.loads(BROWSER_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_browser_json(authorization: str, cookie: str) -> None:
    """Write the spec-compliant YouTube browser headers file."""
    ensure_config_dir()
    data = {
        "Accept": BROWSER_JSON_FIXED["Accept"],
        "Authorization": authorization.strip(),
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


def youtube_preflight_result(ok: bool, error: str = "") -> PreFlightResult:
    """Map a YouTube probe result without treating transient errors as expiry."""
    result = PreFlightResult("YouTube Music")
    result.ok = ok
    if ok:
        return result
    result.error = error or "No se pudo validar YouTube Music"
    status = youtube_http_status(error)
    if status == 401 or (status is None and "unauthorized" in result.error.lower()):
        result.code = AuthFailureCode.YT_EXPIRED
        result.expired = True
        result.error = "401 — sesión de YouTube Music rechazada"
    elif status == 403:
        result.code = AuthFailureCode.YT_FORBIDDEN
        result.error = f"403 — YouTube Music rechazó la solicitud: {error[:160]}"
    elif status == 429:
        result.code = AuthFailureCode.YT_RATE_LIMITED
        result.error = f"429 — límite de solicitudes de YouTube Music: {error[:160]}"
    elif any(term in result.error.lower() for term in (
        "missing config/browser.json", "falta authorization", "no comienza con",
        "cookie is missing", "cookie missing", "invalid cookie",
    )):
        result.code = AuthFailureCode.YT_EXPIRED
        result.expired = True
    else:
        result.code = AuthFailureCode.YT_CHECK_FAILED
    return result


def _apple_403_error(response) -> str:
    """Extract Apple API diagnostics from a 403 response."""
    detail = ""
    try:
        errors = response.json().get("errors", [])
        if errors:
            detail = f"{errors[0].get('code', '')} {errors[0].get('title', '')}".strip()
    except Exception:  # pylint: disable=broad-exception-caught
        detail = (response.text or "")[:120]
    retry_after = (response.headers.get("Retry-After") or "").strip()
    message = "403 — amp-api bloquea este token"
    if detail:
        message += f" · {detail}"
    if retry_after:
        message += f" · Retry-After: {retry_after}"
    return message


def _preflight_apple() -> PreFlightResult:
    result = PreFlightResult("Apple Music")
    env = read_env_values()
    bearer = env.get("APPLE_AUTH_BEARER", "").strip()
    user_token = env.get("APPLE_MUSIC_USER_TOKEN", "").strip()
    if not bearer or not user_token:
        result.error = "APPLE_AUTH_BEARER or APPLE_MUSIC_USER_TOKEN missing"
        return result

    headers = {
        "Authorization": bearer if bearer.startswith("Bearer ") else f"Bearer {bearer}",
        "media-user-token": user_token,
        "x-apple-music-user-token": user_token,
        "Origin": "https://music.apple.com",
        "Referer": "https://music.apple.com/",
        "Accept": "application/json",
    }
    try:
        response = requests.get(f"{APPLE_API_BASE}/me/storefront", headers=headers, timeout=8)
        if response.status_code == 401:
            result.expired = True
            result.code = AuthFailureCode.APPLE_EXPIRED
            result.error = "401 — Apple Music token expired"
            return result
        if response.status_code == 403:
            result.code = AuthFailureCode.APPLE_EXPIRED
            result.error = _apple_403_error(response)
            return result
        if response.status_code != 200:
            result.error = f"Unexpected HTTP {response.status_code}"
            return result

        storefront = response.json().get("data", [{}])[0].get("id", "us")
        catalog = requests.get(
            f"https://api.music.apple.com/v1/catalog/{storefront}/search",
            params={"term": "a", "types": "songs", "limit": 1},
            headers=headers,
            timeout=8,
        )
        if catalog.status_code == 401:
            result.expired = True
            result.code = AuthFailureCode.APPLE_EXPIRED
            result.error = "401 — catálogo rechazó media-user-token"
        elif catalog.status_code == 200:
            result.ok = True
        else:
            result.code = AuthFailureCode.APPLE_EXPIRED
            result.error = f"Catálogo HTTP {catalog.status_code}"
    except requests.RequestException as exc:
        result.error = str(exc)
    return result


def _preflight_spotify() -> PreFlightResult:
    result = PreFlightResult("Spotify")
    spotify = read_spotify_cookies()
    cookies = spotify.get("cookies", {})
    if isinstance(cookies, str):
        cookies = {}
    if not spotify.get("identifier") or not cookies.get("sp_dc") or not cookies.get("sp_key"):
        result.error = "config/spotify_cookies.json: falta identifier, sp_dc o sp_key"
        result.expired = True
        return result
    try:
        from spotapi import Config, Login  # pylint: disable=import-outside-toplevel
        from spotapi.utils.logger import NoopLogger  # pylint: disable=import-outside-toplevel

        login = Login.from_cookies(spotify, Config(logger=NoopLogger()))
        if not login.logged_in:
            result.error = "cookies inválidas (login falló)"
            result.expired = True
            return result
        result.ok = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        message = str(exc).lower()
        result.code = AuthFailureCode.SPOTIFY_EXPIRED
        result.expired = True
        result.error = (
            "401 — cookies expiradas o inválidas"
            if any(word in message for word in ("401", "unauthorized", "invalid", "cookie", "login"))
            else str(exc)[:200]
        )
    return result


def auth_failure_tooltip(result: PreFlightResult) -> str:
    """Build the concise authentication hint displayed by the UI."""
    if result.ok:
        return ""
    if result.platform == "YouTube Music" and not result.expired:
        tag = f"[{result.code}] " if result.code else ""
        return f"{tag}YouTube Music · {result.error}"[:500]
    hints = {
        "YouTube Music": "config/browser.json: Cookie + Authorization (SAPISIDHASH)",
        "Apple Music": "config/.env: APPLE_AUTH_BEARER + APPLE_MUSIC_USER_TOKEN",
        "Spotify": "config/spotify_cookies.json: identifier + sp_dc + sp_key",
    }
    tag = f"[{result.code}] " if result.code else ""
    return f"{tag}{hints.get(result.platform, result.platform)} · {result.error}"[:500]


async def run_preflight(platforms: list[str] | None = None) -> list[PreFlightResult]:
    """Run Apple Music and Spotify checks concurrently in platform order.

    YouTube Music is checked by AuthManager through MusicApiService so the
    probe shares the production client and request path.
    """
    checks = {
        "Apple Music": _preflight_apple,
        "Spotify": _preflight_spotify,
    }
    wanted = set(platforms) if platforms is not None else None
    selected = [
        platform for platform in PLATFORM_ORDER
        if platform in checks and (wanted is None or platform in wanted)
    ]
    if not selected:
        return []
    results = await asyncio.gather(
        *(asyncio.to_thread(checks[platform]) for platform in selected),
        return_exceptions=True,
    )
    output: list[PreFlightResult] = []
    for platform, result in zip(selected, results):
        if isinstance(result, Exception):
            failure = PreFlightResult(platform)
            failure.error = str(result)
            output.append(failure)
        else:
            output.append(result)
    return output


__all__ = [
    "CONFIG_DIR",
    "ENV_FILE",
    "BROWSER_JSON",
    "SPOTIFY_COOKIES_JSON",
    "SEARCH_CACHE_JSON",
    "ENV_KEYS_APPLE",
    "ENV_KEYS_ALL",
    "PLATFORM_ORDER",
    "AuthFailureCode",
    "PreFlightResult",
    "auth_failure_tooltip",
    "ensure_config_dir",
    "load_runtime_env",
    "read_browser_json",
    "write_browser_json",
    "read_env_values",
    "write_env_values",
    "read_spotify_cookies",
    "write_spotify_cookies",
    "run_preflight",
    "youtube_http_status",
    "youtube_preflight_result",
]
