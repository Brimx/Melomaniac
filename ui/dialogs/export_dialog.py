"""ui/dialogs/export_dialog.py — Diálogo de exportación local o añadir a playlist."""

from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft

from core.config import EXPORT_FORMATS
from core.models import Track
from core.state import AppState
from engine.exporters import default_export_path, export_tracks
from ui.components.widgets import (
    _ghost_btn,
    app_dialog,
    app_text_field,
    dialog_action,
    input_border,
    organize_dropdown,
    safe_update,
)
from ui.dialogs.transfer_helpers import (
    add_tracks_to_existing_destination,
    get_synced_playlists_for,
    make_preflight_status_row,
    resolve_tracks_for_destination,
)
from ui.theme.fonts import FONT_HEADLINE, FONT_TEXT, mono_family
from ui.theme.icons import PIcons
from ui.theme.tokens import (
    ACCENT,
    BG_INPUT,
    BG_SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


async def open_export_dialog(
    host,
    *,
    initial_mode: str = "file",
    custom_tracks: list[Track] | None = None,
) -> None:
    """Diálogo de exportación local o inserción en una playlist destino (2.4/2.5)."""
    if custom_tracks is not None:
        src_tracks = list(custom_tracks)
    else:
        if not host.state.tracks:
            host._snack("No hay playlist cargada para exportar", error=True)
            return
        src_tracks = [t for t in host.state.tracks if t.selected]
        if not src_tracks:
            src_tracks = (
                list(host.state.display_tracks)
                if hasattr(host.state, "display_tracks")
                else list(host.state.tracks)
            )
    if not src_tracks:
        host._snack("No hay canciones para exportar", error=True)
        return

    mode_state = {"mode": "playlist" if initial_mode == "playlist" else "file"}
    fmt_val = getattr(host.state, "local_export_format", "txt")
    order_val = getattr(host.state, "local_export_order", "artist-title")

    def _on_fmt_change(e) -> None:
        host.state.local_export_format = e.control.value

    def _on_order_change_exp(e) -> None:
        try:
            val = (
                list(e.control.selected)[0]
                if hasattr(e.control, "selected") and e.control.selected
                else e.control.value
            )
        except Exception:
            val = getattr(e.control, "value", "artist-title")
        if val in ("artist-title", "title-artist"):
            host.state.local_export_order = val

    fmt_dd = ft.Dropdown(
        label="Formato",
        value=fmt_val,
        width=160,
        height=38,
        bgcolor=BG_INPUT,
        border=input_border(),
        text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
        content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
        options=[ft.dropdown.Option(k, k.upper()) for k in EXPORT_FORMATS],
        on_select=_on_fmt_change,
    )
    order_seg = host._make_order_segmented(order_val, on_change=_on_order_change_exp)

    default_path = str(default_export_path(host.state.playlist_name, fmt_val))
    path_field = app_text_field(
        value=default_path,
        label="Ruta destino",
        hint_text=str(default_path),
        prefix_icon=PIcons.FOLDER,
        expand=True,
    )

    async def _pick_save(_e) -> None:
        try:
            res = await host._save_picker.save_file(
                dialog_title="Guardar playlist",
                file_name=path_field.value.split("/")[-1].split("\\")[-1] or "playlist.txt",
                allowed_extensions=list(EXPORT_FORMATS),
            )
            if res and getattr(res, "path", None):
                path_field.value = res.path
                safe_update(path_field)
            elif isinstance(res, str) and res:
                path_field.value = res
                safe_update(path_field)
        except Exception:
            pass

    pick_btn = _ghost_btn(
        "Elegir…",
        PIcons.FOLDER_OPEN,
        lambda e: host.page.run_task(_pick_save, e),
        width=90,
        height=38,
    )
    file_box = ft.Column(
        [
            ft.Text(
                "Guarda la selección actual con el mismo bloque que lee TXT/CSV/M3U/XSPF.",
                size=10,
                color=TEXT_MUTED,
                font_family=FONT_TEXT,
            ),
            ft.Row(
                [fmt_dd, ft.Container(expand=True), order_seg],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Row(
                [path_field, pick_btn],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ],
        spacing=10,
        tight=True,
        visible=(mode_state["mode"] == "file"),
    )

    # Modo «Añadir a playlist»
    default_plat = (
        host.state.destination
        if host.state.destination in AppState.PLATFORMS
        else AppState.PLATFORMS[0]
    )
    target_playlist_dd = organize_dropdown(
        [],
        "",
        "Playlist destino",
        width=480,
    )
    target_help = ft.Text(
        "",
        size=10,
        color=TEXT_MUTED,
        font_family=FONT_TEXT,
        visible=False,
    )
    new_name_field = app_text_field(
        value=host.state.playlist_name or "Playlist Exportada",
        label="O crear nueva playlist",
        hint_text="Nombre si no eliges una existente",
        visible=False,
    )

    primary_btn = dialog_action("Exportar", lambda _: None, kind="primary", icon=PIcons.EXPORT)

    def _on_pf(ok: bool | None) -> None:
        if mode_state["mode"] == "playlist":
            primary_btn.disabled = ok is False
            safe_update(primary_btn)

    pf_banner, pf_state, run_pf = make_preflight_status_row(host, on_status_change=_on_pf)

    def _fill_target_playlists(plat: str) -> None:
        items = get_synced_playlists_for(host, plat)
        target_playlist_dd.options = [
            ft.dropdown.Option(key=str(p.id), text=f"{p.name or p.id} ({p.track_count})")
            for p in items
        ]
        target_playlist_dd.value = str(items[0].id) if items else ""
        target_help.visible = not items
        new_name_field.visible = not items
        if not items:
            target_help.value = (
                f"No hay playlists sincronizadas de {plat}: introduce un nombre para crearla."
            )

    def _on_plat_select(e) -> None:
        plat = str(e.control.value or default_plat)
        _fill_target_playlists(plat)
        safe_update(playlist_box)
        asyncio.create_task(run_pf(plat))

    target_plat_dd = organize_dropdown(
        [(p, p) for p in AppState.PLATFORMS],
        default_plat,
        "Plataforma destino",
        width=480,
        on_select=_on_plat_select,
    )
    _fill_target_playlists(default_plat)

    playlist_box = ft.Column(
        [
            target_plat_dd,
            pf_banner,
            target_playlist_dd,
            target_help,
            new_name_field,
        ],
        spacing=8,
        tight=True,
        visible=(mode_state["mode"] == "playlist"),
    )

    def _sync_exp_mode(new_mode: str) -> None:
        mode_state["mode"] = new_mode
        file_box.visible = new_mode == "file"
        playlist_box.visible = new_mode == "playlist"
        if new_mode == "playlist":
            primary_btn.disabled = pf_state["ok"] is False
            if pf_state["dest"] != str(target_plat_dd.value or default_plat):
                asyncio.create_task(run_pf(str(target_plat_dd.value or default_plat)))
        else:
            primary_btn.disabled = False
        safe_update(file_box)
        safe_update(playlist_box)
        safe_update(primary_btn)

    def _on_exp_mode_change(e) -> None:
        try:
            val = (
                list(e.control.selected)[0]
                if hasattr(e.control, "selected") and e.control.selected
                else e.control.value
            )
        except Exception:
            val = getattr(e.control, "value", "file")
        if val in ("file", "playlist"):
            _sync_exp_mode(val)

    try:
        exp_mode_seg: ft.Control = ft.SegmentedButton(
            selected=[mode_state["mode"]],
            allow_empty_selection=False,
            allow_multiple_selection=False,
            show_selected_icon=False,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
            ),
            segments=[
                ft.Segment(value="file", label=ft.Text("Archivo local", size=11, font_family=FONT_HEADLINE)),
                ft.Segment(value="playlist", label=ft.Text("Añadir a playlist", size=11, font_family=FONT_HEADLINE)),
            ],
            on_change=_on_exp_mode_change,
        )
    except Exception:
        exp_mode_seg = organize_dropdown(
            [("file", "Archivo local"), ("playlist", "Añadir a playlist")],
            mode_state["mode"],
            "Modo",
            width=220,
            on_select=_on_exp_mode_change,
        )

    info_txt = ft.Text(
        f"{len(src_tracks)} canciones · {(host.state.playlist_name or 'Selección')[:30]}",
        size=10,
        color=TEXT_MUTED,
        font_family=mono_family("light"),
    )
    dlg_ref: dict = {}

    async def _submit_export(_e=None) -> None:
        if mode_state["mode"] == "file":
            fmt = getattr(host.state, "local_export_format", "txt")
            order = getattr(host.state, "local_export_order", "artist-title")
            path = (path_field.value or "").strip() or str(
                default_export_path(host.state.playlist_name, fmt)
            )
            p = Path(path).expanduser()
            if not p.suffix:
                p = Path(str(p) + f".{fmt.replace('m3u', 'm3u8')}")
            try:
                out = export_tracks(
                    src_tracks,
                    p,
                    fmt=fmt,
                    order=order,
                    playlist_name=host.state.playlist_name,
                )
                host._close_dlg(dlg_ref["dlg"])
                host._snack(f"Exportado {len(src_tracks)} canciones → {out}")
                host.state.log(f"[INFO] Export local · {len(src_tracks)} → {out} ({fmt}, {order})")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                host._snack(f"Error al exportar: {exc}", error=True)
                host.state.log(f"[ERROR] Export falló: {exc}")
            return

        dest = str(target_plat_dd.value or default_plat)
        ok = await run_pf(dest) if pf_state["ok"] is None else bool(pf_state["ok"])
        if not ok:
            host._snack(f"Sesión de {dest} no válida", error=True)
            return
        pid = str(target_playlist_dd.value or "").strip()
        if pid:
            items = get_synced_playlists_for(host, dest)
            match = next((p for p in items if str(p.id) == pid), None)
            pname = str(getattr(match, "name", "") or pid)
            host._close_dlg(dlg_ref["dlg"])
            await add_tracks_to_existing_destination(host, dest, pid, pname, src_tracks)
            return

        new_title = (new_name_field.value or "").strip()
        if not new_title:
            host._snack("Indica una playlist destino o un nombre para crearla", error=True)
            return
        host._close_dlg(dlg_ref["dlg"])
        resolved = await resolve_tracks_for_destination(host, dest, src_tracks)
        if not resolved:
            host._snack(f"No se encontraron pistas compatibles en {dest}", error=True)
            return
        ok_create, msg, confirmed, _rej = await host.state.create_remote_playlist(
            dest, new_title, [t.id for t in resolved if t.id], host.state.playlist_description
        )
        if ok_create:
            host._snack(f"Playlist «{new_title}» creada en {dest} ({confirmed} pistas)")
        else:
            host._snack(f"No se pudo crear la playlist: {msg}", error=True)

    primary_btn.on_click = lambda e: asyncio.create_task(_submit_export(e))

    dlg = app_dialog(
        "Exportar / Añadir a playlist",
        ft.Column(
            [exp_mode_seg, info_txt, file_box, playlist_box],
            spacing=10,
            tight=True,
        ),
        [
            dialog_action("Cancelar", lambda _: host._close_dlg(dlg), kind="muted"),
            primary_btn,
        ],
        width=560,
        icon=PIcons.EXPORT,
    )
    dlg_ref["dlg"] = dlg
    host.page.show_dialog(dlg)
    if mode_state["mode"] == "playlist":
        asyncio.create_task(run_pf(default_plat))


__all__ = ["open_export_dialog"]
