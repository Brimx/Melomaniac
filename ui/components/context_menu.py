"""Menús contextuales OLED bajo demanda para filas, tarjetas y enlaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import flet as ft

from ui.components.widgets import _as_icon
from ui.theme.fonts import FONT_HEADLINE, FONT_TEXT
from ui.theme.icons import PIcons, app_icon
from ui.theme.tokens import (
    BG_DEEP,
    BG_HOVER,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_ROW,
    ERROR_COL,
    MOTION_HOVER_MS,
    RADIUS_INPUT,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


@dataclass(frozen=True)
class ContextMenuAction:
    """Acción declarativa para menús contextuales OLED."""

    label: str
    icon: str | None = None
    on_click: Callable[[], Any] | None = None
    disabled: bool = False
    danger: bool = False
    visible: bool = True
    divider_before: bool = False


def _resolve_actions(
    actions: Sequence[ContextMenuAction] | Callable[[], Sequence[ContextMenuAction]],
) -> list[ContextMenuAction]:
    resolved = actions() if callable(actions) else actions
    return [a for a in resolved if a.visible]


def _invoke_menu_action(action: ContextMenuAction) -> None:
    if action.disabled or action.on_click is None:
        return
    action.on_click()


def _menu_action_color(action: ContextMenuAction) -> str:
    if action.disabled:
        return TEXT_DIM
    if action.danger:
        return ERROR_COL
    return TEXT_PRIMARY


def _menu_action_icon(action: ContextMenuAction, color: str) -> ft.Control | None:
    if not action.icon:
        return None
    is_glyph = len(action.icon) == 1 and ord(action.icon) >= 0xE000
    if is_glyph:
        return app_icon(action.icon, size=15, color=color)
    return ft.Icon(action.icon, size=15, color=color)


def build_popup_menu_items(
    actions: Sequence[ContextMenuAction],
) -> list[ft.PopupMenuItem]:
    """Construye ``PopupMenuItem`` estilizados con tokens OLED e iconos Phosphor."""
    items: list[ft.PopupMenuItem] = []
    for action in actions:
        if not action.visible:
            continue
        if action.divider_before and items:
            items.append(
                ft.PopupMenuItem(
                    content=ft.Divider(height=1, color=BORDER_ROW),
                    height=9,
                    disabled=True,
                )
            )
        color = _menu_action_color(action)
        row_controls: list[ft.Control] = []
        ico = _menu_action_icon(action, color)
        if ico is not None:
            row_controls.append(ico)
        row_controls.append(
            ft.Text(
                action.label,
                size=12,
                color=color,
                font_family=FONT_TEXT,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
        items.append(
            ft.PopupMenuItem(
                content=ft.Row(
                    controls=row_controls,
                    spacing=8,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                height=34,
                padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                disabled=action.disabled,
                on_click=lambda _e, act=action: _invoke_menu_action(act),
            )
        )
    return items


def build_context_menu_button(
    actions: Sequence[ContextMenuAction] | Callable[[], Sequence[ContextMenuAction]],
    *,
    tooltip: str = "Más opciones",
    icon: str = PIcons.DOTS_THREE_VERTICAL,
    icon_size: float = 16,
    icon_color: str = TEXT_MUTED,
    width: float = 28,
    height: float = 28,
    visible: bool = True,
    title: str | None = None,
    get_page: Callable[[], ft.Page | None] | None = None,
) -> ft.GestureDetector:
    """Botón «⋮» OLED ligero que abre ``show_context_menu`` bajo demanda."""
    ico_ctrl = app_icon(icon, size=icon_size, color=icon_color)
    box = ft.Container(
        content=ico_ctrl,
        width=width,
        height=height,
        alignment=ft.Alignment.CENTER,
        border_radius=RADIUS_INPUT,
        bgcolor=ft.Colors.TRANSPARENT,
        tooltip=tooltip,
        ink=True,
        animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
        animate_scale=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
    )
    tap_pos: dict[str, float | None] = {"x": None, "y": None}

    def _on_hover(e: ft.HoverEvent) -> None:
        hovering = e.data == "true" or e.data is True
        box.bgcolor = BG_HOVER if hovering else ft.Colors.TRANSPARENT
        box.scale = 1.08 if hovering else 1.0
        ico_ctrl.color = TEXT_PRIMARY if hovering else icon_color
        try:
            box.update()
        except Exception:
            pass

    def _open_menu(
        page: ft.Page | None = None,
        *,
        x: float | None = None,
        y: float | None = None,
    ) -> ft.Control | None:
        target_page = (
            page
            or (get_page() if get_page is not None else None)
            or getattr(btn, "page", None)
            or getattr(box, "page", None)
        )
        return show_context_menu(
            target_page,
            actions,
            title=title,
            x=tap_pos["x"] if x is None else x,
            y=tap_pos["y"] if y is None else y,
        )

    def _on_tap_down(e: ft.TapEvent) -> None:
        pos = getattr(e, "global_position", None)
        tap_pos["x"] = getattr(pos, "x", None)
        tap_pos["y"] = getattr(pos, "y", None)

    def _on_click(_e=None) -> None:
        _open_menu()

    box.on_hover = _on_hover
    box.on_click = _on_click
    btn = ft.GestureDetector(
        content=box,
        on_tap_down=_on_tap_down,
        visible=visible,
    )
    btn.actions = actions  # type: ignore[attr-defined]
    btn.open_menu = _open_menu  # type: ignore[attr-defined]
    btn.refresh_items = lambda _e=None: None  # type: ignore[attr-defined]
    return btn


def show_context_menu(
    page: ft.Page | None,
    actions: Sequence[ContextMenuAction] | Callable[[], Sequence[ContextMenuAction]],
    *,
    title: str | None = None,
    x: float | None = None,
    y: float | None = None,
    width: float = 224,
) -> ft.Control | None:
    """Muestra un menú contextual OLED propio en ``page.overlay``.

    Si se pasan coordenadas ``(x, y)`` (por ejemplo desde ``on_secondary_tap_down``),
    posiciona el menú anclado junto al puntero evitando desbordes de ventana.
    """
    visible_actions = _resolve_actions(actions)
    if not visible_actions:
        return None

    # Cierra cualquier menú contextual previo abierto en esta página.
    if page is not None:
        prev = getattr(page, "_active_context_menu", None)
        if prev is not None and hasattr(prev, "dismiss"):
            try:
                prev.dismiss(update_page=False)
            except TypeError:
                try:
                    prev.dismiss()
                except Exception:
                    pass
            except Exception:
                pass

    overlay_holder: dict[str, ft.Control | None] = {"control": None}
    host_holder: dict[str, ft.Container | None] = {"host": None}

    def _dismiss(_e=None, *, update_page: bool = True) -> None:
        ctrl = overlay_holder["control"]
        host = host_holder["host"]
        if page is not None:
            if getattr(page, "_active_context_menu", None) is ctrl:
                try:
                    setattr(page, "_active_context_menu", None)
                except Exception:
                    pass
            if host is not None:
                host.content = None
                host.visible = False
                if update_page:
                    try:
                        host.update()
                    except Exception:
                        pass
            else:
                overlay = getattr(page, "overlay", None)
                if overlay is not None and ctrl in overlay:
                    try:
                        overlay.remove(ctrl)
                    except ValueError:
                        pass
                if update_page:
                    try:
                        page.update()
                    except Exception:
                        pass

    card_controls: list[ft.Control] = []
    if title:
        card_controls.append(
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                content=ft.Text(
                    title,
                    size=10,
                    color=TEXT_MUTED,
                    font_family=FONT_HEADLINE,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            )
        )
        card_controls.append(ft.Divider(height=1, color=BORDER_ROW))

    divider_count = 0
    for action in visible_actions:
        if action.divider_before and len(card_controls) > (2 if title else 0):
            card_controls.append(ft.Divider(height=1, color=BORDER_ROW))
            divider_count += 1
        color = _menu_action_color(action)
        row_controls: list[ft.Control] = []
        ico = _menu_action_icon(action, color)
        if ico is not None:
            row_controls.append(ico)
        row_controls.append(
            ft.Text(
                action.label,
                size=12,
                color=color,
                font_family=FONT_TEXT,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
            )
        )

        item_ctrl = ft.Container(
            height=34,
            margin=ft.Margin.symmetric(horizontal=4),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            border_radius=6,
            bgcolor=ft.Colors.TRANSPARENT,
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            content=ft.Row(
                controls=row_controls,
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        def _make_hover(ctrl: ft.Container, is_disabled: bool):
            def _on_hover(e: ft.HoverEvent) -> None:
                if is_disabled:
                    return
                ctrl.bgcolor = BG_HOVER if (e.data == "true" or e.data is True) else ft.Colors.TRANSPARENT
                try:
                    ctrl.update()
                except Exception:
                    pass
            return _on_hover

        def _make_click(act: ContextMenuAction):
            def _on_click(_e=None) -> None:
                _dismiss()
                _invoke_menu_action(act)
            return _on_click

        if not action.disabled:
            item_ctrl.on_hover = _make_hover(item_ctrl, False)
            item_ctrl.on_click = _make_click(action)
        card_controls.append(item_ctrl)

    menu_card = ft.Container(
        width=width,
        bgcolor=BG_SURFACE,
        border=ft.Border.all(0.8, BORDER_LIGHT),
        border_radius=RADIUS_INPUT,
        padding=ft.Padding.symmetric(vertical=6),
        shadow=ft.BoxShadow(
            blur_radius=18,
            spread_radius=1,
            color=ft.Colors.with_opacity(0.65, BG_DEEP),
        ),
        content=ft.Column(controls=card_controls, spacing=0, tight=True),
    )

    backdrop = ft.GestureDetector(
        on_tap=_dismiss,
        on_secondary_tap_down=_dismiss,
        content=ft.Container(expand=True, bgcolor=ft.Colors.TRANSPARENT),
    )

    if x is not None and y is not None:
        page_w = float(getattr(page, "width", None) or 1200)
        page_h = float(getattr(page, "height", None) or 650)
        est_h = (28 if title else 0) + len(visible_actions) * 34 + divider_count * 1 + 14
        left = max(8.0, min(float(x), max(8.0, page_w - width - 8.0)))
        top = max(8.0, min(float(y), max(8.0, page_h - est_h - 8.0)))
        positioned: ft.Control = ft.Container(content=menu_card, left=left, top=top)
    else:
        positioned = ft.Container(content=menu_card, expand=True, alignment=ft.Alignment.CENTER)

    overlay_ctrl = ft.Stack(controls=[backdrop, positioned], expand=True)
    overlay_ctrl.dismiss = _dismiss  # type: ignore[attr-defined]
    overlay_ctrl.menu_card = menu_card  # type: ignore[attr-defined]
    overlay_holder["control"] = overlay_ctrl

    if page is not None and hasattr(page, "overlay"):
        try:
            setattr(page, "_active_context_menu", overlay_ctrl)
        except Exception:
            pass
        host = getattr(page, "_context_menu_host", None)
        if not isinstance(host, ft.Container):
            host = ft.Container(visible=False, expand=True)
            try:
                setattr(page, "_context_menu_host", host)
            except Exception:
                pass
        host_holder["host"] = host
        overlay = page.overlay
        if host not in overlay:
            overlay.append(host)
            host.content = overlay_ctrl
            host.visible = True
            try:
                page.update()
            except Exception:
                pass
        else:
            host.content = overlay_ctrl
            host.visible = True
            try:
                host.update()
            except Exception:
                try:
                    page.update()
                except Exception:
                    pass
    return overlay_ctrl


def wrap_with_context_menu(
    control: ft.Control,
    actions: Sequence[ContextMenuAction] | Callable[[], Sequence[ContextMenuAction]],
    *,
    title: str | None = None,
    get_page: Callable[[], ft.Page | None] | None = None,
) -> ft.GestureDetector:
    """Envuelve un control para abrir ``show_context_menu`` con clic secundario."""

    def _on_secondary(e: ft.TapEvent) -> None:
        target_page = get_page() if get_page is not None else getattr(control, "page", None)
        pos = getattr(e, "global_position", None)
        show_context_menu(
            target_page,
            actions,
            title=title,
            x=getattr(pos, "x", None),
            y=getattr(pos, "y", None),
        )

    return ft.GestureDetector(
        content=control,
        on_secondary_tap_down=_on_secondary,
    )


