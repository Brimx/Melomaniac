"""
services/stream_proxy.py — Proxy HTTP local en memoria para streaming con headers
═════════════════════════════════════════════════════════════════════════════════
Reenvía peticiones HTTP (incluyendo ``Range`` para seek) desde ``flet-audio`` /
GStreamer hacia URLs de ``googlevideo.com`` inyectando los ``http_headers``
exactos que usó ``yt-dlp``. Evita errores ``403 Forbidden`` sin escribir nada
en disco.
"""

from __future__ import annotations

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import httpx

_LOCK = threading.Lock()
_SERVER: ThreadingHTTPServer | None = None
_THREAD: threading.Thread | None = None
_PORT: int = 0
_ROUTES: dict[str, tuple[str, dict[str, str]]] = {}
_MAX_ROUTES = 64
_CHUNK_SIZE = 65536
_UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=25.0, write=10.0, pool=10.0)
_FORWARD_RESP_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
)


def _sanitize_headers(raw: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        ks = str(k or "").strip()
        vs = str(v or "").strip()
        if not ks or not vs:
            continue
        if ks.lower() in ("host", "range", "connection", "accept-encoding"):
            continue
        out[ks] = vs
    return out


class _StreamProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _resolve_target(self) -> tuple[str, dict[str, str]] | None:
        path = urlparse(self.path).path
        if not path.startswith("/stream/"):
            return None
        tail = path[len("/stream/"):]
        token = tail.split(".", 1)[0]
        with _LOCK:
            return _ROUTES.get(token)

    def _proxy(self, *, send_body: bool) -> None:
        target = self._resolve_target()
        if target is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        upstream_url, base_headers = target
        req_headers = dict(base_headers)
        rng = self.headers.get("Range")
        if rng:
            req_headers["Range"] = rng

        method = "GET" if send_body else "HEAD"
        try:
            with httpx.Client(follow_redirects=True, timeout=_UPSTREAM_TIMEOUT) as client:
                with client.stream(method, upstream_url, headers=req_headers) as resp:
                    self.send_response(resp.status_code)
                    has_accept_ranges = False
                    for hdr in _FORWARD_RESP_HEADERS:
                        val = resp.headers.get(hdr)
                        if val:
                            if hdr == "accept-ranges":
                                has_accept_ranges = True
                            self.send_header(hdr.title(), val)
                    if not has_accept_ranges:
                        self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if not send_body:
                        return
                    for chunk in resp.iter_bytes(_CHUNK_SIZE):
                        if not chunk:
                            continue
                        self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Exception:
            try:
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except Exception:
                return

    def do_HEAD(self) -> None:
        self._proxy(send_body=False)

    def do_GET(self) -> None:
        self._proxy(send_body=True)


def _ensure_server_started() -> int:
    global _SERVER, _THREAD, _PORT
    if _SERVER is not None and _PORT > 0:
        return _PORT
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StreamProxyHandler)
    srv.daemon_threads = True
    port = int(srv.server_address[1])
    thr = threading.Thread(target=srv.serve_forever, name="melomaniac-stream-proxy", daemon=True)
    thr.start()
    _SERVER = srv
    _THREAD = thr
    _PORT = port
    return port


def register_stream_url(
    url: str,
    headers: dict[str, Any] | None = None,
    *,
    ext: str = "m4a",
) -> str:
    """Registra una URL remota en memoria y devuelve su URL local ``127.0.0.1``."""
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return raw
    parsed = urlparse(raw)
    if parsed.hostname in ("127.0.0.1", "localhost"):
        return raw
    clean_ext = "".join(ch for ch in str(ext or "m4a").lower() if ch.isalnum()) or "m4a"
    clean_headers = _sanitize_headers(headers)
    with _LOCK:
        port = _ensure_server_started()
        if len(_ROUTES) >= _MAX_ROUTES:
            _ROUTES.pop(next(iter(_ROUTES)), None)
        token = secrets.token_urlsafe(12)
        _ROUTES[token] = (raw, clean_headers)
    return f"http://127.0.0.1:{port}/stream/{token}.{clean_ext}"


def stop_stream_proxy() -> None:
    """Detiene el servidor HTTP local de streaming si estaba en marcha."""
    global _SERVER, _THREAD, _PORT
    with _LOCK:
        srv = _SERVER
        _SERVER = None
        _THREAD = None
        _PORT = 0
        _ROUTES.clear()
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
