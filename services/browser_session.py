"""Detección de sesiones del navegador y extracción de credenciales en memoria.

Este módulo lee las cookies activas de navegadores Firefox-family (con ``sqlite3``
de la stdlib sobre una copia temporal en RAM incluyendo ``-wal`` y ``-shm``) y
Chromium-family (delegado en ``yt_dlp.cookies.extract_cookies_from_browser`` en
rutas estándar). No persiste nada en disco. Los archivos de ``config/`` actúan
como último candidato (override manual).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Iterator

import httpx

from services.authentication import (
    BROWSER_JSON_FIXED,
    read_browser_json,
    read_env_values,
    read_spotify_cookies,
)

# Nombres permitidos en la consulta SQLite de Firefox y en la extracción general.
# Además de los 8 base de YouTube, se incluyen las cookies Google `__Secure-1P*`,
# `__Secure-3PSIDTS` y `*SIDCC` que YouTube Music exige en sesiones modernas para
# no degradar `browse` a respuesta anónima (`messageRenderer` de inicio de sesión).
YOUTUBE_ALLOWED_COOKIES: tuple[str, ...] = (
    "SAPISID",
    "__Secure-3PAPISID",
    "LOGIN_INFO",
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-1PSID",
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "SIDCC",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC",
)

SPOTIFY_ALLOWED_COOKIES: tuple[str, ...] = (
    "sp_dc",
    "sp_key",
)

APPLE_ALLOWED_COOKIES: tuple[str, ...] = (
    "media-user-token",
)

ALL_ALLOWED_COOKIES: tuple[str, ...] = (
    *YOUTUBE_ALLOWED_COOKIES,
    *SPOTIFY_ALLOWED_COOKIES,
    *APPLE_ALLOWED_COOKIES,
)

# Raíces Firefox-family en orden de prioridad.
_FIREFOX_ROOT_SPECS: tuple[tuple[str, str, str], ...] = (
    ("firefox", "Firefox", "~/.mozilla/firefox"),
    ("firefox", "Firefox", "~/.config/mozilla/firefox"),
    ("firefox", "Firefox", "~/.var/app/org.mozilla.firefox/config/mozilla/firefox"),
    ("firefox", "Firefox", "~/.var/app/org.mozilla.firefox/.mozilla/firefox"),
    ("firefox", "Firefox", "~/snap/firefox/common/.mozilla/firefox"),
    ("zen", "Zen", "~/.config/zen"),
    ("librewolf", "LibreWolf", "~/.librewolf"),
    ("librewolf", "LibreWolf", "~/.var/app/io.gitlab.librewolf-community/.librewolf"),
    ("floorp", "Floorp", "~/.floorp"),
    ("waterfox", "Waterfox", "~/.waterfox"),
    ("mullvad", "Mullvad Browser", "~/.mullvad/mullvad-browser"),
    ("tor", "Tor Browser", "~/tor-browser/Browser/TorBrowser/Data/Browser"),
)

# Los 7 navegadores Chromium de yt_dlp/cookies.py:234.
_CHROMIUM_DISPLAY_NAMES: dict[str, str] = {
    "brave": "Brave",
    "chrome": "Chrome",
    "chromium": "Chromium",
    "edge": "Edge",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
    "whale": "Whale",
}

# Rutas Flatpak/Snap de Chromium-family que yt-dlp no soporta (solo reportar).
_UNSUPPORTED_CHROMIUM_SPECS: tuple[tuple[str, str], ...] = (
    ("Brave (Flatpak)", "~/.var/app/com.brave.Browser"),
    ("Chrome (Flatpak)", "~/.var/app/com.google.Chrome"),
    ("Chromium (Flatpak)", "~/.var/app/org.chromium.Chromium"),
    ("Edge (Flatpak)", "~/.var/app/com.microsoft.Edge"),
    ("Vivaldi (Flatpak)", "~/.var/app/com.vivaldi.Vivaldi"),
    ("Opera (Flatpak)", "~/.var/app/com.opera.Opera"),
    ("Chromium (Snap)", "~/snap/chromium"),
    ("Brave (Snap)", "~/snap/brave"),
    ("Opera (Snap)", "~/snap/opera"),
)

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_APPLE_ASSET_RE = re.compile(r"""src=["'](/assets/[^"']+\.js)["']""")

# Caché en memoria por proceso (nunca a disco).
_DETECTED_PROFILES: list["BrowserProfile"] | None = None
_UNSUPPORTED_NOTES: list[str] = []
_PROFILE_COOKIE_CACHE: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
_APPLE_BUNDLE_BEARER: str | None = None
_APPLE_BUNDLE_FETCHED: bool = False


@dataclass(frozen=True)
class BrowserProfile:
    engine: str        # "firefox" | "chromium"
    browser: str       # "firefox", "zen", "chrome", "brave"…
    label: str         # "Firefox · default-release"
    root: Path
    profile: str | None


def clear_cache() -> None:
    """Vacía toda la caché en memoria de perfiles, cookies y bearer de Apple."""
    global _DETECTED_PROFILES, _APPLE_BUNDLE_BEARER, _APPLE_BUNDLE_FETCHED
    _DETECTED_PROFILES = None
    _UNSUPPORTED_NOTES.clear()
    _PROFILE_COOKIE_CACHE.clear()
    _APPLE_BUNDLE_BEARER = None
    _APPLE_BUNDLE_FETCHED = False


def unsupported_browsers() -> list[str]:
    """Devuelve la lista de navegadores Chromium en Flatpak/Snap detectados y omitidos."""
    if _DETECTED_PROFILES is None:
        detect()
    return list(_UNSUPPORTED_NOTES)


def _clean_firefox_profile_label(dirname: str) -> str:
    """Convierte p. ej. ``a90u8jiw.default-release`` en ``default-release``."""
    if "." in dirname:
        prefix, _, rest = dirname.partition(".")
        if len(prefix) == 8 and prefix.isalnum() and rest:
            return rest
    return dirname


def _discover_firefox_profiles() -> list[tuple[float, BrowserProfile]]:
    found: list[tuple[float, BrowserProfile]] = []
    seen_dbs: set[Path] = set()
    for browser_key, display, raw_root in _FIREFOX_ROOT_SPECS:
        root = Path(raw_root).expanduser()
        if not root.is_dir():
            continue
        candidates: list[Path] = []
        try:
            for child in root.iterdir():
                if child.is_dir():
                    db = child / "cookies.sqlite"
                    if db.is_file():
                        candidates.append(db)
            profiles_sub = root / "Profiles"
            if profiles_sub.is_dir():
                for child in profiles_sub.iterdir():
                    if child.is_dir():
                        db = child / "cookies.sqlite"
                        if db.is_file():
                            candidates.append(db)
        except OSError:
            continue

        for db in candidates:
            try:
                resolved = db.resolve()
                if resolved in seen_dbs:
                    continue
                seen_dbs.add(resolved)
                mtime = db.stat().st_mtime
            except OSError:
                continue
            prof_dir = db.parent
            short_prof = _clean_firefox_profile_label(prof_dir.name)
            label = f"{display} · {short_prof}"
            found.append(
                (
                    mtime,
                    BrowserProfile(
                        engine="firefox",
                        browser=browser_key,
                        label=label,
                        root=prof_dir,
                        profile=prof_dir.name,
                    ),
                )
            )
    found.sort(key=lambda item: item[0], reverse=True)
    return found


def _chromium_standard_roots() -> dict[str, Path]:
    if sys.platform in ("cygwin", "win32"):
        local = Path(os.path.expandvars("%LOCALAPPDATA%"))
        roaming = Path(os.path.expandvars("%APPDATA%"))
        return {
            "brave": local / "BraveSoftware" / "Brave-Browser" / "User Data",
            "chrome": local / "Google" / "Chrome" / "User Data",
            "chromium": local / "Chromium" / "User Data",
            "edge": local / "Microsoft" / "Edge" / "User Data",
            "opera": roaming / "Opera Software" / "Opera Stable",
            "vivaldi": local / "Vivaldi" / "User Data",
            "whale": local / "Naver" / "Naver Whale" / "User Data",
        }
    if sys.platform == "darwin":
        appdata = Path("~/Library/Application Support").expanduser()
        return {
            "brave": appdata / "BraveSoftware" / "Brave-Browser",
            "chrome": appdata / "Google" / "Chrome",
            "chromium": appdata / "Chromium",
            "edge": appdata / "Microsoft Edge",
            "opera": appdata / "com.operasoftware.Opera",
            "vivaldi": appdata / "Vivaldi",
            "whale": appdata / "Naver" / "Whale",
        }
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config = Path(xdg).expanduser() if xdg else Path("~/.config").expanduser()
    return {
        "brave": config / "BraveSoftware" / "Brave-Browser",
        "chrome": config / "google-chrome",
        "chromium": config / "chromium",
        "edge": config / "microsoft-edge",
        "opera": config / "opera",
        "vivaldi": config / "vivaldi",
        "whale": config / "naver-whale",
    }


def _discover_chromium_profiles() -> list[tuple[float, BrowserProfile]]:
    found: list[tuple[float, BrowserProfile]] = []
    roots = _chromium_standard_roots()
    for browser_key, display in _CHROMIUM_DISPLAY_NAMES.items():
        root = roots.get(browser_key)
        if root is None or not root.is_dir():
            continue
        if browser_key == "opera":
            for rel in (Path("Cookies"), Path("Network") / "Cookies"):
                db = root / rel
                if db.is_file():
                    try:
                        mtime = db.stat().st_mtime
                    except OSError:
                        continue
                    found.append(
                        (
                            mtime,
                            BrowserProfile(
                                engine="chromium",
                                browser=browser_key,
                                label=display,
                                root=root,
                                profile=None,
                            ),
                        )
                    )
                    break
            continue

        try:
            children = [c for c in root.iterdir() if c.is_dir()]
        except OSError:
            continue
        for child in children:
            if child.name != "Default" and not child.name.startswith("Profile "):
                continue
            for rel in (Path("Cookies"), Path("Network") / "Cookies"):
                db = child / rel
                if db.is_file():
                    try:
                        mtime = db.stat().st_mtime
                    except OSError:
                        continue
                    found.append(
                        (
                            mtime,
                            BrowserProfile(
                                engine="chromium",
                                browser=browser_key,
                                label=f"{display} · {child.name}",
                                root=root,
                                profile=child.name,
                            ),
                        )
                    )
                    break
    found.sort(key=lambda item: item[0], reverse=True)
    return found


def detect() -> list[BrowserProfile]:
    """Detecta perfiles de Firefox y Chromium instalados en rutas estándar."""
    global _DETECTED_PROFILES
    if _DETECTED_PROFILES is not None:
        return list(_DETECTED_PROFILES)

    _UNSUPPORTED_NOTES.clear()
    for label, raw_path in _UNSUPPORTED_CHROMIUM_SPECS:
        if Path(raw_path).expanduser().exists():
            _UNSUPPORTED_NOTES.append(label)

    ff = [prof for _, prof in _discover_firefox_profiles()]
    cr = [prof for _, prof in _discover_chromium_profiles()]
    _DETECTED_PROFILES = ff + cr
    return list(_DETECTED_PROFILES)


def read_firefox_cookies(
    db_dir_or_file: Path,
    allowed_names: tuple[str, ...] = ALL_ALLOWED_COOKIES,
) -> list[tuple[str, str, str]]:
    """Copia ``cookies.sqlite`` (+ ``-wal`` y ``-shm``) a RAM, lee y borra siempre.

    Filtra por ``originAttributes = ''`` y ``name IN allowed_names``.
    Nunca usa ``nolock=1`` ni ``immutable=1`` sobre el archivo original.
    """
    db_path = (
        db_dir_or_file / "cookies.sqlite"
        if db_dir_or_file.is_dir()
        else db_dir_or_file
    )
    if not db_path.is_file() or not allowed_names:
        return []

    shm_root = Path("/dev/shm")
    tmp_base = str(shm_root) if shm_root.is_dir() and os.access(shm_root, os.W_OK) else None
    tmpdir = tempfile.TemporaryDirectory(dir=tmp_base, prefix="melo_ff_")
    conn: sqlite3.Connection | None = None
    try:
        tmp_path = Path(tmpdir.name)
        for suffix in ("", "-wal", "-shm"):
            src = db_path.parent / f"{db_path.name}{suffix}"
            if src.is_file():
                shutil.copy2(src, tmp_path / src.name)
        copied_db = tmp_path / db_path.name
        conn = sqlite3.connect(f"file:{copied_db.as_posix()}?mode=ro", uri=True)
        placeholders = ",".join("?" for _ in allowed_names)
        query = (
            "SELECT host, name, value FROM moz_cookies "
            f"WHERE originAttributes = '' AND name IN ({placeholders})"
        )
        cur = conn.execute(query, tuple(allowed_names))
        rows = [
            (str(host or ""), str(name or ""), str(value or ""))
            for host, name, value in cur.fetchall()
            if name and value
        ]
        return rows
    except Exception:
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        try:
            tmpdir.cleanup()
        except Exception:
            pass


class _SilentYtdlpLogger:
    """Logger silencioso que jamás imprime ni registra valores de cookies."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str, *args, **kwargs) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def _read_chromium_cookies(
    profile: BrowserProfile,
    allowed_names: tuple[str, ...] = ALL_ALLOWED_COOKIES,
) -> list[tuple[str, str, str]]:
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except Exception:
        return []

    allowed = set(allowed_names)
    try:
        jar = extract_cookies_from_browser(
            profile.browser,
            profile.profile,
            _SilentYtdlpLogger(),
        )
    except Exception:
        return []

    rows: list[tuple[str, str, str]] = []
    for cookie in jar:
        name = str(getattr(cookie, "name", "") or "")
        val = str(getattr(cookie, "value", "") or "")
        domain = str(getattr(cookie, "domain", "") or "")
        if name in allowed and val:
            rows.append((domain, name, val))
    return rows


