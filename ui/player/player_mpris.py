"""ui/player/player_mpris.py — Controlador MPRIS compuesto por PlayerBar."""

from __future__ import annotations

import os

import flet_audio as fta

from core.models import Track
from core.playback import PlayableTrack, RepeatMode
from services import settings_store
from services.mpris_service import LoopStatus, MPRISService, PlaybackStatus


class MprisController:
    """Puente compuesto entre ``PlayerBar`` y el servicio MPRIS de escritorio."""

    def __init__(self, player) -> None:
        self._player = player

    def attach(self, service: MPRISService) -> None:
        """Enlaza el servicio MPRIS y arranca su registro en el bus de sesión."""
        p = self._player
        p._dbus_mpris = service

        def _on_ready() -> None:
            cur = p.queue.current
            if cur is not None:
                self.notify_playing(cur)
            else:
                self.notify()

        service.set_handlers(
            on_ready=_on_ready,
            play=self._play,
            pause=self._pause,
            play_pause=p._on_play_pause_click,
            stop=p._stop_playback,
            next=p._on_next_click,
            previous=p._on_prev_click,
            seek=self._seek_by,
            set_position=self._seek_to,
            set_shuffle=self._set_shuffle,
            set_loop=self._set_loop,
            set_volume=self._set_volume,
            raise_window=self._raise_window,
            quit=self._quit_window,
        )
        try:
            p._page_ref.run_task(service.start)
        except Exception as exc:  # noqa: BLE001
            p._log(f"[MPRIS] No se pudo iniciar: {exc}")

    def _raise_window(self) -> None:
        try:
            if hasattr(self._player._page_ref, "window"):
                self._player._page_ref.window.focus()
        except Exception:  # noqa: BLE001
            pass

    def _quit_window(self) -> None:
        try:
            if hasattr(self._player._page_ref, "window"):
                self._player._page_ref.window.close()
        except Exception:  # noqa: BLE001
            pass

    def _play(self) -> None:
        if self._player._audio_state != fta.AudioState.PLAYING:
            self._player._on_play_pause_click()

    def _pause(self) -> None:
        if self._player._audio_state == fta.AudioState.PLAYING:
            self._player._on_play_pause_click()

    def _seek_to(self, target_ms: int) -> None:
        p = self._player
        if p.queue.current is None:
            return
        target_ms = max(0, int(target_ms))
        if p._duration_ms > 0:
            target_ms = min(target_ms, p._duration_ms)
        p._position_ms = target_ms
        p._call_audio("seek", target_ms)
        p._update_progress_visual(target_ms)
        if p._dbus_mpris is not None:
            p._dbus_mpris.notify_seeked(target_ms)

    def _seek_by(self, offset_ms: int) -> None:
        self._seek_to(self._player._position_ms + int(offset_ms))

    def _set_shuffle(self, enabled: bool) -> None:
        p = self._player
        if enabled != p.queue.shuffle:
            p._on_shuffle_click()

    def _set_loop(self, value: str) -> None:
        p = self._player
        target = {"None": RepeatMode.OFF, "Track": RepeatMode.ONE, "Playlist": RepeatMode.ALL}.get(value)
        for _ in range(3):
            if target is None or p.queue.repeat == target:
                break
            p._on_repeat_click()

    def _set_volume(self, value: float) -> None:
        p = self._player
        p._volume = max(0.0, min(1.0, float(value)))
        p._muted = False
        p._apply_volume()
        p._vol_slider.value = p._volume
        p._sync_volume_icon()
        set_vol = getattr(settings_store, "set_volume", None)
        if callable(set_vol):
            set_vol(p._volume)

    def notify(self) -> None:
        """Sincroniza estado, modos y posición con el servicio MPRIS."""
        p = self._player
        svc = p._dbus_mpris
        if svc is None:
            return
        if p.queue.current is None:
            svc.clear_track()
        if p._audio_state == fta.AudioState.PLAYING:
            svc.set_playback_status(PlaybackStatus.PLAYING)
        elif p._audio_state == fta.AudioState.PAUSED:
            svc.set_playback_status(PlaybackStatus.PAUSED)
        else:
            svc.set_playback_status(PlaybackStatus.STOPPED)
        svc.set_position(p._position_ms)
        svc.set_modes(
            shuffle=p.queue.shuffle,
            loop={RepeatMode.OFF: LoopStatus.NONE, RepeatMode.ONE: LoopStatus.TRACK,
                  RepeatMode.ALL: LoopStatus.PLAYLIST}[p.queue.repeat],
            volume=0.0 if p._muted else p._volume,
        )

    def notify_playing(self, playable_or_track: PlayableTrack | Track) -> None:
        p = self._player
        svc = p._dbus_mpris
        if svc is None:
            return
        if isinstance(playable_or_track, PlayableTrack):
            track = playable_or_track.track
            duration_ms = p._duration_ms or playable_or_track.duration_ms or track.duration_ms
            source_url = playable_or_track.path
        else:
            track = playable_or_track
            duration_ms = p._duration_ms or track.duration_ms
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
        self.notify()
