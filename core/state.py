"""
╔══════════════════════════════════════════════════════════════════════╗
║                    MelomaniacPass v3.2.0                               ║
║                    Estado Global de la Aplicación                    ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: core/state.py
Descripción: Implementa AppState, el ViewModel central de la aplicación
            siguiendo el patrón BLoC (Business Logic Component). Coordina
            toda la lógica de negocio y actúa como única fuente de verdad
            para el estado de la aplicación.

Estrategia de Diseño - Patrón BLoC:
    AppState implementa un ViewModel reactivo que separa completamente
    la lógica de negocio de la UI:
    
    1. Única Fuente de Verdad:
       - Todo el estado de la aplicación reside en AppState
       - La UI solo lee estado, nunca lo modifica directamente
       - Todas las mutaciones ocurren en el event loop de asyncio
    
    2. Patrón Observer:
       - UI se suscribe a cambios con subscribe()
       - AppState notifica cambios con notify()
       - Flujo unidireccional: State → UI (nunca UI → State)
    
    3. Coordinación de Servicios:
       - AppState orquesta llamadas a MusicApiService
       - Maneja reintentos con backoff exponencial
       - Gestiona circuit breakers para rate limiting
    
    4. Gestión de Errores:
       - Post-mortem detallado de fallos
       - Tracking de canciones fallidas
       - Logs estructurados para debugging
    
    5. Estados de Carga y Transferencia:
       - LoadState: IDLE → LOADING_META → LOADING_TRACKS → READY/ERROR
       - TransferState: IDLE → RUNNING → DONE/ERROR
       - Progress tracking granular para feedback visual

Funciones Auxiliares:
    - _failure_reason_from_exc: Extrae razón legible de excepciones
    - _search_with_exponential_rl_backoff: Reintentos con backoff exponencial

Autor: MelomaniacPass Team
Versión: 3.2.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from typing import Callable, Optional

from core.cache import make_cache_key, unwrap_search_result
from core.config import (
    PLATFORM_ORDER,
    PLATFORMS as CFG_PLATFORMS,
    LOCAL_SOURCES as CFG_LOCAL_SOURCES,
    SOURCE_OPTIONS as CFG_SOURCE_OPTIONS,
    get_transfer_concurrency,
)
from core.models import Track, SearchResult, LoadState, TransferState
from utils.circuit_breaker import CircuitBreaker, RateLimitError
from engine.normalizer import clean_metadata
from engine.match import _duration_to_seconds, FUZZY_REVISION_THRESHOLD, FUZZY_IDEAL
from engine.organizer import sort_tracks, split_tracks


def _failure_reason_from_exc(exc: BaseException) -> str:
    """
    Extrae razón legible de una excepción para post-mortem de fallos.
    
    Args:
        exc: Excepción capturada durante operación de API.
    
    Returns:
        String descriptivo del error, truncado a 300 caracteres.
    """
    msg = str(exc)
    return msg[:300] + ("…" if len(msg) > 300 else "")


async def _search_with_exponential_rl_backoff(
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
    """
    Búsqueda con manejo de rate limiting: abre el breaker y falla rápido.

    Estrategia (fail-fast):
        - Ante un 429 se dispara el circuit breaker de la plataforma
          (con su ventana de espera) y la búsqueda se aborta de inmediato.
        - No se martillea la API con reintentos exponenciales: el breaker
          ya serializa el "silencio" posterior a través de check_or_raise()
          en llamadas subsiguientes.

    Args:
        service: Instancia de MusicApiService.
        platform: Plataforma destino ("YouTube Music", "Apple Music").
        name: Título de la canción.
        artist: Nombre del artista.
        local_duration_s: Duración en segundos para matching (opcional).
        log: Función de logging para registrar eventos (opcional).
        backoff_steps: Pasos de reintento (default: 1 = fail-fast).

    Returns:
        SearchResult con el track encontrado o flags de revisión.

    Raises:
        RateLimitError: Si un 429 ocurre (breaker abierto).

    Note:
        El breaker abierto hace que search_track()/check_or_raise() fallen
        inmediatamente sin peticiones extra hasta que expire su ventana.
    """
    rl_backoff: Optional[float] = None
    for step in range(backoff_steps):
        try:
            return await service.search_with_fallback(
                platform, name, artist, local_duration_s=local_duration_s,
                local_duration_ms=local_duration_ms,
                local_is_explicit=local_is_explicit,
                local_isrc=local_isrc,
            )
        except RateLimitError as e:
            ra = max(1, int(e.retry_after))
            if rl_backoff is None:
                rl_backoff = float(ra)
            cb = getattr(service, "_cb", {}).get(platform)
            if cb is not None:
                cb.trip(ra)
            if log:
                log(
                    f"[WARN] 429 {platform}: abriendo breaker ~{ra}s · "
                    f"búsqueda abortada para '{name}'"
                )
            raise
    if log:
        log(f"[ERROR] 429: reintentos agotados en {platform}")
    raise RateLimitError(platform, int(rl_backoff or 60))


class AppState:
    """
    ViewModel central siguiendo el patrón BLoC (Business Logic Component).
    
    Actúa como única fuente de verdad para el estado de la aplicación,
    coordinando toda la lógica de negocio y notificando cambios a la UI
    mediante el patrón Observer.
    
    Responsabilidades:
        1. Gestión de estado de carga de playlists
        2. Coordinación de transferencias entre plataformas
        3. Tracking de progreso y errores
        4. Gestión de circuit breakers
        5. Validación de sesiones de autenticación
        6. Logging estructurado de operaciones
    
    Attributes:
        service: Instancia de MusicApiService para comunicación con APIs.
        source: Plataforma de origen ("YouTube Music", "Apple Music", etc).
        destination: Plataforma destino.
        playlist_id: ID de la playlist cargada.
        playlist_name: Nombre de la playlist.
        tracks: Lista completa de canciones cargadas.
        filtered: Lista filtrada de canciones (por búsqueda).
        load_state: Estado actual de carga (LoadState enum).
        transfer_state: Estado actual de transferencia (TransferState enum).
        transfer_progress: Número de canciones procesadas.
        transfer_total: Total de canciones a transferir.
        log_lines: Líneas de log para telemetría.
        failed_tracks: Canciones que fallaron en transferencia.
        cb: Diccionario de circuit breakers por plataforma.
    
    Constantes:
        PLATFORMS: Lista de plataformas soportadas.
        LOCAL_SOURCES: Set de fuentes locales (archivo, texto).
        SOURCE_OPTIONS: Todas las opciones de fuente disponibles.
    
    Methods:
        subscribe: Registra un listener para notificaciones de cambio.
        notify: Notifica a todos los listeners de un cambio de estado.
        load_playlist: Carga una playlist desde una plataforma.
        start_transfer: Inicia transferencia a plataforma destino.
        cancel_lazy_scan: Cancela escaneo lazy en progreso.
    
    Example:
        >>> state = AppState(service)
        >>> state.subscribe(lambda: print("Estado cambió"))
        >>> await state.load_playlist("youtube", "playlist_id")
        Estado cambió
    
    Note:
        Todas las mutaciones de estado deben ocurrir en el event loop de
        asyncio para garantizar thread-safety. La UI nunca debe modificar
        el estado directamente, solo leerlo y llamar métodos de AppState.
    """

    # Plataformas de streaming soportadas — single source: core/config (re-exporta auth_manager)
    PLATFORMS = CFG_PLATFORMS

    # Fuentes locales (no requieren autenticación)
    LOCAL_SOURCES: frozenset = CFG_LOCAL_SOURCES

    # Todas las opciones de fuente disponibles en la UI
    SOURCE_OPTIONS = CFG_SOURCE_OPTIONS

    def __init__(self, service) -> None:
        """
        Inicializa el estado global de la aplicación.
        
        Args:
            service: Instancia de MusicApiService para comunicación con APIs.
        
        Note:
            El constructor inicializa todos los campos de estado con valores
            por defecto. La UI debe suscribirse inmediatamente después de
            la construcción para recibir notificaciones de cambios.
        """
        self.service = service

        # ──────────────────────────────────────────────────────────────
        # CONFIGURACIÓN DE FUENTE Y DESTINO
        # ──────────────────────────────────────────────────────────────
        
        self.source:      str = "Apple Music"
        self.destination: str = "YouTube Music"
        self.destination_confirmed: bool = True

        # ──────────────────────────────────────────────────────────────
        # ESTADO DE PLAYLIST CARGADA
        # ──────────────────────────────────────────────────────────────
        
        self.playlist_id:   str         = ""
        self.playlist_name: str         = "Cargar una playlist"
        self.tracks:        list[Track] = []
        self.filtered:      list[Track] = []
        self.segments:      dict[str, list[Track]] = {}
        self.active_segment_key: Optional[str]     = None
        self.load_state:    LoadState   = LoadState.IDLE
        self.load_error:    str         = ""

        # ──────────────────────────────────────────────────────────────
        # ESTADO DE TRANSFERENCIA
        # ──────────────────────────────────────────────────────────────
        
        self.transfer_state:    TransferState = TransferState.IDLE
        self.transfer_progress: int           = 0
        self.transfer_total:    int           = 0
        self.log_lines:         list[str]     = []
        self.failed_tracks:     list[Track]   = []

        # ──────────────────────────────────────────────────────────────
        # CONTADORES DE TRACKING
        # ──────────────────────────────────────────────────────────────
        
        self.count_detected:   int            = 0
        self.count_candidates: int            = 0
        self.count_processed:  int            = 0
        self.count_confirmed:  int            = 0
        self.api_rejected_tracks: list[Track] = []

        # ──────────────────────────────────────────────────────────────
        # BÚSQUEDA Y FILTRADO
        # ──────────────────────────────────────────────────────────────
        
        self.search_query: str = ""

        # ──────────────────────────────────────────────────────────────
        # CIRCUIT BREAKERS POR PLATAFORMA
        # ──────────────────────────────────────────────────────────────
        # Protección contra rate limiting de APIs
        
        self.cb: dict[str, CircuitBreaker] = {
            p: CircuitBreaker(p) for p in self.PLATFORMS
        }
        if self.service is not None:
            self.service._cb = self.cb
        # Si service es None (init en app.py), el caller debe inyectar
        # state.service = service y service._cb = state.cb manualmente.

        # ──────────────────────────────────────────────────────────────
        # ESTADO DE AUTENTICACIÓN
        # ──────────────────────────────────────────────────────────────
        
        self.auth_session_ok:   dict[str, bool] = {p: True for p in self.PLATFORMS}
        self.auth_session_hint: dict[str, str]  = {p: "" for p in self.PLATFORMS}

        # ──────────────────────────────────────────────────────────────
        # TRACKING DE CANCIONES PROBLEMÁTICAS
        # ──────────────────────────────────────────────────────────────
        
        self.pending_review_tracks: list[Track] = []
        self.transfer_error_tracks: list[Track] = []

        # ──────────────────────────────────────────────────────────────
        # LAZY SCAN (ESCANEO DIFERIDO)
        # ──────────────────────────────────────────────────────────────
        
        self.lazy_scan_running: bool = False
        self.lazy_scan_done:    bool = False

        # ──────────────────────────────────────────────────────────────
        # PATRÓN OBSERVER
        # ──────────────────────────────────────────────────────────────
        
        self._listeners: list[Callable[[], None]] = []
        self._lazy_task: Optional[asyncio.Task]   = None

    # ══════════════════════════════════════════════════════════════════
    # PATRÓN OBSERVER
    # ══════════════════════════════════════════════════════════════════

    def subscribe(self, cb: Callable[[], None]) -> None:
        """
        Registra un callback para recibir notificaciones de cambio de estado.
        
        El callback será invocado cada vez que notify() sea llamado,
        típicamente después de cualquier mutación de estado.
        
        Args:
            cb: Función sin argumentos que será llamada en cada cambio.
        
        Example:
            >>> def on_change():
            ...     print("Estado actualizado")
            >>> state.subscribe(on_change)
        
        Note:
            Los callbacks deben ser síncronos y rápidos. Operaciones
            pesadas deben delegarse a tareas asyncio separadas.
        """
        self._listeners.append(cb)

    def notify(self) -> None:
        for cb in self._listeners:
            try:
                cb()
            except Exception as e:  # pylint: disable=broad-exception-caught
                print(f"🔴 UI ERROR: {e}")
                traceback.print_exc()

    # ── Computed properties ────────────────────────────────────────────

    @property
    def selected_count(self) -> int:
        return sum(1 for t in self.tracks if t.selected)

    @property
    def select_all(self) -> bool:
        return all(t.selected for t in self.tracks) if self.tracks else False

    def _base_list(self) -> list[Track]:
        """Fuente única para lista base considerando segmentos (regla 1)."""
        if self.active_segment_key and self.active_segment_key in self.segments:
            return self.segments[self.active_segment_key]
        return self.tracks

    @property
    def display_tracks(self) -> list[Track]:
        base_list = self._base_list()
        return self.filtered if self.search_query else base_list

    # ── Actions ────────────────────────────────────────────────────────

    async def load_playlist(self, playlist_id: str) -> None:
        if not playlist_id.strip():
            return
        self.playlist_id   = playlist_id.strip()
        self.tracks        = []
        self.filtered      = []
        self.search_query  = ""
        self.load_state    = LoadState.LOADING_META
        self.load_error    = ""
        self.playlist_name = "Cargando metadatos…"
        self.lazy_scan_running = False
        self.lazy_scan_done    = False
        # Nueva carga: descarta el estado de transferencia anterior para
        # que la barra de progreso desaparezca al ingresar otra playlist
        self.transfer_state    = TransferState.IDLE
        self.transfer_progress = 0
        self.transfer_total    = 0
        self.notify()

        def _progress(_fetched: int, total: int, name: str) -> None:
            self.playlist_name = name
            if total:
                self.load_state = LoadState.LOADING_TRACKS
            self.notify()

        if self._lazy_task and not self._lazy_task.done():
            self._lazy_task.cancel()
            self._lazy_task = None

        try:
            name, tracks = await self.service.fetch_playlist(
                self.source, self.playlist_id, _progress
            )
            self.playlist_name = name
            self.tracks        = tracks
            self.load_state    = LoadState.READY
        except RateLimitError as e:
            self.cb[self.source].trip(e.retry_after)
            self.load_state = LoadState.ERROR
            self.load_error = f"Rate limit en {e.platform}. Espera {e.retry_after}s."
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.load_state = LoadState.ERROR
            self.load_error = str(e)
        finally:
            self.notify()

    def load_local_tracks(self, tracks: list, playlist_name: str = "Playlist Local") -> None:
        self.cancel_lazy_scan()
        self.playlist_id   = f"local_{uuid.uuid4().hex[:8]}"
        self.playlist_name = playlist_name
        self.tracks        = list(tracks)
        self.filtered      = []
        self.search_query  = ""
        self.load_state    = LoadState.READY
        self.load_error    = ""
        self.lazy_scan_running = False
        self.lazy_scan_done    = False
        self.destination_confirmed = False
        self._log(f"[INFO] Ingesta local · {len(tracks)} pistas cargadas")
        self.notify()

    def reset_session(self) -> None:
        self.cancel_lazy_scan()
        self.playlist_id   = ""
        self.playlist_name = "Cargar una playlist"
        self.tracks        = []
        self.filtered      = []
        self.segments      = {}
        self.active_segment_key = None
        self.search_query  = ""
        self.load_state    = LoadState.IDLE
        self.load_error    = ""
        self.lazy_scan_running     = False
        self.lazy_scan_done        = False
        self.transfer_state        = TransferState.IDLE
        self.transfer_progress     = 0
        self.transfer_total        = 0
        self.failed_tracks         = []
        self.api_rejected_tracks   = []
        self.pending_review_tracks = []
        self.transfer_error_tracks = []
        self.log_lines             = []
        self.destination_confirmed = True
        self.notify()

    async def transfer_playlist(self) -> None:
        selected = [t for t in self.tracks if t.selected]
        if not selected:
            return

        self.cancel_lazy_scan()
        self.lazy_scan_running = False
        self.lazy_scan_done    = False

        self.transfer_state        = TransferState.RUNNING
        self.transfer_progress     = 0
        self.transfer_total        = len(selected)
        self.failed_tracks         = []
        self.api_rejected_tracks   = []
        self.pending_review_tracks = []
        self.transfer_error_tracks = []
        self.count_detected        = len(selected)
        self.count_candidates      = 0
        self.count_processed       = 0
        self.count_confirmed       = 0
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

        def _batched_notify() -> None:
            batch_pending[0] += 1
            if batch_pending[0] >= BATCH_SIZE:
                batch_pending[0] = 0
                self.notify()

        transfer_sem = asyncio.Semaphore(get_transfer_concurrency(self.destination))

        async def _transfer_one(track: Track) -> Optional[str]:
            nonlocal completed_count, batch_pending

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
            local_dur_s = (track.duration_ms // 1000) if getattr(track, "duration_ms", 0) else _duration_to_seconds(track.duration)

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
                    match = await _search_with_exponential_rl_backoff(
                        self.service, self.destination,
                        track.name, track.artist,
                        local_duration_s=local_dur_s,
                        local_duration_ms=track.duration_ms,
                        local_is_explicit=track.is_explicit,
                        local_isrc=track.isrc,
                        log=self._log,
                    )
                    break
                except RateLimitError:
                    raise
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    if getattr(exc, "status_code", None) in (401, 403):
                        raise
                    last_exc = exc
                    wait_s = 2 ** attempt
                    if attempt < 2:
                        self._log(
                            f"[ERROR] Intento {attempt+1}/3 · "
                            f"{track.name[:30]} — reintentando en {wait_s}s"
                        )
                        await asyncio.sleep(wait_s)

            self.service.search_cache[cache_key] = match
            if match.isrc:
                track.isrc = match.isrc
            self.service.save_search_cache()

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
                track.failure_reason  = _failure_reason_from_exc(last_exc) if last_exc else "Sin resultados en la API del destino"
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
                    track.failure_reason  = _failure_reason_from_exc(result)
                    self._log(f"[ERROR] Excepción en '{track.name[:30]}': {result}")
                    if track not in self.failed_tracks:
                        self.failed_tracks.append(track)
                    if track not in self.transfer_error_tracks:
                        self.transfer_error_tracks.append(track)
                elif result:
                    dest_ids.append(result)
                    dest_id_to_track[result] = track

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
                self._log(f"[INFO]  📁 Creando playlist con {len(dest_ids)} canciones…")
                self.notify()
                ok, msg, confirmed_count, rejected_ids = await self.service.create_playlist(
                    self.destination, self.playlist_name, dest_ids
                )
                if ok:
                    self.count_confirmed   = confirmed_count
                    self.transfer_progress = confirmed_count
                    for vid in rejected_ids:
                        t = dest_id_to_track.get(vid)
                        if t:
                            t.transfer_status = "error"
                            self._log(f"[ERROR] ⚠ No insertada por API ({self.destination}): {t.name[:42]}")
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
            self._log(f"[ERROR] ⚠ Rate limit en {e.platform}: espera {e.retry_after}s")
            self.transfer_state = TransferState.ERROR
        except Exception as e:  # pylint: disable=broad-exception-caught
            self._log(f"[ERROR] ✗ Error general: {e}")
            self.transfer_state = TransferState.ERROR
        finally:
            self.notify()

    async def _ensure_auth(self, platform: str) -> bool:
        if platform == "YouTube Music":
            return await self.service.init_youtube()
        elif platform == "Apple Music":
            return await self.service.init_apple()
        elif platform == "Spotify":
            return await self.service.init_spotify()
        return False

    def toggle_select_all(self) -> None:
        new_val = not self.select_all
        for t in self.tracks:
            t.selected = new_val
        self.notify()

    def toggle_track(self, track_id: str) -> None:
        for t in self.tracks:
            if t.id == track_id:
                t.selected = not t.selected
                break
        self.notify()

    def apply_search(self, query: str) -> None:
        self.search_query = query
        base_list = self._base_list()
        if not query:
            self.filtered = []
        else:
            q = query.lower()
            self.filtered = [
                t for t in base_list
                if q in t.name.lower() or q in t.artist.lower() or q in t.album.lower()
            ]
        self.notify()

    def organize_sort(self, keys: list[str], reverse: bool = False) -> None:
        self.tracks = sort_tracks(self.tracks, keys, reverse)
        if self.segments:
            for k in self.segments:
                self.segments[k] = sort_tracks(self.segments[k], keys, reverse)
        self.apply_search(self.search_query)  # Re-aplica filtro y notifica

    def organize_split(self, key: str) -> None:
        self.segments = split_tracks(self.tracks, key)
        if self.segments:
            # Selecciona el primer segmento por defecto (ordenado alfabéticamente)
            self.active_segment_key = sorted(list(self.segments.keys()))[0]
        else:
            self.active_segment_key = None
        self.apply_search(self.search_query)

    def clear_split(self) -> None:
        self.segments = {}
        self.active_segment_key = None
        self.apply_search(self.search_query)

    def set_active_segment(self, key: str) -> None:
        if key in self.segments:
            self.active_segment_key = key
            self.apply_search(self.search_query)

    def set_source(self, val: str) -> None:
        self.source = val
        self.destination_confirmed = val not in self.LOCAL_SOURCES
        self.notify()

    def set_destination(self, val: str) -> None:
        self.destination = val
        self.destination_confirmed = True
        self.notify()

    def _log(self, msg: str) -> None:
        self.log_lines.append(msg)
        if len(self.log_lines) > 200:
            self.log_lines = self.log_lines[-200:]

    def log(self, msg: str) -> None:
        self._log(msg)

    def cancel_lazy_scan(self) -> None:
        if self._lazy_task and not self._lazy_task.done():
            self._lazy_task.cancel()
            self._lazy_task = None

    async def _lazy_availability_scan(self, tracks: list) -> None:
        dest_ok = await self._ensure_auth(self.destination)
        if not dest_ok:
            return

        self.lazy_scan_running = True
        self.lazy_scan_done    = False
        self.transfer_total    = len(tracks)
        self.transfer_progress = 0
        self.notify()

        BATCH_SIZE = 5
        done_count = 0

        if self.destination == "Apple Music":
            exact_matches = await self.service.search_by_isrcs(
                [track.isrc for track in tracks if track.isrc]
            )
            for track in tracks:
                if not track.isrc:
                    continue
                exact = exact_matches.get(track.isrc)
                if exact and exact.track_id:
                    self.service.search_cache[make_cache_key(
                        track.name, track.artist, self.destination
                    )] = exact
            if exact_matches:
                self.service.save_search_cache()

        async def _check_one(track: Track) -> None:
            nonlocal done_count
            cache_key = make_cache_key(track.name, track.artist, self.destination)
            local_dur_s = (track.duration_ms // 1000) if getattr(track, "duration_ms", 0) else _duration_to_seconds(track.duration)

            if cache_key in self.service.search_cache:
                res = unwrap_search_result(self.service.search_cache[cache_key])
                track.transfer_status = (
                    "not_found" if not res.track_id
                    else "revision_necesaria" if res.needs_review
                    else "found"
                )
            else:
                try:
                    result = await _search_with_exponential_rl_backoff(
                        self.service, self.destination,
                        track.name, track.artist,
                        local_duration_s=local_dur_s,
                        local_duration_ms=track.duration_ms,
                        local_is_explicit=track.is_explicit,
                        local_isrc=track.isrc,
                        log=self._log,
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    result = SearchResult(None, False)
                self.service.search_cache[cache_key] = result
                track.transfer_status = (
                    "not_found" if not result.track_id
                    else "revision_necesaria" if result.needs_review
                    else "found"
                )

            done_count += 1
            self.transfer_progress = done_count
            if done_count % BATCH_SIZE == 0:
                self.notify()

        try:
            if self.destination == "Apple Music":
                for track in tracks:
                    await _check_one(track)
            else:
                await asyncio.gather(*[_check_one(t) for t in tracks], return_exceptions=True)
        except asyncio.CancelledError:
            self.lazy_scan_running = False
            self.notify()
            return
        self.lazy_scan_running = False
        self.lazy_scan_done    = True
        self.notify()
