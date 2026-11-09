"""core/transfer_flow.py — Transferencia entre plataformas: búsqueda, creación, verificación y reporte.

Mixin de `AppState` (core/state.py): los métodos se mezclan en la clase
y comparten su `self`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from core.transfer import (
    append_transfer_log,
    build_transfer_report,
    failure_reason_from_exc,
    search_with_rate_limit_backoff,
)
from core.config import TRANSFER_MAX_CONSECUTIVE_ERRORS, get_transfer_concurrency
from core.cache import make_cache_key, unwrap_search_result
from engine.match import FUZZY_IDEAL, FUZZY_REVISION_THRESHOLD, duration_to_seconds
from services.authentication import TRANSFER_HISTORY_JSONL
from core.models import SearchResult, Track, TransferState
from datetime import datetime
from services.circuit_breaker import RateLimitError
from engine.normalizer import clean_metadata


# Sentinela interno: la pista no llegó a procesarse porque el lote abortó.
_TRANSFER_ABORTED = object()


class TransferMixin:
    """Transferencia entre plataformas: búsqueda, creación, verificación y reporte."""

    async def transfer_playlist(self, title_override: Optional[str] = None,
                                  description_override: Optional[str] = None) -> None:
        selected = [t for t in self.tracks if t.selected]
        if not selected:
            return

        eff_title = (title_override if title_override is not None
                     else self.playlist_name).strip()
        eff_description = (description_override if description_override is not None
                           else self.playlist_description)
        self.transfer_started_at  = time.monotonic()
        self.transfer_finished_at = 0.0
        self.transfer_started_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        self.last_report = None
        self.last_created_playlist_id = ""
        self.last_created_title       = eff_title
        self.last_created_description = eff_description
        if not eff_title:
            self._log("[ERROR] Título de playlist vacío; se cancela la transferencia.")
            self.transfer_total = len(selected)
            self.transfer_progress = 0
            self._prepare_transfer_tracks(selected)
            for track in selected:
                track.transfer_status = "error"
                track.failure_reason = "No procesada: título de playlist vacío"
                self.transfer_error_tracks.append(track)
            self.transfer_state = TransferState.ERROR
            self._finalize_transfer_report(detected=len(selected))
            self.notify()
            return

        self.cancel_lazy_scan()
        self.lazy_scan_running = False
        self.lazy_scan_done    = False

        self.transfer_state        = TransferState.RUNNING
        self.transfer_progress     = 0
        self.transfer_total        = len(selected)
        self._prepare_transfer_tracks(selected)
        self._log(
            f"[INFO] Iniciando transferencia · "
            f"{self.count_detected} detectadas → {self.destination}"
        )
        self.notify()

        dest_ids: list[str]          = []
        dest_id_to_track: dict[str, Track] = {}
        completed_count = 0
        # BatchedNotifier: notifica cada 10 items para no saturar UI (regla 6)
        BATCH_SIZE = 10
        batch_pending = [0]  # lista para mutar en closure

        # Corte temprano ante una racha de errores de red: las pistas que
        # todavía no entraron a _transfer_one quedan "pending" y el reporte
        # las marca como interrumpidas, en vez de llenarse de not_found falsos.
        abort_event = asyncio.Event()
        network_error_streak = [0]

        def _note_network_error() -> None:
            network_error_streak[0] += 1
            if (network_error_streak[0] >= TRANSFER_MAX_CONSECUTIVE_ERRORS
                    and not abort_event.is_set()):
                abort_event.set()
                self._log(
                    f"[ERROR] {network_error_streak[0]} errores de red "
                    f"consecutivos; se aborta la transferencia."
                )

        def _batched_notify() -> None:
            batch_pending[0] += 1
            if batch_pending[0] >= BATCH_SIZE:
                batch_pending[0] = 0
                self.notify()

        transfer_sem = asyncio.Semaphore(get_transfer_concurrency(self.destination))

        async def _transfer_one(track: Track) -> Optional[str] | object:
            nonlocal completed_count, batch_pending

            if abort_event.is_set():
                return _TRANSFER_ABORTED

            cn, ca = clean_metadata(track.name, track.artist)
            if not cn.strip():
                track.transfer_status = "error"
                track.failure_reason = "Metadatos vacíos tras The Purge"
                self._log(f"[ERROR] Metadatos vacíos, saltando: '{track.name[:42]}'")
                if track not in self.failed_tracks:
                    self.failed_tracks.append(track)
                completed_count += 1
                self.transfer_progress = completed_count
                _batched_notify()
                return None

            self.count_candidates += 1
            cache_key = make_cache_key(track.name, track.artist, self.destination)
            # Preferir duration_ms (ms exacto) si existe, fallback a parsing string
            local_dur_s = (track.duration_ms // 1000) if getattr(track, "duration_ms", 0) else duration_to_seconds(track.duration)

            if cache_key in self.service.search_cache:
                cached = unwrap_search_result(self.service.search_cache[cache_key])
                if cached.isrc:
                    track.isrc = cached.isrc
                if not cached.track_id:
                    track.transfer_status = "not_found"
                    track.failure_reason  = track.failure_reason or "Sin resultados (caché)"
                    if track not in self.failed_tracks:
                        self.failed_tracks.append(track)
                elif cached.needs_review:
                    track.transfer_status = "revision_necesaria"
                    track.failure_reason  = "Fuzzy <40% (caché)"
                    if track not in self.pending_review_tracks:
                        self.pending_review_tracks.append(track)
                    self._log(f"[WARN]  ⚠ Revisión (caché): {track.name[:42]}")
                else:
                    track.transfer_status = "found"
                    self.count_processed += 1
                self._log(f"[INFO]  ⚡ Caché: {track.name[:42]}")
                completed_count += 1
                self.transfer_progress = completed_count
                _batched_notify()
                return cached.track_id if cached.track_id and not cached.needs_review else None

            track.transfer_status = "searching"
            self._log(f"[INFO]  🔍 Buscando: {track.name[:42]}")

            match     = SearchResult(None, False)
            last_exc: Optional[BaseException] = None
            for attempt in range(3):
                try:
                    match = await search_with_rate_limit_backoff(
                        self.service, self.destination,
                        track.name, track.artist,
                        local_duration_s=local_dur_s,
                        local_duration_ms=track.duration_ms,
                        local_is_explicit=track.is_explicit,
                        # Apple ISRCs were already checked in batches above.
                        local_isrc=None if self.destination == "Apple Music" else track.isrc,
                        log=self._log,
                    )
                    break
                except RateLimitError:
                    raise
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    auth_rejected = self._record_auth_operation_failure(self.destination, exc)
                    if auth_rejected or getattr(exc, "status_code", None) in (401, 403):
                        raise
                    last_exc = exc
                    wait_s = 2 ** attempt
                    if attempt < 2:
                        self._log(
                            f"[ERROR] Intento {attempt+1}/3 · "
                            f"{track.name[:30]} — reintentando en {wait_s}s"
                        )
                        await asyncio.sleep(wait_s)

            if last_exc is not None:
                # Los errores de red no se cachean: un fallo transitorio no
                # debe convertirse en "not_found" permanente ni reanudarse
                # como si la búsqueda hubiera terminado.
                _note_network_error()
                completed_count += 1
                self.transfer_progress = completed_count
                _batched_notify()
                raise last_exc

            network_error_streak[0] = 0
            self.service.search_cache[cache_key] = match
            if match.isrc:
                track.isrc = match.isrc
            self.service.mark_search_cache_dirty()

            if match.track_id and match.needs_review:
                track.transfer_status = "revision_necesaria"
                track.failure_reason  = f"Confianza fuzzy <{FUZZY_REVISION_THRESHOLD}% (título/artista)"
                if track not in self.pending_review_tracks:
                    self.pending_review_tracks.append(track)
                self._log(f"[WARN]  ⚠ Revisión necesaria (fuzzy <{FUZZY_REVISION_THRESHOLD}%): {track.name[:42]}")
            elif match.track_id and match.low_confidence:
                if getattr(track, 'platform', '') == 'local':
                    track.transfer_status = "not_found"
                    track.failure_reason  = f"Similitud <{FUZZY_IDEAL}% (umbral local estricto)"
                    self._log(f"[WARN]  ✗ Local · fuzzy <{FUZZY_IDEAL}% rechazado: {track.name[:42]}")
                    if track not in self.failed_tracks:
                        self.failed_tracks.append(track)
                else:
                    track.transfer_status = "found"
                    self.count_processed += 1
                    self._log(f"[INFO]  Hunter · fuzzy 70–84% (aceptado): {track.name[:42]}")
            elif match.track_id:
                track.transfer_status = "found"
                self.count_processed += 1
                self._log(f"[SUCCESS] ✓ Encontrada: {track.name[:42]}")
            else:
                track.transfer_status = "not_found"
                track.failure_reason  = "Sin resultados en la API del destino"
                self._log(f"[ERROR]   ✗ No encontrada: {track.name[:42]}")
                if track not in self.failed_tracks:
                    self.failed_tracks.append(track)

            completed_count += 1
            self.transfer_progress = completed_count
            _batched_notify()

            return match.track_id if match.track_id and not match.needs_review else None

        try:
            init_ok = await self._ensure_auth(self.destination)
            if not init_ok:
                raise RuntimeError(f"No se pudo autenticar en {self.destination}")

            # Resuelve primero los ISRC disponibles en lotes de 25. Los
            # faltantes no se cachean para que sigan al fallback fuzzy.
            if self.destination == "Apple Music":
                exact_matches = await self.service.search_by_isrcs(
                    [track.isrc for track in selected if track.isrc]
                )
                for track in selected:
                    if not track.isrc:
                        continue
                    exact = exact_matches.get(track.isrc)
                    if exact and exact.track_id:
                        self.service.search_cache[make_cache_key(
                            track.name, track.artist, self.destination
                        )] = exact
                if exact_matches:
                    self.service.save_search_cache()

            async def _bounded(track: Track):
                async with transfer_sem:
                    return await _transfer_one(track)

            if self.destination == "Apple Music":
                # Una búsqueda termina antes de iniciar la siguiente: el
                # semáforo global por sí solo no evita ráfagas entre tasks.
                results = []
                for track in selected:
                    try:
                        results.append(await _transfer_one(track))
                    except RateLimitError:
                        raise
                    except Exception as exc:  # se procesa en el resumen común
                        if getattr(exc, "status_code", None) in (401, 403):
                            raise
                        results.append(exc)
            else:
                results = list(await asyncio.gather(
                    *(_bounded(t) for t in selected),
                    return_exceptions=True,
                ))

            for track, result in zip(selected, results):
                if result is _TRANSFER_ABORTED:
                    continue
                if isinstance(result, RateLimitError):
                    self.cb[self.destination].trip(result.retry_after)
                    track.transfer_status = "error"
                    track.failure_reason  = f"Rate limit ({result.retry_after}s)"
                    if track not in self.failed_tracks:
                        self.failed_tracks.append(track)
                    if track not in self.transfer_error_tracks:
                        self.transfer_error_tracks.append(track)
                elif isinstance(result, Exception):
                    track.transfer_status = "error"
                    track.failure_reason  = failure_reason_from_exc(result)
                    self._log(f"[ERROR] Excepción en '{track.name[:30]}': {result}")
                    if track not in self.failed_tracks:
                        self.failed_tracks.append(track)
                    if track not in self.transfer_error_tracks:
                        self.transfer_error_tracks.append(track)
                elif result:
                    dest_ids.append(result)
                    dest_id_to_track[result] = track

            if abort_event.is_set():
                raise RuntimeError(
                    f"Transferencia abortada tras {network_error_streak[0]} "
                    f"errores de red consecutivos"
                )

            self._log(
                f"[INFO]  🔎 Resumen pre-insert · "
                f"Detectadas: {self.count_detected} · "
                f"Candidatas: {self.count_candidates} · "
                f"Procesadas: {self.count_processed}"
            )
            if self.pending_review_tracks:
                names = ", ".join(t.name[:36] for t in self.pending_review_tracks[:15])
                if len(self.pending_review_tracks) > 15:
                    names += "…"
                self._log(f"[INFO]  📋 Pendientes de revisión ({len(self.pending_review_tracks)}): {names}")

            if dest_ids:
                self._log(f"[INFO]  📁 Creando '{eff_title[:60]}' con {len(dest_ids)} canciones…")
                if self.destination == "Spotify" and eff_description.strip():
                    self._log("[WARN]  Spotify (SpotAPI) no soporta descripción; se crea solo con título.")
                self.notify()
                ok, msg, confirmed_count, rejected_ids = await self.service.create_playlist(
                    self.destination, eff_title, dest_ids, eff_description
                )
                if ok:
                    self.count_confirmed   = confirmed_count
                    self.transfer_progress = confirmed_count
                    if msg and msg != "Playlist creada":
                        self.last_created_playlist_id = msg
                    for vid in rejected_ids:
                        t = dest_id_to_track.get(vid)
                        if t:
                            t.transfer_status = "error"
                            t.failure_reason  = "La API del destino no confirmó la inserción"
                            self._log(f"[ERROR] ⚠ No insertada por API ({self.destination}): {t.name[:42]}")
                            if t in self.failed_tracks:
                                self.failed_tracks.remove(t)
                            if t not in self.api_rejected_tracks:
                                self.api_rejected_tracks.append(t)
                    self._log(
                        f"[SUCCESS] ✅ Transferencia completa · "
                        f"Detectadas: {self.count_detected} · "
                        f"Procesadas: {self.count_processed} · "
                        f"Confirmadas: {self.count_confirmed} · "
                        f"Rechazadas API: {len(rejected_ids)} · "
                        f"No encontradas: {len(self.failed_tracks)}"
                    )
                    self.transfer_state = TransferState.DONE
                else:
                    raise RuntimeError(msg)
            elif any(t.transfer_status == "revision_necesaria" for t in selected):
                self._log("[WARN]  Solo coincidencias con baja confianza (revisión); no se creó playlist.")
                self.transfer_state = TransferState.DONE
            else:
                raise RuntimeError("No se encontraron coincidencias en el destino.")

        except RateLimitError as e:
            self.cb[e.platform].trip(e.retry_after)
            self._absorb_partial_playlist(e, dest_id_to_track)
            self._log(f"[ERROR] ⚠ Rate limit en {e.platform}: espera {e.retry_after}s")
            self.transfer_state = TransferState.ERROR
        except Exception as e:  # pylint: disable=broad-exception-caught
            self._record_auth_operation_failure(self.destination, e)
            self._absorb_partial_playlist(e, dest_id_to_track)
            self._log(f"[ERROR] ✗ Error general: {e}")
            self.transfer_state = TransferState.ERROR
        finally:
            # Persiste lo acumulado aunque la transferencia haya abortado.
            self.service.save_search_cache()
            self._finalize_transfer_report()
            self.notify()

    def _prepare_transfer_tracks(self, selected: list[Track]) -> None:
        """Limpia contadores y estados de una transferencia anterior.

        Sin este reset, una pista que quedó ``found`` en un intento previo
        podría no listarse en el reporte si el nuevo intento se interrumpe
        antes de volver a procesarla.
        """
        self.failed_tracks         = []
        self.api_rejected_tracks   = []
        self.pending_review_tracks = []
        self.transfer_error_tracks = []
        self.count_detected        = len(selected)
        self.count_candidates      = 0
        self.count_processed       = 0
        self.count_confirmed       = 0
        for track in selected:
            track.transfer_status = "pending"
            track.failure_reason = ""

    def _absorb_partial_playlist(self, exc: BaseException,
                                 dest_id_to_track: dict[str, Track]) -> None:
        """Recupera el contexto de una playlist creada antes de un fallo.

        ``_am_create`` adjunta ``playlist_id``/``confirmed_ids``/``pending_ids``
        cuando un lote posterior falla. Así el reporte conserva el enlace y
        puede listar y resolver las pistas que no llegaron a insertarse.
        """
        playlist_id = getattr(exc, "playlist_id", "")
        if playlist_id:
            self.last_created_playlist_id = playlist_id
        confirmed_ids = getattr(exc, "confirmed_ids", None)
        if confirmed_ids is None:
            return
        self.count_confirmed = len(confirmed_ids)
        for vid in getattr(exc, "pending_ids", []):
            track = dest_id_to_track.get(vid)
            if track is None:
                continue
            track.transfer_status = "error"
            track.failure_reason = "Inserción interrumpida por error de API"
            if track in self.failed_tracks:
                self.failed_tracks.remove(track)
            if track not in self.api_rejected_tracks:
                self.api_rejected_tracks.append(track)

    def _finalize_transfer_report(self, detected: Optional[int] = None) -> None:
        """Construye y persiste el reporte al cerrar la transferencia.

        Se ejecuta tanto en DONE como en ERROR para no perder las
        incidencias parciales. ``last_report`` hace de snapshot estable:
        las listas de trabajo pueden limpiarse después sin afectarlo.
        """
        if self.last_report is not None:
            return
        if self.transfer_state not in (TransferState.DONE, TransferState.ERROR):
            return
        finished_mono = time.monotonic()
        self.transfer_finished_at = finished_mono
        started_mono = self.transfer_started_at or finished_mono
        finished_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        status = "DONE" if self.transfer_state == TransferState.DONE else "ERROR"
        errored = list(self.transfer_error_tracks)
        if self.transfer_state == TransferState.ERROR:
            # Un corte a mitad deja pistas sin procesar: se listan también
            # para que el reporte no parezca más limpio de lo que fue.
            known = {
                id(track)
                for track in (
                    *self.failed_tracks,
                    *self.pending_review_tracks,
                    *self.api_rejected_tracks,
                    *errored,
                )
            }
            for track in self.tracks:
                if not track.selected or id(track) in known:
                    continue
                if track.transfer_status in ("pending", "searching"):
                    track.failure_reason = "No procesada: transferencia interrumpida"
                    errored.append(track)
                elif track.transfer_status == "found" and not self.last_created_playlist_id:
                    # Se encontró en el destino pero la playlist no llegó a
                    # crearse (o se desconoce su ID): no está en ningún lado.
                    track.transfer_status = "error"
                    track.failure_reason = "No insertada: no se creó la playlist de destino"
                    errored.append(track)
        report = build_transfer_report(
            started_at=self.transfer_started_iso or finished_iso,
            finished_at=finished_iso,
            elapsed_s=max(0.0, finished_mono - started_mono),
            destination=self.destination,
            status=status,
            title=self.last_created_title or self.playlist_name,
            description=self.last_created_description or self.playlist_description,
            playlist_id=self.last_created_playlist_id,
            detected=self.count_detected if detected is None else detected,
            processed=self.count_processed,
            confirmed=self.count_confirmed,
            failed=self.failed_tracks,
            pending_review=self.pending_review_tracks,
            api_rejected=self.api_rejected_tracks,
            errored=errored,
        )
        self.last_report = report
        try:
            append_transfer_log(report, TRANSFER_HISTORY_JSONL)
        except OSError as exc:
            self._log(f"[WARN] No se pudo escribir el historial de transferencia: {exc}")

    def _record_auth_operation_failure(self, platform: str, error: BaseException) -> bool:
        if getattr(self, "on_auth_failure", None) is not None:
            return self.on_auth_failure(platform, error)
        auth_manager = getattr(self.service, "auth_manager", None)
        if auth_manager is not None:
            return auth_manager.record_operation_failure(platform, error)
        return False

    async def search_candidates(self, platform: str, query: str, limit: int = 5) -> list[Track]:
        """Busca pistas candidatas en la plataforma de destino."""
        return await self.service.search_candidates(platform, query, limit)

    async def add_track_to_remote(
        self, platform: str, playlist_id: str, track_id: str
    ) -> tuple[bool, str]:
        """Inserta una pista en una playlist remota ya existente."""
        return await self.service.add_track(platform, playlist_id, track_id)

    def remember_resolution(
        self, name: str, artist: str, platform: str, track_id: str, *, isrc: str = ""
    ) -> None:
        """Persiste en caché una resolución manual de alternativa."""
        self.service.remember_resolution(name, artist, platform, track_id, isrc=isrc)

    async def create_remote_playlist(
        self, platform: str, title: str, track_ids: list[str], description: str = ""
    ) -> tuple[bool, str, int, list[str]]:
        """Crea una playlist remota en la plataforma indicada."""
        return await self.service.create_playlist(platform, title, track_ids, description)

    async def delete_remote_playlist(self, platform: str, playlist_id: str) -> tuple[bool, str]:
        """Elimina una playlist remota en la plataforma indicada."""
        return await self.service.delete_playlist(platform, playlist_id)

    # Despacho por plataforma: un dict evita repetir la cadena de comparaciones.
    _INIT_METHODS = {
        "YouTube Music": "init_youtube",
        "Apple Music": "init_apple",
        "Spotify": "init_spotify",
    }

    async def _ensure_auth(self, platform: str) -> bool:
        method = self._INIT_METHODS.get(platform)
        if method is None:
            return False
        return await getattr(self.service, method)()
