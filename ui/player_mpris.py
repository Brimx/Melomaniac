"""ui/player_mpris.py — Puente entre el reproductor y MPRIS (controles del escritorio).

Mixin de `PlayerBar` (ui/player_bar.py): los métodos se mezclan en la clase
y comparten su `self`.
"""

from __future__ import annotations

import flet_audio as fta
import os

from core.playback import PlayableTrack, RepeatMode
from services.mpris_service import LoopStatus, MPRISService, PlaybackStatus
from core.models import Track
from services import settings_store


class MprisBridgeMixin:
    """Puente entre el reproductor y MPRIS (controles del escritorio)."""

    def attach_mpris(self, service: MPRISService) -> None:
        """Enlaza el servicio MPRIS y arranca su registro en el bus de sesión."""
        self._dbus_mpris = service

        def _on_ready() -> None:
            cur = self.queue.current
            if cur is not None:
                self._notify_mpris_playing(cur)
            else:
                self._notify_mpris()

        service.set_handlers(
            on_ready=_on_ready,
            play=self._mpris_play,
            pause=self._mpris_pause,
            play_pause=self._on_play_pause_click,
            stop=self._stop_playback,
            next=self._on_next_click,
            previous=self._on_prev_click,
            seek=self._mpris_seek_by,
            set_position=self._mpris_seek_to,
            set_shuffle=self._mpris_set_shuffle,
            set_loop=self._mpris_set_loop,
            set_volume=self._mpris_set_volume,
            raise_window=self._mpris_raise,
            quit=self._mpris_quit,
        )
        try:
            self._page_ref.run_task(service.start)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[MPRIS] No se pudo iniciar: {exc}")

    def _mpris_raise(self) -> None:
        try:
            if hasattr(self._page_ref, "window"):
                self._page_ref.window.focus()
        except Exception:  # noqa: BLE001
            pass

    def _mpris_quit(self) -> None:
        try:
            if hasattr(self._page_ref, "window"):
                self._page_ref.window.close()
        except Exception:  # noqa: BLE001
            pass

    def _mpris_play(self) -> None:
        if self._audio_state != fta.AudioState.PLAYING:
            self._on_play_pause_click()

    def _mpris_pause(self) -> None:
        if self._audio_state == fta.AudioState.PLAYING:
            self._on_play_pause_click()

    def _mpris_seek_to(self, target_ms: int) -> None:
        if self.queue.current is None:
            return
        target_ms = max(0, int(target_ms))
        if self._duration_ms > 0:
            target_ms = min(target_ms, self._duration_ms)
        self._position_ms = target_ms
        self._call_audio("seek", target_ms)
        self._update_progress_visual(target_ms)
        if self._dbus_mpris is not None:
            self._dbus_mpris.notify_seeked(target_ms)

    def _mpris_seek_by(self, offset_ms: int) -> None:
        self._mpris_seek_to(self._position_ms + int(offset_ms))

    def _mpris_set_shuffle(self, enabled: bool) -> None:
        if enabled != self.queue.shuffle:
            self._on_shuffle_click()

    def _mpris_set_loop(self, value: str) -> None:
        target = {"None": RepeatMode.OFF, "Track": RepeatMode.ONE, "Playlist": RepeatMode.ALL}.get(value)
        for _ in range(3):
            if target is None or self.queue.repeat == target:
                break
            self._on_repeat_click()

    def _mpris_set_volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, float(value)))
        self._muted = False
        self._apply_volume()
        self._vol_slider.value = self._volume
        self._sync_volume_icon()
        set_vol = getattr(settings_store, "set_volume", None)
        if callable(set_vol):
            set_vol(self._volume)

    def _notify_mpris(self) -> None:
        """Sincroniza estado, modos y posición con el servicio MPRIS."""
        svc = self._dbus_mpris
        if svc is None:
            return
        if self.queue.current is None:
            svc.clear_track()
        if self._audio_state == fta.AudioState.PLAYING:
            svc.set_playback_status(PlaybackStatus.PLAYING)
        elif self._audio_state == fta.AudioState.PAUSED:
            svc.set_playback_status(PlaybackStatus.PAUSED)
        else:
            svc.set_playback_status(PlaybackStatus.STOPPED)
        svc.set_position(self._position_ms)
        svc.set_modes(
            shuffle=self.queue.shuffle,
            loop={RepeatMode.OFF: LoopStatus.NONE, RepeatMode.ONE: LoopStatus.TRACK,
                  RepeatMode.ALL: LoopStatus.PLAYLIST}[self.queue.repeat],
            volume=0.0 if self._muted else self._volume,
        )

    def _notify_mpris_playing(self, playable_or_track: PlayableTrack | Track) -> None:
        svc = self._dbus_mpris
        if svc is None:
            return
        if isinstance(playable_or_track, PlayableTrack):
            track = playable_or_track.track
            duration_ms = self._duration_ms or playable_or_track.duration_ms or track.duration_ms
            source_url = playable_or_track.path
        else:
            track = playable_or_track
            duration_ms = self._duration_ms or track.duration_ms
            source_url = getattr(track, "source_path", "") or getattr(track, "source_url", "")
        img_url = track.img_url or ""
        if not img_url and source_url and os.path.isfile(source_url):
            try:
                from engine.parsers import _extract_embedded_cover, _folder_cover
                folder = os.path.dirname(os.path.abspath(source_url))
                img_url = _folder_cover(folder) or _extract_embedded_cover(source_url)
            except Exception:
                pass
        svc.set_current_track(
            track.name,
            track.artist,
            album=track.album,
            img_url=img_url,
            source_url=source_url,
            duration_ms=duration_ms,
        )
        self._notify_mpris()
