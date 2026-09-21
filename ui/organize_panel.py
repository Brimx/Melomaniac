"""
ui/organize_panel.py — Melomaniac v4.2 — Panel unificado Organizar/Agrupar

Extraído de ui/main_ui.py _on_organize/_on_split para permitir toggle entre
operaciones sin cerrar diálogo (Tabs). Preserva allowlist de engine/organizer
y usa animaciones cookbook Flet. Genre excluido (pendiente biblioteca §10).

Tabs:
  0 Ordenar — sort + grouping estable (GROUP→SORT per-grupo §14)
  1 Agrupar — split + selector lista artistas/albumes con counts + scope
"""

from __future__ import annotations

import flet as ft

from ui.tokens import BG_SURFACE, BG_INPUT, BORDER_LIGHT, ACCENT, TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM, CHIP_BG
from ui.widgets import dialog_action, app_dialog, organize_dropdown
from engine.organizer import split_tracks


def _scope_seg(current: str, on_change=None) -> ft.Control:
    """Reusa tokens, fallback Dropdown si SegmentedButton no disponible."""
    try:
        return ft.SegmentedButton(
            selected=[current],
            allow_empty_selection=False,
            allow_multiple_selection=False,
            show_selected_icon=False,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
            ),
            segments=[
                ft.Segment(value="all", label=ft.Text("Todo", size=11)),
                ft.Segment(value="visible", label=ft.Text("Visibles", size=11)),
                ft.Segment(value="selected", label=ft.Text("Seleccionadas", size=11)),
            ],
            on_change=on_change,
        )
    except Exception:
        return ft.Dropdown(
            value=current, width=200, height=38,
            bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            options=[ft.dropdown.Option("all","Todo"), ft.dropdown.Option("visible","Visibles"), ft.dropdown.Option("selected","Seleccionadas")],
            on_select=on_change,
        )


def _close_dlg(page: ft.Page, dlg: ft.AlertDialog | None) -> None:
    """Cierre canónico: DialogMixin.close_dialog — Flet 0.86 no expone Page.close_dialog."""
    if dlg is None:
        return
    try:
        dlg.open = False
        page.update()
    except Exception:
        pass


