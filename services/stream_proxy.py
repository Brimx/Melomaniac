"""
services/stream_proxy.py — Proxy HTTP local en memoria para streaming con Range
════════════════════════════════════════════════════════════════════════════════
Reenvía peticiones HTTP desde ``flet-audio`` / GStreamer hacia URLs de
``googlevideo.com`` traduciendo ``HEAD`` y ``GET`` (con o sin ``Range``) a
peticiones ``GET`` por bloques con ``Range`` y los ``http_headers`` de
``yt-dlp``. Evita ``403 Forbidden`` sin escribir nada en disco.
"""

from __future__ import annotations

import re
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
_RANGE_BLOCK_SIZE = 512 * 1024  # 512 KB por bloque Range hacia upstream
_CHUNK_SIZE = 65536
_UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=25.0, write=10.0, pool=10.0)
_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)
_REQ_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)", re.IGNORECASE)


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


def _parse_total_from_content_range(header_val: str | None) -> int | None:
    if not header_val:
        return None
    m = _CONTENT_RANGE_RE.search(header_val)
    if not m or m.group(3) == "*":
        return None
    try:
        return int(m.group(3))
    except ValueError:
        return None


def _parse_request_range(header_val: str | None) -> tuple[int, int | None] | None:
    """Devuelve ``(start, end_inclusive_or_None)`` o ``None`` si no hay Range."""
    if not header_val:
        return None
    m = _REQ_RANGE_RE.search(header_val)
    if not m:
        return None
    start_s, end_s = m.group(1), m.group(2)
    if not start_s:
        return None
    try:
        start = max(0, int(start_s))
        end = int(end_s) if end_s else None
        if end is not None and end < start:
            return None
        return start, end
    except ValueError:
        return None


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

    def _probe_upstream(
        self, client: httpx.Client, upstream_url: str, base_headers: dict[str, str]
    ) -> tuple[int, str, int | None]:
        """Consulta 1 byte con Range 0-0 para obtener tamaño total y Content-Type."""
        hdrs = {**base_headers, "Range": "bytes=0-0"}
        with client.stream("GET", upstream_url, headers=hdrs) as resp:
            status = resp.status_code
            ct = resp.headers.get("content-type") or "audio/mp4"
            total = _parse_total_from_content_range(resp.headers.get("content-range"))
            if total is None and status == 200:
                cl = resp.headers.get("content-length")
                if cl and cl.isdigit():
                    total = int(cl)
            return status, ct, total

    def _proxy(self, *, send_body: bool) -> None:
        target = self._resolve_target()
        if target is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        upstream_url, base_headers = target
        req_range = _parse_request_range(self.headers.get("Range"))

        try:
            with httpx.Client(follow_redirects=True, timeout=_UPSTREAM_TIMEOUT) as client:
                if not send_body:
                    status, ct, total = self._probe_upstream(client, upstream_url, base_headers)
                    if status not in (200, 206):
                        self.send_response(status)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("Accept-Ranges", "bytes")
                    if total is not None:
                        self.send_header("Content-Length", str(total))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return

                start = req_range[0] if req_range is not None else 0
                req_end = req_range[1] if req_range is not None else None
                first_end = (
                    min(req_end, start + _RANGE_BLOCK_SIZE - 1)
                    if req_end is not None
                    else start + _RANGE_BLOCK_SIZE - 1
                )

                # Primer bloque con Range: googlevideo exige Range en GET (sin Range da 403).
                first_hdrs = {**base_headers, "Range": f"bytes={start}-{first_end}"}
                with client.stream("GET", upstream_url, headers=first_hdrs) as resp:
                    if resp.status_code not in (200, 206):
                        self.send_response(resp.status_code)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                    ct = resp.headers.get("content-type") or "audio/mp4"
                    total = _parse_total_from_content_range(resp.headers.get("content-range"))
                    if total is None and resp.status_code == 200:
                        cl = resp.headers.get("content-length")
                        if cl and cl.isdigit():
                            total = int(cl)

                    # Calcular límite final real
                    if total is not None:
                        final_end = min(req_end, total - 1) if req_end is not None else (total - 1)
                    else:
                        final_end = req_end

                    if req_range is not None and total is not None and final_end is not None:
                        self.send_response(206)
                        self.send_header("Content-Range", f"bytes {start}-{final_end}/{total}")
                        self.send_header("Content-Length", str(max(0, final_end - start + 1)))
                    elif req_range is not None and resp.headers.get("content-range"):
                        self.send_response(206)
                        self.send_header("Content-Range", resp.headers["content-range"])
                        if resp.headers.get("content-length"):
                            self.send_header("Content-Length", resp.headers["content-length"])
                    else:
                        self.send_response(200)
                        if total is not None:
                            self.send_header("Content-Length", str(total))
                    self.send_header("Content-Type", ct)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Connection", "close")
                    self.end_headers()

                    sent = 0
                    for chunk in resp.iter_bytes(_CHUNK_SIZE):
                        if not chunk:
                            continue
                        self.wfile.write(chunk)
                        sent += len(chunk)
                    self.wfile.flush()

                    # Si upstream devolvió 200 completo (no parcial), ya enviamos todo.
                    if resp.status_code == 200:
                        return

                # Streamear bloques Range restantes hasta final_end
                pos = start + sent
                while final_end is not None and pos <= final_end:
                    blk_end = min(final_end, pos + _RANGE_BLOCK_SIZE - 1)
                    blk_hdrs = {**base_headers, "Range": f"bytes={pos}-{blk_end}"}
                    with client.stream("GET", upstream_url, headers=blk_hdrs) as resp:
                        if resp.status_code not in (200, 206):
                            break
                        got = 0
                        for chunk in resp.iter_bytes(_CHUNK_SIZE):
                            if not chunk:
                                continue
                            self.wfile.write(chunk)
                            got += len(chunk)
                        self.wfile.flush()
                        if got <= 0:
                            break
                        pos += got
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