def _profile_cookies(profile: BrowserProfile) -> list[tuple[str, str, str]]:
    cache_key = (profile.engine, f"{profile.root}::{profile.profile or ''}")
    if cache_key in _PROFILE_COOKIE_CACHE:
        return _PROFILE_COOKIE_CACHE[cache_key]
    if profile.engine == "firefox":
        rows = read_firefox_cookies(profile.root, ALL_ALLOWED_COOKIES)
    elif profile.engine == "chromium":
        rows = _read_chromium_cookies(profile, ALL_ALLOWED_COOKIES)
    else:
        rows = []
    _PROFILE_COOKIE_CACHE[cache_key] = rows
    return rows


def compute_sapisidhash(
    sapisid: str,
    ts: int | None = None,
    origin: str = "https://music.youtube.com",
) -> str:
    """Calcula ``SAPISIDHASH <ts>_<sha1>`` desde ``SAPISID`` sin persistir."""
    now = int(time.time()) if ts is None else int(ts)
    digest = hashlib.sha1(f"{now} {sapisid} {origin}".encode("utf-8")).hexdigest()
    return f"SAPISIDHASH {now}_{digest}"


def parse_cookie_header(raw_cookie: str) -> dict[str, str]:
    """Convierte un header ``Cookie`` en un diccionario ``{nombre: valor}``."""
    out: dict[str, str] = {}
    for chunk in str(raw_cookie or "").split(";"):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        k, _, v = piece.partition("=")
        k = k.strip()
        v = v.strip()
        if k and v:
            out[k] = v
    return out


