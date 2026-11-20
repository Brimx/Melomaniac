"""
ui/player_bar.py — Barra de reproducción global integrada con flet-audio
════════════════════════════════════════════════════════════════════════
Control visual propio sobre una única instancia de ``flet_audio.Audio``
registrada en ``page.services``. Toda la lógica de cola vive en
``core/playback.py``.

Desde ``flet-audio`` 1.1.2 (#6133) el servicio ``Audio`` se crea una sola
vez sin ``src`` al inicializar la barra y cada reproducción actualiza
``src`` + ``update()`` + ``play(0)``.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import replace
from datetime import datetime
from typing import Callable

import flet as ft
import flet_audio as fta

from core.models import Track
from core.playback import (
    PlayableTrack,
    PlayQueue,
    RepeatMode,
    build_pending_stream_track,
    build_playable_track,
    build_stream_playable_track,
    build_playable_for_track,
)
from engine.download_search import invalidate_stream_cache, resolve_stream_info
from services.mpris_service import MPRISService
from services import settings_store
from ui.theme.fonts import (
    FONT_HEADLINE_BOLD, FONT_TEXT, FONT_PHOSPHOR, FONT_PHOSPHOR_FILL,
    font_family_for, mono_family
)
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_HOVER,
    BG_PANEL,
    BG_SURFACE,
    BORDER_MUTED,
    CHIP_BG,
    OVERLAY_08,
    PLAYER_BAR_H,
    PLAYER_PROGRESS_ANIM_MS,
    PLAYER_PROGRESS_H,
    PLAYER_THUMB_SIZE,
    RADIUS_NONE,
    SPACE_2,
    SPACE_4,
    TEXT_MUTED,
    TEXT_PRIMARY,
    MOTION_HOVER_MS,
)
from ui.components.song_row import _thumb_content_for
from ui.components.widgets import (
    app_text,
    fmt_ms as _fmt_ms,
    notify,
)
from ui.player.bar_layout import build_player_bar_layout
from ui.player.player_mpris import MprisController

_TICKER_INTERVAL_S = 0.25
_POSITION_BACKSTEP_MS = 1500
_DEFAULT_TRACK_WIDTH = 240.0
_POS_LOG_ENV = "MELOMANIAC_AUDIO_POS_LOG"
_DEFAULT_POS_LOG_PATH = "/tmp/melomaniac_audio_pos.log"


def _to_ms(value) -> int:
    """Normaliza ``int | float | ft.Duration`` a milisegundos enteros."""
    if value is None:
        return 0
    in_ms = getattr(value, "in_milliseconds", None)
    if in_ms is not None:
        try:
            return max(0, int(in_ms))
        except (TypeError, ValueError):
            return 0
    total_sec = getattr(value, "total_seconds", None)
    if callable(total_sec):
        try:
            return max(0, int(total_sec() * 1000))
        except Exception:
            return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _resolve_pos_log_path() -> str | None:
    raw = (os.environ.get(_POS_LOG_ENV) or "").strip()
    if not raw or raw in ("0", "false", "no", "off"):
        return None
    if raw.lower() in ("1", "true", "yes", "on"):
        return _DEFAULT_POS_LOG_PATH
    return raw


class PlayerBar(ft.Container):
    """Barra inferior global del reproductor y dueño del servicio ``Audio``."""

    def __init__(
        self,
        page: ft.Page,
        app_state=None,
        *,
        on_toggle_queue: Callable[[], None] | None = None,
        on_open_lyrics: Callable[[], None] | None = None,
        dbus_mpris: MPRISService | None = None,
    ) -> None:
        super().__init__(
            height=PLAYER_BAR_H,
            bgcolor=BG_PANEL,
            border=ft.Border.only(top=ft.BorderSide(1, BORDER_MUTED)),
            border_radius=RADIUS_NONE,
            padding=ft.Padding.symmetric(horizontal=SPACE_4, vertical=SPACE_2),
        )
        self._page_ref = page
        self.app_state = app_state
        self._on_toggle_queue = on_toggle_queue
        self._on_open_lyrics = on_open_lyrics
        self._last_thumb_url: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._pos_listeners: list[Callable[[int, int], None]] = []
        self._last_notified_pos: tuple[int, int] = (-1, -1)
        self._lyrics_cache: dict[tuple, object] = {}
        self._now_playing_scene = None
        self._dbus_mpris = dbus_mpris
        self._mpris = MprisController(self)

        # Preferencias persistidas (con fallback si aún no existen)
        init_vol = float(getattr(settings_store, "get_volume", lambda: 1.0)())
        init_mute = bool(getattr(settings_store, "get_mute", lambda: False)())
        init_shuf = bool(getattr(settings_store, "get_shuffle", lambda: False)())
        init_rep_raw = str(getattr(settings_store, "get_repeat", lambda: "off")()).lower()
        init_rep = {
            "all": RepeatMode.ALL,
            "one": RepeatMode.ONE,
        }.get(init_rep_raw, RepeatMode.OFF)

        self._volume: float = max(0.0, min(1.0, init_vol))
        self._muted: bool = init_mute
        self.queue = PlayQueue(shuffle=init_shuf, repeat=init_rep)

        self._audio_state: fta.AudioState = fta.AudioState.STOPPED
        self._position_ms: int = 0
        self._duration_ms: int = 0
        self._track_width: float = _DEFAULT_TRACK_WIDTH
        self._scrubbing: bool = False
        self._scrub_ms: int = 0
        self._ticker_task: asyncio.Task | None = None
        self._fade_task: asyncio.Task | None = None
        self._xfade_audio: fta.Audio | None = None
        self._crossfade_triggered: bool = False
        self._pending_seek_ms: int | None = None
        self._bg_tasks: set[asyncio.Task] = set()
        self._persist_task: asyncio.Task | None = None
        self._persist_seq: int = 0
        self._persist_lock = threading.Lock()
        self._stream_req_seq: int = 0
        self._expected_playing_stream: PlayableTrack | None = None
        self._stream_retry_count: int = 0
        self._stream_fail_streak: int = 0
        self._disposed: bool = False

        # Instrumentación de cadencia de on_position_change
        self._pos_event_count: int = 0
        self._last_pos_event_mono: float | None = None

        # Instancia única de ``Audio`` registrada en ``page.services``.
        self.audio: fta.Audio | None = None
        self._ensure_audio()
        self._build_ui()
        self._sync_ui()

    def _register_audio_service(self, audio: fta.Audio) -> None:
        services = getattr(self._page_ref, "services", None)
        if isinstance(services, list) and audio not in services:
            services.append(audio)
        registry = getattr(self._page_ref, "_services", None)
        reg_list = getattr(registry, "_services", None)
        if (
            registry is not None
            and isinstance(reg_list, list)
            and audio not in reg_list
            and hasattr(registry, "register_service")
        ):
            try:
                registry.register_service(audio)
            except Exception:
                pass

    def _create_audio_instance(self) -> fta.Audio:
        audio = fta.Audio(
            volume=0.0 if self._muted else self._volume,
            release_mode=fta.ReleaseMode.STOP,
            on_loaded=self._on_audio_loaded,
            on_duration_change=self._on_duration_change,
            on_state_change=self._on_state_change,
            on_position_change=self._on_position_change,
            on_seek_complete=self._on_seek_complete,
        )
        self._register_audio_service(audio)
        return audio

    def _ensure_audio(self, src: str | None = None) -> fta.Audio:
        """Devuelve la instancia única de ``fta.Audio`` y actualiza ``src`` si se indica.

        Desde ``flet-audio`` 1.1.2 (#6133) ``Audio`` puede crearse sin ``src``
        y recibir la fuente más tarde mediante ``update()`` + ``play(0)``.
        """
        audio = self.audio
        if audio is None:
            audio = self._create_audio_instance()
            self.audio = audio

        if src is not None:
            audio.src = src
            audio.volume = 0.0 if self._muted else self._volume
            audio.release_mode = fta.ReleaseMode.STOP
            try:
                audio.update()
            except Exception as exc:  # noqa: BLE001
                self._log(f"[AUDIO] No se pudo actualizar el servicio: {exc}")
        return audio

    def _ensure_xfade_audio(self) -> fta.Audio:
        """Crea bajo demanda el segundo deck para crossfade."""
        if self._xfade_audio is None:
            self._xfade_audio = self._create_audio_instance()
        return self._xfade_audio

    def _log(self, msg: str) -> None:
        """Traza del reproductor hacia el log de la app (diagnóstico)."""
        print(msg, flush=True)
        if self.app_state is not None and hasattr(self.app_state, "log"):
            try:
                self.app_state.log(msg)
            except Exception:
                pass

    def log_audio_services_count(self, context: str = "") -> int:
        count = sum(1 for s in self._page_ref.services if isinstance(s, fta.Audio))
        prefix = f"[AUDIO] Instancias Audio en page.services={count}"
        msg = f"{prefix} ({context})" if context else prefix
        print(msg, flush=True)
        if self.app_state is not None and hasattr(self.app_state, "log"):
            self.app_state.log(msg)
        return count

    # ── suscripción para el panel de cola, letras y escena ────────────

    @property
    def position_ms(self) -> int:
        return self._position_ms

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def is_playing(self) -> bool:
        return self._audio_state == fta.AudioState.PLAYING

    def subscribe(self, cb: Callable[[], None]) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)

    def unsubscribe(self, cb: Callable[[], None]) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def subscribe_position(self, cb: Callable[[int, int], None]) -> None:
        if cb not in self._pos_listeners:
            self._pos_listeners.append(cb)

    def unsubscribe_position(self, cb: Callable[[int, int], None]) -> None:
        if cb in self._pos_listeners:
            self._pos_listeners.remove(cb)

    def _notify_position_listeners(self, pos_ms: int, dur_ms: int) -> None:
        sig = (pos_ms, dur_ms)
        if sig == self._last_notified_pos:
            return
        self._last_notified_pos = sig
        for cb in list(self._pos_listeners):
            try:
                cb(pos_ms, dur_ms)
            except Exception:
                pass

    def seek_to(self, target_ms: int) -> None:
        """Salta a ``target_ms`` si hay pista cargada con duración conocida."""
        if self.queue.current is None or self._duration_ms <= 0:
            return
        clamped = max(0, min(int(target_ms), self._duration_ms))
        if self._audio_state == fta.AudioState.PAUSED and self._pending_seek_ms is not None:
            self._pending_seek_ms = clamped
        else:
            self._pending_seek_ms = None
        self._position_ms = clamped
        self._call_audio("seek", clamped)
        self._update_progress_visual(clamped)
        self._persist_playback_state()

    def set_volume_level(self, value: float, *, persist: bool = False) -> None:
        """Ajusta volumen (0.0..1.0) desde la barra o la escena ampliada."""
        self._volume = max(0.0, min(1.0, float(value)))
        if self._volume > 0 and self._muted:
            self._muted = False
        self._vol_slider.value = 0.0 if self._muted else self._volume
        self._apply_volume()
        self._sync_volume_icon()
        if persist:
            set_vol = getattr(settings_store, "set_volume", None)
            if callable(set_vol):
                set_vol(self._volume)
            set_mute = getattr(settings_store, "set_mute", None)
            if callable(set_mute):
                set_mute(self._muted)
        self._notify_listeners()

    def open_now_playing_scene(self) -> None:
        """Abre la escena Reproduciendo sobre ``page.overlay``."""
        if self._disposed:
            return
        if self._now_playing_scene is None:
            from ui.player.now_playing_scene import NowPlayingScene
            self._now_playing_scene = NowPlayingScene(self._page_ref, self)
        self._now_playing_scene.open()

    def close_now_playing_scene(self) -> None:
        if self._now_playing_scene is not None:
            self._now_playing_scene.close()

    def _open_lyrics_panel(self) -> None:
        if callable(self._on_open_lyrics):
            self._on_open_lyrics()
        elif callable(self._on_toggle_queue):
            self._on_toggle_queue()

    # ── construcción visual ────────────────────────────────────────────

    def _build_ui(self) -> None:
        build_player_bar_layout(self, default_track_width=_DEFAULT_TRACK_WIDTH)

    # ── despacho seguro de métodos de Audio (sync o coroutine) ─────────

    def _track_task(self, task: asyncio.Task) -> asyncio.Task:
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _schedule_audio(self, fn: Callable, *args, **kwargs) -> None:
        """Ejecuta métodos de ``flet_audio.Audio`` tanto si son sync como coroutine."""
        if self._disposed:
            return
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[AUDIO] Fallo al invocar {getattr(fn, '__name__', fn)}: {exc}")
            return
        if asyncio.iscoroutine(result):
            async def _run_audio_coro() -> None:
                try:
                    await result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self._log(f"[AUDIO] Error en {getattr(fn, '__name__', fn)}: {exc}")

            try:
                self._track_task(asyncio.get_running_loop().create_task(_run_audio_coro()))
            except RuntimeError:
                try:
                    self._page_ref.run_task(_run_audio_coro)
                except Exception:
                    try:
                        result.close()
                    except Exception:
                        pass

    def _call_audio(self, method: str, *args) -> None:
        """Invoca ``method`` del servicio si ya existe (no-op antes del primer play)."""
        audio = self.audio
        if audio is None:
            return
        self._schedule_audio(getattr(audio, method), *args)

    # ── API pública de reproducción y cola ─────────────────────────────

    def play_playable(self, playable: PlayableTrack, *, enqueue_first: bool = True) -> None:
        if enqueue_first:
            self.queue.enqueue(playable, play_now=True)
        self._load_and_play_current()

    def play_stream(
        self,
        stream_url: str,
        track: Track,
        info: dict | None = None,
        *,
        enqueue_only: bool = False,
    ) -> bool:
        """Reproduce o encola una pista remota por streaming sin descargar."""
        playable = build_stream_playable_track(stream_url, track, info)
        if playable is None:
            notify(self._page_ref, "URL de streaming inválida", kind="error")
            return False
        was_empty = self.queue.current is None
        self.queue.enqueue(playable, play_now=(not enqueue_only) or was_empty)
        if not enqueue_only or was_empty:
            self._load_and_play_current()
        else:
            self._sync_ui()
            self._notify_listeners()
        return True

    async def play_remote_track(self, track: Track) -> bool:
        """Resuelve con yt-dlp el stream de ``track`` sin descargar y lo reproduce."""
        if not bool(getattr(settings_store, "get_stream_without_download", lambda: True)()):
            notify(self._page_ref, "Activa «Reproducir sin descargar» en Configuración", kind="warn")
            return False
        self._stream_req_seq += 1
        req_seq = self._stream_req_seq
        info = await asyncio.to_thread(resolve_stream_info, track)
        if self._disposed or req_seq != self._stream_req_seq:
            return False
        url = str(info.get("url") or "")
        if not url:
            err = str(info.get("error") or "No se pudo resolver el audio")
            notify(self._page_ref, err, kind="error")
            return False
        return self.play_stream(url, track, info)

    def play_file(self, path: str, track: Track | None = None) -> bool:
        playable = build_playable_track(path, track)
        if playable is None:
            notify(self._page_ref, "El archivo de audio no está disponible", kind="error")
            return False
        self.queue.enqueue(playable, play_now=True)
        self._load_and_play_current()
        return True

    def enqueue_file(self, path: str, track: Track | None = None, *, play_now: bool = False) -> bool:
        playable = build_playable_track(path, track)
        if playable is None:
            notify(self._page_ref, "El archivo de audio no está disponible", kind="error")
            return False
        was_empty = self.queue.current is None
        self.queue.enqueue(playable, play_now=play_now or was_empty)
        if play_now or was_empty:
            self._load_and_play_current()
        else:
            self._sync_ui()
            self._notify_listeners()
        return True

    def play_files(
        self,
        items: list[tuple[str, Track | None]] | list[str],
        *,
        start_index: int = 0,
    ) -> int:
        playables: list[PlayableTrack] = []
        for idx, entry in enumerate(items):
            if isinstance(entry, tuple):
                p, tr = entry
            else:
                p, tr = str(entry), None
            pt = build_playable_track(p, tr, probe_file=(idx == start_index))
            if pt is not None:
                playables.append(pt)
        if not playables:
            notify(self._page_ref, "No hay archivos locales reproducibles", kind="error")
            return 0
        self.queue.set_tracks(playables, start_index=start_index)
        self._load_and_play_current()
        return len(playables)

    def play_tracks(
        self,
        tracks: list[Track],
        *,
        start_index: int = 0,
        include_remote: bool | None = None,
    ) -> int:
        """Encola la playlist completa: archivos locales y pistas remotas por streaming.

        Las pistas sin archivo entran como pendientes y se resuelven con yt-dlp
        cuando les toca sonar. ``start_index`` se interpreta sobre ``tracks``.
        """
        if include_remote is None:
            include_remote = bool(getattr(settings_store, "get_stream_without_download", lambda: True)())
        playables: list[PlayableTrack] = []
        start_pos = 0
        for idx, tr in enumerate(tracks):
            pt = build_playable_for_track(
                tr,
                include_remote=include_remote,
                probe_file=(idx == start_index),
            )
            if pt is None:
                continue
            if idx <= start_index:
                start_pos = len(playables)
            playables.append(pt)
        if not playables:
            notify(self._page_ref, "No hay pistas reproducibles", kind="error")
            return 0
        self.queue.set_tracks(playables, start_index=start_pos)
        self._load_and_play_current()
        return len(playables)

    def select_queue_index(self, queue_pos: int) -> None:
        target = self.queue.select(queue_pos)
        if target is not None:
            self._load_and_play_current()

    def remove_queue_index(self, queue_pos: int) -> None:
        prev_cur = self.queue.current
        new_cur = self.queue.remove(queue_pos)
        if new_cur is None:
            self._stop_playback()
        elif new_cur is not prev_cur:
            self._load_and_play_current()
        else:
            self._sync_ui()
            self._notify_listeners()

    def move_queue_item(self, old_pos: int, new_pos: int) -> None:
        self.queue.move(old_pos, new_pos)
        self._sync_ui()
        self._notify_listeners()

    def clear_queue(self) -> None:
        self.queue.clear()
        self._stop_playback()

    def _cancel_fade(self) -> None:
        task = getattr(self, "_fade_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._fade_task = None
        if self._crossfade_triggered and self._xfade_audio is not None and not self._disposed:
            self._schedule_audio(self._xfade_audio.pause)
        self._crossfade_triggered = False

    def _spawn_ui_task(self, coro) -> asyncio.Task | None:
        try:
            return self._track_task(asyncio.get_running_loop().create_task(coro))
        except RuntimeError:
            try:
                self._page_ref.run_task(lambda: coro)
            except Exception:
                try:
                    coro.close()
                except Exception:
                    pass
            return None

    def _start_fade_in(self, audio: fta.Audio, duration_s: float) -> None:
        self._cancel_fade()
        steps = 20
        step_s = max(0.02, float(duration_s) / steps)

        async def _fade() -> None:
            try:
                for i in range(1, steps + 1):
                    await asyncio.sleep(step_s)
                    if self._disposed or self.audio is not audio or self._audio_state != fta.AudioState.PLAYING:
                        return
                    target = 0.0 if self._muted else self._volume
                    audio.volume = round(target * (i / steps), 4)
                    try:
                        audio.update()
                    except Exception:
                        return
            except asyncio.CancelledError:
                return

        self._fade_task = self._spawn_ui_task(_fade())

    def _start_fade_out_pause(self, audio: fta.Audio, duration_s: float) -> None:
        self._cancel_fade()
        steps = 12
        fade_len = min(0.45, max(0.12, float(duration_s) * 0.35))
        step_s = fade_len / steps
        start_vol = float(getattr(audio, "volume", self._volume) or 0.0)

        async def _fade_out() -> None:
            try:
                for i in range(1, steps + 1):
                    await asyncio.sleep(step_s)
                    if self._disposed or self._audio_state != fta.AudioState.PAUSED:
                        return
                    audio.volume = round(max(0.0, start_vol * (1.0 - (i / steps))), 4)
                    try:
                        audio.update()
                    except Exception:
                        break
                self._schedule_audio(audio.pause)
                audio.volume = 0.0 if self._muted else self._volume
                try:
                    audio.update()
                except Exception:
                    pass
            except asyncio.CancelledError:
                return

        self._fade_task = self._spawn_ui_task(_fade_out())

    @staticmethod
    def _playable_src(item: PlayableTrack | None) -> str | None:
        if item is None:
            return None
        if bool(getattr(item, "is_stream", False)) or str(item.path).startswith(("http://", "https://")):
            return str(item.path)
        abs_path = os.path.abspath(item.path)
        return abs_path if os.path.isfile(abs_path) else None

    def _begin_crossfade_to_next(self, fade_s: int) -> bool:
        """Crossfade real entre el deck activo y el siguiente tema en la cola."""
        if self._disposed or fade_s <= 0 or self.audio is None or not self.queue.has_next():
            return False
        nxt_peek = self.queue.peek_next()
        if nxt_peek is None or (nxt_peek.is_stream and nxt_peek.is_stream_expired()):
            return False
        if not self._playable_src(nxt_peek):
            return False
        nxt = self.queue.next()
        if nxt is None:
            self._stop_playback()
            return True
        next_src = self._playable_src(nxt)
        if not next_src:
            self._load_and_play_current()
            return True

        self._cancel_fade()
        self._stream_req_seq += 1
        self._crossfade_triggered = True
        self._expected_playing_stream = nxt if nxt.is_stream else None
        outgoing = self.audio
        incoming = self._ensure_xfade_audio()
        self.audio = incoming
        self._xfade_audio = outgoing

        self._pending_seek_ms = None
        self._position_ms = 0
        self._duration_ms = max(0, int(nxt.duration_ms or 0))
        incoming.src = next_src
        incoming.volume = 0.0
        incoming.release_mode = fta.ReleaseMode.STOP
        try:
            incoming.update()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[AUDIO] No se pudo preparar crossfade: {exc}")

        self._audio_state = fta.AudioState.PLAYING
        self._schedule_audio(incoming.play, 0)

        steps = 20
        step_s = max(0.03, float(fade_s) / steps)
        out_start = float(getattr(outgoing, "volume", 0.0) or 0.0)

        async def _xfade() -> None:
            try:
                for i in range(1, steps + 1):
                    await asyncio.sleep(step_s)
                    if self._disposed or self.audio is not incoming:
                        return
                    frac = i / steps
                    target = 0.0 if self._muted else self._volume
                    outgoing.volume = round(max(0.0, out_start * (1.0 - frac)), 4)
                    incoming.volume = round(target * frac, 4)
                    try:
                        outgoing.update()
                        incoming.update()
                    except Exception:
                        break
            except asyncio.CancelledError:
                return
            finally:
                if not self._disposed and self.audio is not outgoing:
                    self._schedule_audio(outgoing.pause)
                if self._fade_task is asyncio.current_task():
                    self._crossfade_triggered = False

        self._fade_task = self._spawn_ui_task(_xfade())
        self._ensure_ticker()
        self._sync_ui()
        self._notify_listeners()
        self._notify_mpris_playing(nxt)
        self._persist_playback_state()
        return True

    @staticmethod
    def _serialize_playback_item(item: PlayableTrack) -> dict | None:
        if bool(getattr(item, "is_stream", False)):
            t = item.track
            if not str(getattr(t, "name", "") or "").strip():
                return None
            return {
                "kind": "stream",
                "id": str(getattr(t, "id", "") or ""),
                "name": str(getattr(t, "name", "") or ""),
                "artist": str(getattr(t, "artist", "") or ""),
                "album": str(getattr(t, "album", "") or ""),
                "duration": str(getattr(t, "duration", "") or ""),
                "img_url": str(getattr(t, "img_url", "") or ""),
                "platform": str(getattr(t, "platform", "") or ""),
                "duration_ms": max(0, int(item.duration_ms or getattr(t, "duration_ms", 0) or 0)),
                "isrc": getattr(t, "isrc", None),
                "source_url": str(getattr(t, "source_url", "") or ""),
            }
        if not item.path:
            return None
        return {"kind": "local", "path": os.path.abspath(item.path)}

    @staticmethod
    def _deserialize_playback_item(raw: dict) -> PlayableTrack | None:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "").lower()
        if kind == "stream":
            name = str(raw.get("name") or "").strip()
            if not name:
                return None
            try:
                dur_ms = max(0, int(raw.get("duration_ms") or 0))
            except (TypeError, ValueError):
                dur_ms = 0
            tr = Track(
                id=str(raw.get("id") or ""),
                name=name,
                artist=str(raw.get("artist") or ""),
                album=str(raw.get("album") or ""),
                duration=str(raw.get("duration") or ""),
                img_url=str(raw.get("img_url") or ""),
                platform=str(raw.get("platform") or ""),
                duration_ms=dur_ms,
                isrc=raw.get("isrc"),
                source_url=str(raw.get("source_url") or ""),
            )
            return build_pending_stream_track(tr)
        path_val = str(raw.get("path") or "")
        if not path_val or not os.path.isfile(path_val):
            return None
        return build_playable_track(path_val)

    def _persist_playback_state(self, *, sync: bool = False) -> None:
        if not bool(getattr(settings_store, "get_resume_on_open", lambda: False)()):
            return
        cur = self.queue.current
        if cur is None:
            return
        cur_item = self._serialize_playback_item(cur)
        if cur_item is None:
            return
        cur_is_stream = bool(getattr(cur, "is_stream", False))
        abs_path = "" if cur_is_stream else os.path.abspath(cur.path)

        ordered = self.queue.ordered_tracks()[:50]
        raw_items: list[tuple[bool, dict]] = []
        for it in ordered:
            serialized = self._serialize_playback_item(it)
            if serialized is not None:
                raw_items.append((it is cur, serialized))
        if not any(is_cur for is_cur, _ in raw_items):
            raw_items.insert(0, (True, cur_item))

        pos_ms = max(0, int(self._position_ms))
        setter = getattr(settings_store, "set_last_playback", None)
        if not callable(setter):
            return

        self._persist_seq += 1
        seq = self._persist_seq

        def _write() -> None:
            with self._persist_lock:
                if seq < self._persist_seq:
                    return
                if not cur_is_stream and not os.path.isfile(abs_path):
                    return
                valid_items: list[dict] = []
                q_paths: list[str] = []
                cur_index = 0
                for is_cur, entry in raw_items:
                    if entry.get("kind") == "local":
                        p = str(entry.get("path") or "")
                        if not p or not os.path.isfile(p):
                            continue
                        q_paths.append(p)
                    if is_cur:
                        cur_index = len(valid_items)
                    valid_items.append(entry)
                if not valid_items:
                    return
                if seq < self._persist_seq:
                    return
                try:
                    setter({
                        "path": abs_path,
                        "position_ms": pos_ms,
                        "queue_paths": q_paths,
                        "current": cur_item,
                        "queue_items": valid_items,
                        "current_index": cur_index,
                    })
                except Exception:
                    pass

        if sync:
            _write()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _write()
            return

        if self._persist_task is not None and not self._persist_task.done():
            self._persist_task.cancel()

        async def _run_bg() -> None:
            try:
                await asyncio.to_thread(_write)
            except asyncio.CancelledError:
                return

        self._persist_task = self._track_task(loop.create_task(_run_bg()))

    def restore_last_playback(self) -> bool:
        """Restaura la última pista y posición en pausa si ``resume_on_open`` está activo."""
        if not bool(getattr(settings_store, "get_resume_on_open", lambda: False)()):
            return False
        getter = getattr(settings_store, "get_last_playback", None)
        saved = getter() if callable(getter) else {}
        if not isinstance(saved, dict):
            return False
        try:
            raw_pos = saved.get("position_ms") or 0
            pos_ms = 0 if isinstance(raw_pos, bool) else max(0, int(raw_pos))
        except (TypeError, ValueError):
            pos_ms = 0

        playables: list[PlayableTrack] = []
        start_idx = 0
        raw_items = saved.get("queue_items")
        raw_current = saved.get("current")
        if isinstance(raw_items, list) or isinstance(raw_current, dict):
            try:
                raw_idx = saved.get("current_index")
                want_idx = None if raw_idx is None or isinstance(raw_idx, bool) else int(raw_idx)
            except (TypeError, ValueError):
                want_idx = None
            idx_map: dict[int, int] = {}
            for orig_i, entry in enumerate(raw_items or []):
                pt = self._deserialize_playback_item(entry)
                if pt is not None:
                    idx_map[orig_i] = len(playables)
                    playables.append(pt)
            if want_idx is not None and want_idx in idx_map:
                start_idx = idx_map[want_idx]
            elif not playables and isinstance(raw_current, dict):
                pt = self._deserialize_playback_item(raw_current)
                if pt is not None:
                    playables = [pt]
                    start_idx = 0
            elif playables and isinstance(raw_current, dict):
                cur_id = str(raw_current.get("id") or "")
                cur_path = os.path.abspath(str(raw_current.get("path") or "")) if raw_current.get("path") else ""
                for i, pt in enumerate(playables):
                    if cur_path and not pt.is_stream and os.path.abspath(pt.path) == cur_path:
                        start_idx = i
                        break
                    if cur_id and pt.is_stream and pt.track.id == cur_id:
                        start_idx = i
                        break
        else:
            last_path = str(saved.get("path") or "")
            if not last_path or not os.path.isfile(last_path):
                return False
            abs_last = os.path.abspath(last_path)
            raw_q = saved.get("queue_paths")
            raw_paths = raw_q if isinstance(raw_q, list) and raw_q else [abs_last]
            for p in raw_paths:
                if not isinstance(p, (str, os.PathLike)):
                    continue
                p_str = str(p)
                if not p_str or not os.path.isfile(p_str):
                    continue
                pt = build_playable_track(p_str)
                if pt is not None:
                    if os.path.abspath(pt.path) == abs_last:
                        start_idx = len(playables)
                    playables.append(pt)
            if not playables:
                pt = build_playable_track(abs_last)
                if pt is None:
                    return False
                playables = [pt]
                start_idx = 0

        if not playables:
            return False
        self.queue.set_tracks(playables, start_index=start_idx)
        cur = self.queue.current
        if cur is None:
            return False
        dur_ms = max(0, int(cur.duration_ms or 0))
        if dur_ms > 0:
            pos_ms = min(pos_ms, max(0, dur_ms - 1000))
        self._position_ms = pos_ms
        self._pending_seek_ms = pos_ms
        self._duration_ms = dur_ms
        self._audio_state = fta.AudioState.PAUSED
        self._ensure_audio(None if cur.is_stream else os.path.abspath(cur.path))
        self._sync_ui()
        self._notify_listeners()
        return True

    def _skip_unresolvable(self, message: str) -> None:
        """Una pista remota no se pudo resolver: avisa y salta a la siguiente de la cola."""
        notify(self._page_ref, message, kind="error")
        self._stream_fail_streak += 1
        if (
            self._stream_fail_streak >= max(1, len(self.queue.order))
            or self.queue.repeat == RepeatMode.ONE
            or self.queue.next() is None
        ):
            self._stream_fail_streak = 0
            self._stop_playback()
            return
        self._load_and_play_current()

    async def _refresh_stream_and_play(
        self,
        item: PlayableTrack,
        req_seq: int,
        *,
        seek_ms: int = 0,
        is_retry: bool = False,
    ) -> None:
        if is_retry:
            invalidate_stream_cache(item.track)
        info = await asyncio.to_thread(resolve_stream_info, item.track)
        if self._disposed or req_seq != self._stream_req_seq or self.queue.current is not item:
            return
        url = str(info.get("url") or "")
        if not url:
            err = str(info.get("error") or "No se pudo resolver el audio")
            self._skip_unresolvable(f"{item.track.name}: {err}")
            return
        fresh = build_stream_playable_track(url, item.track, info)
        if fresh is None:
            self._skip_unresolvable(f"{item.track.name}: URL de streaming inválida")
            return
        self._stream_fail_streak = 0
        idx = self.queue._find_track_index(item)
        if idx >= 0:
            self.queue.tracks[idx] = fresh
        self._load_and_play_current(seek_ms=seek_ms, _is_retry=is_retry)

    def _load_and_play_current(self, *, seek_ms: int = 0, _is_retry: bool = False) -> None:
        self._stream_req_seq += 1
        req_seq = self._stream_req_seq
        if not _is_retry:
            self._stream_retry_count = 0
        visited_positions: set[int] = set()
        max_attempts = max(1, len(self.queue.order))
        cur: PlayableTrack | None = None
        src: str | None = None

        for _ in range(max_attempts):
            cur = self.queue.current
            if cur is None:
                self._stop_playback()
                return
            if cur.is_stream and cur.is_pending and cur.track.source_path and os.path.isfile(cur.track.source_path):
                promoted = build_playable_track(cur.track.source_path, cur.track)
                if promoted is not None:
                    idx = self.queue._find_track_index(cur)
                    if idx >= 0:
                        self.queue.tracks[idx] = promoted
                    cur = promoted
            if cur.is_stream and cur.is_stream_expired():
                self._cancel_fade()
                self._pending_seek_ms = None
                self._expected_playing_stream = None
                self._position_ms = max(0, int(seek_ms or 0))
                self._duration_ms = max(0, int(cur.duration_ms or 0))
                if self._audio_state == fta.AudioState.PLAYING:
                    # El audio anterior no debe seguir sonando mientras se resuelve el siguiente.
                    self._call_audio("pause")
                self._audio_state = fta.AudioState.STOPPED
                self._sync_ui()
                try:
                    self._artist_txt.value = "Resolviendo audio…"
                    safe_update(self._artist_txt)
                except Exception:
                    pass
                self._notify_listeners()
                self._spawn_ui_task(
                    self._refresh_stream_and_play(cur, req_seq, seek_ms=seek_ms, is_retry=_is_retry)
                )
                return
            src = self._playable_src(cur)
            if src:
                break
            notify(self._page_ref, f"Archivo no encontrado: {os.path.basename(cur.path)}", kind="error")
            if self.queue.repeat == RepeatMode.ONE or self.queue.pos in visited_positions:
                self._stop_playback()
                return
            visited_positions.add(self.queue.pos)
            if self.queue.next() is None:
                self._stop_playback()
                return

        if cur is None or not src:
            self._stop_playback()
            return

        self._cancel_fade()
        self._pending_seek_ms = None
        self._expected_playing_stream = cur if cur.is_stream else None
        if not cur.is_stream:
            self._stream_fail_streak = 0
        dur_ms = max(0, int(cur.duration_ms or 0))
        start_pos = max(0, int(seek_ms or 0))
        if dur_ms > 0:
            start_pos = min(start_pos, max(0, dur_ms - 1000))
        self._position_ms = start_pos
        self._duration_ms = dur_ms
        audio = self._ensure_audio(src)
        fade_s = int(getattr(settings_store, "get_fade_seconds", lambda: 0)() or 0)
        use_fade = fade_s > 0 and not self._muted and self._volume > 0.0
        if use_fade:
            audio.volume = 0.0
            try:
                audio.update()
            except Exception:
                pass
        self._audio_state = fta.AudioState.PLAYING
        self._schedule_audio(audio.play, start_pos)
        if use_fade:
            self._start_fade_in(audio, fade_s)
        self._ensure_ticker()
        self._sync_ui()
        self._notify_listeners()
        self._notify_mpris_playing(cur)
        self._persist_playback_state()

    def _stop_playback(self) -> None:
        self._cancel_fade()
        self._pending_seek_ms = None
        self._expected_playing_stream = None
        self._stream_req_seq += 1
        self._audio_state = fta.AudioState.STOPPED
        self._position_ms = 0
        self._duration_ms = 0
        self._stop_ticker()
        self._call_audio("release")
        if self._xfade_audio is not None:
            self._schedule_audio(self._xfade_audio.release)
        self._sync_ui()
        self._notify_listeners()
        self._notify_mpris()

    # ── MPRIS (exposición al escritorio) ───────────────────────────────

    def release(self) -> None:
        """Libera el decodificador nativo y detiene el ticker (hard_cleanup)."""
        self._persist_playback_state(sync=True)
        self._cancel_fade()
        self._stop_ticker()
        for task in list(self._bg_tasks):
            if not task.done():
                task.cancel()
        self._bg_tasks.clear()
        if self._now_playing_scene is not None:
            try:
                self._now_playing_scene.dispose()
            except Exception:
                pass
        if self._dbus_mpris is not None:
            self._dbus_mpris.stop()
        self._disposed = True
        for audio in (self.audio, self._xfade_audio):
            if audio is None:
                continue
            try:
                res = audio.release()
                if asyncio.iscoroutine(res):
                    async def _safe_release(coro) -> None:
                        try:
                            await coro
                        except Exception:
                            pass

                    try:
                        asyncio.get_running_loop().create_task(_safe_release(res))
                    except RuntimeError:
                        res.close()
            except Exception as exc:  # noqa: BLE001
                self._log(f"[AUDIO] No se pudo liberar el servicio: {exc}")

    # ── eventos de controles ───────────────────────────────────────────

    def _on_play_pause_click(self, _e=None) -> None:
        cur = self.queue.current
        if cur is None:
            if self.queue.order:
                self.queue.select(0)
                self._load_and_play_current()
            return
        fade_s = int(getattr(settings_store, "get_fade_seconds", lambda: 0)() or 0)
        if self._audio_state == fta.AudioState.PLAYING:
            self._cancel_fade()
            self._audio_state = fta.AudioState.PAUSED
            self._expected_playing_stream = None
            self._stop_ticker()
            self._persist_playback_state()
            if fade_s > 0 and self.audio is not None and not self._muted and self._volume > 0.0:
                self._start_fade_out_pause(self.audio, fade_s)
            else:
                self._call_audio("pause")
        elif self._audio_state == fta.AudioState.PAUSED:
            if cur.is_stream and cur.is_stream_expired():
                resume_pos = (
                    int(self._pending_seek_ms)
                    if self._pending_seek_ms is not None
                    else max(0, int(self._position_ms))
                )
                self._pending_seek_ms = None
                self._load_and_play_current(seek_ms=resume_pos)
                return
            self._cancel_fade()
            self._audio_state = fta.AudioState.PLAYING
            self._expected_playing_stream = cur if cur.is_stream else None
            audio = self.audio
            use_fade = fade_s > 0 and audio is not None and not self._muted and self._volume > 0.0
            if use_fade and audio is not None:
                audio.volume = 0.0
                try:
                    audio.update()
                except Exception:
                    pass
            elif audio is not None:
                self._apply_volume()
            if self._pending_seek_ms is not None and audio is not None:
                seek_pos = self._pending_seek_ms
                self._pending_seek_ms = None
                self._schedule_audio(audio.play, seek_pos)
            else:
                self._call_audio("resume")
            if use_fade and audio is not None:
                self._start_fade_in(audio, min(1.0, float(fade_s)))
            self._ensure_ticker()
        else:
            self._load_and_play_current()
            return
        self._sync_ui()
        self._notify_listeners()
        self._notify_mpris()

    def _on_prev_click(self, _e=None) -> None:
        if not self.queue.order:
            return
        self._stream_req_seq += 1
        if self._position_ms > PlayQueue.RESTART_THRESHOLD_MS and self.queue.current is not None:
            self._position_ms = 0
            if self._audio_state == fta.AudioState.PAUSED and self._pending_seek_ms is not None:
                self._pending_seek_ms = 0
            else:
                self._pending_seek_ms = None
                self._call_audio("seek", 0)
            if self._audio_state != fta.AudioState.PLAYING:
                cur_prev = self.queue.current
                if cur_prev is not None and cur_prev.is_stream and cur_prev.is_stream_expired():
                    self._pending_seek_ms = None
                    self._load_and_play_current(seek_ms=0)
                    return
                self._audio_state = fta.AudioState.PLAYING
                if self._pending_seek_ms is not None:
                    self._pending_seek_ms = None
                    self._call_audio("play", 0)
                else:
                    self._call_audio("resume")
                self._ensure_ticker()
            self._sync_ui()
            return
        prev_cur = self.queue.current
        target = self.queue.prev(elapsed_ms=self._position_ms)
        if target is not None:
            if target is prev_cur:
                self._position_ms = 0
                if self._audio_state == fta.AudioState.PAUSED and self._pending_seek_ms is not None:
                    self._pending_seek_ms = 0
                else:
                    self._pending_seek_ms = None
                    self._call_audio("seek", 0)
                self._sync_ui()
            else:
                self._load_and_play_current()

    def _on_next_click(self, _e=None) -> None:
        if not self.queue.order:
            return
        fade_s = int(getattr(settings_store, "get_fade_seconds", lambda: 0)() or 0)
        if (
            fade_s > 0
            and self._audio_state == fta.AudioState.PLAYING
            and not self._muted
            and self._volume > 0.0
            and self._begin_crossfade_to_next(fade_s)
        ):
            return
        target = self.queue.next()
        if target is not None:
            self._load_and_play_current()
        else:
            self._stop_playback()

    def _on_shuffle_click(self, _e=None) -> None:
        new_val = not self.queue.shuffle
        self.queue.set_shuffle(new_val)
        setter = getattr(settings_store, "set_shuffle", None)
        if callable(setter):
            setter(new_val)
        self._sync_ui()
        self._notify_listeners()

    def _on_repeat_click(self, _e=None) -> None:
        mode = self.queue.cycle_repeat()
        setter = getattr(settings_store, "set_repeat", None)
        if callable(setter):
            setter(mode.name.lower())
        self._sync_ui()
        self._notify_listeners()

    def _on_mute_click(self, _e=None) -> None:
        self._muted = not self._muted
        self._apply_volume()
        self._vol_slider.value = 0.0 if self._muted else self._volume
        setter = getattr(settings_store, "set_mute", None)
        if callable(setter):
            setter(self._muted)
        self._sync_ui()
        self._notify_listeners()

    def _on_volume_change(self, e: ft.ControlEvent) -> None:
        try:
            val = float(e.control.value or 0.0)
        except (TypeError, ValueError):
            val = 1.0
        self._volume = max(0.0, min(1.0, val))
        if self._volume > 0 and self._muted:
            self._muted = False
        self._apply_volume()
        self._sync_volume_icon()

    def _apply_volume(self) -> None:
        """Propaga volumen/silencio al servicio; sin servicio solo guarda el valor."""
        audio = self.audio
        if audio is None:
            return
        audio.volume = 0.0 if self._muted else self._volume
        try:
            audio.update()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[AUDIO] No se pudo aplicar volumen: {exc}")

    def _on_volume_change_end(self, _e: ft.ControlEvent) -> None:
        set_vol = getattr(settings_store, "set_volume", None)
        if callable(set_vol):
            set_vol(self._volume)
        set_mute = getattr(settings_store, "set_mute", None)
        if callable(set_mute):
            set_mute(self._muted)
        self._notify_listeners()

    # ── barra de progreso animada y seek/scrub ─────────────────────────

    def _on_track_size_change(self, e: ft.LayoutSizeChangeEvent) -> None:
        try:
            w = float(e.width or 0.0)
        except (TypeError, ValueError):
            w = 0.0
        if w > 0:
            self._track_width = w
            self._update_progress_visual(self._scrub_ms if self._scrubbing else self._position_ms)

    def _x_to_ms(self, local_x: float) -> int:
        if self._duration_ms <= 0 or self._track_width <= 0:
            return 0
        frac = max(0.0, min(1.0, local_x / self._track_width))
        return int(frac * self._duration_ms)

    def _on_progress_tap(self, e: ft.TapEvent) -> None:
        if self.queue.current is None or self._duration_ms <= 0:
            return
        pos = getattr(e, "local_position", None)
        x = float(getattr(pos, "x", 0.0) or 0.0)
        target_ms = self._x_to_ms(x)
        if self._audio_state == fta.AudioState.PAUSED and self._pending_seek_ms is not None:
            self._pending_seek_ms = target_ms
        else:
            self._pending_seek_ms = None
        self._position_ms = target_ms
        self._call_audio("seek", target_ms)
        self._update_progress_visual(target_ms)
        self._persist_playback_state()

    def _on_scrub_start(self, _e) -> None:
        if self.queue.current is None or self._duration_ms <= 0:
            return
        self._scrubbing = True
        self._scrub_ms = self._position_ms
        # Suspende interpolación durante el arrastre
        self._progress_fill.animate = None

    def _on_scrub_update(self, e: ft.DragUpdateEvent) -> None:
        if not self._scrubbing or self._duration_ms <= 0:
            return
        pos = getattr(e, "local_position", None)
        x = float(getattr(pos, "x", 0.0) or 0.0)
        self._scrub_ms = self._x_to_ms(x)
        self._update_progress_visual(self._scrub_ms)

    def _on_scrub_end(self, _e) -> None:
        if not self._scrubbing:
            return
        target_ms = self._scrub_ms
        self._scrubbing = False
        if self._audio_state == fta.AudioState.PAUSED and self._pending_seek_ms is not None:
            self._pending_seek_ms = target_ms
        else:
            self._pending_seek_ms = None
        self._position_ms = target_ms
        self._progress_fill.animate = ft.Animation(
            PLAYER_PROGRESS_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT
        )
        self._call_audio("seek", target_ms)
        self._update_progress_visual(target_ms)
        self._persist_playback_state()

    # ── callbacks del servicio Audio e instrumentación ─────────────────

    def _is_active_audio_event(self, e) -> bool:
        ctrl = getattr(e, "control", None)
        return ctrl is None or ctrl is self.audio

    def _on_audio_loaded(self, e) -> None:
        if not self._is_active_audio_event(e):
            return
        async def _probe_duration() -> None:
            audio = self.audio
            if audio is None:
                return
            try:
                dur = await audio.get_duration()
                ms = _to_ms(dur)
                if ms > 0 and self.audio is audio:
                    self._duration_ms = ms
                    if not self._scrubbing:
                        self._update_progress_visual(self._position_ms)
            except Exception as exc:  # noqa: BLE001
                self._log(f"[AUDIO] No se pudo leer la duración: {exc}")

        try:
            self._track_task(asyncio.get_running_loop().create_task(_probe_duration()))
        except RuntimeError:
            pass

    def _on_duration_change(self, e: fta.AudioDurationChangeEvent) -> None:
        if not self._is_active_audio_event(e):
            return
        ms = _to_ms(getattr(e, "duration", None))
        if ms > 0:
            self._duration_ms = ms
            if not self._scrubbing:
                self._update_progress_visual(self._position_ms)

    def _on_position_change(self, e: fta.AudioPositionChangeEvent) -> None:
        if not self._is_active_audio_event(e):
            return
        pos_ms = _to_ms(getattr(e, "position", None))
        self._record_position_cadence(pos_ms)
        if self._ticker_alive() and self._audio_state == fta.AudioState.PLAYING:
            # Con el ticker activo, él es la única fuente de posición: mezclar los
            # eventos del plugin (que llegan con otro desfase, sobre todo en
            # streaming) hacía saltar el contador 2 s → 1 s → 2 s.
            if self._dbus_mpris is not None:
                self._dbus_mpris.set_position(self._position_ms)
            return
        self._position_ms = pos_ms
        if not self._scrubbing:
            self._update_progress_visual(pos_ms)
        if self._dbus_mpris is not None:
            self._dbus_mpris.set_position(pos_ms)

    def _ticker_alive(self) -> bool:
        return self._ticker_task is not None and not self._ticker_task.done()

    def _accept_polled_position(self, ms: int) -> bool:
        """Descarta lecturas que retroceden unos instantes (jitter) sin ser un seek."""
        back = self._position_ms - ms
        return not (0 < back < _POSITION_BACKSTEP_MS)

    def _record_position_cadence(self, pos_ms: int) -> None:
        log_path = _resolve_pos_log_path()
        now_mono = time.monotonic()
        delta_ms = (
            (now_mono - self._last_pos_event_mono) * 1000.0
            if self._last_pos_event_mono is not None
            else 0.0
        )
        self._last_pos_event_mono = now_mono
        self._pos_event_count += 1
        if not log_path:
            return
        ts_iso = datetime.now().isoformat(timespec="milliseconds")
        line = (
            f"seq={self._pos_event_count} ts={ts_iso} "
            f"mono={now_mono:.6f} delta_ms={delta_ms:.1f} pos_ms={pos_ms}\n"
        )
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    def _on_seek_complete(self, _e) -> None:
        if not self._scrubbing:
            self._update_progress_visual(self._position_ms)

    def _on_state_change(self, e: fta.AudioStateChangeEvent) -> None:
        if not self._is_active_audio_event(e):
            return
        state = getattr(e, "state", None)
        if isinstance(state, str):
            try:
                state = fta.AudioState(state)
            except ValueError:
                state = fta.AudioState.STOPPED
        if state is None:
            return
        prev_state = self._audio_state
        if (
            state == fta.AudioState.STOPPED
            and prev_state == fta.AudioState.PLAYING
            and self._expected_playing_stream is not None
            and self.queue.current is self._expected_playing_stream
        ):
            stream_item = self._expected_playing_stream
            dur_ms = max(0, int(self._duration_ms or stream_item.duration_ms or 0))
            pos_ms = max(0, int(self._position_ms or 0))
            is_startup_drop = pos_ms == 0
            is_mid_track_drop = dur_ms > 0 and 0 < pos_ms < max(0, dur_ms - 3000)
            if (is_startup_drop or is_mid_track_drop) and self._stream_retry_count < 1:
                self._stream_retry_count += 1
                self._expected_playing_stream = None
                self._stream_req_seq += 1
                resume_ms = pos_ms if is_mid_track_drop else 0
                self._spawn_ui_task(
                    self._refresh_stream_and_play(
                        stream_item,
                        self._stream_req_seq,
                        seek_ms=resume_ms,
                        is_retry=True,
                    )
                )
                return
        self._audio_state = state
        self._notify_mpris()
        if state == fta.AudioState.COMPLETED:
            self._expected_playing_stream = None
            if self.queue.repeat == RepeatMode.ONE and self.queue.current is not None:
                self._position_ms = 0
                self._audio_state = fta.AudioState.PLAYING
                self._call_audio("play", 0)
                self._ensure_ticker()
                self._sync_ui()
                return
            nxt = self.queue.next()
            if nxt is not None:
                self._load_and_play_current()
            else:
                self._stop_playback()
            return

        if state == fta.AudioState.PLAYING:
            self._ensure_ticker()
        elif state in (fta.AudioState.PAUSED, fta.AudioState.STOPPED, fta.AudioState.DISPOSED):
            if state in (fta.AudioState.STOPPED, fta.AudioState.DISPOSED):
                self._expected_playing_stream = None
            self._stop_ticker()
        self._sync_ui()

    # ── ticker de 250 ms independiente del plugin ──────────────────────

    def _ensure_ticker(self) -> None:
        if self._disposed:
            return
        if self._ticker_task is not None and not self._ticker_task.done():
            return
        try:
            self._ticker_task = self._track_task(asyncio.get_running_loop().create_task(self._ticker_loop()))
        except RuntimeError:
            pass

    def _stop_ticker(self) -> None:
        if self._ticker_task is not None and not self._ticker_task.done():
            self._ticker_task.cancel()
        self._ticker_task = None

    async def _ticker_loop(self) -> None:
        try:
            while not self._disposed and self._audio_state == fta.AudioState.PLAYING:
                await asyncio.sleep(_TICKER_INTERVAL_S)
                if self._disposed or self._audio_state != fta.AudioState.PLAYING:
                    break
                audio = self.audio
                if audio is None:
                    break
                cur_before = self.queue.current
                try:
                    pos = await audio.get_current_position()
                    if (
                        self._disposed
                        or self.audio is not audio
                        or self.queue.current is not cur_before
                        or self._audio_state != fta.AudioState.PLAYING
                    ):
                        continue
                    ms = _to_ms(pos)
                    if ms >= 0 and self._accept_polled_position(ms):
                        self._position_ms = ms
                    if self._duration_ms <= 0:
                        dur = await audio.get_duration()
                        if (
                            self._disposed
                            or self.audio is not audio
                            or self.queue.current is not cur_before
                            or self._audio_state != fta.AudioState.PLAYING
                        ):
                            continue
                        dms = _to_ms(dur)
                        if dms > 0:
                            self._duration_ms = dms
                except Exception:
                    pass
                if (
                    self._disposed
                    or self.audio is not audio
                    or self.queue.current is not cur_before
                    or self._audio_state != fta.AudioState.PLAYING
                ):
                    continue
                if not self._scrubbing:
                    self._update_progress_visual(self._position_ms)
                fade_s = int(getattr(settings_store, "get_fade_seconds", lambda: 0)() or 0)
                if (
                    fade_s > 0
                    and not self._crossfade_triggered
                    and not self._muted
                    and self._volume > 0.0
                    and self.queue.repeat != RepeatMode.ONE
                    and self._duration_ms > (fade_s * 1000 + 500)
                    and self._position_ms >= self._duration_ms - (fade_s * 1000)
                ):
                    if self._begin_crossfade_to_next(fade_s):
                        continue
        except asyncio.CancelledError:
            return

    # ── sincronización visual ──────────────────────────────────────────

    def _update_progress_visual(self, pos_ms: int) -> None:
        dur = self._duration_ms
        frac = max(0.0, min(1.0, pos_ms / dur)) if dur > 0 else 0.0
        self._progress_fill.width = frac * self._track_width
        self._time_pos_txt.value = _fmt_ms(pos_ms)
        self._time_dur_txt.value = f"−{_fmt_ms(max(0, dur - pos_ms))}" if dur > 0 else "0:00"
        try:
            self._progress_fill.update()
            self._time_pos_txt.update()
            self._time_dur_txt.update()
        except Exception:
            pass
        self._notify_position_listeners(pos_ms, dur)

    def _sync_volume_icon(self) -> None:
        if self._muted or self._volume <= 0.0:
            self._mute_btn.icon_text.value = PIcons.SPEAKER_SLASH
            self._mute_btn.icon_text.color = ACCENT if self._muted else TEXT_MUTED
        elif self._volume < 0.5:
            self._mute_btn.icon_text.value = PIcons.SPEAKER_LOW
            self._mute_btn.icon_text.color = TEXT_MUTED
        else:
            self._mute_btn.icon_text.value = PIcons.SPEAKER_HIGH
            self._mute_btn.icon_text.color = TEXT_MUTED
        try:
            self._mute_btn.update()
        except Exception:
            pass

    def _sync_ui(self) -> None:
        cur = self.queue.current
        has_queue = bool(self.queue.order)
        playing = self._audio_state == fta.AudioState.PLAYING

        if cur is not None:
            tr = cur.track
            is_stream = bool(getattr(cur, "is_stream", False))
            title = tr.name or os.path.basename(cur.path)
            fallback_artist = "Artista desconocido" if is_stream else "Archivo local"
            artist = tr.artist or fallback_artist
            self._title_txt.value = title
            self._title_txt.font_family = font_family_for(title, "bold")
            self._artist_txt.value = artist
            self._artist_txt.font_family = font_family_for(artist)
            img_url = tr.img_url or ""
            if img_url != self._last_thumb_url:
                self._last_thumb_url = img_url
                self._thumb_box.content = _thumb_content_for(
                    img_url, box=PLAYER_THUMB_SIZE, icon_size=18
                )
            if is_stream:
                self._badge_box.visible = False
            else:
                self._badge_txt.value = cur.badge_text
                self._badge_box.tooltip = cur.stream_tooltip
                self._badge_box.visible = True
            if self._duration_ms <= 0 and cur.duration_ms > 0:
                self._duration_ms = cur.duration_ms
        else:
            self._title_txt.value = "Sin reproducción"
            self._title_txt.font_family = FONT_HEADLINE_BOLD
            self._artist_txt.value = "Selecciona una pista descargada"
            self._artist_txt.font_family = FONT_TEXT
            if self._last_thumb_url != "":
                self._last_thumb_url = ""
                self._thumb_box.content = _thumb_content_for(
                    "", box=PLAYER_THUMB_SIZE, icon_size=18
                )
            self._badge_txt.value = "—"
            self._badge_box.tooltip = "Formato real del archivo"
            self._badge_box.visible = False

        self._play_btn.icon_text.value = PIcons.PAUSE if playing else PIcons.PLAY
        self._play_btn.icon_text.font_family = FONT_PHOSPHOR_FILL
        self._play_btn.disabled = not has_queue
        self._play_btn.opacity = 0.4 if not has_queue else 1.0
        self._prev_btn.disabled = not has_queue
        self._prev_btn.opacity = 0.4 if not has_queue else 1.0
        self._next_btn.disabled = not has_queue
        self._next_btn.opacity = 0.4 if not has_queue else 1.0

        self._shuffle_btn.icon_text.color = ACCENT if self.queue.shuffle else TEXT_MUTED
        self._shuffle_btn.icon_text.font_family = (
            FONT_PHOSPHOR_FILL if self.queue.shuffle else FONT_PHOSPHOR
        )
        self._shuffle_btn.tooltip = (
            "Aleatorio: activado" if self.queue.shuffle else "Aleatorio: desactivado"
        )

        if self.queue.repeat == RepeatMode.ONE:
            self._repeat_btn.icon_text.value = PIcons.REPEAT_ONCE
            self._repeat_btn.icon_text.font_family = FONT_PHOSPHOR_FILL
            self._repeat_btn.icon_text.color = ACCENT
            self._repeat_btn.tooltip = "Repetir: esta canción"
        elif self.queue.repeat == RepeatMode.ALL:
            self._repeat_btn.icon_text.value = PIcons.REPEAT
            self._repeat_btn.icon_text.font_family = FONT_PHOSPHOR_FILL
            self._repeat_btn.icon_text.color = ACCENT
            self._repeat_btn.tooltip = "Repetir: toda la cola"
        else:
            self._repeat_btn.icon_text.value = PIcons.REPEAT
            self._repeat_btn.icon_text.font_family = FONT_PHOSPHOR
            self._repeat_btn.icon_text.color = TEXT_MUTED
            self._repeat_btn.tooltip = "Repetir: desactivado"

        self._queue_count_txt.value = str(len(self.queue.order))
        self._sync_volume_icon()
        if not self._scrubbing:
            self._update_progress_visual(self._position_ms)

        try:
            self.update()
        except Exception:
            pass

    def attach_mpris(self, service: MPRISService) -> None:
        """Enlaza el servicio MPRIS a través de ``MprisController``."""
        self._mpris.attach(service)

    def _notify_mpris(self) -> None:
        self._mpris.notify()

    def _notify_mpris_playing(self, playable_or_track: PlayableTrack | Track) -> None:
        self._mpris.notify_playing(playable_or_track)
