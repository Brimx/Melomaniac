"""
services/mpris_service.py — Exposición MPRIS (D-Bus) de Melomaniac en Linux.

Publica ``org.mpris.MediaPlayer2.melomaniac`` en el bus de sesión de D-Bus para
que el escritorio (playerctl, Caelestia/Quickshell, Waybar, KDE, GNOME, teclas
multimedia) detecte y controle Melomaniac.

Usa ``dbus-next`` sobre el loop asyncio nativo de Flet. En plataformas sin D-Bus
(como Windows) o si falta la dependencia, opera de manera segura como un no-op.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import sys
from enum import Enum
from typing import Callable
from urllib.parse import quote

_HAS_DBUS = False
if sys.platform.startswith("linux"):
    try:
        from dbus_next import Variant
        from dbus_next.aio import MessageBus
        from dbus_next.constants import PropertyAccess
        from dbus_next.service import ServiceInterface, dbus_property, method, signal
        _HAS_DBUS = True
    except ImportError:  # pragma: no cover
        pass

logger = logging.getLogger(__name__)

BUS_NAME = "org.mpris.MediaPlayer2.melomaniac"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"


class PlaybackStatus(Enum):
    PLAYING = "Playing"
    PAUSED = "Paused"
    STOPPED = "Stopped"


class LoopStatus(Enum):
    NONE = "None"
    TRACK = "Track"
    PLAYLIST = "Playlist"


def _format_track_uri(path_or_url: str) -> str:
    """Convierte una ruta local o URL de streaming en una URI válida (file:// o http://)."""
    if not path_or_url:
        return ""
    if path_or_url.startswith(("http://", "https://", "file://")):
        return path_or_url
    if os.path.isabs(path_or_url) or path_or_url.startswith("/"):
        return f"file://{quote(os.path.abspath(path_or_url))}"
    return ""


def _format_art_uri(img_url: str) -> str:
    """Convierte portada (data URI, HTTP o archivo local) en una URI estándar compatible con MPRIS."""
    if not img_url:
        return ""

    # 1. Caso data URI (portadas incrustadas en archivos locales vía Mutagen)
    if img_url.startswith("data:image/"):
        try:
            header, b64_data = img_url.split(",", 1)
            ext = ".jpg"
            if "image/png" in header:
                ext = ".png"
            elif "image/webp" in header:
                ext = ".webp"
            raw_bytes = base64.b64decode(b64_data)
            h = hashlib.sha1(raw_bytes).hexdigest()[:20]

            from services.cover_cache import CACHE_DIR
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            target = CACHE_DIR / f"embedded_{h}{ext}"
            if not target.exists():
                tmp = target.with_suffix(".tmp")
                tmp.write_bytes(raw_bytes)
                os.replace(tmp, target)
            return f"file://{os.path.abspath(target)}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error procesando data URI para MPRIS: %s", exc)
            return ""

    # 2. Caso archivo local existente
    if img_url.startswith("file://"):
        return img_url
    if os.path.exists(img_url):
        return f"file://{os.path.abspath(img_url)}"

    # 3. Caso HTTP / HTTPS (streaming o API remota)
    if img_url.startswith(("http://", "https://")):
        try:
            from services.cover_cache import cached_path_for_url
            cached = cached_path_for_url(img_url)
            if cached and cached.exists():
                return f"file://{os.path.abspath(cached)}"
        except Exception:  # noqa: BLE001
            pass
        return img_url

    # 4. Fallback si es ruta absoluta
    if os.path.isabs(img_url) or img_url.startswith("/"):
        return f"file://{os.path.abspath(img_url)}"

    return ""


def _track_path(key: str) -> str:
    """Genera un D-Bus ObjectPath válido para el identificador de pista."""
    return f"/org/melomaniac/track/{abs(hash(key)) % (10 ** 12)}"


