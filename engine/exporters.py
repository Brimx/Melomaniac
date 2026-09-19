"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.x.x                                 ║
║              Exportadores de Playlists Locales                       ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: engine/exporters.py
Descripción: Espejo de engine/parsers.py para exportación.
              Convierte list[Track] → archivo local (TXT/CSV/M3U/XSPF)
              usando pathlib + FilePicker.save_file. Respeta `order`
              ("artist-title" vs "title-artist") para round-trip con
              TuneMyMusic y con el parser propio.

Formatos:
 - TXT: una línea por track "Artista - Título" o "Título - Artista"
 - CSV: headers Artist,Title,Album,Duration,ISRC (orden de cols según `order`)
 - M3U8: #EXTM3U + #EXTINF + nombre archivo saneado
 - XSPF: XML con <title>/<creator>/<album>

Autor: Melomaniac Team
Versión: 4.x.x
"""

from __future__ import annotations

import csv
import io
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from core.models import Track

ExportFormat = Literal["txt", "csv", "m3u", "m3u8", "xspf"]
Order = Literal["artist-title", "title-artist"]

_DEFAULT_EXPORT_DIR = Path.home() / "Documentos" / "Melomaniac" / "Exports"

# Fallback si ~/Documentos no existe (portable)
_FALLBACK_EXPORT_DIR = Path.cwd() / "exports"


def _resolve_export_dir(preferred: Path | str | None = None) -> Path:
    if preferred:
        p = Path(preferred).expanduser()
        if p.suffix:
            # es archivo, usa su parent
            return p.parent
        return p
    # intenta default, si no existe usa fallback
    if _DEFAULT_EXPORT_DIR.parent.exists():
        return _DEFAULT_EXPORT_DIR
    return _FALLBACK_EXPORT_DIR


def _sane_filename(name: str) -> str:
    s = "".join(c if c not in r'\/:*?"<>|' else "_" for c in name.strip())
    s = " ".join(s.split())
    return s[:120] if len(s) > 120 else s or "Playlist"


def _line_for_track(t: Track, order: Order) -> str:
    artist = (t.artist or "").strip()
    title = (t.name or "").strip()
    # NFC para no romper diacríticos al escribir
    artist = unicodedata.normalize("NFC", artist)
    title = unicodedata.normalize("NFC", title)
    if order == "artist-title":
        if artist and title:
            return f"{artist} - {title}"
        return title or artist
    else:
        if artist and title:
            return f"{title} - {artist}"
        return title or artist


def _tracks_to_txt(tracks: list[Track], order: Order) -> str:
    return "\n".join(_line_for_track(t, order) for t in tracks) + ("\n" if tracks else "")


def _tracks_to_csv(tracks: list[Track], order: Order) -> str:
    buf = io.StringIO()
    # headers según order para que re-import con mismo order haga round-trip
    if order == "artist-title":
        headers = ["Artist", "Title", "Album", "Duration", "ISRC"]
    else:
        headers = ["Title", "Artist", "Album", "Duration", "ISRC"]
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(headers)
    for t in tracks:
        artist = unicodedata.normalize("NFC", (t.artist or "").strip())
        title = unicodedata.normalize("NFC", (t.name or "").strip())
        row = [
            artist, title, (t.album or "").strip(), (t.duration or "").strip(), (t.isrc or "").strip()
        ] if order == "artist-title" else [
            title, artist, (t.album or "").strip(), (t.duration or "").strip(), (t.isrc or "").strip()
        ]
        w.writerow(row)
    return buf.getvalue()


def _tracks_to_m3u(tracks: list[Track], order: Order) -> str:
    lines = ["#EXTM3U"]
    for t in tracks:
        dur_s = t.duration_ms // 1000 if getattr(t, "duration_ms", 0) else -1
        info = _line_for_track(t, order)
        # sane basename for M3U file entry (no real path, just display)
        lines.append(f"#EXTINF:{dur_s},{info}")
        # si tiene source_path real, usa su basename, si no usa título
        base = Path(t.source_path).name if getattr(t, "source_path", "") else f"{_sane_filename(info)}.mp3"
        lines.append(base)
    return "\n".join(lines) + ("\n" if tracks else "")


def _tracks_to_xspf(tracks: list[Track], playlist_name: str) -> str:
    NS = "http://xspf.org/ns/0/"
    ET.register_namespace("", NS)
    playlist = ET.Element(f"{{{NS}}}playlist", version="1", xmlns=NS)
    title_el = ET.SubElement(playlist, "title")
    title_el.text = playlist_name or "Playlist"
    tracklist = ET.SubElement(playlist, "trackList")
    for t in tracks:
        tr = ET.SubElement(tracklist, "track")
        ti = ET.SubElement(tr, "title")
        ti.text = unicodedata.normalize("NFC", (t.name or "").strip())
        cr = ET.SubElement(tr, "creator")
        cr.text = unicodedata.normalize("NFC", (t.artist or "").strip())
        al = ET.SubElement(tr, "album")
        al.text = unicodedata.normalize("NFC", (t.album or "").strip())
        if getattr(t, "duration_ms", 0):
            du = ET.SubElement(tr, "duration")
            du.text = str(t.duration_ms)
        if getattr(t, "isrc", None):
            # extensión propia para conservar ISRC
            ext = ET.SubElement(tr, "extension", application="https://melomaniac/isrc")
            isrc_el = ET.SubElement(ext, "isrc")
            isrc_el.text = t.isrc
    # pretty
    ET.indent(playlist, space="  ")
    return ET.tostring(playlist, encoding="unicode", xml_declaration=True)


def export_tracks(
    tracks: list[Track],
    path: Path | str,
    fmt: ExportFormat = "txt",
    order: Order = "artist-title",
    playlist_name: str = "",
) -> Path:
    """
    Escribe `tracks` en `path` según `fmt` y `order`.

    Args:
        tracks: lista ya filtrada/ordenada (ej: selected_in_scope).
        path: archivo destino (Path o str). Se crean parents con pathlib.
        fmt: txt/csv/m3u/m3u8/xspf (alias: m3u==m3u8).
        order: "artist-title" (Artista - Título, default TuneMyMusic) o "title-artist".
        playlist_name: solo para XSPF title.

    Returns:
        Path resuelto escrito.
    """
    p = Path(path).expanduser()
    # si `path` es directorio, añade filename saneado
    if p.exists() and p.is_dir() or (not p.suffix and not p.name.count(".")):
        # heurística: sin extensión y sin ser archivo existente → directorio
        # solo si el caller pasó dir; si tiene nombre sin ext lo tratamos como dir
        # para no romper exports que pasan carpeta.
        # Mejor: si p.suffix == "" y len(tracks) >0 y not p.is_file():
        # usamos _resolve_export_dir implícita no aquí, el caller ya resuelve.
        pass
    # normaliza fmt
    fmt = fmt.lower().strip().lstrip(".")  # type: ignore
    if fmt == "m3u":
        fmt = "m3u8"  # type: ignore
    if fmt == "txt":
        content = _tracks_to_txt(tracks, order)
    elif fmt == "csv":
        content = _tracks_to_csv(tracks, order)
    elif fmt in ("m3u8",):
        content = _tracks_to_m3u(tracks, order)
    elif fmt == "xspf":
        content = _tracks_to_xspf(tracks, playlist_name or p.stem)
    else:
        raise ValueError(f"Formato no soportado: {fmt}")

    p.parent.mkdir(parents=True, exist_ok=True)
    # escribe UTF-8 NFC
    p.write_text(content, encoding="utf-8")
    return p


def default_export_path(playlist_name: str, fmt: ExportFormat = "txt", directory: Path | str | None = None) -> Path:
    """
    Construye Path por defecto `~/Documentos/Melomaniac/Exports/<name>.<ext>`.
    Usa pathlib y fallback a ./exports si Documents no existe.
    """
    fmt = fmt.lower().strip().lstrip(".")  # type: ignore
    ext = "m3u8" if fmt == "m3u" else fmt
    base = _resolve_export_dir(directory)
    fname = f"{_sane_filename(playlist_name)}.{ext}"
    return base / fname
