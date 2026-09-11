"""Lectura y escritura tolerante de metadatos de archivos de audio.

Mutagen se importa de forma diferida para que la aplicación siga funcionando
con playlists de texto aunque la dependencia no esté disponible.
"""

from __future__ import annotations

import os
from typing import Any


def _text(value: Any) -> str:
    """Convierte valores de tags de Mutagen a texto utilizable."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if hasattr(value, "text"):
        value = value.text
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value or "").strip()


def _tag(tags: Any, *keys: str) -> str:
    if not tags:
        return ""
    for key in keys:
        try:
            if key in tags:
                value = _text(tags[key])
                if value:
                    return value
        except (KeyError, TypeError):
            continue
    return ""


def _normalize_isrc(value: str) -> str | None:
    from engine.normalizer import normalize_isrc
    return normalize_isrc(value)


def read_audio_metadata(path: str) -> dict[str, Any]:
    """Lee tags comunes e ISRC (`TSRC`) sin fallar por archivos inválidos."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
    except ImportError:
        return {}

    try:
        audio = MutagenFile(path, easy=False)
    except Exception:  # un MP3 con solo tags no tiene frames MPEG
        audio = None
    if audio is None and os.path.splitext(path)[1].lower() == ".mp3":
        try:
            audio = ID3(path)
        except Exception:
            return {}
    if audio is None:
        return {}

    tags = audio if isinstance(audio, ID3) else getattr(audio, "tags", None)
    result: dict[str, Any] = {}
    values = {
        "title": ("TIT2", "©nam", "TITLE", "title"),
        "artist": ("TPE1", "©ART", "ARTIST", "artist"),
        "album": ("TALB", "©alb", "ALBUM", "album"),
        "album_artist": ("TPE2", "aART", "ALBUMARTIST", "albumartist"),
        "date": ("TDRC", "©day", "DATE", "date"),
        "track_number": ("TRCK", "trkn", "TRACKNUMBER", "tracknumber"),
        "isrc": ("TSRC", "ISRC", "isrc", "----:com.apple.iTunes:ISRC"),
    }
    for field, keys in values.items():
        value = _tag(tags, *keys)
        if field == "isrc":
            value = _normalize_isrc(value)
        if value:
            result[field] = value

    length = getattr(getattr(audio, "info", None), "length", 0) or 0
    if length:
        result["duration_ms"] = int(length * 1000)
    return result


def write_audio_metadata(
    path: str,
    metadata: dict[str, Any],
    artwork: bytes | None = None,
) -> bool:
    """Actualiza tags disponibles; devuelve False para formatos no soportados."""
    if not path or not os.path.isfile(path) or not metadata:
        return False
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK, TSRC
        from mutagen.mp4 import MP4, MP4Cover
        from mutagen.flac import Picture
    except ImportError:
        return False

    try:
        try:
            audio = MutagenFile(path, easy=False)
        except Exception:
            audio = None
        if audio is None and os.path.splitext(path)[1].lower() == ".mp3":
            audio = ID3(path)
        if audio is None:
            return False
        is_id3_file = isinstance(audio, ID3)
        if not is_id3_file and getattr(audio, "tags", None) is None:
            audio.add_tags()
        tags = audio if is_id3_file else audio.tags
        values = {
            "title": "title", "artist": "artist", "album": "album",
            "album_artist": "album_artist", "date": "date",
            "track_number": "track_number", "isrc": "isrc",
        }

        if isinstance(tags, ID3):
            frame_types = {
                "title": TIT2, "artist": TPE1, "album": TALB,
                "album_artist": TPE2, "date": TDRC, "track_number": TRCK,
                "isrc": TSRC,
            }
            for field, frame_type in frame_types.items():
                value = metadata.get(values[field])
                if value:
                    if field == "isrc":
                        value = _normalize_isrc(str(value))
                    if value:
                        tags.setall(frame_type.__name__, [frame_type(encoding=3, text=[str(value)])])
            if artwork:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=artwork))
        elif isinstance(audio, MP4):
            mp4_values = {
                "title": "©nam", "artist": "©ART", "album": "©alb",
                "album_artist": "aART", "date": "©day",
                "isrc": "----:com.apple.iTunes:ISRC",
            }
            for field, key in mp4_values.items():
                value = metadata.get(values[field])
                if value:
                    if field == "isrc":
                        value = _normalize_isrc(str(value))
                    if value:
                        tags[key] = [str(value)] if field != "isrc" else [str(value).encode()]
            track_number = metadata.get("track_number")
            if track_number:
                try:
                    tags["trkn"] = [(int(str(track_number).split("/", 1)[0]), 0)]
                except (TypeError, ValueError):
                    pass
            if artwork:
                tags["covr"] = [MP4Cover(artwork, imageformat=MP4Cover.FORMAT_JPEG)]
        else:
            # FLAC, OggVorbis y OggOpus usan tags textuales. FLAC además
            # permite guardar la portada como Picture.
            generic_keys = {
                "title": "title", "artist": "artist", "album": "album",
                "album_artist": "albumartist", "date": "date",
                "track_number": "tracknumber", "isrc": "isrc",
            }
            for field, key in generic_keys.items():
                value = metadata.get(values[field])
                if value:
                    if field == "isrc":
                        value = _normalize_isrc(str(value))
                    if value:
                        tags[key] = [str(value)]
            if artwork and hasattr(audio, "add_picture"):
                picture = Picture()
                picture.type = 3
                picture.mime = "image/jpeg"
                picture.data = artwork
                audio.clear_pictures()
                audio.add_picture(picture)
        audio.save()
        return True
    except Exception:  # la metadata no debe detener una transferencia
        return False


def write_track_metadata(track: Any, path: str | None = None, artwork: bytes | None = None) -> bool:
    """Adaptador para escribir los campos de un `Track` en un archivo."""
    target = path or getattr(track, "source_path", "")
    if not target:
        return False
    return write_audio_metadata(target, {
        "title": getattr(track, "name", ""),
        "artist": getattr(track, "artist", ""),
        "album": getattr(track, "album", ""),
        "album_artist": getattr(track, "album_artist", ""),
        "track_number": getattr(track, "track_number", ""),
        "date": getattr(track, "release_date", ""),
        "isrc": getattr(track, "isrc", ""),
    }, artwork=artwork)
