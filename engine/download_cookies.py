"""engine/download_cookies.py — Cookies de la sesión de YouTube para yt-dlp.

El jar vive solo en memoria: nunca se serializa a disco.
"""

from __future__ import annotations

import yt_dlp
from typing import Any, Callable, Optional

from urllib.parse import unquote


# ──────────────────────────────────────────────────────────────────────
# Cookies de la sesión existente
# ──────────────────────────────────────────────────────────────────────

def _load_cookie_jar(
    cookie_provider: Optional[Callable[[], dict[str, str]]] = None,
) -> Optional[Any]:
    """Construye un cookie jar en memoria desde ``cookie_provider`` o ``browser_session.youtube_cookies()``.

    La decisión de Descargas prohíbe introducir un ``cookies.txt`` en disco.
    Se reutilizan las cookies de YouTube resueltas desde el navegador o desde
    ``config/browser.json`` como fallback. Si no hay cookies, devuelve ``None``.

    El jar se mantiene en memoria: nunca se serializa a disco.
    """
    try:
        from yt_dlp.cookies import YoutubeDLCookieJar
    except ImportError:
        return None

    if cookie_provider is None:
        try:
            from services.browser_session import youtube_cookies
        except ImportError:
            return None
        cookie_provider = youtube_cookies

    cookies = cookie_provider()
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


def _apply_cookie_jar(ydl: "yt_dlp.YoutubeDL", opts: dict[str, Any]) -> None:
    """Inyecta el cookie jar privado en la instancia de ``YoutubeDL``."""
    jar = opts.pop("_cookie_jar", None)
    if jar is not None:
        ydl.cookiejar = jar
