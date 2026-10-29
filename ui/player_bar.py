"""
ui/player_bar.py — Barra de reproducción global integrada con flet-audio
════════════════════════════════════════════════════════════════════════
Control visual propio sobre una única instancia de ``flet_audio.Audio``
registrada en ``page.services``. Toda la lógica de cola vive en
``core/playback.py``.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Callable

import flet as ft
import flet_audio as fta

from core.models import Track
from core.playback import PlayableTrack, PlayQueue, RepeatMode, build_playable_track
from services import settings_store
from ui.fonts import FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for, mono_family
from ui.tokens import (
    ACCENT, BG_HOVER, BG_LIST, BG_PANEL, BG_SURFACE, BORDER_MUTED, CHIP_BG,
    OVERLAY_08, PLAYER_BAR_H, PLAYER_PROGRESS_ANIM_MS, PLAYER_PROGRESS_H,
    PLAYER_THUMB_SIZE, RADIUS_BUTTON, RADIUS_INPUT, TEXT_DIM, TEXT_MUTED,
    TEXT_PRIMARY,
)
from ui.widgets import app_text, notify

_TICKER_INTERVAL_S = 0.25
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


def _fmt_ms(ms: int) -> str:
    sec = max(0, int(ms) // 1000)
    m, s = divmod(sec, 60)
    return f"{m}:{s:02d}"


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
    ) -> None:
        super().__init__(
            height=PLAYER_BAR_H,
            bgcolor=BG_PANEL,
            border=ft.Border.only(top=ft.BorderSide(0.8, BORDER_MUTED)),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
        )
        self._page_ref = page
        self.app_state = app_state
        self._on_toggle_queue = on_toggle_queue
        self._listeners: list[Callable[[], None]] = []

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
        self._disposed: bool = False

        # Instrumentación de cadencia de on_position_change
        self._pos_event_count: int = 0
        self._last_pos_event_mono: float | None = None

        # Única instancia de Audio por proceso en page.services
        self.audio = self._ensure_single_audio_service(page)
        self._build_ui()
        self._sync_ui()

    def _ensure_single_audio_service(self, page: ft.Page) -> fta.Audio:
        existing = [s for s in page.services if isinstance(s, fta.Audio)]
        if existing:
            audio = existing[0]
            for extra in existing[1:]:
                try:
                    page.services.remove(extra)
                except ValueError:
                    pass
        else:
            audio = fta.Audio(
                autoplay=False,
                volume=0.0 if self._muted else self._volume,
                release_mode=fta.ReleaseMode.STOP,
            )
            page.services.append(audio)

        audio.release_mode = fta.ReleaseMode.STOP
        audio.volume = 0.0 if self._muted else self._volume
        audio.on_loaded = self._on_audio_loaded
        audio.on_duration_change = self._on_duration_change
        audio.on_state_change = self._on_state_change
        audio.on_position_change = self._on_position_change
        audio.on_seek_complete = self._on_seek_complete
        return audio

    def log_audio_services_count(self, context: str = "") -> int:
        count = sum(1 for s in self._page_ref.services if isinstance(s, fta.Audio))
        prefix = f"[AUDIO] Instancias Audio en page.services={count}"
        msg = f"{prefix} ({context})" if context else prefix
        print(msg, flush=True)
        if self.app_state is not None and hasattr(self.app_state, "log"):
            self.app_state.log(msg)
        return count

    # ── suscripción para el panel de cola ──────────────────────────────

    def subscribe(self, cb: Callable[[], None]) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)

    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    # ── construcción visual ────────────────────────────────────────────

    def _build_ui(self) -> None:
        # [thumb]
        self._thumb_icon = ft.Icon(ft.Icons.MUSIC_NOTE, size=18, color=TEXT_DIM)
        self._thumb_img = ft.Image(
            src="",
            width=PLAYER_THUMB_SIZE,
            height=PLAYER_THUMB_SIZE,
            fit=ft.BoxFit.COVER,
            border_radius=RADIUS_INPUT,
            visible=False,
        )
        self._thumb_box = ft.Container(
            width=PLAYER_THUMB_SIZE,
            height=PLAYER_THUMB_SIZE,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            alignment=ft.Alignment.CENTER,
            content=ft.Stack(controls=[self._thumb_icon, self._thumb_img]),
        )

        # [título/artista]
        self._title_txt = app_text(
            "Sin reproducción",
            size=12,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._artist_txt = app_text(
            "Selecciona una pista descargada",
            size=11,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        meta_col = ft.Container(
            width=190,
            content=ft.Column(
                controls=[self._title_txt, self._artist_txt],
                spacing=1,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )

        # [badge formato]
        self._badge_txt = app_text(
            "—",
            size=10,
            color=TEXT_MUTED,
            font_family=mono_family("medium"),
        )
        self._badge_box = ft.Container(
            bgcolor=CHIP_BG,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            content=self._badge_txt,
            tooltip="Formato real del archivo",
        )

        # [▶/⏸] [⏮ ⏭]
        self._play_btn = ft.IconButton(
            icon=ft.Icons.PLAY_CIRCLE_FILL,
            icon_color=ACCENT,
            icon_size=30,
            tooltip="Reproducir / Pausar",
            on_click=self._on_play_pause_click,
        )
        self._prev_btn = ft.IconButton(
            icon=ft.Icons.SKIP_PREVIOUS_ROUNDED,
            icon_color=TEXT_PRIMARY,
            icon_size=20,
            tooltip="Anterior (o reiniciar si > 3 s)",
            on_click=self._on_prev_click,
        )
        self._next_btn = ft.IconButton(
            icon=ft.Icons.SKIP_NEXT_ROUNDED,
            icon_color=TEXT_PRIMARY,
            icon_size=20,
            tooltip="Siguiente",
            on_click=self._on_next_click,
        )

        # [tiempo]
        self._time_txt = app_text(
            "0:00 / 0:00",
            size=11,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
        )

        # [barra]
        self._progress_fill = ft.Container(
            width=0,
            height=PLAYER_PROGRESS_H,
            bgcolor=ACCENT,
            border_radius=PLAYER_PROGRESS_H / 2,
            animate=ft.Animation(PLAYER_PROGRESS_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        self._progress_track = ft.Container(
            height=PLAYER_PROGRESS_H,
            bgcolor=BG_SURFACE,
            border_radius=PLAYER_PROGRESS_H / 2,
            alignment=ft.Alignment.CENTER_LEFT,
            on_size_change=self._on_track_size_change,
            content=self._progress_fill,
        )
        self._progress_gesture = ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap_down=self._on_progress_tap,
            on_horizontal_drag_start=self._on_scrub_start,
            on_horizontal_drag_update=self._on_scrub_update,
            on_horizontal_drag_end=self._on_scrub_end,
            content=ft.Container(
                padding=ft.Padding.symmetric(vertical=8),
                content=self._progress_track,
            ),
            expand=True,
        )

        # [volumen]
        self._mute_btn = ft.IconButton(
            icon=ft.Icons.VOLUME_UP_ROUNDED,
            icon_color=TEXT_MUTED,
            icon_size=18,
            tooltip="Silenciar / Activar sonido",
            on_click=self._on_mute_click,
        )
        self._vol_slider = ft.Slider(
            min=0.0,
            max=1.0,
            value=0.0 if self._muted else self._volume,
            width=100,
            active_color=ACCENT,
            inactive_color=BG_SURFACE,
            on_change=self._on_volume_change,
            on_change_end=self._on_volume_change_end,
        )
        vol_row = ft.Row(
            controls=[self._mute_btn, self._vol_slider],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # [aleatorio] [repetir] [cola]
        self._shuffle_btn = ft.IconButton(
            icon=ft.Icons.SHUFFLE_ROUNDED,
            icon_color=TEXT_MUTED,
            icon_size=18,
            tooltip="Aleatorio",
            on_click=self._on_shuffle_click,
        )
        self._repeat_btn = ft.IconButton(
            icon=ft.Icons.REPEAT_ROUNDED,
            icon_color=TEXT_MUTED,
            icon_size=18,
            tooltip="Repetir: desactivado",
            on_click=self._on_repeat_click,
        )
        self._queue_count_txt = app_text(
            "0",
            size=10,
            color=TEXT_MUTED,
            font_family=mono_family("medium"),
        )
        self._queue_btn = ft.Container(
            bgcolor=OVERLAY_08,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_BUTTON,
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            ink=True,
            tooltip="Mostrar / ocultar cola de reproducción",
            on_click=lambda _e: self._on_toggle_queue() if self._on_toggle_queue else None,
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.QUEUE_MUSIC_ROUNDED, size=16, color=TEXT_PRIMARY),
                    self._queue_count_txt,
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        self.content = ft.Row(
            controls=[
                self._thumb_box,
                meta_col,
                self._badge_box,
                self._play_btn,
                ft.Row(controls=[self._prev_btn, self._next_btn], spacing=0),
                self._time_txt,
                self._progress_gesture,
                vol_row,
                self._shuffle_btn,
                self._repeat_btn,
                self._queue_btn,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )

    # ── despacho seguro de métodos de Audio (sync o coroutine) ─────────

    def _schedule_audio(self, fn: Callable, *args, **kwargs) -> None:
        """Ejecuta métodos de ``flet_audio.Audio`` tanto si son sync como coroutine."""
        if self._disposed:
            return
        try:
            result = fn(*args, **kwargs)
        except Exception:
            return
        if asyncio.iscoroutine(result):
            try:
                asyncio.get_running_loop().create_task(result)
            except RuntimeError:
                try:
                    self._page_ref.run_task(lambda: result)
                except Exception:
                    try:
                        result.close()
                    except Exception:
                        pass

    # ── API pública de reproducción y cola ─────────────────────────────

    def play_playable(self, playable: PlayableTrack, *, enqueue_first: bool = True) -> None:
        if enqueue_first:
            self.queue.enqueue(playable, play_now=True)
        self._load_and_play_current()

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
        for entry in items:
            if isinstance(entry, tuple):
                p, tr = entry
            else:
                p, tr = str(entry), None
            pt = build_playable_track(p, tr)
            if pt is not None:
                playables.append(pt)
        if not playables:
            notify(self._page_ref, "No hay archivos locales reproducibles", kind="error")
            return 0
        self.queue.set_tracks(playables, start_index=start_index)
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

    def _load_and_play_current(self) -> None:
        cur = self.queue.current
        if cur is None:
            self._stop_playback()
            return
        abs_path = os.path.abspath(cur.path)
        if not os.path.isfile(abs_path):
            notify(self._page_ref, f"Archivo no encontrado: {os.path.basename(abs_path)}", kind="error")
            return
        self._position_ms = 0
        self._duration_ms = max(0, int(cur.duration_ms or 0))
        self.audio.src = abs_path
        self.audio.release_mode = fta.ReleaseMode.STOP
        self.audio.volume = 0.0 if self._muted else self._volume
        self._audio_state = fta.AudioState.PLAYING
        try:
            self.audio.update()
        except Exception:
            pass
        self._schedule_audio(self.audio.play, 0)
        self._ensure_ticker()
        self._sync_ui()
        self._notify_listeners()

    def _stop_playback(self) -> None:
        self._audio_state = fta.AudioState.STOPPED
        self._position_ms = 0
        self._duration_ms = 0
        self._stop_ticker()
        self._schedule_audio(self.audio.release)
        self._sync_ui()
        self._notify_listeners()

    def release(self) -> None:
        """Libera el decodificador nativo y detiene el ticker (hard_cleanup)."""
        self._disposed = True
        self._stop_ticker()
        try:
            res = self.audio.release()
            if asyncio.iscoroutine(res):
                try:
                    asyncio.get_running_loop().create_task(res)
                except RuntimeError:
                    res.close()
        except Exception:
            pass

    # ── eventos de controles ───────────────────────────────────────────

    def _on_play_pause_click(self, _e=None) -> None:
        cur = self.queue.current
        if cur is None:
            if self.queue.order:
                self.queue.select(0)
                self._load_and_play_current()
            return
        if self._audio_state == fta.AudioState.PLAYING:
            self._audio_state = fta.AudioState.PAUSED
            self._schedule_audio(self.audio.pause)
            self._stop_ticker()
        elif self._audio_state == fta.AudioState.PAUSED:
            self._audio_state = fta.AudioState.PLAYING
            self._schedule_audio(self.audio.resume)
            self._ensure_ticker()
        else:
            self._load_and_play_current()
            return
        self._sync_ui()
        self._notify_listeners()

    def _on_prev_click(self, _e=None) -> None:
        if not self.queue.order:
            return
        if self._position_ms > PlayQueue.RESTART_THRESHOLD_MS and self.queue.current is not None:
            self._position_ms = 0
            self._schedule_audio(self.audio.seek, 0)
            if self._audio_state != fta.AudioState.PLAYING:
                self._audio_state = fta.AudioState.PLAYING
                self._schedule_audio(self.audio.resume)
                self._ensure_ticker()
            self._sync_ui()
            return
        prev_cur = self.queue.current
        target = self.queue.prev(elapsed_ms=self._position_ms)
        if target is not None:
            if target is prev_cur:
                self._position_ms = 0
                self._schedule_audio(self.audio.seek, 0)
                self._sync_ui()
            else:
                self._load_and_play_current()

    def _on_next_click(self, _e=None) -> None:
        if not self.queue.order:
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
        self.audio.volume = 0.0 if self._muted else self._volume
        self._vol_slider.value = 0.0 if self._muted else self._volume
        try:
            self.audio.update()
        except Exception:
            pass
        setter = getattr(settings_store, "set_mute", None)
        if callable(setter):
            setter(self._muted)
        self._sync_ui()

    def _on_volume_change(self, e: ft.ControlEvent) -> None:
        try:
            val = float(e.control.value or 0.0)
        except (TypeError, ValueError):
            val = 1.0
        self._volume = max(0.0, min(1.0, val))
        if self._volume > 0 and self._muted:
            self._muted = False
        self.audio.volume = 0.0 if self._muted else self._volume
        try:
            self.audio.update()
        except Exception:
            pass
        self._sync_volume_icon()

    def _on_volume_change_end(self, _e: ft.ControlEvent) -> None:
        set_vol = getattr(settings_store, "set_volume", None)
        if callable(set_vol):
            set_vol(self._volume)
        set_mute = getattr(settings_store, "set_mute", None)
        if callable(set_mute):
            set_mute(self._muted)

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
        self._position_ms = target_ms
        self._schedule_audio(self.audio.seek, target_ms)
        self._update_progress_visual(target_ms)

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
        self._position_ms = target_ms
        self._progress_fill.animate = ft.Animation(
            PLAYER_PROGRESS_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT
        )
        self._schedule_audio(self.audio.seek, target_ms)
        self._update_progress_visual(target_ms)

    # ── callbacks del servicio Audio e instrumentación ─────────────────

    def _on_audio_loaded(self, _e) -> None:
        async def _probe_duration() -> None:
            try:
                dur = await self.audio.get_duration()
                ms = _to_ms(dur)
                if ms > 0:
                    self._duration_ms = ms
                    if not self._scrubbing:
                        self._update_progress_visual(self._position_ms)
            except Exception:
                pass

        try:
            asyncio.get_running_loop().create_task(_probe_duration())
        except RuntimeError:
            pass

    def _on_duration_change(self, e: fta.AudioDurationChangeEvent) -> None:
        ms = _to_ms(getattr(e, "duration", None))
        if ms > 0:
            self._duration_ms = ms
            if not self._scrubbing:
                self._update_progress_visual(self._position_ms)

    def _on_position_change(self, e: fta.AudioPositionChangeEvent) -> None:
        pos_ms = _to_ms(getattr(e, "position", None))
        self._record_position_cadence(pos_ms)
        self._position_ms = pos_ms
        if not self._scrubbing:
            self._update_progress_visual(pos_ms)

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
        state = getattr(e, "state", None)
        if isinstance(state, str):
            try:
                state = fta.AudioState(state)
            except ValueError:
                state = fta.AudioState.STOPPED
        if state is None:
            return
        self._audio_state = state

        if state == fta.AudioState.COMPLETED:
            if self.queue.repeat == RepeatMode.ONE and self.queue.current is not None:
                self._position_ms = 0
                self._audio_state = fta.AudioState.PLAYING
                self._schedule_audio(self.audio.play, 0)
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
            self._stop_ticker()
        self._sync_ui()

    # ── ticker de 250 ms independiente del plugin ──────────────────────

    def _ensure_ticker(self) -> None:
        if self._disposed:
            return
        if self._ticker_task is not None and not self._ticker_task.done():
            return
        try:
            self._ticker_task = asyncio.get_running_loop().create_task(self._ticker_loop())
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
                try:
                    pos = await self.audio.get_current_position()
                    ms = _to_ms(pos)
                    if ms >= 0:
                        self._position_ms = ms
                    if self._duration_ms <= 0:
                        dur = await self.audio.get_duration()
                        dms = _to_ms(dur)
                        if dms > 0:
                            self._duration_ms = dms
                except Exception:
                    pass
                if not self._scrubbing:
                    self._update_progress_visual(self._position_ms)
        except asyncio.CancelledError:
            return

    # ── sincronización visual ──────────────────────────────────────────

    def _update_progress_visual(self, pos_ms: int) -> None:
        dur = self._duration_ms
        frac = max(0.0, min(1.0, pos_ms / dur)) if dur > 0 else 0.0
        self._progress_fill.width = frac * self._track_width
        self._time_txt.value = f"{_fmt_ms(pos_ms)} / {_fmt_ms(dur)}"
        try:
            self._progress_fill.update()
            self._time_txt.update()
        except Exception:
            pass

    def _sync_volume_icon(self) -> None:
        if self._muted or self._volume <= 0.0:
            self._mute_btn.icon = ft.Icons.VOLUME_OFF_ROUNDED
            self._mute_btn.icon_color = ACCENT if self._muted else TEXT_MUTED
        elif self._volume < 0.5:
            self._mute_btn.icon = ft.Icons.VOLUME_DOWN_ROUNDED
            self._mute_btn.icon_color = TEXT_MUTED
        else:
            self._mute_btn.icon = ft.Icons.VOLUME_UP_ROUNDED
            self._mute_btn.icon_color = TEXT_MUTED
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
            title = tr.name or os.path.basename(cur.path)
            artist = tr.artist or "Archivo local"
            self._title_txt.value = title
            self._title_txt.font_family = font_family_for(title, bold=True)
            self._artist_txt.value = artist
            self._artist_txt.font_family = font_family_for(artist)
            if tr.img_url:
                self._thumb_img.src = tr.img_url
                self._thumb_img.visible = True
            else:
                self._thumb_img.visible = False
            self._badge_txt.value = cur.badge_text
            self._badge_box.tooltip = cur.stream_tooltip
            if self._duration_ms <= 0 and cur.duration_ms > 0:
                self._duration_ms = cur.duration_ms
        else:
            self._title_txt.value = "Sin reproducción"
            self._title_txt.font_family = FONT_HEADLINE_BOLD
            self._artist_txt.value = "Selecciona una pista descargada"
            self._artist_txt.font_family = FONT_TEXT
            self._thumb_img.visible = False
            self._badge_txt.value = "—"
            self._badge_box.tooltip = "Formato real del archivo"

        self._play_btn.icon = (
            ft.Icons.PAUSE_CIRCLE_FILLED if playing else ft.Icons.PLAY_CIRCLE_FILL
        )
        self._play_btn.disabled = not has_queue
        self._prev_btn.disabled = not has_queue
        self._next_btn.disabled = not has_queue

        self._shuffle_btn.icon_color = ACCENT if self.queue.shuffle else TEXT_MUTED
        self._shuffle_btn.tooltip = (
            "Aleatorio: activado" if self.queue.shuffle else "Aleatorio: desactivado"
        )

        if self.queue.repeat == RepeatMode.ONE:
            self._repeat_btn.icon = ft.Icons.REPEAT_ONE_ROUNDED
            self._repeat_btn.icon_color = ACCENT
            self._repeat_btn.tooltip = "Repetir: esta canción"
        elif self.queue.repeat == RepeatMode.ALL:
            self._repeat_btn.icon = ft.Icons.REPEAT_ROUNDED
            self._repeat_btn.icon_color = ACCENT
            self._repeat_btn.tooltip = "Repetir: toda la cola"
        else:
            self._repeat_btn.icon = ft.Icons.REPEAT_ROUNDED
            self._repeat_btn.icon_color = TEXT_MUTED
            self._repeat_btn.tooltip = "Repetir: desactivado"

        self._queue_count_txt.value = str(len(self.queue.order))
        self._sync_volume_icon()
        if not self._scrubbing:
            self._update_progress_visual(self._position_ms)

        try:
            self.update()
        except Exception:
            pass