def build_youtube_headers_from_cookies(cookies: dict[str, str]) -> dict[str, str] | None:
    """Construye los headers de ``YTMusic`` recalculando ``SAPISIDHASH``."""
    sapisid = (cookies.get("SAPISID") or cookies.get("__Secure-3PAPISID") or "").strip()
    if not sapisid:
        return None
    cookie_Map = dict(cookies)
    if "SAPISID" not in cookie_Map:
        cookie_Map["SAPISID"] = sapisid
    if "__Secure-3PAPISID" not in cookie_Map:
        cookie_Map["__Secure-3PAPISID"] = sapisid
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_Map.items() if k and v)
    if not cookie_str:
        return None
    return {
        "Accept": BROWSER_JSON_FIXED["Accept"],
        "Authorization": compute_sapisidhash(sapisid),
        "Content-Type": BROWSER_JSON_FIXED["Content-Type"],
        "X-Goog-AuthUser": BROWSER_JSON_FIXED["X-Goog-AuthUser"],
        "x-origin": BROWSER_JSON_FIXED["x-origin"],
        "Cookie": cookie_str,
    }


def _extract_youtube_cookie_map(rows: list[tuple[str, str, str]]) -> dict[str, str]:
    allowed = set(YOUTUBE_ALLOWED_COOKIES)
    cookies: dict[str, str] = {}
    for host, name, val in rows:
        host_l = host.lower()
        if name not in allowed:
            continue
        if not (
            host_l == "youtube.com"
            or host_l.endswith(".youtube.com")
            or host_l == "google.com"
            or host_l.endswith(".google.com")
        ):
            continue
        # Preferir el valor de youtube.com cuando exista en ambos dominios.
        if "youtube.com" in host_l or name not in cookies:
            cookies[name] = val
    return cookies


