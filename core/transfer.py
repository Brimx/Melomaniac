"""Shared transfer helpers used by :mod:`core.state`.

This module contains transfer concerns that do not need to own ``AppState``:
rate-limit aware searches and compact error descriptions.  The state object
remains the workflow coordinator; these helpers only receive their explicit
service and logging dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from core.models import SearchResult, Track
from services.circuit_breaker import RateLimitError


# ══════════════════════════════════════════════════════════════════════
# REPORTE DE TRANSFERENCIA
# ══════════════════════════════════════════════════════════════════════

KIND_PRIORITY = {"error": 4, "api_rejected": 3, "not_found": 2, "revision": 1}
KIND_LABELS = {
    "not_found": "No encontradas",
    "revision": "Revisión necesaria",
    "api_rejected": "Rechazadas por la API",
    "error": "Errores",
}
KIND_DEFAULT_REASON = {
    "not_found": "Sin resultados en el destino",
    "revision": "Confianza insuficiente; requiere revisión manual",
    "api_rejected": "La API del destino no confirmó la inserción",
    "error": "Error al procesar la canción",
}


def _track_payload(track: Track) -> dict:
    """Serializa solo metadatos públicos de una pista (sin credenciales)."""
    return {
        "id": str(track.id or ""),
        "name": track.name,
        "artist": track.artist,
        "album": track.album,
        "platform": track.platform,
        "duration_ms": int(getattr(track, "duration_ms", 0) or 0),
        "isrc": track.isrc or "",
        "source_url": track.source_url or "",
    }


@dataclass
class TransferIssue:
    """Una pista que no quedó confirmada en la playlist de destino."""

    track: Track
    kind: str
    reason: str = ""
    resolved: bool = False

    def to_dict(self) -> dict:
        return {
            **_track_payload(self.track),
            "kind": self.kind,
            "reason": self.reason,
            "resolved": self.resolved,
        }


@dataclass
class TransferReport:
    """Resumen persistible de una transferencia y sus incidencias."""

    started_at: str
    finished_at: str
    elapsed_s: float
    destination: str
    status: str
    title: str
    description: str
    playlist_id: str
    detected: int
    processed: int
    confirmed: int
    issues: list[TransferIssue] = field(default_factory=list)

    @property
    def missing(self) -> list[TransferIssue]:
        return [issue for issue in self.issues if not issue.resolved]

    def counts(self) -> dict:
        return {
            "detected": self.detected,
            "processed": self.processed,
            "confirmed": self.confirmed,
            "missing": len(self.missing),
        }

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": round(self.elapsed_s, 3),
            "destination": self.destination,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "playlist_id": self.playlist_id,
            "counts": self.counts(),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _issue_key(track: Track) -> tuple:
    return (
        str(track.platform or ""),
        str(track.id or ""),
        (track.name or "").strip().lower(),
        (track.artist or "").strip().lower(),
    )


def build_transfer_report(
    *,
    started_at: str,
    finished_at: str,
    elapsed_s: float,
    destination: str,
    status: str,
    title: str,
    description: str,
    playlist_id: str,
    detected: int,
    processed: int,
    confirmed: int,
    failed: Iterable[Track] = (),
    pending_review: Iterable[Track] = (),
    api_rejected: Iterable[Track] = (),
    errored: Iterable[Track] = (),
) -> TransferReport:
    """Construye el reporte deduplicado con prioridad de motivo.

    Una misma pista puede aparecer en varias listas (por ejemplo, un fallo
    de red y luego un error de inserción). Se conserva una sola incidencia
    con el motivo más informativo: error > rechazo API > no encontrada >
    revisión.
    """
    issues: dict[tuple, TransferIssue] = {}

    def add(tracks: Iterable[Track], kind: str) -> None:
        for track in tracks:
            if track is None:
                continue
            key = _issue_key(track)
            reason = (getattr(track, "failure_reason", "") or "").strip()
            if not reason:
                reason = KIND_DEFAULT_REASON.get(kind, "")
            current = issues.get(key)
            if current is None or KIND_PRIORITY.get(kind, 0) > KIND_PRIORITY.get(current.kind, 0):
                issues[key] = TransferIssue(track=track, kind=kind, reason=reason)

    add(failed, "not_found")
    add(pending_review, "revision")
    add(api_rejected, "api_rejected")
    add(errored, "error")

    ordered = sorted(
        issues.values(),
        key=lambda issue: (-KIND_PRIORITY.get(issue.kind, 0), issue.track.name.lower()),
    )
    # Refleja el motivo elegido en la pista para que la telemetría y el
    # export existentes lo muestren sin duplicar lógica.
    for issue in ordered:
        if not (getattr(issue.track, "failure_reason", "") or "").strip():
            try:
                issue.track.failure_reason = issue.reason
            except Exception:  # pylint: disable=broad-exception-caught
                pass
    return TransferReport(
        started_at=started_at,
        finished_at=finished_at,
        elapsed_s=elapsed_s,
        destination=destination,
        status=status,
        title=title,
        description=description,
        playlist_id=normalize_playlist_id(destination, playlist_id),
        detected=detected,
        processed=processed,
        confirmed=confirmed,
        issues=ordered,
    )


def format_elapsed(seconds: float) -> str:
    """Formatea segundos como mm:ss (o h:mm:ss si supera la hora)."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def normalize_playlist_id(platform: str, playlist_id: str) -> str:
    """Devuelve el ID desnudo que esperan las APIs.

    Acepta IDs, URIs de Spotify y URLs de las tres plataformas. Es
    defensivo para reportes históricos que guardaron el URI completo.
    """
    pid = (playlist_id or "").strip()
    if not pid:
        return ""
    if platform == "Spotify":
        if ":playlist:" in pid:
            pid = pid.split(":playlist:", 1)[1]
        if "/playlist/" in pid:
            pid = pid.split("/playlist/", 1)[1]
        return pid.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    if platform == "YouTube Music":
        if "list=" in pid:
            pid = pid.split("list=", 1)[1]
        return pid.split("&", 1)[0].split("#", 1)[0].strip()
    if platform == "Apple Music":
        if "/playlist/" in pid:
            pid = pid.split("/playlist/", 1)[1]
            pid = pid.split("?", 1)[0].split("#", 1)[0].strip("/")
            if "/" in pid:
                pid = pid.rsplit("/", 1)[-1]
        return pid
    return pid


