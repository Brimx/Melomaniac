"""
ui/dialogs/add_to_playlist_dialog.py — Diálogo unificado «Añadir a playlist…»
═════════════════════════════════════════════════════════════════════════════
Dos pestañas:
- **Local** (siempre disponible sin sesión): añade a una playlist local/importada
  existente en ``Local > Playlists`` o crea una nueva.
- **Plataformas** (filtrada solo a cuentas con sesión activa): añade a una
  playlist sincronizada vía ``add_tracks_to_existing_destination``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Callable, Sequence

import flet as ft

from core.config import LOCAL_LIBRARY_LABEL, PLATFORM_ORDER
from core.models import Track
from engine.local_manifests import (
    append_tracks_to_manifest,
    list_local_manifests,
    save_local_manifest,
)
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    app_dialog,
    app_segmented_toggle,
    app_text_field,
    close_dialog,
    notify,
    organize_dropdown,
    safe_update,
    spawn_task,
)
from ui.dialogs.transfer_helpers import add_tracks_to_existing_destination
from ui.theme.fonts import FONT_TEXT
from ui.theme.tokens import (
    BG_INSET,
    BG_SURFACE,
    BORDER_CONTROL,
    FS_META,
    RADIUS_SHARP,
    RADIUS_SM,
    SPACE_2,
    SPACE_3,
    TEXT_MUTED,
)


def _connected_platforms(app_state) -> list[str]:
    if app_state is None:
        return []
    auth_ok = getattr(app_state, "auth_session_ok", None) or {}
    return [p for p in PLATFORM_ORDER if auth_ok.get(p) is True]


def show_add_to_playlist_dialog(
    page: ft.Page | None,
    tracks: Sequence[Track],
    *,
    app_state=None,
    library_state=None,
    on_saved: Callable[[], None] | None = None,
) -> None:
    """Abre el diálogo unificado «Añadir a playlist…» (Local y Plataformas)."""
    track_list = list(tracks or ())
    if page is None or not track_list:
        return

    manifests = list_local_manifests()
    connected = _connected_platforms(app_state)

    tab_state = {"value": "local"}
    local_mode = {"value": "existing" if manifests else "new"}
    selected_manifest_id = {"value": manifests[0].manifest_id if manifests else ""}
    selected_platform = {"value": connected[0] if connected else ""}
    selected_dest_pl = {"value": ""}
    dest_pl_names: dict[str, str] = {}

    # ── Pestaña Local ──────────────────────────────────────────────────
    manifest_options = [
        (m.manifest_id, f"{m.name} ({len(m.tracks)})")
        for m in manifests
    ]
    dd_local = organize_dropdown(
        manifest_options or [("", "Sin playlists locales")],
        selected_manifest_id["value"],
        "Playlist local",
        width=360,
    )
    dd_local.on_select = lambda e: selected_manifest_id.__setitem__("value", str(e.control.value or ""))  # type: ignore

    tf_new_name = app_text_field(
        hint_text="Nombre de la nueva playlist…",
        width=360,
        height=38,
    )

    local_existing_wrap = ft.Container(content=dd_local, visible=(local_mode["value"] == "existing"))
    local_new_wrap = ft.Container(content=tf_new_name, visible=(local_mode["value"] == "new"))

    def _on_local_mode_change(e) -> None:
        local_mode["value"] = e.control.value
        local_existing_wrap.visible = local_mode["value"] == "existing"
        local_new_wrap.visible = local_mode["value"] == "new"
        safe_update(local_existing_wrap)
        safe_update(local_new_wrap)

    local_controls: list[ft.Control] = []
    if manifests:
        seg_local_mode = app_segmented_toggle(
            [("existing", "Existente"), ("new", "Nueva playlist")],
            local_mode["value"],
            on_change=_on_local_mode_change,
            height=34,
            expand=True,
        )
        local_controls.append(seg_local_mode)
    local_controls.extend([local_existing_wrap, local_new_wrap])
    local_panel = ft.Column(local_controls, tight=True, spacing=SPACE_2, visible=True)

    # ── Pestaña Plataformas ────────────────────────────────────────────
    dd_plat_pl = organize_dropdown([("", "Sin playlists sincronizadas")], "", "Playlist destino", width=360)

    def _refresh_platform_playlists(plat: str) -> None:
        dest_pl_names.clear()
        opts: list[tuple[str, str]] = []
        if library_state is not None and plat:
            try:
                for summ in library_state.get_playlists(plat) or []:
                    opts.append((summ.id, f"{summ.name} ({summ.track_count})"))
                    dest_pl_names[summ.id] = summ.name
            except Exception:
                opts = []
        dd_plat_pl.options = [
            ft.dropdown.Option(k, lbl)
            for k, lbl in (opts or [("", "Sincroniza esta plataforma en Biblioteca")])
        ]
        selected_dest_pl["value"] = opts[0][0] if opts else ""
        dd_plat_pl.value = selected_dest_pl["value"]
        safe_update(dd_plat_pl)

    dd_plat_pl.on_select = lambda e: selected_dest_pl.__setitem__("value", str(e.control.value or ""))  # type: ignore

    if connected:
        dd_plat = organize_dropdown(
            [(p, p) for p in connected],
            selected_platform["value"],
            "Plataforma conectada",
            width=360,
        )

        def _on_plat_change(e) -> None:
            selected_platform["value"] = str(e.control.value or "")
            _refresh_platform_playlists(selected_platform["value"])

        dd_plat.on_select = _on_plat_change  # type: ignore
        _refresh_platform_playlists(selected_platform["value"])
        platform_panel = ft.Column(
            [dd_plat, dd_plat_pl],
            tight=True,
            spacing=SPACE_2,
            visible=False,
        )
    else:
        platform_panel = ft.Container(
            bgcolor=BG_INSET,
            border=ft.Border.all(0.8, BORDER_CONTROL),
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.all(SPACE_3),
            visible=False,
            content=ft.Text(
                "No hay cuentas conectadas. Inicia sesión desde Configuración o usa la pestaña Local.",
                size=FS_META,
                color=TEXT_MUTED,
                font_family=FONT_TEXT,
            ),
        )

    def _on_tab_change(e) -> None:
        tab_state["value"] = e.control.value
        local_panel.visible = tab_state["value"] == "local"
        platform_panel.visible = tab_state["value"] == "platform"
        safe_update(local_panel)
        safe_update(platform_panel)

    seg_tab = app_segmented_toggle(
        [("local", "Local"), ("platform", "Plataformas")],
        tab_state["value"],
        on_change=_on_tab_change,
        height=36,
        expand=True,
    )

    subtitle = ft.Text(
        f"1 canción seleccionada" if len(track_list) == 1 else f"{len(track_list)} canciones seleccionadas",
        size=FS_META,
        color=TEXT_MUTED,
        font_family=FONT_TEXT,
    )

    content = ft.Column(
        [subtitle, seg_tab, local_panel, platform_panel],
        tight=True,
        spacing=SPACE_3,
    )

    dlg_ref: dict[str, ft.AlertDialog] = {}

    def _finish_local_save() -> None:
        if library_state is not None and hasattr(library_state, "notify"):
            try:
                library_state.notify()
            except Exception:
                pass
        if callable(on_saved):
            try:
                on_saved()
            except Exception:
                pass

    def _on_submit(_e) -> None:
        if tab_state["value"] == "local":
            if local_mode["value"] == "new" or not selected_manifest_id["value"]:
                name = str(tf_new_name.value or "").strip()
                if not name:
                    notify(page, "Escribe un nombre para la playlist local", kind="error")
                    return
                close_dialog(page, dlg_ref.get("dlg"))

                async def _create_local() -> None:
                    await asyncio.to_thread(
                        save_local_manifest,
                        platform=LOCAL_LIBRARY_LABEL,
                        name=name,
                        tracks=track_list,
                    )
                    _finish_local_save()
                    notify(page, f"Playlist «{name}» creada en Local ({len(track_list)})")

                try:
                    page.run_task(_create_local)
                except Exception:
                    spawn_task(_create_local())
                return

            mid = selected_manifest_id["value"]
            close_dialog(page, dlg_ref.get("dlg"))

            async def _append_local() -> None:
                updated, added = await asyncio.to_thread(append_tracks_to_manifest, mid, track_list)
                if updated is None:
                    notify(page, "No se encontró la playlist local", kind="error")
                    return
                _finish_local_save()
                if added > 0:
                    notify(page, f"{added} añadida(s) a «{updated.name}»")
                else:
                    notify(page, f"Ya estaban en «{updated.name}»")

            try:
                page.run_task(_append_local)
            except Exception:
                spawn_task(_append_local())
            return

        # Pestaña Plataformas
        plat = selected_platform["value"]
        pl_id = selected_dest_pl["value"]
        if not plat or not pl_id:
            notify(page, "Selecciona una plataforma conectada y una playlist destino", kind="error")
            return
        pl_name = dest_pl_names.get(pl_id, pl_id)
        close_dialog(page, dlg_ref.get("dlg"))

        async def _add_remote() -> None:
            host = SimpleNamespace(
                state=app_state,
                page=page,
                _snack=lambda msg, error=False: notify(page, msg, kind="error" if error else "info"),
            )
            await add_tracks_to_existing_destination(host, plat, pl_id, pl_name, track_list)
            _finish_local_save()

        try:
            page.run_task(_add_remote)
        except Exception:
            spawn_task(_add_remote())

    actions = [
        _ghost_btn("Cancelar", None, lambda _: close_dialog(page, dlg_ref.get("dlg")), height=36),
        _primary_btn("Añadir", None, _on_submit, height=36),
    ]
    dlg = app_dialog(
        "Añadir a playlist",
        ft.Container(content=content, width=380, padding=ft.Padding.all(SPACE_2)),
        actions,
        width=420,
        bgcolor=BG_SURFACE,
        radius=RADIUS_SM,
    )
    dlg_ref["dlg"] = dlg
    page.show_dialog(dlg)