def select_apple_jwt(js_text: str) -> str | None:
    """Elige del bundle JS el único JWT con ``kid == 'WebPlayKid'`` e ``iss == 'AMPWebPlay'``."""
    for token in _JWT_RE.findall(js_text or ""):
        parts = token.split(".")
        if len(parts) != 3:
            continue
        try:
            hdr_raw = base64.urlsafe_b64decode(parts[0] + "===")
            pay_raw = base64.urlsafe_b64decode(parts[1] + "===")
            hdr = json.loads(hdr_raw.decode("utf-8"))
            pay = json.loads(pay_raw.decode("utf-8"))
        except Exception:
            continue
        if (
            isinstance(hdr, dict)
            and isinstance(pay, dict)
            and hdr.get("kid") == "WebPlayKid"
            and pay.get("iss") == "AMPWebPlay"
        ):
            return token
    return None


def _derive_apple_bearer() -> str | None:
    """Obtiene el bearer de ``config/.env`` si existe; si no, lo deriva del bundle público."""
    env = read_env_values()
    env_bearer = env.get("APPLE_AUTH_BEARER", "").strip()
    if env_bearer:
        return env_bearer

    global _APPLE_BUNDLE_BEARER, _APPLE_BUNDLE_FETCHED
    if _APPLE_BUNDLE_FETCHED:
        return _APPLE_BUNDLE_BEARER

    _APPLE_BUNDLE_FETCHED = True
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    try:
        with httpx.Client(
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": ua},
        ) as client:
            resp = client.get("https://music.apple.com/")
            if not resp.is_success:
                return None
            assets = _APPLE_ASSET_RE.findall(resp.text)
            for asset in assets:
                js_resp = client.get(f"https://music.apple.com{asset}")
                if not js_resp.is_success:
                    continue
                jwt = select_apple_jwt(js_resp.text)
                if jwt:
                    _APPLE_BUNDLE_BEARER = jwt
                    return jwt
    except Exception:
        return None
    return None


