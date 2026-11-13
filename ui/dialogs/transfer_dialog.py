"""ui/dialogs/transfer_dialog.py — Diálogo unificado de transferencia y ejecución."""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import EXPORT_DEST_LABEL
from core.state import AppState
from ui.components.widgets import (
    _section_label,
    app_dialog,
    app_text_field,
    dialog_action,
    organize_dropdown,
    safe_update,
)
from ui.dialogs.transfer_helpers import (
    add_tracks_to_existing_destination,
    get_synced_playlists_for,
    make_preflight_status_row,
)
from ui.theme.fonts import FONT_HEADLINE, FONT_TEXT
from ui.theme.icons import PIcons
from ui.theme.tokens import (
    ACCENT,
    BG_SURFACE,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


async def execute_new_playlist_transfer(
    host,
    *,
    dest: str,
    title: str,
    description: str,
    delete_original: bool,
    has_divisions: bool,
    per_div_dests: dict[str, str] | None = None,
) -> None:
    """Ejecuta la creación de una o varias playlists según divisiones activas."""
    s = host.state
    if has_divisions:
        sel_divs = (
            s.active_segment_keys
            if s.active_segment_keys is not None
            else set(s.segments.keys())
        )
        overrides = per_div_dests or {}
        successes = 0
        seg_keys = list(s.segments.keys())
        for div in sorted(sel_divs, key=lambda k: seg_keys.index(k) if k in s.segments else 9999):
            div_tracks = s.segments.get(div, [])
            div_selected = (
                [t for t in div_tracks if t.selected]
                if any(t.selected for t in div_tracks)
                else div_tracks
            )
            if not div_selected:
                continue
            div_dest = overrides.get(div, "Mantener")
            if not div_dest or div_dest == "Mantener":
                div_dest = dest
            try:
                ok, msg, confirmed, _rejected = await s.create_remote_playlist(
                    div_dest,
                    f"{title} — {div}",
                    [t.id for t in div_selected if t.id],
                    description,
                )
                if ok:
                    successes += 1
                    s.log(f"[SUCCESS] División '{div}' → {div_dest}: {confirmed} canciones")
                else:
                    s.log(f"[ERROR] División '{div}' fallo: {msg}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                s.log(f"[ERROR] División '{div}' excepción: {exc}")
        host._snack(f"{successes}/{len(sel_divs)} divisiones transferidas")
        if overrides:
            s.division_destinations.update(overrides)
    else:
        await s.transfer_playlist(
            title_override=title,
            description_override=description,
        )

    if delete_original and s.playlist_id and s.source not in AppState.LOCAL_SOURCES:
        try:
            ok, msg = await s.delete_remote_playlist(s.source, s.playlist_id)
            if ok:
                host._snack(f"Original eliminada ({s.source})")
                s.log(f"[INFO] Original {s.playlist_id} eliminada tras reemplazo")
            else:
                host._snack(f"No se pudo eliminar original: {msg}", error=True)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            host._snack(f"Error eliminando original: {exc}", error=True)


async def open_transfer_dialog(host, *, has_divisions: bool = False) -> None:
    """Diálogo de ejecución de transferencia: destino, playlist existente o nueva y preflight."""
    s = host.state
    has_transform = bool(
        s.segments
        or getattr(s, "show_dual", False)
        or (getattr(s, "source_tracks", None) and s.tracks != s.source_tracks)
    )
    is_local_or_merged = (
        s.source in AppState.LOCAL_SOURCES
        or str(getattr(s, "playlist_id", "")).startswith(("local_", "merged_"))
    )
    default_dest = (
        s.destination
        if s.destination in (*AppState.PLATFORMS, EXPORT_DEST_LABEL)
        else AppState.PLATFORMS[0]
    )
    if default_dest == s.source and not has_transform and not is_local_or_merged:
        other = [p for p in AppState.PLATFORMS if p != s.source]
        if other:
            default_dest = other[0]

    dest_opts = [*AppState.PLATFORMS, EXPORT_DEST_LABEL]
    mode_state = {"mode": "new"}  # "new" | "existing"
    per_div_override: dict[str, str] = {}

    title_field = app_text_field(
        value=s.playlist_name,
        hint_text="Nombre de la playlist",
        label="Nombre de la playlist",
        autofocus=True,
    )
    desc_field = app_text_field(
        value=s.playlist_description,
        hint_text="Descripción (opcional)",
        label="Descripción",
        multiline=True,
        min_lines=2,
        max_lines=3,
    )
    src_requires_auth = (
        s.source not in AppState.LOCAL_SOURCES
        and s.auth_session_ok.get(s.source) is True
    )
    delete_chk = ft.Checkbox(
        label=(
            "Eliminar original (avanzado, requiere sesión activa)"
            if src_requires_auth
            else "Eliminar original (no disponible para local)"
        ),
        value=False,
        disabled=not src_requires_auth,
        fill_color={ft.ControlState.SELECTED: ACCENT},
        check_color=TEXT_PRIMARY,
        label_style=ft.TextStyle(color=TEXT_MUTED, size=11, font_family=FONT_TEXT),
        border_side=ft.BorderSide(1.2, TEXT_DIM),
    )
    existing_dd = organize_dropdown(
        [],
        "",
        "Playlist destino sincronizada",
        width=460,
    )
    existing_help = ft.Text(
        "",
        size=10,
        color=TEXT_MUTED,
        font_family=FONT_TEXT,
        visible=False,
    )
    new_box = ft.Column([title_field, desc_field, delete_chk], spacing=8, tight=True)
    existing_box = ft.Column([existing_dd, existing_help], spacing=6, tight=True, visible=False)

    confirm_btn = dialog_action(
        "Transferir",
        lambda _: None,
        kind="primary",
        icon=PIcons.ARROWS_LEFT_RIGHT,
    )

    def _on_preflight_status(ok: bool | None) -> None:
        confirm_btn.disabled = ok is False
        safe_update(confirm_btn)

    preflight_banner, preflight_state, run_preflight = make_preflight_status_row(
        host,
        on_status_change=_on_preflight_status,
    )

    def _refresh_existing_options(dest: str) -> None:
        items = get_synced_playlists_for(host, dest)
        existing_dd.options = [
            ft.dropdown.Option(key=str(p.id), text=f"{p.name or p.id} ({p.track_count})")
            for p in items
        ]
        existing_dd.value = str(items[0].id) if items else ""
        existing_help.visible = not items
        if not items:
            existing_help.value = (
                f"Sin playlists sincronizadas de {dest} en Biblioteca. "
                "Sincroniza esa plataforma en Biblioteca o crea una playlist nueva."
            )

    def _sync_mode_visibility(dest: str) -> None:
        is_export = dest == EXPORT_DEST_LABEL
        is_existing = mode_state["mode"] == "existing" and not is_export
        mode_row.visible = not is_export and not has_divisions
        new_box.visible = not is_export and not is_existing
        existing_box.visible = is_existing
        if is_export:
            confirm_btn.disabled = False
        safe_update(mode_row)
        safe_update(new_box)
        safe_update(existing_box)
        safe_update(confirm_btn)

    def _on_dest_select(e) -> None:
        dest = str(e.control.value or default_dest)
        _refresh_existing_options(dest)
        _sync_mode_visibility(dest)
        asyncio.create_task(run_preflight(dest))

    dest_dd = organize_dropdown(
        [(p, p) for p in dest_opts],
        default_dest,
        "Plataforma destino",
        width=460,
        on_select=_on_dest_select,
    )

    def _on_mode_change(e) -> None:
        try:
            val = (
                list(e.control.selected)[0]
                if hasattr(e.control, "selected") and e.control.selected
                else e.control.value
            )
        except Exception:
            val = getattr(e.control, "value", "new")
        if val in ("new", "existing"):
            mode_state["mode"] = val
        _sync_mode_visibility(str(dest_dd.value or default_dest))

    try:
        mode_seg: ft.Control = ft.SegmentedButton(
            selected=["new"],
            allow_empty_selection=False,
            allow_multiple_selection=False,
            show_selected_icon=False,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
            ),
            segments=[
                ft.Segment(value="new", label=ft.Text("Nueva playlist", size=11, font_family=FONT_HEADLINE)),
                ft.Segment(value="existing", label=ft.Text("Playlist existente", size=11, font_family=FONT_HEADLINE)),
            ],
            on_change=_on_mode_change,
        )
    except Exception:
        mode_seg = organize_dropdown(
            [("new", "Nueva playlist"), ("existing", "Playlist existente")],
            "new",
            "Destino",
            width=220,
            on_select=_on_mode_change,
        )

    mode_row = ft.Row(
        [ft.Text("Modo:", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE), mode_seg],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        visible=not has_divisions,
    )

    dlg_ref: dict = {}

    async def _confirm_transfer(_e=None) -> None:
        dest = str(dest_dd.value or default_dest)
        if dest == EXPORT_DEST_LABEL:
            host._close_dlg(dlg_ref["dlg"])
            s.set_destination(dest)
            await host._open_export_dialog()
            return
        if preflight_state["ok"] is None:
            ok = await run_preflight(dest)
        else:
            ok = bool(preflight_state["ok"])
        if not ok:
            host._snack(
                f"Sesión de {dest} no válida; configura o revalida antes de transferir",
                error=True,
            )
            return

        mode = "new" if has_divisions else mode_state["mode"]
        if mode == "existing":
            target_pid = str(existing_dd.value or "").strip()
            if not target_pid:
                host._snack(
                    f"Elige una playlist sincronizada de {dest} o cambia a «Nueva playlist»",
                    error=True,
                )
                return
            items = get_synced_playlists_for(host, dest)
            match = next((p for p in items if str(p.id) == target_pid), None)
            target_name = str(getattr(match, "name", "") or target_pid)
            selected_tracks = [t for t in s.tracks if t.selected]
            host._close_dlg(dlg_ref["dlg"])
            s.set_destination(dest)
            await add_tracks_to_existing_destination(
                host, dest, target_pid, target_name, selected_tracks
            )
            return

        if s.source == dest and not has_transform and not is_local_or_merged:
            host._snack(
                "Origen y destino no pueden ser iguales (solo permitido tras Organizar/Dividir)",
                error=True,
            )
            return
        clean_title = (title_field.value or "").strip()
        if not clean_title:
            host._snack("El nombre de la playlist no puede estar vacío", error=True)
            return
        clean_desc = (desc_field.value or "").strip()
        delete_orig = bool(delete_chk.value) and src_requires_auth
        host._close_dlg(dlg_ref["dlg"])
        s.set_destination(dest)
        await execute_new_playlist_transfer(
            host,
            dest=dest,
            title=clean_title,
            description=clean_desc,
            delete_original=delete_orig,
            has_divisions=has_divisions,
            per_div_dests=per_div_override,
        )

    confirm_btn.on_click = lambda e: asyncio.create_task(_confirm_transfer(e))

    _refresh_existing_options(default_dest)
    _sync_mode_visibility(default_dest)

    dlg = app_dialog(
        "Transferir playlist",
        ft.Column(
            [
                _section_label("DESTINO Y SESIÓN"),
                dest_dd,
                preflight_banner,
                mode_row,
                new_box,
                existing_box,
            ],
            spacing=10,
            tight=True,
        ),
        [
            dialog_action("Cancelar", lambda _: host._close_dlg(dlg), kind="muted"),
            confirm_btn,
        ],
        width=500,
        icon=PIcons.ARROWS_LEFT_RIGHT,
    )
    dlg_ref["dlg"] = dlg
    host.page.show_dialog(dlg)
    asyncio.create_task(run_preflight(default_dest))


__all__ = ["execute_new_playlist_transfer", "open_transfer_dialog"]