def playlist_url(platform: str, playlist_id: str) -> str:
    """URL externa de la playlist creada, por plataforma."""
    pid = normalize_playlist_id(platform, playlist_id)
    if not pid:
        return ""
    if platform == "Apple Music":
        return f"https://music.apple.com/library/playlist/{pid}"
    if platform == "Spotify":
        return f"https://open.spotify.com/playlist/{pid}"
    if platform == "YouTube Music":
        return f"https://music.youtube.com/playlist?list={pid}"
    return ""


def _append_jsonl(path: str | Path, record: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_transfer_log(report: TransferReport, path: str | Path) -> None:
    """Añade una línea JSONL con el reporte completo de la transferencia."""
    record = {"type": "transfer", "ts": report.finished_at, **report.to_dict()}
    _append_jsonl(path, record)


def append_resolution_log(
    report: TransferReport,
    issue: TransferIssue,
    alternative: Track,
    path: str | Path,
    *,
    query: str = "",
) -> None:
    """Registra la inserción manual de una alternativa en la playlist."""
    original = issue.to_dict()
    original["resolved"] = False
    record = {
        "type": "resolution",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "playlist_id": report.playlist_id,
        "destination": report.destination,
        "title": report.title,
        "original": original,
        "alternative": _track_payload(alternative),
        "query": query,
    }
    _append_jsonl(path, record)


def report_to_text(report: TransferReport) -> str:
    """Representación legible del reporte para exportación TXT."""
    lines = [
        "# Melomaniac — Reporte de transferencia\n",
        f"# Fecha: {report.finished_at}\n",
        f"# Destino: {report.destination}\n",
        f"# Playlist: {report.title}\n",
    ]
    if report.description:
        lines.append(f"# Descripción: {report.description}\n")
    if report.playlist_id:
        lines.append(f"# Playlist ID: {report.playlist_id}\n")
    lines.append(
        f"# Estado: {report.status} · Tiempo: {format_elapsed(report.elapsed_s)}\n"
    )
    lines.append(
        f"# Detectadas: {report.detected} · Confirmadas: {report.confirmed} · "
        f"Faltantes: {len(report.missing)}\n\n"
    )
    for kind, label in KIND_LABELS.items():
        group = [issue for issue in report.issues if issue.kind == kind]
        if not group:
            continue
        lines.append(f"[{label}] ({len(group)})\n")
        for issue in group:
            track = issue.track
            mark = "✓ resuelta" if issue.resolved else "✗"
            lines.append(
                f"{mark} {track.name} | {track.artist} | "
                f"{issue.reason or KIND_DEFAULT_REASON.get(kind, '—')}\n"
            )
        lines.append("\n")
    return "".join(lines)


def failure_reason_from_exc(exc: BaseException) -> str:
    """Return a short, user-facing description for a failed operation."""
    msg = str(exc)
    return msg[:300] + ("…" if len(msg) > 300 else "")


async def search_with_rate_limit_backoff(
    service,
    platform: str,
    name: str,
    artist: str,
    *,
    local_duration_s: Optional[int] = None,
    local_duration_ms: int = 0,
    local_is_explicit: bool = False,
    local_isrc: str | None = None,
    log: Optional[Callable[[str], None]] = None,
    backoff_steps: int = 1,
) -> SearchResult:
    """Search through the service and trip the platform breaker on HTTP 429.

    The default strategy is fail-fast: the circuit breaker owns the cooldown,
    so this helper does not issue extra requests after a rate-limit response.
    ``backoff_steps`` remains available for compatibility with the existing
    caller contract.
    """
    rl_backoff: Optional[float] = None
    for _step in range(backoff_steps):
        try:
            return await service.search_with_fallback(
                platform,
                name,
                artist,
                local_duration_s=local_duration_s,
                local_duration_ms=local_duration_ms,
                local_is_explicit=local_is_explicit,
                local_isrc=local_isrc,
            )
        except RateLimitError as exc:
            retry_after = max(1, int(exc.retry_after))
            if rl_backoff is None:
                rl_backoff = float(retry_after)
            breaker = getattr(service, "_cb", {}).get(platform)
            if breaker is not None:
                breaker.trip(retry_after)
            if log:
                log(
                    f"[WARN] 429 {platform}: abriendo breaker ~{retry_after}s · "
                    f"búsqueda abortada para '{name}'"
                )
            raise

    if log:
        log(f"[ERROR] 429: reintentos agotados en {platform}")
    raise RateLimitError(platform, int(rl_backoff or 60))