def youtube_candidates(preferred_source: str | None = None) -> Iterator[tuple[str, dict]]:
    """Itera candidatos ``(origen, headers)`` para YouTube Music: navegador primero, archivo después."""
    items: list[tuple[str, dict]] = []
    for prof in detect():
        cookies = _extract_youtube_cookie_map(_profile_cookies(prof))
        headers = build_youtube_headers_from_cookies(cookies)
        if headers is not None:
            items.append((prof.label, headers))

    bj = read_browser_json()
    raw_cookie = str(bj.get("Cookie", "") or "").strip()
    if raw_cookie:
        cookies = parse_cookie_header(raw_cookie)
        headers = build_youtube_headers_from_cookies(cookies)
        if headers is None and bj.get("Authorization"):
            headers = dict(bj)
        if headers is not None:
            items.append(("config/browser.json", headers))

    yield from _order_candidates(items, preferred_source)


def spotify_candidates(preferred_source: str | None = None) -> Iterator[tuple[str, dict]]:
    """Itera candidatos ``(origen, dump)`` para Spotify: navegador primero, archivo después."""
    items: list[tuple[str, dict]] = []
    for prof in detect():
        sp_cookies: dict[str, str] = {}
        for host, name, val in _profile_cookies(prof):
            host_l = host.lower()
            if name in SPOTIFY_ALLOWED_COOKIES and (
                host_l == "spotify.com" or host_l.endswith(".spotify.com")
            ):
                sp_cookies[name] = val
        if sp_cookies.get("sp_dc") and sp_cookies.get("sp_key"):
            items.append(
                (
                    prof.label,
                    {
                        "identifier": "browser",
                        "cookies": {
                            "sp_dc": sp_cookies["sp_dc"],
                            "sp_key": sp_cookies["sp_key"],
                        },
                    },
                )
            )

    sc = read_spotify_cookies()
    cookies = sc.get("cookies", {})
    if isinstance(cookies, str):
        cookies = parse_cookie_header(cookies)
    if isinstance(cookies, dict) and cookies.get("sp_dc") and cookies.get("sp_key"):
        ident = str(sc.get("identifier", "") or "").strip() or "manual"
        items.append(
            (
                "config/spotify_cookies.json",
                {
                    "identifier": ident,
                    "cookies": {
                        "sp_dc": str(cookies["sp_dc"]).strip(),
                        "sp_key": str(cookies["sp_key"]).strip(),
                    },
                },
            )
        )

    yield from _order_candidates(items, preferred_source)


