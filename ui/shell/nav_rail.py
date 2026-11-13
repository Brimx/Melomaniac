"""ui/shell/nav_rail.py — Navigation rail flotante y cambio de módulos del shell."""

from __future__ import annotations

import flet as ft

from core.config import APP_VERSION
from ui.components.widgets import safe_update
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_MEDIUM, brand_family, mono_family
from ui.theme.icons import FONT_PHOSPHOR, FONT_PHOSPHOR_FILL, PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    ACCENT_HALO,
    BG_DEEP,
    BG_HOVER,
    BORDER_MUTED,
    MOTION_HOVER_MS,
    RADIUS_RAIL,
    RADIUS_RAIL_ITEM,
    RAIL_ANIM_MS,
    RAIL_BRAND_W,
    RAIL_HEADER_H,
    RAIL_ITEM_H,
    RAIL_ITEM_PAD_V,
    RAIL_ITEM_SPACING,
    RAIL_LABEL_W,
    RAIL_MARGIN,
    RAIL_PAD_H,
    RAIL_PAD_V,
    RAIL_VERSION_H,
    RAIL_VERSION_H_COLLAPSED,
    RAIL_W_COLLAPSED,
    RAIL_W_EXPANDED,
    SIDEBAR_BG,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def build_nav_rail(ui) -> None:
    """Construye el rail lateral flotante docked y sus handlers en ``ui``."""
    ui._current_module = 0  # 0=Gestión, 1=Biblioteca, 2=Descargas, 3=Config
    ui._rail_collapsed = True
    ui._rail_w_collapsed = RAIL_W_COLLAPSED
    ui._rail_w_expanded = RAIL_W_EXPANDED
    ui._rail_dests = [
        ("Gestión", PIcons.ARROWS_LEFT_RIGHT, PIcons.ARROWS_LEFT_RIGHT),
        ("Biblioteca", PIcons.BOOKS, PIcons.BOOKS),
        ("Descargas", PIcons.DOWNLOAD_SIMPLE, PIcons.DOWNLOAD_SIMPLE),
        ("Config", PIcons.GEAR, PIcons.GEAR),
    ]
    ui._rail_label_boxes = []
    ui._rail_item_refs = []

    def _select_module(idx: int) -> None:
        if idx == ui._current_module:
            return
        prev_idx = ui._current_module
        ui._current_module = idx
        try:
            refreshed_self = False
            prev_panel = (
                ui._module_panels[prev_idx]
                if 0 <= prev_idx < len(ui._module_panels)
                else None
            )
            curr_panel = (
                ui._module_panels[idx]
                if 0 <= idx < len(ui._module_panels)
                else None
            )
            with ft.context.disable_auto_update():
                for i, panel in enumerate(ui._module_panels):
                    panel.visible = (i == idx)
                ui._refresh_rail_selection()
            # Publica el cambio visual de pestaña antes de refrescar contenido diferido.
            safe_update(prev_panel)
            safe_update(ui._nav_rail)
            if curr_panel is not None and hasattr(curr_panel, "refresh"):
                try:
                    curr_panel.refresh(update=False)
                except TypeError:
                    curr_panel.refresh()
                    refreshed_self = True
            if idx == 0:
                ui._sync_skeleton_count()
                if getattr(ui, "_gestion_dirty", False):
                    ui._on_state_changed()
                    refreshed_self = True
            if not refreshed_self:
                if not safe_update(curr_panel):
                    safe_update(ui._module_stack)
        except Exception:
            pass

    ui._select_module = _select_module

    def _on_rail_item_click(e):
        try:
            idx = int(e.control.data)
        except Exception:
            idx = 0
        _select_module(idx)

    ui._rail_header_brand = None
    ui._rail_header_brand_box = None
    ui._rail_header_box = None
    ui._rail_header_row = None
    ui._rail_version_text = None
    ui._rail_version_box = None

    def _rail_item(idx: int, label: str, icon_out: str, icon_sel: str) -> ft.Container:
        is_sel = (idx == ui._current_module)
        txt = ft.Text(
            label,
            size=13,
            color=TEXT_PRIMARY if is_sel else TEXT_MUTED,
            font_family=FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE,
            expand=True,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.CLIP,
        )
        # Solo animan ancho/margen/opacidad de la caja para que el colapso sea
        # un recorte progresivo y el icono permanezca fijo.
        label_box = ft.Container(
            content=txt,
            width=RAIL_LABEL_W if not ui._rail_collapsed else 0,
            height=20,
            margin=ft.Margin.only(left=12) if not ui._rail_collapsed else ft.Margin.only(left=0),
            opacity=1.0 if not ui._rail_collapsed else 0.0,
            alignment=ft.Alignment.CENTER_LEFT,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        ui._rail_label_boxes.append(label_box)
        icon = app_icon(
            icon_sel if is_sel else icon_out,
            size=20,
            color=ACCENT if is_sel else TEXT_DIM,
            fill=is_sel,
        )
        row = ft.Row(
            [icon, label_box],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            alignment=ft.MainAxisAlignment.START,
        )

        cont = ft.Container(
            content=row,
            bgcolor=ACCENT_HALO if is_sel else ft.Colors.TRANSPARENT,
            border=ft.Border.all(0.8, ACCENT if is_sel else ft.Colors.TRANSPARENT),
            border_radius=RADIUS_RAIL_ITEM,
            padding=ft.Padding.symmetric(horizontal=12, vertical=RAIL_ITEM_PAD_V),
            height=RAIL_ITEM_H,
            data=str(idx),
            tooltip=label if ui._rail_collapsed else None,
            on_click=_on_rail_item_click,
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            alignment=ft.Alignment.CENTER_LEFT,
        )

        def _on_hover(e: ft.HoverEvent) -> None:
            if idx == ui._current_module:
                return
            is_h = e.data == "true" or e.data is True
            cont.bgcolor = BG_HOVER if is_h else ft.Colors.TRANSPARENT
            cont.border = ft.Border.all(0.8, ACCENT if is_h else ft.Colors.TRANSPARENT)
            icon.color = TEXT_PRIMARY if is_h else TEXT_DIM
            txt.color = TEXT_PRIMARY if is_h else TEXT_MUTED
            if cont.page:
                cont.update()

        cont.on_hover = _on_hover
        ui._rail_item_refs.append(cont)
        return cont

    def _toggle_rail(_e=None):
        ui._rail_collapsed = not ui._rail_collapsed
        expanded = not ui._rail_collapsed
        try:
            ui._nav_rail.width = ui._rail_w_expanded if expanded else ui._rail_w_collapsed
            ui._rail_toggle_btn.tooltip = "Colapsar" if expanded else "Expandir"
            if ui._rail_header_brand_box is not None:
                ui._rail_header_brand_box.width = RAIL_BRAND_W if expanded else 0
                ui._rail_header_brand_box.margin = (
                    ft.Margin.only(left=8) if expanded else ft.Margin.only(left=0)
                )
                ui._rail_header_brand_box.opacity = 1.0 if expanded else 0.0
            if ui._rail_header_box is not None:
                ui._rail_header_box.alignment = ft.Alignment.CENTER_LEFT
            if ui._rail_version_text is not None:
                ui._rail_version_text.opacity = 0.7 if expanded else 0.0
            if ui._rail_version_box is not None:
                ui._rail_version_box.height = (
                    RAIL_VERSION_H if expanded else RAIL_VERSION_H_COLLAPSED
                )
            for i, label_box in enumerate(ui._rail_label_boxes):
                label_box.width = RAIL_LABEL_W if expanded else 0
                label_box.margin = (
                    ft.Margin.only(left=12) if expanded else ft.Margin.only(left=0)
                )
                label_box.opacity = 1.0 if expanded else 0.0
                try:
                    ui._rail_item_refs[i].tooltip = None if expanded else ui._rail_dests[i][0]
                except Exception:
                    pass
            ui._nav_rail.update()
        except Exception:
            pass

    ui._rail_toggle_btn = app_icon_button(
        PIcons.SIDEBAR_SIMPLE,
        size=18,
        icon_color=TEXT_MUTED,
        hover_color_icon=TEXT_PRIMARY,
        tooltip="Expandir" if ui._rail_collapsed else "Colapsar",
        on_click=_toggle_rail,
        width=32,
        height=32,
    )
    ui._rail_header_brand = ft.Text(
        "Melomaniac",
        size=20,
        color=TEXT_PRIMARY,
        font_family=brand_family("bold"),
        visible=True,
        expand=True,
        no_wrap=True,
        max_lines=1,
        overflow=ft.TextOverflow.CLIP,
    )
    ui._rail_header_brand_box = ft.Container(
        content=ui._rail_header_brand,
        width=RAIL_BRAND_W if not ui._rail_collapsed else 0,
        height=20,
        margin=ft.Margin.only(left=8) if not ui._rail_collapsed else ft.Margin.only(left=0),
        opacity=1.0 if not ui._rail_collapsed else 0.0,
        alignment=ft.Alignment.CENTER_LEFT,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
    )
    ui._rail_header_row = ft.Row(
        [ui._rail_toggle_btn, ui._rail_header_brand_box],
        spacing=0,
        height=RAIL_HEADER_H,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.START,
    )
    ui._rail_header_box = ft.Container(
        content=ui._rail_header_row,
        height=RAIL_HEADER_H,
        alignment=ft.Alignment.CENTER_LEFT,
    )
    rail_items = [
        _rail_item(i, lbl, ico, sel)
        for i, (lbl, ico, sel) in enumerate(ui._rail_dests[:3])
    ]
    config_item = _rail_item(3, *ui._rail_dests[3])

    def _refresh_rail_selection():
        for i, cont in enumerate(ui._rail_item_refs):
            is_sel = (i == ui._current_module)
            try:
                cont.bgcolor = ACCENT_HALO if is_sel else ft.Colors.TRANSPARENT
                cont.border = ft.Border.all(0.8, ACCENT if is_sel else ft.Colors.TRANSPARENT)
                row = cont.content
                if row and len(row.controls) >= 2:
                    icon_ctrl = row.controls[0]
                    label_box = row.controls[1]
                    lbl_ctrl = label_box.content if isinstance(label_box, ft.Container) else None
                    glyph = ui._rail_dests[i][2] if is_sel else ui._rail_dests[i][1]
                    icon_ctrl.value = glyph
                    icon_ctrl.font_family = FONT_PHOSPHOR_FILL if is_sel else FONT_PHOSPHOR
                    icon_ctrl.color = ACCENT if is_sel else TEXT_DIM
                    if lbl_ctrl:
                        lbl_ctrl.color = TEXT_PRIMARY if is_sel else TEXT_MUTED
                        lbl_ctrl.font_family = FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE
            except Exception:
                pass

    ui._refresh_rail_selection = _refresh_rail_selection  # type: ignore

    ui._rail_version_text = ft.Text(
        f"v{APP_VERSION}",
        size=8,
        color=TEXT_DIM,
        font_family=mono_family("light"),
        visible=True,
        height=RAIL_VERSION_H,
        no_wrap=True,
        max_lines=1,
        opacity=0.7 if not ui._rail_collapsed else 0.0,
        animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        text_align=ft.TextAlign.CENTER,
    )
    ui._rail_version_box = ft.Container(
        content=ui._rail_version_text,
        height=RAIL_VERSION_H if not ui._rail_collapsed else RAIL_VERSION_H_COLLAPSED,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
    )
    rail_bottom = ft.Column(
        controls=[config_item, ui._rail_version_box],
        spacing=2,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    ui._nav_rail = ft.Container(
        width=ui._rail_w_expanded if not ui._rail_collapsed else ui._rail_w_collapsed,
        margin=ft.Margin.all(RAIL_MARGIN),
        bgcolor=SIDEBAR_BG,
        border=ft.Border.all(0.8, BORDER_MUTED),
        border_radius=RADIUS_RAIL,
        padding=ft.Padding.symmetric(horizontal=RAIL_PAD_H, vertical=RAIL_PAD_V),
        shadow=ft.BoxShadow(
            blur_radius=18,
            color=ft.Colors.with_opacity(0.45, BG_DEEP),
            offset=ft.Offset(0, 4),
        ),
        animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Column(
            controls=[
                ui._rail_header_box,
                ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                ft.Column(
                    controls=rail_items,
                    spacing=RAIL_ITEM_SPACING,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                ft.Container(expand=True),
                ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                rail_bottom,
            ],
            spacing=10,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )
