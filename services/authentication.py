"""Credential storage and platform pre-flight checks.

This module owns the runtime configuration contract.  Credentials and the
search cache live exclusively under the project-level ``config/`` directory;
no legacy root-level paths are consulted.
"""

from __future__ import annotations

import asyncio
import json
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


def _preflight_youtube() -> PreFlightResult:
    result = PreFlightResult("YouTube Music")
    browser = read_browser_json()
    if not browser.get("Authorization") or not browser.get("Cookie"):
        result.error = "config/browser.json: falta Authorization o Cookie"
        result.expired = True
        return result
    if not browser.get("Authorization", "").startswith("SAPISIDHASH"):
        result.error = "Authorization no comienza con 'SAPISIDHASH'"
        result.expired = True
        return result
    try:
        from ytmusicapi import YTMusic  # pylint: disable=import-outside-toplevel

        ytm = YTMusic(str(BROWSER_JSON))
        ytm.get_history()
        result.ok = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        message = str(exc).lower()
        result.code = AuthFailureCode.YT_EXPIRED
        result.expired = True
        result.error = (
            "401 — token expirado o inválido"
            if any(word in message for word in ("401", "unauthorized", "sign in", "cookie", "parse"))
            else str(exc)[:200]
        )
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
    hints = {
        "YouTube Music": "config/browser.json: Cookie + Authorization (SAPISIDHASH)",
        "Apple Music": "config/.env: APPLE_AUTH_BEARER + APPLE_MUSIC_USER_TOKEN",
        "Spotify": "config/spotify_cookies.json: identifier + sp_dc + sp_key",
    }
    tag = f"[{result.code}] " if result.code else ""
    return f"{tag}{hints.get(result.platform, result.platform)} · {result.error}"[:500]


async def run_preflight() -> list[PreFlightResult]:
    """Run all platform checks concurrently in the configured order."""
    checks = {
        "YouTube Music": _preflight_youtube,
        "Apple Music": _preflight_apple,
        "Spotify": _preflight_spotify,
    }
    results = await asyncio.gather(
        *(asyncio.to_thread(checks[platform]) for platform in PLATFORM_ORDER),
        return_exceptions=True,
    )
    output: list[PreFlightResult] = []
    for platform, result in zip(PLATFORM_ORDER, results):
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
]
