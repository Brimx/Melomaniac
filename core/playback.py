"""
core/playback.py — Cola y estado lógico de reproducción (sin Flet)
══════════════════════════════════════════════════════════════════
Separa los algoritmos de cola (aleatorio, repetir, historial, mover,
eliminar y avance) de la interfaz gráfica para poder probarlos de forma
aislada con ``unittest``.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterable

from core.models import Track


@dataclass(frozen=True, eq=False)
class PlayableTrack:
    """Pista con archivo local verificado y metadatos reales del stream."""

    track: Track
    path: str
    container: str
    codec: str
    duration_ms: int = 0
    bitrate_kbps: int | None = None
    sample_rate: int | None = None

    @property
    def badge_text(self) -> str:
        container = (self.container or "").strip().upper()
        codec = (self.codec or "").strip().upper()
        if container and codec and codec != container:
            return f"{container} · {codec}"
        return container or codec or "AUDIO"

    @property
    def stream_tooltip(self) -> str:
        parts = [self.badge_text]
        if self.bitrate_kbps:
            parts.append(f"{self.bitrate_kbps} kbps")
        if self.sample_rate:
            parts.append(f"{self.sample_rate} Hz")
        return " · ".join(parts)


class RepeatMode(Enum):
    OFF = auto()
    ALL = auto()
    ONE = auto()


class PlayQueue:
    """Cola de reproducción con orden desacoplado, aleatorio e historial."""

    RESTART_THRESHOLD_MS = 3000

    def __init__(
        self,
        tracks: Iterable[PlayableTrack] = (),
        *,
        shuffle: bool = False,
        repeat: RepeatMode = RepeatMode.OFF,
        rng: random.Random | None = None,
    ) -> None:
        self._rng = rng or random.Random()
        self.tracks: list[PlayableTrack] = list(tracks)
        self.order: list[int] = list(range(len(self.tracks)))
        self.pos: int = 0 if self.tracks else -1
        self.shuffle: bool = False
        self.repeat: RepeatMode = repeat
        self.history: list[PlayableTrack] = []
        if shuffle:
            self.set_shuffle(True)

    @property
    def current(self) -> PlayableTrack | None:
        if 0 <= self.pos < len(self.order):
            idx = self.order[self.pos]
            if 0 <= idx < len(self.tracks):
                return self.tracks[idx]
        return None

    def ordered_tracks(self) -> list[PlayableTrack]:
        return [self.tracks[idx] for idx in self.order]

    def _find_track_index(self, target: PlayableTrack) -> int:
        for idx, item in enumerate(self.tracks):
            if item is target:
                return idx
        return -1

    def _find_order_pos(self, target: PlayableTrack) -> int:
        for pos_idx, track_idx in enumerate(self.order):
            if self.tracks[track_idx] is target:
                return pos_idx
        return -1

    def set_tracks(
        self,
        tracks: Iterable[PlayableTrack],
        start_index: int = 0,
    ) -> PlayableTrack | None:
        self.tracks = list(tracks)
        self.history.clear()
        if not self.tracks:
            self.order = []
            self.pos = -1
            return None
        clamped = max(0, min(start_index, len(self.tracks) - 1))
        self.order = list(range(len(self.tracks)))
        self.pos = clamped
        if self.shuffle:
            self.set_shuffle(True)
        return self.current

    def enqueue(
        self,
        item: PlayableTrack,
        *,
        play_now: bool = False,
    ) -> PlayableTrack | None:
        for pos_idx, track_idx in enumerate(self.order):
            existing = self.tracks[track_idx]
            if existing is item or (item.path and existing.path == item.path):
                if play_now:
                    return self.select(pos_idx)
                return self.current

        new_track_idx = len(self.tracks)
        self.tracks.append(item)
        self.order.append(new_track_idx)
        new_pos = len(self.order) - 1
        if play_now or self.pos < 0:
            return self.select(new_pos)
        return self.current

    def select(self, queue_pos: int) -> PlayableTrack | None:
        if not (0 <= queue_pos < len(self.order)):
            return None
        cur = self.current
        if cur is not None and queue_pos != self.pos:
            self.history.append(cur)
        self.pos = queue_pos
        return self.current

    def clear(self) -> None:
        self.tracks.clear()
        self.order.clear()
        self.history.clear()
        self.pos = -1

    def set_shuffle(self, enabled: bool) -> None:
        self.shuffle = bool(enabled)
        if not self.tracks:
            self.order = []
            self.pos = -1
            return

        cur = self.current
        if self.shuffle:
            if cur is not None:
                cur_idx = self._find_track_index(cur)
                rest = [i for i in range(len(self.tracks)) if i != cur_idx]
                self._rng.shuffle(rest)
                self.order = [cur_idx] + rest
                self.pos = 0
            else:
                rest = list(range(len(self.tracks)))
                self._rng.shuffle(rest)
                self.order = rest
        else:
            self.order = list(range(len(self.tracks)))
            if cur is not None:
                self.pos = self._find_order_pos(cur)
            elif self.pos >= len(self.order):
                self.pos = -1

    def cycle_repeat(self) -> RepeatMode:
        if self.repeat == RepeatMode.OFF:
            self.repeat = RepeatMode.ALL
        elif self.repeat == RepeatMode.ALL:
            self.repeat = RepeatMode.ONE
        else:
            self.repeat = RepeatMode.OFF
        return self.repeat

    def next(self) -> PlayableTrack | None:
        if not self.order or self.pos < 0:
            return None
        if self.repeat == RepeatMode.ONE:
            return self.current

        cur = self.current
        if cur is not None:
            self.history.append(cur)

        if self.pos + 1 < len(self.order):
            self.pos += 1
            return self.current

        if self.repeat == RepeatMode.ALL:
            self.pos = 0
            return self.current

        self.pos = -1
        return None

    def prev(self, elapsed_ms: int = 0) -> PlayableTrack | None:
        if not self.order:
            return None
        if self.pos >= 0 and elapsed_ms > self.RESTART_THRESHOLD_MS:
            return self.current
        if self.repeat == RepeatMode.ONE and self.pos >= 0:
            return self.current

        while self.history:
            prev_track = self.history.pop()
            prev_pos = self._find_order_pos(prev_track)
            if prev_pos >= 0:
                self.pos = prev_pos
                return self.current

        if self.pos < 0:
            self.pos = len(self.order) - 1
            return self.current
        if self.pos > 0:
            self.pos -= 1
            return self.current
        if self.repeat == RepeatMode.ALL:
            self.pos = len(self.order) - 1
            return self.current
        self.pos = 0
        return self.current

    def remove(self, queue_pos: int) -> PlayableTrack | None:
        if not (0 <= queue_pos < len(self.order)):
            return self.current

        was_current = queue_pos == self.pos
        was_before = 0 <= queue_pos < self.pos
        cur_before = self.current

        removed_track_idx = self.order.pop(queue_pos)
        removed_track = self.tracks.pop(removed_track_idx)
        self.order = [
            (idx - 1 if idx > removed_track_idx else idx)
            for idx in self.order
        ]
        self.history = [h for h in self.history if h is not removed_track]

        if not self.order:
            self.pos = -1
            return None

        if was_current:
            if queue_pos < len(self.order):
                self.pos = queue_pos
                return self.current
            if self.repeat == RepeatMode.ALL:
                self.pos = 0
                return self.current
            self.pos = -1
            return None

        if cur_before is not None:
            self.pos = self._find_order_pos(cur_before)
        elif was_before and self.pos > 0:
            self.pos -= 1
        return self.current

    def move(self, old_pos: int, new_pos: int) -> PlayableTrack | None:
        if not (0 <= old_pos < len(self.order)):
            return self.current
        cur = self.current
        item = self.order.pop(old_pos)
        target = max(0, min(new_pos, len(self.order)))
        self.order.insert(target, item)
        if cur is not None:
            self.pos = self._find_order_pos(cur)
        return self.current


def build_playable_track(path: str, track: Track | None = None) -> PlayableTrack | None:
    """Construye un ``PlayableTrack`` leyendo metadatos y stream reales del archivo."""
    if not path or not os.path.isfile(path):
        return None
    abs_path = os.path.abspath(path)
    ext = os.path.splitext(abs_path)[1].lstrip(".").lower()

    info: dict = {}
    try:
        from engine import audio_metadata
        reader = getattr(audio_metadata, "read_stream_info", None)
        if callable(reader):
            info = reader(abs_path) or {}
    except Exception:
        info = {}

    if track is None:
        from engine.parsers import build_local_tracks
        built = build_local_tracks([abs_path])
        if built:
            track = built[0]
        else:
            stem = os.path.splitext(os.path.basename(abs_path))[0]
            track = Track(
                id=abs_path,
                name=stem,
                artist="Artista Desconocido",
                album="Archivo Local",
                duration="0:00",
                img_url="",
                platform="local",
                source_path=abs_path,
            )

    container = str(info.get("container") or ext or "audio").lower()
    codec = str(info.get("codec") or container).lower()
    duration_ms = int(info.get("duration_ms") or track.duration_ms or 0)
    bitrate_kbps = info.get("bitrate_kbps")
    sample_rate = info.get("sample_rate")
    return PlayableTrack(
        track=track,
        path=abs_path,
        container=container,
        codec=codec,
        duration_ms=duration_ms,
        bitrate_kbps=int(bitrate_kbps) if bitrate_kbps else None,
        sample_rate=int(sample_rate) if sample_rate else None,
    )