def show_organize_divide_dialog(page: ft.Page, state) -> None:
    """Diálogo unificado con Tabs Ordenar|Agrupar. No duplica lógica de transformación (engine)."""
    # estado local UI
    scope = {"value": getattr(state, "_dual_scope", "visible") or "visible"}
    sort_key = {"value": "artist"}
    group_by = {"value": "none"}  # none|artist|album
    reverse = {"value": False}
    split_key = {"value": "artist"}

    def _on_scope_change(e):
        try:
            v = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
        except Exception:
            v = getattr(e.control, "value", "visible")
        if v in ("all","visible","selected"):
            scope["value"] = v
            _refresh_split_preview()

    scope_seg_ord = _scope_seg(scope["value"], on_change=_on_scope_change)
    scope_seg_grp = _scope_seg(scope["value"], on_change=_on_scope_change)

    # --- Ordenar controls — solo sort (sin grupo; agrupar va en Tab Agrupar) ---
    dd_sort = organize_dropdown(
        [("artist","Artista"), ("album","Álbum"), ("name","Título"), ("duration_ms","Duración"), ("release_date","Fecha lanzamiento"), ("original_position","Original")],
        sort_key["value"], "Ordenar por",
    )
    def _on_sort_change(e):
        sort_key["value"] = e.control.value
    dd_sort.on_select = _on_sort_change  # type: ignore

    # Mantener compat: group_by solo usado si algun flujo externo lo setea, no visible en Ordenar
    # (eliminado Seg Ninguno|Artista|Álbum de Ordenar para división clara §11 vs §16)

    sw_rev = ft.Switch(label="Descendente", value=False, active_color=ACCENT, on_change=lambda e: reverse.__setitem__("value", bool(e.control.value)))

    # --- Agrupar controls ---
    dd_split = organize_dropdown([("artist","Artista"), ("album","Álbum")], split_key["value"], "Agrupar por")
    def _on_split_change(e):
        split_key["value"] = e.control.value
        _refresh_split_preview()
    dd_split.on_select = _on_split_change  # type: ignore

    # Preview lista artistas/albumes con counts (modo eficiente: split sobre visible/all según scope)
    preview_list = ft.ListView(height=140, spacing=4, padding=ft.Padding.all(4), expand=False, visible=True)
    preview_empty = ft.Text("Sin segmentos (carga una playlist)", size=11, color=TEXT_DIM, font_family="IBM Plex Sans")
    preview_wrap = ft.Container(content=ft.Column([preview_list, preview_empty], spacing=6), bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, padding=ft.Padding.all(8), visible=True)

    def _refresh_split_preview():
        try:
            # fuente según scope
            if scope["value"] in ("visible","selected"):
                src = state.visible_tracks() if scope["value"]=="visible" else state.selected_in_scope("visible")
                if not src:
                    src = state.tracks
            else:
                src = state.tracks
            segs = split_tracks(src, split_key["value"])
            if not segs:
                preview_list.controls = []
                preview_empty.visible = True
                preview_empty.value = "Sin segmentos para criterio/scopo actual"
                preview_wrap.update()
                return
            preview_empty.visible = False
            # ordena por count desc para vista rápida
            sorted_keys = sorted(segs.keys(), key=lambda k: len(segs[k]), reverse=True)
            controls = []
            for k in sorted_keys[:40]:  # ventana 40, evita crear miles
                cnt = len(segs[k])
                # chip con nombre + badge
                row = ft.Row([
                    ft.Icon(ft.Icons.PERSON_OUTLINE if split_key["value"]=="artist" else ft.Icons.ALBUM_OUTLINED, size=14, color=TEXT_MUTED),
                    ft.Text(k, size=11, color=TEXT_PRIMARY, font_family="IBM Plex Sans", expand=True, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                    ft.Container(content=ft.Text(str(cnt), size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"), bgcolor=BG_SURFACE, border_radius=8, padding=ft.Padding.symmetric(horizontal=6, vertical=2)),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                # click selecciona segmento inmediatamente (feedback)
                def _on_pick(e, key=k):
                    try:
                        state.set_active_segment(key)
                        # cierra preview? no, solo selecciona
                        page.update()
                    except Exception:
                        pass
                controls.append(ft.Container(content=row, ink=True, on_click=_on_pick, padding=ft.Padding.symmetric(horizontal=6, vertical=4), border_radius=6))
            preview_list.controls = controls
            preview_list.update()
            preview_wrap.update()
        except Exception:
            pass

    _refresh_split_preview()

    # Tabs content — Segmented toggle (cookbook compatible)
    tab_ordenar = ft.Column([
        ft.Text("Ordena la lista por criterio. Para agrupar+ordenar usa Tab Agrupar §14.", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
        dd_sort,
        sw_rev,
        ft.Text("Alcance:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
        scope_seg_ord,
    ], tight=True, spacing=10)

    tab_agrupar = ft.Column([
        ft.Text("Divide en segmentos y selecciona artistas/albumes. Cada partición mantiene su orden.", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
        dd_split,
        ft.Text("Alcance:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
        scope_seg_grp,
        ft.Text("Vista previa (top 40 por count) — clic selecciona segmento:", size=9, color=TEXT_DIM, font_family="IBM Plex Sans"),
        preview_wrap,
    ], tight=True, spacing=10, scroll=ft.ScrollMode.AUTO)

    # Switch Ordenar|Agrupar via SegmentedButton (compatible 0.86)
    wrap_ordenar = ft.Container(content=tab_ordenar, padding=ft.Padding.all(8), visible=True, animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT))
    wrap_agrupar = ft.Container(content=tab_agrupar, padding=ft.Padding.all(8), visible=False, animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT))
    mode = {"value": "ordenar"}

    # acciones — single primary que cambia según tab (§11 vs §16 división clara)
    dlg_ref: dict = {}
    btn_primary_ref: dict = {}

    def _apply_ordenar(_e):
        try:
            state._dual_scope = scope["value"]
        except Exception:
            pass
        state.organize_sort([sort_key["value"]], reverse["value"], scope=scope["value"])
        _close_dlg(page, dlg_ref.get("dlg"))

    def _apply_agrupar(_e):
        try:
            state._dual_scope = scope["value"]
        except Exception:
            pass
        state.organize_split(split_key["value"], scope=scope["value"])
        _close_dlg(page, dlg_ref.get("dlg"))

    def _clear(_e):
        state.clear_split()
        _close_dlg(page, dlg_ref.get("dlg"))

    def _on_mode_toggle(e):
        try:
            v = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
        except Exception:
            v = getattr(e.control, "value", "ordenar")
        mode["value"] = v if v in ("ordenar","agrupar") else "ordenar"
        wrap_ordenar.visible = (mode["value"]=="ordenar")
        wrap_agrupar.visible = (mode["value"]=="agrupar")
        try:
            wrap_ordenar.update(); wrap_agrupar.update()
        except Exception:
            pass
        if mode["value"]=="agrupar":
            _refresh_split_preview()
        # actualiza botón primario según tab activa
        try:
            btn = btn_primary_ref.get("btn")
            if btn is not None:
                is_ordenar = mode["value"]=="ordenar"
                btn.text = "Ordenar" if is_ordenar else "Agrupar"
                btn.on_click = _apply_ordenar if is_ordenar else _apply_agrupar
                btn.update()
        except Exception:
            pass

    try:
        mode_seg = ft.SegmentedButton(
            selected=["ordenar"], allow_empty_selection=False, allow_multiple_selection=False, show_selected_icon=False,
            style=ft.ButtonStyle(bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE}, color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED}),
            segments=[ft.Segment(value="ordenar", label=ft.Text("Ordenar", size=11)), ft.Segment(value="agrupar", label=ft.Text("Agrupar", size=11))],
            on_change=_on_mode_toggle,
        )
    except Exception:
        mode_seg = ft.Dropdown(value="ordenar", width=200, height=38, bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
                               text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
                               options=[ft.dropdown.Option("ordenar","Ordenar"), ft.dropdown.Option("agrupar","Agrupar")],
                               on_select=_on_mode_toggle)

    content = ft.Container(
        content=ft.Column([
            mode_seg,
            ft.Stack(controls=[wrap_ordenar, wrap_agrupar], expand=True),
        ], spacing=8, tight=True),
        width=540, height=460,
        padding=ft.Padding.all(4),
    )

    # primary single según tab activa (evita dos botones primarios a la vez)
    btn_primary = dialog_action("Ordenar", _apply_ordenar, kind="primary")
    btn_primary_ref["btn"] = btn_primary

    actions: list[ft.Control] = []
    if bool(getattr(state, "segments", {})):
        actions.append(dialog_action("Limpiar División", _clear, kind="danger"))
    actions += [
        dialog_action("Cancelar", lambda _: _close_dlg(page, dlg_ref.get("dlg")), kind="muted"),
        btn_primary,
    ]

    dlg = app_dialog(
        "Organizar / Agrupar",
        content,
        actions,
        width=560,
        actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN if len(actions)==3 else ft.MainAxisAlignment.END,
        bgcolor=BG_SURFACE,
        radius=10,
    )
    dlg_ref["dlg"] = dlg
    page.show_dialog(dlg)
