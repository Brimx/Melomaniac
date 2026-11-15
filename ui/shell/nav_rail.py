"""ui/shell/nav_rail.py — Navigation rail colapsable (68↔220 px) y cambio de módulos."""

from __future__ import annotations

import asyncio
import logging

import flet as ft

log = logging.getLogger(__name__)

from core.config import APP_VERSION
from core.models import TransferState
from services import settings_store
from ui.components.widgets import apply_region_focus, safe_update
from ui.theme.fonts import (
    FONT_HEADLINE,
    FONT_HEADLINE_BOLD,
    FONT_HEADLINE_MEDIUM,
    brand_family,
    mono_family,
)
from ui.theme.icons import FONT_PHOSPHOR, FONT_PHOSPHOR_FILL, PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    ACCENT_HALO,
    BG_DEEP,
    BG_HOVER,
    BORDER_LIGHT,
    BORDER_MUTED,
    DECOR_LINE,
    FOCUS_RING_WIDTH,
    FS_META,
    MOTION_HOVER_MS,
    ON_ACCENT,
    RADIUS_NONE,
    RADIUS_PILL,
    RADIUS_SHARP,
    RAIL_ANIM_MS,
    RAIL_BRAND_SLOT_SZ,
    RAIL_BRAND_W,
    RAIL_HEADER_H,
    RAIL_ITEM_H,
    RAIL_ITEM_PAD_V,
    RAIL_ITEM_SPACING,
    RAIL_LABEL_W,
    RAIL_PAD_H,
    RAIL_PAD_V,
    RAIL_VERSION_H,
    RAIL_W_COLLAPSED,
    RAIL_W_EXPANDED,
    SELECTION_BAR_W,
    SIDEBAR_BG,
    SPACE_0,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

HOME_MODULE_IDX = 4
_RAIL_COLLAPSED_KEY = "rail_collapsed"
_RAIL_AUTO_COLLAPSE_BP = 1100.0
_HOUSE_GLYPH = getattr(PIcons, "HOUSE", "\ue2c2")


def _read_rail_collapsed(page: ft.Page | None) -> bool:
    """Lee el estado persistido del rail y aplica auto-colapso bajo 1100 px."""
    page_w = 0.0
    if page is not None:
        try:
            page_w = float(getattr(page, "width", 0) or 0)
            if page_w <= 0:
                win = getattr(page, "window", None)
                page_w = float(getattr(win, "width", 0) or 0)
        except (TypeError, ValueError):
            page_w = 0.0
    if 0 < page_w < _RAIL_AUTO_COLLAPSE_BP:
        return True
    try:
        saved = settings_store.load_settings().get(_RAIL_COLLAPSED_KEY)
        if saved is not None:
            return bool(saved)
    except Exception as exc:
        log.debug("No se pudo leer estado del rail: %s", exc)
    return False


def _save_rail_collapsed_sync(collapsed: bool) -> None:
    try:
        data = settings_store.load_settings()
        data[_RAIL_COLLAPSED_KEY] = bool(collapsed)
        settings_store.save_settings(data)
    except Exception as exc:
        log.debug("No se pudo guardar estado del rail: %s", exc)