if _HAS_DBUS:

    class _RootInterface(ServiceInterface):
        def __init__(self, svc: "MPRISService") -> "":
            super().__init__("org.mpris.MediaPlayer2")
            self._svc = svc

        @method()
        def Raise(self) -> "":
            self._svc._call("raise")

        @method()
        def Quit(self) -> "":
            self._svc._call("quit")

        @dbus_property(access=PropertyAccess.READ)
        def CanQuit(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanRaise(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def HasTrackList(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def Identity(self) -> "s":
            return "Melomaniac"

        @dbus_property(access=PropertyAccess.READ)
        def DesktopEntry(self) -> "s":
            return "melomaniac"

        @dbus_property(access=PropertyAccess.READ)
        def SupportedUriSchemes(self) -> "as":
            return ["file", "http", "https"]

        @dbus_property(access=PropertyAccess.READ)
        def SupportedMimeTypes(self) -> "as":
            return ["audio/mpeg", "audio/flac", "audio/ogg", "audio/mp4", "audio/x-wav"]

    class _PlayerInterface(ServiceInterface):
        def __init__(self, svc: "MPRISService") -> "":
            super().__init__("org.mpris.MediaPlayer2.Player")
            self._svc = svc

        @method()
        def Next(self) -> "":
            self._svc._call("next")

        @method()
        def Previous(self) -> "":
            self._svc._call("previous")

        @method()
        def Pause(self) -> "":
            self._svc._call("pause")

        @method()
        def PlayPause(self) -> "":
            self._svc._call("play_pause")

        @method()
        def Stop(self) -> "":
            self._svc._call("stop")

        @method()
        def Play(self) -> "":
            self._svc._call("play")

        @method()
        def Seek(self, offset: "x") -> "":
            self._svc._call("seek", offset // 1000)

        @method()
        def SetPosition(self, track_id: "o", position: "x") -> "":
            if track_id == self._svc._track_id:
                self._svc._call("set_position", position // 1000)

        @method()
        def OpenUri(self, uri: "s") -> "":
            self._svc._call("open_uri", uri)

        @signal()
        def Seeked(self) -> "x":
            return self._svc._position_us

        @dbus_property(access=PropertyAccess.READ)
        def PlaybackStatus(self) -> "s":
            return self._svc._status.value

        @dbus_property()
        def LoopStatus(self) -> "s":
            return self._svc._loop.value

        @LoopStatus.setter
        def LoopStatus(self, val: "s") -> "":
            self._svc._call("set_loop", val)

        @dbus_property(access=PropertyAccess.READ)
        def Rate(self) -> "d":
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MinimumRate(self) -> "d":
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MaximumRate(self) -> "d":
            return 1.0

        @dbus_property()
        def Shuffle(self) -> "b":
            return self._svc._shuffle

        @Shuffle.setter
        def Shuffle(self, val: "b") -> "":
            self._svc._call("set_shuffle", bool(val))

        @dbus_property(access=PropertyAccess.READ)
        def Metadata(self) -> "a{sv}":
            return self._svc._metadata()

        @dbus_property()
        def Volume(self) -> "d":
            return self._svc._volume

        @Volume.setter
        def Volume(self, val: "d") -> "":
            self._svc._call("set_volume", float(val))

        @dbus_property(access=PropertyAccess.READ)
        def Position(self) -> "x":
            return self._svc._position_us

        @dbus_property(access=PropertyAccess.READ)
        def CanGoNext(self) -> "b":
            return self._svc._has_track

        @dbus_property(access=PropertyAccess.READ)
        def CanGoPrevious(self) -> "b":
            return self._svc._has_track

        @dbus_property(access=PropertyAccess.READ)
        def CanPlay(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanPause(self) -> "b":
            return self._svc._has_track

        @dbus_property(access=PropertyAccess.READ)
        def CanSeek(self) -> "b":
            return self._svc._has_track

        @dbus_property(access=PropertyAccess.READ)
        def CanControl(self) -> "b":
            return True


class MPRISService:
    """Fachada MPRIS segura frente a caídas y no-op en Windows o sin D-Bus."""

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._root: _RootInterface | None = None
        self._player: _PlayerInterface | None = None
        self._handlers: dict[str, Callable] = {}
        self._status = PlaybackStatus.STOPPED
        self._loop = LoopStatus.NONE
        self._shuffle = False
        self._volume = 1.0
        self._position_us = 0
        self._has_track = False
        self._track: dict | None = None
        self._track_id = NO_TRACK
        self._on_ready: Callable[[], None] | None = None

    @property
    def is_available(self) -> bool:
        return _HAS_DBUS

    # ── ciclo de vida ──────────────────────────────────────────────────
    def set_handlers(self, on_ready: Callable[[], None] | None = None, **handlers: Callable) -> None:
        self._on_ready = on_ready
        self._handlers = {k: v for k, v in handlers.items() if callable(v)}

    async def start(self) -> bool:
        if not _HAS_DBUS:
            logger.info("MPRIS omitido: plataforma no Linux o dbus-next no disponible.")
            return False
        if self._bus is not None:
            return True
        try:
            bus = await MessageBus().connect()
            self._root = _RootInterface(self)
            self._player = _PlayerInterface(self)
            bus.export(OBJECT_PATH, self._root)
            bus.export(OBJECT_PATH, self._player)
            await bus.request_name(BUS_NAME)
            self._bus = bus
            logger.info("MPRIS registrado como %s", BUS_NAME)
            if callable(self._on_ready):
                try:
                    self._on_ready()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("MPRIS on_ready falló: %s", exc)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("MPRIS no disponible: %s", exc)
            return False

    def stop(self) -> None:
        bus, self._bus = self._bus, None
        self._root = None
        self._player = None
        if bus is not None:
            try:
                bus.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _call(self, name: str, *args) -> None:
        fn = self._handlers.get(name)
        if fn is None:
            return
        try:
            res = fn(*args)
            if asyncio.iscoroutine(res):
                asyncio.ensure_future(res)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MPRIS handler %s falló: %s", name, exc)

    # ── estado hacia el escritorio ─────────────────────────────────────
    def _metadata(self) -> dict:
        if not _HAS_DBUS or self._track is None:
            return {"mpris:trackid": Variant("o", NO_TRACK)} if _HAS_DBUS else {}
        t = self._track
        md = {
            "mpris:trackid": Variant("o", self._track_id),
            "xesam:title": Variant("s", t["title"]),
            "xesam:artist": Variant("as", [t["artist"]] if t["artist"] else []),
            "xesam:album": Variant("s", t["album"]),
            "mpris:length": Variant("x", int(t["duration_ms"]) * 1000),
        }
        if t["art"]:
            md["mpris:artUrl"] = Variant("s", t["art"])
        if t["url"]:
            md["xesam:url"] = Variant("s", t["url"])
        return md

    def _emit(self, **props) -> None:
        if self._bus is None or self._player is None:
            return
        try:
            self._player.emit_properties_changed(props)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MPRIS emit falló: %s", exc)

    def set_current_track(
        self,
        title: str,
        artist: str,
        *,
        album: str = "",
        img_url: str = "",
        source_url: str = "",
        duration_ms: int = 0,
    ) -> None:
        art_uri = _format_art_uri(img_url)
        track_uri = _format_track_uri(source_url)
        self._track = {
            "title": title or "",
            "artist": artist or "",
            "album": album or "",
            "art": art_uri,
            "url": track_uri,
            "duration_ms": max(0, int(duration_ms or 0)),
        }
        self._track_id = _track_path(f"{title}|{artist}|{album}")
        self._has_track = True
        self._position_us = 0
        self._emit(
            Metadata=self._metadata(),
            CanGoNext=True,
            CanGoPrevious=True,
            CanPause=True,
            CanSeek=True,
        )
        self.notify_seeked(0)

    def clear_track(self) -> None:
        self._track = None
        self._track_id = NO_TRACK
        self._has_track = False
        self._position_us = 0
        self._emit(
            Metadata=self._metadata(),
            CanGoNext=False,
            CanGoPrevious=False,
            CanPause=False,
            CanSeek=False,
        )

    def set_playback_status(self, status: PlaybackStatus) -> None:
        if status == self._status:
            return
        self._status = status
        self._emit(PlaybackStatus=status.value)

    def set_position(self, position_ms: int) -> None:
        """Actualiza Position sin emitir señal (los clientes la consultan en polling)."""
        self._position_us = max(0, int(position_ms)) * 1000

    def notify_seeked(self, position_ms: int) -> None:
        """Emite la señal Seeked requerida por los widgets al cambiar pista o saltar."""
        self.set_position(position_ms)
        if self._bus is not None and self._player is not None:
            try:
                self._player.Seeked(self._position_us)
            except Exception:  # noqa: BLE001
                pass

    def set_modes(self, *, shuffle: bool, loop: LoopStatus, volume: float) -> None:
        self._shuffle = shuffle
        self._loop = loop
        self._volume = max(0.0, min(1.0, volume))
        self._emit(
            Shuffle=self._shuffle,
            LoopStatus=self._loop.value,
            Volume=self._volume,
        )
