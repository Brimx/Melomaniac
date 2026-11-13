"""ui/dialogs/merge_dialog.py — Diálogo de fusión de playlists locales y remotas."""

from __future__ import annotations

import asyncio

import flet as ft

from core.models import LoadState, Track
from core.state import AppState
from engine.parsers import build_local_tracks, parse_local_playlist_with_paths
from services.local_index import scan_folder_async
from ui.components.widgets import (
    _ghost_btn,
    app_dialog,
    app_text_field,
    debounce,
    dialog_action,
    limit_label,
    safe_update,
)
from ui.dialogs.transfer_helpers import get_synced_playlists_for
from ui.theme.fonts import FONT_TEXT, mono_family
from ui.theme.icons import PIcons
from ui.theme.tokens import (
    ACCENT,
    BORDER_LIGHT,
    CHIP_BG,
    DIALOG_OPTION_LIMIT,
    RADIUS_INPUT,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def open_merge_dialog(host) -> None:
    """Diálogo de fusión de playlists locales/remotas con deduplicación (2.2)."""
    sources: list[dict] = []
    if host.state.tracks:
        sources.append(
            {
                "key": "__current__",
                "kind": "current",
                "label": f"Actual: {host.state.playlist_name} ({len(host.state.tracks)})",
                "platform": host.state.source,
                "tracks": list(host.state.tracks),
                "selected": True,
            }
        )
    lib = getattr(host, "library_state", None)
    if lib is not None:
        for plat in AppState.PLATFORMS:
            for p in get_synced_playlists_for(host, plat):
                sources.append(
                    {
                        "key": f"{plat}::{p.id}",
                        "kind": "remote",
                        "label": f"{p.name or p.id} · {plat} ({p.track_count})",
                        "platform": plat,
                        "playlist_id": str(p.id),
                        "selected": False,
                    }
                )
        try:
            local_items = list(lib.local_summaries() or [])
        except Exception:
            local_items = []
        for p in local_items:
            sources.append(
                {
                    "key": f"local::{p.id}",
                    "kind": "local_summary",
                    "label": f"{p.name or p.id} · Local ({p.track_count})",
                    "summary_id": str(p.id),
                    "selected": False,
                }
            )

    name_field = app_text_field(
        value="Playlist Fusionada",
        label="Nombre de la playlist fusionada",
        hint_text="Ej. Mix Verano",
    )
    filter_field = app_text_field(
        hint_text="Filtrar fuentes…",
        prefix_icon=PIcons.MAGNIFYING_GLASS,
        height=36,
    )
    limit_txt = ft.Text("", size=10, color=TEXT_DIM, font_family=mono_family("light"))
    lv = ft.ListView(spacing=4, height=230)
    dedup_chk = ft.Checkbox(
        label="Omitir canciones duplicadas (ISRC, ID o título y artista)",
        value=True,
        fill_color={ft.ControlState.SELECTED: ACCENT},
        check_color=TEXT_PRIMARY,
        label_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
    )

    def _rebuild_list(query: str = "") -> None:
        q = (query or "").strip().lower()
        matched = [it for it in sources if not q or q in it["label"].lower()]
        shown = matched[:DIALOG_OPTION_LIMIT]
        limit_txt.value = limit_label(len(shown), len(matched))
        lv.controls = [
            ft.Checkbox(
                label=it["label"],
                value=bool(it["selected"]),
                fill_color={ft.ControlState.SELECTED: ACCENT},
                check_color=TEXT_PRIMARY,
                label_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
                on_change=lambda e, item=it: item.__setitem__("selected", bool(e.control.value)),
            )
            for it in shown
        ]
        if not shown:
            lv.controls = [
                ft.Text(
                    "Sin fuentes sincronizadas. Añade un archivo local o sincroniza Biblioteca.",
                    size=11,
                    color=TEXT_MUTED,
                    font_family=FONT_TEXT,
                )
            ]
        safe_update(limit_txt)
        safe_update(lv)

    filter_field.on_change = lambda e: debounce(
        ("merge_filter", id(host)),
        lambda: _rebuild_list(e.control.value or ""),
    )

    async def _add_local_file(_e) -> None:
        try:
            files = await host._file_picker.pick_files(
                dialog_title="Añadir playlist local a la fusión",
                allowed_extensions=["txt", "csv", "m3u", "m3u8", "pls", "wpl", "xspf", "xml"],
            )
        except Exception:
            files = None
        if not files:
            return
        f = files[0]
        try:
            with open(f.path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            pairs = parse_local_playlist_with_paths(
                text, filename=f.path, order=host.state.local_parse_order
            )
            tracks = build_local_tracks(pairs)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            host._snack(f"No se pudo leer '{f.name}': {exc}", error=True)
            return
        if not tracks:
            host._snack(f"'{f.name}' no contiene pistas reconocibles", error=True)
            return
        sources.append(
            {
                "key": f"file::{f.path}",
                "kind": "current",
                "label": f"Archivo: {f.name} ({len(tracks)})",
                "tracks": tracks,
                "selected": True,
            }
        )
        _rebuild_list(filter_field.value or "")

    dlg_ref: dict = {}

    async def _execute_merge(_e=None) -> None:
        picked = [it for it in sources if it.get("selected")]
        if len(picked) < 2:
            host._snack("Selecciona al menos 2 fuentes para fusionar", error=True)
            return
        final_name = (name_field.value or "").strip() or "Playlist Fusionada"
        deduplicate = bool(dedup_chk.value)
        host._close_dlg(dlg_ref["dlg"])
        prev_load_state = host.state.load_state
        host.state.load_state = LoadState.LOADING_TRACKS
        host.state.notify()
        resolved_lists: list[list[Track]] = []
        service = getattr(host.state, "service", None)
        try:
            for it in picked:
                kind = it["kind"]
                if kind == "current":
                    resolved_lists.append(list(it["tracks"]))
                elif kind == "remote":
                    if service is None or not hasattr(service, "fetch_playlist"):
                        continue
                    try:
                        _meta, tracks = await service.fetch_playlist(
                            it["platform"], it["playlist_id"], None
                        )
                        if tracks:
                            resolved_lists.append(list(tracks))
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        host.state.log(f"[WARN] Error cargando '{it['label']}': {exc}")
                elif kind == "local_summary" and lib is not None:
                    sid = it["summary_id"]
                    try:
                        manifest = await asyncio.to_thread(lib.load_local_manifest, sid)
                        if manifest is not None and getattr(manifest, "tracks", None):
                            resolved_lists.append(list(manifest.tracks))
                            continue
                        folder_tracks = await scan_folder_async(sid)
                        if folder_tracks:
                            resolved_lists.append(list(folder_tracks))
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        host.state.log(f"[WARN] Error leyendo local '{it['label']}': {exc}")

            if len(resolved_lists) < 2:
                host.state.load_state = prev_load_state
                host.state.notify()
                host._snack("No se pudieron cargar al menos 2 fuentes con pistas", error=True)
                return
            raw_total = sum(len(lst) for lst in resolved_lists)
            merged = host.state.merge_sources(
                resolved_lists,
                name=final_name,
                deduplicate=deduplicate,
            )
            msg = (
                f"Fusionadas {len(merged)} pistas únicas (de {raw_total})"
                if deduplicate
                else f"Fusionadas {len(merged)} pistas (incluyendo duplicadas)"
            )
            host._snack(msg)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            host.state.load_state = prev_load_state
            host.state.notify()
            host._snack(f"Error al fusionar: {exc}", error=True)

    _rebuild_list("")

    dlg = app_dialog(
        "Fusionar playlists",
        ft.Column(
            [
                name_field,
                ft.Row(
                    [
                        filter_field,
                        _ghost_btn(
                            "+ Archivo",
                            PIcons.FOLDER_OPEN,
                            lambda e: asyncio.create_task(_add_local_file(e)),
                            height=36,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                limit_txt,
                ft.Container(
                    content=lv,
                    bgcolor=CHIP_BG,
                    border=ft.Border.all(0.8, BORDER_LIGHT),
                    border_radius=RADIUS_INPUT,
                    padding=ft.Padding.all(8),
                ),
                dedup_chk,
            ],
            spacing=10,
            tight=True,
        ),
        [
            dialog_action("Cancelar", lambda _: host._close_dlg(dlg), kind="muted"),
            dialog_action(
                "Fusionar",
                lambda e: asyncio.create_task(_execute_merge(e)),
                kind="primary",
                icon=PIcons.QUEUE,
            ),
        ],
        width=540,
        icon=PIcons.QUEUE,
    )
    dlg_ref["dlg"] = dlg
    host.page.show_dialog(dlg)


__all__ = ["open_merge_dialog"]