def apple_candidates(preferred_source: str | None = None) -> Iterator[tuple[str, dict]]:
    """Itera candidatos ``(origen, {'bearer', 'user_token'})`` para Apple Music."""
    profile_tokens: list[tuple[str, str]] = []
    for prof in detect():
        token = ""
        for host, name, val in _profile_cookies(prof):
            host_l = host.lower()
            if name == "media-user-token" and "music.apple.com" in host_l:
                if host_l in (".music.apple.com", "music.apple.com") or not token:
                    token = val
        if token:
            profile_tokens.append((prof.label, token))

    env = read_env_values()
    env_bearer = env.get("APPLE_AUTH_BEARER", "").strip()
    env_utok = env.get("APPLE_MUSIC_USER_TOKEN", "").strip()

    def _gen() -> Iterator[tuple[str, dict]]:
        if profile_tokens:
            bearer = _derive_apple_bearer()
            if bearer:
                for label, utok in profile_tokens:
                    yield (label, {"bearer": bearer, "user_token": utok})
        if env_utok:
            bearer = env_bearer or _derive_apple_bearer()
            if bearer:
                yield ("config/.env", {"bearer": bearer, "user_token": env_utok})

    if not preferred_source:
        yield from _gen()
        return

    all_items = list(_gen())
    yield from _order_candidates(all_items, preferred_source)


def _order_candidates(
    items: list[tuple[str, dict]],
    preferred_source: str | None,
) -> Iterator[tuple[str, dict]]:
    if not preferred_source:
        yield from items
        return
    preferred = [it for it in items if it[0] == preferred_source]
    others = [it for it in items if it[0] != preferred_source]
    yield from preferred
    yield from others


def youtube_cookies() -> dict[str, str]:
    """Devuelve las cookies de YouTube en memoria para ``engine/downloader.py``."""
    for _, headers in youtube_candidates():
        parsed = parse_cookie_header(headers.get("Cookie", ""))
        if parsed:
            return parsed
    return {}


__all__ = [
    "ALL_ALLOWED_COOKIES",
    "APPLE_ALLOWED_COOKIES",
    "BrowserProfile",
    "SPOTIFY_ALLOWED_COOKIES",
    "YOUTUBE_ALLOWED_COOKIES",
    "apple_candidates",
    "build_youtube_headers_from_cookies",
    "clear_cache",
    "compute_sapisidhash",
    "detect",
    "parse_cookie_header",
    "read_firefox_cookies",
    "select_apple_jwt",
    "spotify_candidates",
    "unsupported_browsers",
    "youtube_candidates",
    "youtube_cookies",
]