def _persist_rail_collapsed(collapsed: bool) -> None:
    """Persiste ``rail_collapsed`` fuera del hilo UI cuando hay loop activo."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _save_rail_collapsed_sync(collapsed)
        return
    loop.create_task(asyncio.to_thread(_save_rail_collapsed_sync, collapsed))


def _active_downloads_count(state) -> int:
    """Cuenta descargas activas/en espera para el badge del rail."""
    if state is None:
        return 0
    items = getattr(state, "download_items", None) or []
    active = sum(
        1
        for it in items
        if isinstance(it, dict) and it.get("status") in ("downloading", "pending", "paused")
    )
    if active > 0:
        return active
    if getattr(state, "download_state", None) == TransferState.RUNNING:
        total = int(getattr(state, "download_total", 0) or 0)
        prog = int(getattr(state, "download_progress", 0) or 0)
        return max(1, total - prog)
    return 0


def build_nav_rail(ui) -> None:
    """Construye el rail lateral colapsable (5 destinos) y sus handlers en ``ui``."""
    ui._current_module = 0  # 0=Gestión, 1=Biblioteca, 2=Descargas, 3=Config, 4=Inicio
    ui._rail_collapsed = _read_rail_collapsed(getattr(ui, "page", None))
    ui._rail_user_pref_collapsed = ui._rail_collapsed
    ui._rail_w_collapsed = RAIL_W_COLLAPSED
    ui._rail_w_expanded = RAIL_W_EXPANDED
    # Orden visual en el rail: Inicio arriba -> Gestión -> Biblioteca -> Descargas -> Config al pie.
    ui._rail_dests = [
        (HOME_MODULE_IDX, "Inicio", _HOUSE_GLYPH, _HOUSE_GLYPH),
        (0, "Gestión", PIcons.ARROWS_LEFT_RIGHT, PIcons.ARROWS_LEFT_RIGHT),
        (1, "Biblioteca", PIcons.BOOKS, PIcons.BOOKS),
        (2, "Descargas", PIcons.DOWNLOAD_SIMPLE, PIcons.DOWNLOAD_SIMPLE),
        (3, "Config", PIcons.GEAR, PIcons.GEAR),
    ]
    ui._rail_label_boxes = []
    ui._rail_item_refs = []
    ui._rail_focus_idx: int | None = None

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
            ui._current_module = prev_idx

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
    ui._rail_brand_slot = None
    ui._rail_version_text = None
    ui._rail_version_box = None
    ui._rail_dl_badge_expanded = None
    ui._rail_dl_badge_text = None
    ui._rail_dl_badge_dot = None

    _ACTIVE_TINT = ft.Colors.with_opacity(0.45, ACCENT_HALO)

    def _item_base_border(mod_idx: int) -> ft.Border:
        is_sel = (mod_idx == ui._current_module)
        return ft.Border.only(
            left=ft.BorderSide(SELECTION_BAR_W, ACCENT if is_sel else ft.Colors.TRANSPARENT)
        )

    def _rail_item(visual_idx: int, mod_idx: int, label: str, icon_out: str, icon_sel: str) -> ft.Container:
        is_sel = (mod_idx == ui._current_module)
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

        label_controls: list[ft.Control] = [txt]
        if mod_idx == 2:
            badge_txt = ft.Text(
                "0",
                size=FS_META,
                color=ON_ACCENT,
                font_family=mono_family("regular"),
                weight=ft.FontWeight.BOLD,
                no_wrap=True,
            )
            badge_pill = ft.Container(
                content=badge_txt,
                bgcolor=ACCENT,
                border_radius=RADIUS_SHARP,
                padding=ft.Padding.symmetric(horizontal=6, vertical=1),
                visible=False,
            )
            ui._rail_dl_badge_text = badge_txt
            ui._rail_dl_badge_expanded = badge_pill
            label_controls.append(badge_pill)

        label_row = ft.Row(
            label_controls,
            spacing=SPACE_1,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )

        # Solo animan ancho/margen/opacidad de la caja para que el colapso sea
        # un recorte progresivo y el icono permanezca fijo.
        label_box = ft.Container(
            content=label_row,
            width=RAIL_LABEL_W if not ui._rail_collapsed else 0,
            height=22,
            margin=ft.Margin.only(left=SPACE_3) if not ui._rail_collapsed else ft.Margin.only(left=0),
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
            color=ACCENT if is_sel else DECOR_LINE,
            fill=is_sel,
        )

        if mod_idx == 2:
            dot = ft.Container(
                width=8,
                height=8,
                border_radius=RADIUS_PILL,
                bgcolor=ACCENT,
                border=ft.Border.all(1, SIDEBAR_BG),
                right=0,
                top=0,
                visible=False,
            )
            ui._rail_dl_badge_dot = dot
            icon_slot: ft.Control = ft.Stack(
                controls=[
                    ft.Container(content=icon, alignment=ft.Alignment.CENTER, width=22, height=22),
                    dot,
                ],
                width=22,
                height=22,
            )
        else:
            icon_slot = icon

        row = ft.Row(
            [icon_slot, label_box],
            spacing=SPACE_0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            alignment=ft.MainAxisAlignment.START,
        )

        cont = ft.Container(
            content=row,
            bgcolor=_ACTIVE_TINT if is_sel else ft.Colors.TRANSPARENT,
            border=_item_base_border(mod_idx),
            border_radius=RADIUS_NONE,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=RAIL_ITEM_PAD_V),
            height=RAIL_ITEM_H,
            data=str(mod_idx),
            tooltip=label if ui._rail_collapsed else None,
            on_click=_on_rail_item_click,
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            alignment=ft.Alignment.CENTER_LEFT,
        )
        cont._rail_icon_ctrl = icon  # type: ignore[attr-defined]
        cont._rail_label_ctrl = txt  # type: ignore[attr-defined]
        cont._rail_mod_idx = mod_idx  # type: ignore[attr-defined]
        cont._rail_visual_idx = visual_idx  # type: ignore[attr-defined]

        def _on_hover(e: ft.HoverEvent) -> None:
            if mod_idx == ui._current_module:
                return
            is_h = e.data == "true" or e.data is True
            cont.bgcolor = BG_HOVER if is_h else ft.Colors.TRANSPARENT
            if ui._rail_focus_idx != visual_idx:
                cont.border = _item_base_border(mod_idx)
            icon.color = TEXT_PRIMARY if is_h else DECOR_LINE
            txt.color = TEXT_PRIMARY if is_h else TEXT_MUTED
            safe_update(cont)

        cont.on_hover = _on_hover
        ui._rail_item_refs.append(cont)
        return cont

    def _apply_rail_layout(*, expanded: bool, update: bool = True) -> None:
        ui._nav_rail.width = ui._rail_w_expanded if expanded else ui._rail_w_collapsed
        ui._rail_toggle_btn.tooltip = "Colapsar ([)" if expanded else "Expandir ([)"
        if ui._rail_brand_slot is not None:
            ui._rail_brand_slot.visible = not expanded
        if ui._rail_header_brand_box is not None:
            ui._rail_header_brand_box.width = RAIL_BRAND_W if expanded else 0
            ui._rail_header_brand_box.margin = (
                ft.Margin.only(left=SPACE_2) if expanded else ft.Margin.only(left=0)
            )
            ui._rail_header_brand_box.opacity = 1.0 if expanded else 0.0
        if ui._rail_header_box is not None:
            ui._rail_header_box.alignment = (
                ft.Alignment.CENTER_LEFT if expanded else ft.Alignment.CENTER
            )
        if ui._rail_version_text is not None:
            ui._rail_version_text.opacity = 0.7 if expanded else 0.0
        for i, label_box in enumerate(ui._rail_label_boxes):
            label_box.width = RAIL_LABEL_W if expanded else 0
            label_box.margin = (
                ft.Margin.only(left=SPACE_3) if expanded else ft.Margin.only(left=0)
            )
            label_box.opacity = 1.0 if expanded else 0.0
            if i < len(ui._rail_item_refs) and i < len(ui._rail_dests):
                ui._rail_item_refs[i].tooltip = None if expanded else ui._rail_dests[i][1]
        _refresh_rail_downloads_badge(update=False)
        if update:
            safe_update(ui._nav_rail)

    def _set_rail_collapsed(collapsed: bool, *, persist: bool = True, update: bool = True) -> None:
        collapsed_flag = bool(collapsed)
        if collapsed_flag == ui._rail_collapsed:
            return
        ui._rail_collapsed = collapsed_flag
        if persist:
            ui._rail_user_pref_collapsed = collapsed_flag
            _persist_rail_collapsed(collapsed_flag)
        _apply_rail_layout(expanded=not collapsed_flag, update=update)

    def _toggle_rail(_e=None) -> None:
        _set_rail_collapsed(not ui._rail_collapsed, persist=True, update=True)

    ui._set_rail_collapsed = _set_rail_collapsed
    ui._toggle_rail = _toggle_rail

    def _sync_rail_breakpoint(width: float) -> None:
        if width <= 0:
            return
        if width < _RAIL_AUTO_COLLAPSE_BP:
            _set_rail_collapsed(True, persist=False, update=True)
        elif not getattr(ui, "_rail_user_pref_collapsed", False):
            _set_rail_collapsed(False, persist=False, update=True)

    ui._sync_rail_breakpoint = _sync_rail_breakpoint

    # Cuadrado vacío reservado para marca cuando el rail está colapsado (sin monograma).
    ui._rail_brand_slot = ft.Container(
        width=RAIL_BRAND_SLOT_SZ,
        height=RAIL_BRAND_SLOT_SZ,
        border=ft.Border.all(1, BORDER_LIGHT),
        border_radius=RADIUS_NONE,
        bgcolor=ft.Colors.TRANSPARENT,
        tooltip="Melomaniac",
        visible=ui._rail_collapsed,
    )

    ui._rail_toggle_btn = app_icon_button(
        PIcons.SIDEBAR_SIMPLE,
        size=18,
        icon_color=TEXT_MUTED,
        hover_color_icon=TEXT_PRIMARY,
        tooltip="Expandir ([)" if ui._rail_collapsed else "Colapsar ([)",
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
        no_wrap=True,
        max_lines=1,
        overflow=ft.TextOverflow.CLIP,
    )
    ui._rail_version_text = ft.Text(
        f"v{APP_VERSION}",
        size=FS_META,
        color=TEXT_MUTED,
        font_family=mono_family("light"),
        visible=True,
        height=RAIL_VERSION_H,
        no_wrap=True,
        max_lines=1,
        opacity=0.7 if not ui._rail_collapsed else 0.0,
        animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
    )
    ui._rail_header_brand_box = ft.Container(
        content=ft.Column(
            controls=[ui._rail_header_brand, ui._rail_version_text],
            spacing=SPACE_0,
            tight=True,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        width=RAIL_BRAND_W if not ui._rail_collapsed else 0,
        height=RAIL_HEADER_H,
        margin=ft.Margin.only(left=SPACE_2) if not ui._rail_collapsed else ft.Margin.only(left=0),
        opacity=1.0 if not ui._rail_collapsed else 0.0,
        alignment=ft.Alignment.CENTER_LEFT,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
    )
    ui._rail_header_row = ft.Row(
        [ui._rail_toggle_btn, ui._rail_header_brand_box],
        spacing=SPACE_0,
        height=RAIL_HEADER_H,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.START,
    )
    # Altura fija de cabecera para que los destinos no salten al colapsar/expandir.
    ui._rail_header_box = ft.Container(
        height=RAIL_HEADER_H + RAIL_BRAND_SLOT_SZ,
        content=ft.Column(
            controls=[ui._rail_brand_slot, ui._rail_header_row],
            spacing=SPACE_1,
            tight=True,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.Alignment.CENTER if ui._rail_collapsed else ft.Alignment.CENTER_LEFT,
    )

    rail_items = [
        _rail_item(v_idx, mod_idx, lbl, ico, sel)
        for v_idx, (mod_idx, lbl, ico, sel) in enumerate(ui._rail_dests[:4])
    ]
    config_item = _rail_item(4, *ui._rail_dests[4])

    def _refresh_rail_selection() -> None:
        for v_idx, ((mod_idx, _lbl, ico, sel), cont) in enumerate(
            zip(ui._rail_dests, ui._rail_item_refs)
        ):
            is_sel = (mod_idx == ui._current_module)
            is_foc = (ui._rail_focus_idx == v_idx)
            try:
                cont.bgcolor = _ACTIVE_TINT if is_sel else ft.Colors.TRANSPARENT
                if is_foc:
                    apply_region_focus(cont, True, ring_width=FOCUS_RING_WIDTH, inset=True)
                else:
                    cont.border = _item_base_border(mod_idx)
                    cont.animate = ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT)
                icon_ctrl = getattr(cont, "_rail_icon_ctrl", None)
                lbl_ctrl = getattr(cont, "_rail_label_ctrl", None)
                glyph = sel if is_sel else ico
                if icon_ctrl is not None:
                    icon_ctrl.value = glyph
                    icon_ctrl.font_family = FONT_PHOSPHOR_FILL if is_sel else FONT_PHOSPHOR
                    icon_ctrl.color = ACCENT if is_sel else DECOR_LINE
                if lbl_ctrl is not None:
                    lbl_ctrl.color = TEXT_PRIMARY if is_sel else TEXT_MUTED
                    lbl_ctrl.font_family = FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE
            except Exception as exc:
                log.debug("No se pudo refrescar selección de rail: %s", exc)

    ui._refresh_rail_selection = _refresh_rail_selection  # type: ignore

    def _set_rail_focus(visual_idx: int | None, *, update: bool = True) -> None:
        ui._rail_focus_idx = visual_idx
        for v_idx, cont in enumerate(ui._rail_item_refs):
            mod_idx = getattr(cont, "_rail_mod_idx", v_idx)
            is_foc = (visual_idx == v_idx)
            if is_foc:
                apply_region_focus(cont, True, ring_width=FOCUS_RING_WIDTH, inset=True)
            else:
                cont.border = _item_base_border(mod_idx)
                cont.animate = ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT)
        if update:
            safe_update(ui._nav_rail)

    ui._set_rail_focus = _set_rail_focus

    def _refresh_rail_downloads_badge(*, update: bool = True) -> None:
        count = _active_downloads_count(getattr(ui, "state", None))
        has_active = count > 0
        txt = getattr(ui, "_rail_dl_badge_text", None)
        pill = getattr(ui, "_rail_dl_badge_expanded", None)
        dot = getattr(ui, "_rail_dl_badge_dot", None)
        changed = False
        if txt is not None:
            new_val = str(count if count <= 99 else "99+")
            if txt.value != new_val:
                txt.value = new_val
                changed = True
        if pill is not None:
            show_pill = has_active and not ui._rail_collapsed
            if pill.visible != show_pill:
                pill.visible = show_pill
                changed = True
        if dot is not None:
            show_dot = has_active and ui._rail_collapsed
            if dot.visible != show_dot:
                dot.visible = show_dot
                changed = True
        if update and changed:
            safe_update(ui._nav_rail)

    ui._refresh_rail_downloads_badge = _refresh_rail_downloads_badge

    rail_bottom = ft.Column(
        controls=[config_item],
        spacing=SPACE_1,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    ui._nav_rail = ft.Container(
        width=ui._rail_w_expanded if not ui._rail_collapsed else ui._rail_w_collapsed,
        margin=ft.Margin.all(SPACE_0),
        bgcolor=SIDEBAR_BG,
        border=ft.Border.only(right=ft.BorderSide(1, BORDER_MUTED)),
        border_radius=RADIUS_NONE,
        padding=ft.Padding.symmetric(horizontal=RAIL_PAD_H, vertical=RAIL_PAD_V),
        animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=ft.Column(
            controls=[
                ui._rail_header_box,
                ft.Divider(height=1, color=BORDER_MUTED, thickness=1),
                ft.Column(
                    controls=rail_items,
                    spacing=RAIL_ITEM_SPACING,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                ft.Container(expand=True),
                ft.Divider(height=1, color=BORDER_MUTED, thickness=1),
                rail_bottom,
            ],
            spacing=SPACE_3,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )
    _refresh_rail_downloads_badge(update=False)
