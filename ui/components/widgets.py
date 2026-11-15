"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.9.0                               ║
║                  Widgets UI Reutilizables                            ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: ui/widgets.py
Descripción: Biblioteca de componentes UI reutilizables para la interfaz
            de Melomaniac. Proporciona botones, labels e iconos con
            estilos consistentes siguiendo el sistema de diseño OLED.

Componentes:
    - _section_label: Labels de sección con tipografía uppercase y tracking
    - _primary_btn: Botón primario con fondo de acento y elevación
    - _ghost_btn: Botón secundario con borde y fondo transparente
    - _status_icon: Iconos de estado para tracking de transferencias
    - debounce: Agrupador de eventos para buscadores que reconstruyen listas
    - limit_label: Texto de ayuda para listas de diálogo truncadas

Sistema de Diseño:
    Implementa un sistema de tokens de diseño consistente con:
    - Paleta de colores optimizada para OLED (negros profundos)
    - Tipografía IBM Plex Sans con pesos y tamaños específicos
    - Animaciones sutiles (120ms) para feedback táctil
    - Estados interactivos (default, hover, pressed, disabled)
    - Elevación y sombras para jerarquía visual

Autor: Melomaniac Team
Versión: 4.9.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import flet as ft

from ui.theme.fonts import (
    FONT_HEADLINE, FONT_HEADLINE_BOLD,
    FONT_TEXT, FONT_TEXT_MEDIUM, FONT_TEXT_SEMI, mono_family,
)
from ui.theme.icons import app_icon, PIcons

from ui.theme.tokens import (
    TEXT_PRIMARY, TEXT_MUTED,
    ACCENT, ACCENT_HOVER, ACCENT_DIM, ACCENT_HALO, BG_HOVER,
    BG_DEEP, BG_SURFACE, BORDER_ROW, BORDER_CONTROL,
    SUCCESS, ERROR_COL, WARNING, BORDER_MUTED, BORDER_LIGHT,
    BG_INPUT, BG_PANEL, WARN_BG, WARN_BORDER,
    ON_ACCENT, PAPER_BG, SHADOW_INK, SHADOW_INK_2,
    PAPER_LIFT, PAPER_LIFT_SM, PAPER_CANTO_PX,
    FOCUS_RING_WIDTH, FOCUS_RING_OFFSET, SELECTION_BAR_W,
    FS_META, RADIUS_SHARP, RADIUS_SM, HANKO, BG_INSET,
    SPACE_0, SPACE_1, SPACE_2, SPACE_3, SPACE_5,
    MOTION_HOVER_MS, MOTION_PRESS_MS,
)

FONT = FONT_TEXT
FONT_BOLD = FONT_HEADLINE_BOLD
FONT_MEDIUM = FONT_TEXT_MEDIUM
FONT_SEMI = FONT_TEXT_SEMI

_DEBOUNCE_TASKS: dict[Any, asyncio.Task] = {}


def control_page(ctrl: ft.Control | None) -> ft.Page | None:
    """Devuelve ``ctrl.page`` o ``None`` si el control aún no está montado en Flet."""
    if ctrl is None:
        return None
    try:
        return ctrl.page
    except Exception:
        return None


def is_mounted(control: object) -> bool:
    """Devuelve True solo si el control sigue adjunto a una Page de Flet."""
    return control_page(control) is not None  # type: ignore[arg-type]


def safe_update(control: object) -> bool:
    """Actualiza el control solo si está montado en una Page; ignora desconexiones."""
    if not is_mounted(control):
        return False
    try:
        control.update()
        return True
    except Exception:
        return False


def debounce(key: Any, fn: Callable[[], None], delay: float = 0.25) -> None:
    """Ejecuta ``fn`` una vez tras ``delay``; cancela la llamada previa de ``key``.

    Se usa en buscadores de diálogos que reconstruyen listas de controles.
    Si no hay loop activo, ejecuta de inmediato para no perder el evento.
    """
    previous = _DEBOUNCE_TASKS.get(key)
    if previous is not None and not previous.done():
        previous.cancel()

    async def _run() -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        finally:
            if _DEBOUNCE_TASKS.get(key) is asyncio.current_task():
                _DEBOUNCE_TASKS.pop(key, None)
        try:
            fn()
        except Exception:
            pass

    try:
        _DEBOUNCE_TASKS[key] = asyncio.create_task(_run())
    except RuntimeError:
        fn()


def fmt_ms(ms: int | None) -> str:
    """Duración ``m:ss`` a partir de milisegundos (``0:00`` si es 0)."""
    sec = max(0, int(ms or 0) // 1000)
    minutes, seconds = divmod(sec, 60)
    return f"{minutes}:{seconds:02d}"


def limit_label(shown: int, total: int) -> str:
    """Texto de ayuda canónico para listas de diálogo truncadas."""
    return (
        f"Mostrando {shown} de {total} — escribe para filtrar"
        if shown < total else ""
    )


def app_text(
    text: str,
    size: int = 12,
    color: str = TEXT_PRIMARY,
    font_family: str = FONT,
    **kwargs,
) -> ft.Text:
    """Texto canónico para no repetir font_family/size/color inline."""
    return ft.Text(text, size=size, color=color, font_family=font_family, **kwargs)


def _as_icon(value, height: float | None = None) -> ft.Control:
    """Convierte un glifo suelto en un icono de prefijo/sufijo alineado.

    Los glifos de Phosphor son caracteres sueltos en el rango privado, así que
    se distinguen por ``str`` y longitud. Un ``ft.Control`` ya construido se
    devuelve tal cual.

    Material alinea el ``prefixIcon``/``suffixIcon`` al techo del hueco, así que
    el glifo queda desviado hacia arriba aunque su caja de línea esté bien
    centrada. Envolverlo en un ``Container`` con ``alignment=CENTER`` y la
    altura del campo lo devuelve al centro vertical.
    """
    if isinstance(value, str) and len(value) == 1 and ord(value) >= 0xE000:
        return ft.Container(
            content=app_icon(value, size=16, color=TEXT_MUTED),
            alignment=ft.Alignment.CENTER,
            width=20, height=height,
        )
    return value


def input_border(
    border_color: str = BORDER_LIGHT,
    focused_color: str = ACCENT,
    radius: float = RADIUS_SHARP,
    width: float = 1.0,
) -> dict:
    """Borde canónico OLED para TextField/Dropdown y Theme.input_decoration_theme.

    Reemplaza las props deprecadas border_color/focused_border_color/border_radius.
    """
    return {
        ft.ControlState.DEFAULT: ft.OutlineInputBorder(
            side=ft.BorderSide(color=border_color, width=width),
            border_radius=radius,
        ),
        ft.ControlState.FOCUSED: ft.OutlineInputBorder(
            side=ft.BorderSide(color=focused_color, width=width),
            border_radius=radius,
        ),
        ft.ControlState.DISABLED: ft.OutlineInputBorder(
            side=ft.BorderSide(color=BORDER_MUTED, width=width),
            border_radius=radius,
        ),
        ft.ControlState.ERROR: ft.OutlineInputBorder(
            side=ft.BorderSide(color=ERROR_COL, width=width),
            border_radius=radius,
        ),
    }


def build_input_decoration_theme() -> ft.InputDecorationTheme:
    """Tema centralizado OLED para TextField/Dropdown (usado en app.py)."""
    return ft.InputDecorationTheme(
        border=input_border(BORDER_LIGHT, ACCENT, RADIUS_SHARP),
        filled=True,
        fill_color=BG_INPUT,
        dense=True,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        hint_style=ft.TextStyle(color=TEXT_MUTED, size=11),
        label_style=ft.TextStyle(color=TEXT_MUTED, size=FS_META, font_family=FONT_HEADLINE),
        floating_label_style=ft.TextStyle(color=ACCENT, size=FS_META, font_family=FONT_HEADLINE),
    )


_FOCUSED_INPUT: ft.TextField | None = None


def track_input_focus(field: ft.TextField) -> ft.TextField:
    """Registra foco/desenfoque del campo.

    En Flet 1.1.2 ``KeyboardEvent.control`` siempre es la ``Page``, por lo que
    el foco de entrada se rastrea con ``on_focus``/``on_blur`` de cada campo.
    """
    prev_focus, prev_blur = field.on_focus, field.on_blur

    def _focus(e):
        global _FOCUSED_INPUT
        _FOCUSED_INPUT = field
        if prev_focus is not None:
            return prev_focus(e)

    def _blur(e):
        global _FOCUSED_INPUT
        if _FOCUSED_INPUT is field:
            _FOCUSED_INPUT = None
        if prev_blur is not None:
            return prev_blur(e)

    field.on_focus, field.on_blur = _focus, _blur
    return field


def focused_input() -> ft.TextField | None:
    """Campo de texto con foco actual (o ``None``)."""
    return _FOCUSED_INPUT


def app_text_field(
    label: str | None = None,
    hint_text: str | None = None,
    value: str = "",
    *,
    password: bool = False,
    can_reveal_password: bool = False,
    multiline: bool = False,
    min_lines: int | None = None,
    max_lines: int | None = None,
    width: float | None = None,
    expand: bool = False,
    autofocus: bool = False,
    on_submit=None,
    content_padding=None,
    text_style: ft.TextStyle | None = None,
    border_radius: float = RADIUS_SHARP,
    **kwargs,
) -> ft.TextField:
    """TextField canónico OLED. Base: playlist_meta (radius 3, dense).

    Unifica playlist_meta._field_style + wizard._field_style/_make_field
    + main_ui paste/id/name fields y se alinea con InputDecorationTheme.
    """
    # Traduce props deprecadas si algún caller aún las pasa
    _bc = kwargs.pop("border_color", BORDER_CONTROL)
    _fbc = kwargs.pop("focused_border_color", ACCENT)
    _br = kwargs.pop("border", None)
    _bg = kwargs.pop("bgcolor", BG_INSET)
    border = _br if _br is not None else input_border(_bc, _fbc, border_radius)

    # La altura del campo es la referencia para centrar sus iconos.
    _icon_h = kwargs.get("height")

    prefix_icon = kwargs.pop("prefix_icon", None)
    if prefix_icon:
        kwargs["prefix_icon"] = _as_icon(prefix_icon, _icon_h)

    suffix_icon = kwargs.pop("suffix_icon", None)
    if suffix_icon:
        kwargs["suffix_icon"] = _as_icon(suffix_icon, _icon_h)
    return track_input_focus(ft.TextField(
        label=label,
        hint_text=hint_text,
        value=value,
        password=password,
        can_reveal_password=can_reveal_password,
        multiline=multiline,
        min_lines=min_lines,
        max_lines=max_lines,
        width=width,
        expand=expand,
        autofocus=autofocus,
        on_submit=on_submit,
        bgcolor=_bg,
        filled=kwargs.pop("filled", True),
        fill_color=kwargs.pop("fill_color", _bg),
        border=border,
        hint_style=kwargs.pop("hint_style", ft.TextStyle(color=TEXT_MUTED, size=11)),
        label_style=kwargs.pop(
            "label_style",
            ft.TextStyle(color=TEXT_MUTED, size=FS_META, font_family=FONT_HEADLINE),
        ),
        text_style=text_style or ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
        text_size=kwargs.pop("text_size", 12),
        dense=kwargs.pop("dense", True),
        content_padding=content_padding or ft.Padding.symmetric(horizontal=12, vertical=8),
        **kwargs,
    ))



def dialog_action(
    text: str,
    on_click,
    kind: str = "primary",
    icon: str | None = None,
) -> ft.TextButton:
    """Acción de diálogo canónica. kind: primary/muted/danger."""
    color = {"primary": ACCENT, "muted": TEXT_MUTED, "danger": WARNING}.get(kind, ACCENT)
    is_glyph = bool(icon and len(icon) == 1 and ord(icon) >= 0xE000)
    if is_glyph:
        content = ft.Row(
            [app_icon(icon, size=16, color=color), ft.Text(text, color=color, font_family=FONT_HEADLINE)],
            alignment=ft.MainAxisAlignment.CENTER,
            tight=True,
            spacing=SPACE_2,
        )
        return ft.TextButton(
            content=content,
            on_click=on_click,
            style=ft.ButtonStyle(color={ft.ControlState.DEFAULT: color}),
        )
    return ft.TextButton(
        text,
        icon=icon,
        on_click=on_click,
        style=ft.ButtonStyle(color={ft.ControlState.DEFAULT: color}),
    )


def app_dialog(
    title: str | ft.Control,
    content: ft.Control,
    actions: list[ft.Control],
    *,
    width: float = 400,
    icon: str | None = None,
    actions_alignment: ft.MainAxisAlignment = ft.MainAxisAlignment.END,
    bgcolor: str = BG_PANEL,
    radius: float = RADIUS_SM,
) -> ft.AlertDialog:
    """AlertDialog canónico OLED: modal + BG_PANEL + radius 6."""
    if isinstance(title, str):
        ic = icon or PIcons.INFO
        is_glyph = bool(ic and len(ic) == 1 and ord(ic) >= 0xE000)
        ico_ctrl = app_icon(ic, color=ACCENT, size=18) if is_glyph else ft.Icon(ic, color=ACCENT, size=18)
        title_ctrl: ft.Control = ft.Row(
            controls=[
                ico_ctrl,
                ft.Text(title, size=14, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            ],
            spacing=SPACE_2,
        )
    else:
        title_ctrl = title
    return ft.AlertDialog(
        modal=True,
        title=title_ctrl,
        content=ft.Container(content=content, width=width, padding=ft.Padding.only(top=6)),
        actions=actions,
        actions_alignment=actions_alignment,
        bgcolor=bgcolor,
        shape=ft.RoundedRectangleBorder(radius=radius),
    )


def organize_dropdown(
    options: list[tuple[str, str]],
    value: str,
    label: str,
    width: float = 200,
    **kwargs,
) -> ft.Dropdown:
    """Dropdown canónico para Organizar/Dividir. Alineado con InputDecorationTheme y SHRINK_WRAP."""
    _bc = kwargs.pop("border_color", BORDER_CONTROL)
    _fbc = kwargs.pop("focused_border_color", ACCENT)
    _br = kwargs.pop("border", None)
    _bg = kwargs.pop("bgcolor", BG_INSET)
    return ft.Dropdown(
        options=[ft.dropdown.Option(key=k, text=t) for k, t in options],
        value=value,
        label=label,
        width=width,
        bgcolor=_bg,
        filled=kwargs.pop("filled", True),
        fill_color=kwargs.pop("fill_color", _bg),
        dense=kwargs.pop("dense", True),
        content_padding=kwargs.pop(
            "content_padding", ft.Padding.symmetric(horizontal=12, vertical=8)
        ),
        border=_br if _br is not None else input_border(_bc, _fbc, RADIUS_SHARP),
        label_style=kwargs.pop(
            "label_style",
            ft.TextStyle(color=TEXT_MUTED, size=FS_META, font_family=FONT_HEADLINE),
        ),
        text_style=kwargs.pop(
            "text_style",
            ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
        ),
        text_size=kwargs.pop("text_size", 12),
        **kwargs,
    )


def show_dialog(page: ft.Page, dlg: ft.AlertDialog) -> None:
    """Abre ``dlg`` usando el canal estándar de ``page.show_dialog``."""
    page.show_dialog(dlg)


def close_dialog(page: ft.Page, dlg: ft.AlertDialog | None) -> None:
    """Cierra ``dlg`` actualizando solo el diálogo (evita ``page.update()`` completo)."""
    if dlg is None:
        return
    try:
        dlg.open = False
        if control_page(dlg) is not None:
            dlg.update()
        else:
            page.update()
    except Exception:
        try:
            page.update()
        except Exception:
            pass


def notify(
    page: ft.Page,
    msg: str,
    kind: str = "info",
    *,
    action: str | None = None,
    on_action=None,
    duration: int | None = None,
) -> ft.SnackBar:
    """SnackBar canónico FLOATING. Unifica telemetry + main_ui x3."""
    from ui.theme.tokens import BG_PANEL as _BG_PANEL, ERROR_COL as _ERR
    bgcolor = _ERR if kind == "error" else _BG_PANEL
    snack = ft.SnackBar(
        content=ft.Text(msg, color=ft.Colors.WHITE, font_family=FONT_TEXT, size=12, opacity=1.0),
        bgcolor=bgcolor,
        duration=duration or (6000 if action else 3500),
        behavior=ft.SnackBarBehavior.FLOATING,
        width=440 if action else 380,
        action=action,
        on_action=on_action,
        show_close_icon=True,
        close_icon_color=ACCENT,
    )
    show_fn = getattr(page, "show_dialog", None)
    if callable(show_fn):
        try:
            show_fn(snack)
            return snack
        except Exception:
            pass
    page.overlay.append(snack)
    snack.open = True
    page.update()
    return snack


def status_banner(
    message: str,
    *,
    icon: str = PIcons.INFO,
    kind: str = "warning",
    action: ft.Control | None = None,
) -> ft.Container:
    """Banner persistente de estado, con acción opcional.

    A diferencia de ``notify`` no desaparece solo: sirve para condiciones que
    el usuario debe resolver (FFmpeg ausente, rate limit en curso). El texto
    queda en ``container.content.controls[1]`` para poder actualizarlo sin
    reconstruir el banner.
    """
    accent = WARNING if kind == "warning" else ERROR_COL
    is_glyph = bool(icon and len(icon) == 1 and ord(icon) >= 0xE000)
    icon_ctrl = (
        app_icon(icon, color=accent, size=14)
        if is_glyph
        else ft.Icon(icon, color=accent, size=14)
    )
    row_controls: list[ft.Control] = [
        icon_ctrl,
        ft.Text(message, size=FS_META, color=accent, font_family=mono_family("light"), opacity=1.0),
    ]
    if action is not None:
        row_controls.extend([ft.Container(expand=True), action])
    return ft.Container(
        content=ft.Row(row_controls, spacing=SPACE_2,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=WARN_BG if kind == "warning" else BG_PANEL,
        border=ft.Border.all(0.8, WARN_BORDER if kind == "warning" else accent),
        border_radius=RADIUS_SHARP,
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        visible=False,
    )


def _section_label(text: str) -> ft.Text:
    """
    Crea un label de sección con tipografía uppercase y letter-spacing.
    
    Utilizado para encabezados de secciones y categorías en la UI.
    Implementa tipografía condensada con tracking amplio (1.4px) para
    máxima legibilidad en tamaños pequeños.
    
    Args:
        text: Texto del label (se renderiza en uppercase automáticamente).
    
    Returns:
        Componente ft.Text configurado con estilos de sección.
    
    Example:
        >>> label = _section_label("PLAYLISTS")
        >>> # Renderiza: "PLAYLISTS" en gris dim con tracking amplio
    
    Note:
        El letter-spacing de 1.4px es crítico para legibilidad en
        tamaños de fuente pequeños (9pt). Sin tracking, las letras
        uppercase se perciben aglomeradas.
    """
    return ft.Text(
        text, size=FS_META, color=TEXT_MUTED,
        font_family=FONT_HEADLINE_BOLD,
        style=ft.TextStyle(letter_spacing=1.4),
        opacity=1.0,
    )


def section_label(text: str) -> ft.Text:
    """Alias público de _section_label para reutilizar sin guion bajo."""
    return _section_label(text)


def _primary_btn(
    text: str,
    icon: str,
    on_click,
    width=None,
    height=None,
    *,
    disabled: bool = False,
) -> ft.Button:
    """
    Crea un botón primario con fondo de acento y elevación en hover.
    
    Botón de acción principal con estados interactivos completos:
    - Default: Fondo azul acento sin elevación
    - Hover: Fondo azul claro con elevación 6 y sombra de halo
    - Pressed: Fondo azul oscuro sin elevación
    - Disabled: Fondo gris atenuado y texto muted
    
    Args:
        text: Texto del botón.
        icon: Glifo Unicode de Phosphor (ej: PIcons.PLAY) o icono de Flet.
        on_click: Callback ejecutado al hacer clic.
        width: Ancho opcional del botón en píxeles.
        height: Alto opcional del botón en píxeles.
    
    Returns:
        Componente ft.Button configurado con estilos primarios.
    
    Example:
        >>> btn = _primary_btn(
        ...     "Transferir",
        ...     PIcons.ARROWS_LEFT_RIGHT,
        ...     on_click=lambda e: print("Transferir")
        ... )
    
    Note:
        La elevación en hover (6px) con sombra de halo proporciona feedback
        táctil visual que indica interactividad. La animación de 120ms
        es suficientemente rápida para sentirse responsiva sin ser abrupta.
    """
    is_glyph = bool(icon and len(icon) == 1 and ord(icon) >= 0xE000)
    if is_glyph:
        btn_content = ft.Row(
            [app_icon(icon, size=18, color=ON_ACCENT), ft.Text(text, opacity=1.0, font_family=FONT_HEADLINE)],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            spacing=SPACE_2,
        )
        btn_icon = None
    else:
        btn_content = ft.Text(text, opacity=1.0, font_family=FONT_HEADLINE)
        btn_icon = icon

    pad_v = 6 if (height and height <= 34) else 12
    return ft.Button(
        content=btn_content,
        icon=btn_icon,
        on_click=on_click,
        style=ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DEFAULT: ACCENT,
                ft.ControlState.HOVERED: ACCENT_HOVER,
                ft.ControlState.PRESSED: ACCENT_DIM,
                ft.ControlState.DISABLED: BG_HOVER,
            },
            color={ft.ControlState.DEFAULT: ON_ACCENT, ft.ControlState.DISABLED: TEXT_MUTED},
            # Relieve de papel: sombra dura de tinta que se hunde al pulsar.
            elevation={
                ft.ControlState.DEFAULT: PAPER_LIFT,
                ft.ControlState.HOVERED: PAPER_LIFT,
                ft.ControlState.PRESSED: 0,
                ft.ControlState.DISABLED: 0,
            },
            shadow_color=SHADOW_INK,
            shape=ft.RoundedRectangleBorder(radius=RADIUS_SHARP),
            padding=ft.Padding.symmetric(horizontal=16, vertical=pad_v),
            animation_duration=MOTION_PRESS_MS,
        ),
        width=width,
        height=height,
        disabled=disabled,
    )


def _ghost_btn(text: str, icon: str | None, on_click, width=None, height=None, disabled: bool = False) -> ft.OutlinedButton:
    """
    Crea un botón secundario con borde y fondo transparente.
    
    Botón de acción secundaria con énfasis visual reducido:
    - Default: Borde gris oscuro, texto muted
    - Hover: Borde azul acento, texto primary
    - Disabled: Opacidad reducida, no interactivo
    
    Args:
        text: Texto del botón.
        icon: Nombre del icono de Flet o glifo Unicode de PIcons.
        on_click: Callback ejecutado al hacer clic.
        width: Ancho opcional del botón en píxeles.
        height: Alto opcional del botón en píxeles.
        disabled: Si True, el botón se renderiza deshabilitado.
    
    Returns:
        Componente ft.OutlinedButton configurado con estilos ghost.
    
    Example:
        >>> btn = _ghost_btn(
        ...     "Cancelar",
        ...     PIcons.X,
        ...     on_click=lambda e: print("Cancelar"),
        ...     disabled=False
        ... )
    
    Note:
        Los botones ghost son ideales para acciones secundarias o
        destructivas que no deben competir visualmente con la acción
        primaria. El cambio de borde a acento en hover proporciona
        feedback claro sin ser intrusivo.
    """
    is_glyph = bool(icon and len(icon) == 1 and ord(icon) >= 0xE000)
    if is_glyph:
        btn_content = ft.Row(
            [app_icon(icon, size=18, color=TEXT_MUTED), ft.Text(text, opacity=1.0, font_family=FONT_HEADLINE)],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            spacing=SPACE_2,
        )
        btn_icon = None
    else:
        btn_content = ft.Text(text, opacity=1.0, font_family=FONT_HEADLINE)
        btn_icon = icon

    pad_v = 6 if (height and height <= 34) else 12
    return ft.OutlinedButton(
        content=btn_content,
        icon=btn_icon,
        on_click=on_click,
        disabled=disabled,
        style=ft.ButtonStyle(
            color={
                ft.ControlState.DEFAULT: TEXT_MUTED,
                ft.ControlState.HOVERED: TEXT_PRIMARY,
            },
            side={
                ft.ControlState.DEFAULT: ft.BorderSide(1, BORDER_CONTROL),
                ft.ControlState.HOVERED: ft.BorderSide(0.8, ACCENT),
            },
            shape=ft.RoundedRectangleBorder(radius=RADIUS_SHARP),
            padding=ft.Padding.symmetric(horizontal=14, vertical=pad_v),
            animation_duration=MOTION_PRESS_MS,
        ),
        width=width,
        height=height,
    )


def set_button_text(btn: ft.Control | None, text: str, *, icon: str | None = None) -> None:
    """Actualiza el texto (y opcionalmente el glifo) de un botón creado con ``_primary_btn``, ``_ghost_btn`` o ``dialog_action``."""
    if btn is None:
        return
    content = getattr(btn, "content", None)
    if isinstance(content, ft.Row) and content.controls:
        if icon is not None and len(content.controls) >= 2 and hasattr(content.controls[0], "value"):
            content.controls[0].value = icon
        last = content.controls[-1]
        if hasattr(last, "value"):
            last.value = text
            return
    if hasattr(content, "value"):
        content.value = text
        return
    try:
        btn.text = text  # type: ignore[attr-defined]
    except Exception:
        pass



def _status_icon(status: str) -> ft.Control:
    """
    Retorna un icono de estado para tracking visual de transferencias.
    
    Mapea estados de transferencia a iconos y colores semánticos:
    - found: Check verde (canción encontrada en plataforma destino)
    - not_found: X roja (canción no encontrada)
    - searching: Loop azul (búsqueda en progreso)
    - transferred: Cloud verde (transferencia completada)
    - error: Error rojo (fallo en transferencia)
    - pending: Radio button gris (pendiente de procesar)
    - local_pending: Folder naranja (cargado desde archivo local)
    - revision_necesaria: Flag naranja (requiere revisión manual)
    
    Args:
        status: String identificando el estado de la canción.
    
    Returns:
        Componente ft.Icon con icono y color apropiados.
    
    Example:
        >>> icon = _status_icon("found")
        >>> # Retorna: Icono de check verde
        >>> icon = _status_icon("revision_necesaria")
        >>> # Retorna: Icono de flag naranja
    
    Note:
        Los colores semánticos son críticos para escaneo visual rápido
        en listas largas de canciones. Verde = éxito, Rojo = error,
        Naranja = atención requerida, Azul = en progreso, Gris = pendiente.
        
        El tamaño de 15px está optimizado para alineación vertical con
        texto de 13-14px en filas de canciones.
    """
    icons = {
        "found":                (PIcons.CHECK_CIRCLE,     SUCCESS,   True),
        "not_found":            (PIcons.X,                ERROR_COL, False),
        "searching":            (PIcons.ARROWS_CLOCKWISE, ACCENT,    False),
        "transferred":          (PIcons.CHECK_CIRCLE,     SUCCESS,   True),
        "error":                (PIcons.WARNING_CIRCLE,   ERROR_COL, True),
        "pending":              (PIcons.CLOCK,            TEXT_MUTED, False),
        "local_pending":        (PIcons.FOLDER,           WARNING,   False),
        "revision_necesaria":   (PIcons.WARNING,          WARNING,   True),
    }
    ico, col, fill = icons.get(status, (PIcons.CLOCK, TEXT_MUTED, False))
    return app_icon(ico, color=col, size=15, fill=fill)


def open_external(page: ft.Page | None, url: str) -> None:
    """Abre una URL externa resolviendo que ``ft.Page.launch_url`` es coroutine.

    Programa ``page.launch_url(url)`` en el loop activo y usa ``webbrowser.open``
    como fallback si no hay loop o si ``page`` no está disponible.
    """
    if not url:
        return
    result = None
    try:
        if page is not None and hasattr(page, "launch_url"):
            result = page.launch_url(url)
    except Exception:
        result = None
    if asyncio.iscoroutine(result):
        try:
            asyncio.get_running_loop().create_task(result)
            return
        except RuntimeError:
            try:
                result.close()
            except Exception:
                pass
    elif result is not None:
        return
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass


# ── Relieve de papel, selección, chips, obi y foco ─────────────────────


def paper_shadow(
    layers: int = 1,
    lift: float = PAPER_LIFT,
    *,
    pressed: bool = False,
) -> list[ft.BoxShadow]:
    """Sombra dura con luz única arriba-izquierda (+x, +y) según gramática del demo."""
    if pressed:
        return [
            ft.BoxShadow(
                blur_radius=0,
                spread_radius=0,
                offset=ft.Offset(1, 1),
                color=SHADOW_INK,
            )
        ]
    shadows = [
        ft.BoxShadow(
            blur_radius=0,
            spread_radius=0,
            offset=ft.Offset(lift, lift),
            color=SHADOW_INK,
        )
    ]
    if layers >= 2:
        shadows.append(
            ft.BoxShadow(
                blur_radius=0,
                spread_radius=0,
                offset=ft.Offset(lift * 2, lift * 2),
                color=SHADOW_INK_2,
            )
        )
    return shadows


def paper_border(
    color: str = BORDER_CONTROL,
    *,
    canto_color: str = BORDER_LIGHT,
    side_width: float = 0.8,
    canto_width: float = PAPER_CANTO_PX,
) -> ft.Border:
    """Borde de hoja con filete inferior (canto de 2 px) para tarjetas y platters."""
    return ft.Border(
        left=ft.BorderSide(side_width, color),
        top=ft.BorderSide(side_width, color),
        right=ft.BorderSide(side_width, color),
        bottom=ft.BorderSide(canto_width, canto_color),
    )


def apply_paper_relief(
    control: ft.Container,
    *,
    layers: int = 1,
    lift: float = PAPER_LIFT,
    border_color: str = BORDER_CONTROL,
    canto_color: str = BORDER_LIGHT,
    side_width: float = 0.8,
    canto: bool = True,
    pressed: bool = False,
) -> ft.Container:
    """Aplica sombra dura (+x, +y) y canto inferior de 2 px a un ``ft.Container``."""
    control.shadow = paper_shadow(layers=layers, lift=lift, pressed=pressed)
    if canto:
        control.border = paper_border(
            border_color,
            canto_color=canto_color,
            side_width=side_width,
        )
    return control


def selection_border(
    selected: bool,
    *,
    base_color: str = BORDER_ROW,
    bar_color: str = ACCENT,
    bar_width: float = SELECTION_BAR_W,
    bottom_width: float = 0.5,
) -> ft.Border:
    """Marca de selección de 2 px a la izquierda sin fondo tintado en filas."""
    return ft.Border(
        left=ft.BorderSide(bar_width, bar_color if selected else ft.Colors.TRANSPARENT),
        bottom=ft.BorderSide(bottom_width, base_color),
    )


def status_chip(
    label: str,
    *,
    kind: str = "neutral",
    icon: str | None = None,
) -> ft.Container:
    """Chip de estado compacto (``ok``/``warn``/``err``/``accent``/``neutral``)."""
    palette = {
        "ok": SUCCESS,
        "warn": WARNING,
        "err": ERROR_COL,
        "accent": ACCENT,
        "neutral": TEXT_MUTED,
    }
    fg = palette.get(kind, TEXT_MUTED)
    border_col = fg if kind != "neutral" else BORDER_LIGHT
    controls: list[ft.Control] = []
    if icon:
        is_glyph = len(icon) == 1 and ord(icon) >= 0xE000
        controls.append(
            app_icon(icon, size=12, color=fg)
            if is_glyph
            else ft.Icon(icon, size=12, color=fg)
        )
    controls.append(
        ft.Text(
            label.upper(),
            size=FS_META,
            color=fg,
            font_family=mono_family("regular"),
            no_wrap=True,
            style=ft.TextStyle(letter_spacing=1.1),
        )
    )
    return ft.Container(
        content=ft.Row(
            controls,
            spacing=SPACE_1,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=6, vertical=3),
        border=ft.Border.all(0.8, border_col),
        border_radius=RADIUS_SHARP,
        bgcolor=ft.Colors.TRANSPARENT,
    )


def obi_strip(
    label: str,
    *,
    height: float | None = None,
    width: float = 15,
    bgcolor: str = PAPER_BG,
    text_color: str = BG_SURFACE,
) -> ft.Container:
    """Banda vertical tipo obi japonés para portadas y tarjetas de playlist."""
    txt = ft.Text(
        (label or "").strip().upper(),
        size=FS_META,
        color=text_color,
        font_family=mono_family("regular"),
        no_wrap=True,
        max_lines=1,
        overflow=ft.TextOverflow.CLIP,
        text_align=ft.TextAlign.CENTER,
        style=ft.TextStyle(letter_spacing=1.6),
    )
    rotated = ft.Container(
        content=txt,
        rotate=ft.Rotate(-math.pi / 2),
        alignment=ft.Alignment.CENTER,
    )
    return ft.Container(
        content=rotated,
        width=width,
        height=height,
        bgcolor=bgcolor,
        alignment=ft.Alignment.CENTER,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        shadow=[
            ft.BoxShadow(
                blur_radius=0,
                spread_radius=0,
                offset=ft.Offset(1, 0),
                color=ft.Colors.with_opacity(0.35, BG_DEEP),
            )
        ],
    )


def focus_ring_container(
    content: ft.Control,
    *,
    focused: bool = False,
    radius: float = RADIUS_SHARP,
    expand: bool = False,
    padding: float = FOCUS_RING_OFFSET,
    ring_width: float = FOCUS_RING_WIDTH,
    ring_color: str = ACCENT,
) -> ft.Container:
    """Envuelve un control con anillo de foco externo 2+2 px a 0 ms."""
    return ft.Container(
        content=content,
        padding=ft.Padding.all(padding),
        border=ft.Border.all(
            ring_width,
            ring_color if focused else ft.Colors.TRANSPARENT,
        ),
        border_radius=radius,
        expand=expand,
        animate=None,
    )


def set_focus_ring_container(
    wrapper: ft.Container,
    focused: bool,
    *,
    ring_color: str = ACCENT,
    ring_width: float = FOCUS_RING_WIDTH,
    update: bool = False,
) -> ft.Container:
    """Actualiza el anillo de foco 2+2 px de ``focus_ring_container`` sin animar."""
    wrapper.border = ft.Border.all(
        ring_width,
        ring_color if focused else ft.Colors.TRANSPARENT,
    )
    wrapper.animate = None
    if update:
        safe_update(wrapper)
    return wrapper


def apply_region_focus(
    control: ft.Container,
    focused: bool,
    *,
    base_border: ft.Border | None = None,
    base_color: str = BORDER_MUTED,
    base_width: float = 0.8,
    ring_color: str = ACCENT,
    ring_width: float = FOCUS_RING_WIDTH,
    inset: bool = False,
    update: bool = False,
) -> ft.Container:
    """Pinta el anillo de foco visible de región (0 ms, sin halo difuso en filas)."""
    if focused:
        control.border = ft.Border.all(ring_width, ring_color)
    else:
        control.border = (
            base_border
            if base_border is not None
            else ft.Border.all(base_width, base_color)
        )
    if inset:
        control.shadow = []
    control.animate = None
    if update:
        safe_update(control)
    return control




# ── Helpers Sala de escucha (Ola 1) ──────────────────────────────────────────

def kicker(text: str, color: str = TEXT_MUTED) -> ft.Text:
    """Etiqueta mono 11 px en mayúsculas con letter-spacing amplio."""
    return ft.Text(
        (text or "").upper(),
        size=11,
        color=color,
        font_family=mono_family("regular"),
        no_wrap=True,
        style=ft.TextStyle(letter_spacing=1.3),
    )


def tab_underline(
    items: Sequence[tuple[str, str]],
    active_key: str,
    on_change: Callable[[str], None],
) -> ft.Row:
    """Pestañas subrayadas: ``items`` = [(clave, etiqueta)]; activa con línea ACCENT de 2 px."""
    def _tab(key: str, label: str) -> ft.Container:
        active = key == active_key
        return ft.Container(
            content=ft.Text(
                label,
                size=13,
                color=TEXT_PRIMARY if active else TEXT_MUTED,
                font_family=FONT_SEMI if active else FONT_MEDIUM,
            ),
            padding=ft.Padding.only(left=2, right=2, top=6, bottom=8),
            border=ft.Border(bottom=ft.BorderSide(2, ACCENT if active else ft.Colors.TRANSPARENT)),
            ink=True,
            on_click=lambda e, k=key: on_change(k),
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
        )

    return ft.Row([_tab(k, l) for k, l in items], spacing=SPACE_5, tight=True)


def table_header(columns: Sequence[str | tuple[str, int | None]]) -> ft.Container:
    """Cabecera fija de tabla. Columna = ``"TÍTULO"`` o ``("TÍTULO", expand)``; sin expand se ajusta al contenido."""
    cells: list[ft.Control] = []
    for col in columns:
        label, expand = (col, 1) if isinstance(col, str) else col
        cells.append(ft.Container(content=kicker(label), expand=expand))
    return ft.Container(
        content=ft.Row(cells, spacing=SPACE_3, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border=ft.Border(bottom=ft.BorderSide(1, BORDER_ROW)),
    )


def empty_state(
    icon: str,
    title: str,
    subtitle: str = "",
    action_label: str | None = None,
    on_action=None,
) -> ft.Column:
    """Estado vacío alineado a la izquierda: icono Phosphor 24 px, título, frase y botón opcional."""
    controls: list[ft.Control] = [
        app_icon(icon, size=24, color=TEXT_MUTED),
        ft.Text(title, size=15, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
    ]
    if subtitle:
        controls.append(ft.Text(subtitle, size=12, color=TEXT_MUTED, font_family=FONT))
    if action_label and on_action:
        controls.append(_ghost_btn(action_label, None, on_action, height=32))
    return ft.Column(
        controls,
        spacing=SPACE_2,
        horizontal_alignment=ft.CrossAxisAlignment.START,
        tight=True,
    )


def hanko_seal(glyph: str, size: float = 18) -> ft.Container:
    """Sello cuadrado bermellón (radio 2 px) con un glifo."""
    return ft.Container(
        content=ft.Text(
            glyph,
            size=max(8, int(size * 0.6)),
            color=PAPER_BG,
            font_family=FONT_HEADLINE_BOLD,
            text_align=ft.TextAlign.CENTER,
        ),
        width=size,
        height=size,
        bgcolor=HANKO,
        border_radius=RADIUS_SHARP,
        alignment=ft.Alignment.CENTER,
    )


def collapsible_drawer(
    title: str,
    subtitle: str,
    content: ft.Control,
    expanded: bool = False,
) -> ft.Container:
    """Cajón colapsable de una línea («REGISTRO · N eventos»); clic en la cabecera alterna el contenido."""
    body = ft.Container(content=content, visible=expanded, padding=ft.Padding.only(top=8))
    caret = app_icon(PIcons.CARET_UP if expanded else PIcons.CARET_DOWN, size=14, color=TEXT_MUTED)

    def _toggle(e=None):
        body.visible = not body.visible
        caret.value = PIcons.CARET_UP if body.visible else PIcons.CARET_DOWN
        safe_update(body)
        safe_update(caret)

    header = ft.Container(
        content=ft.Row(
            [kicker(title), ft.Text(subtitle, size=11, color=TEXT_MUTED, font_family=FONT), ft.Container(expand=True), caret],
            spacing=SPACE_3,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        ink=True,
        on_click=_toggle,
    )
    return ft.Container(
        content=ft.Column([header, body], spacing=SPACE_0, tight=True),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border=ft.Border.all(1, BORDER_ROW),
        border_radius=RADIUS_SHARP,
    )


def platform_select(
    options: Sequence[tuple[str, str, str]],
    value: str,
    on_change: Callable[[str], None],
    label: str = "PLATAFORMA",
) -> ft.PopupMenuButton:
    """Selector de plataforma: ``options`` = [(clave, etiqueta, glifo PIcons)]; kicker + icono + caret."""
    by_key = {k: (l, g) for k, l, g in options}
    cur_label, cur_glyph = by_key.get(value, next(iter(by_key.values()), ("", PIcons.INFO)))
    face = ft.Container(
        content=ft.Row(
            [
                app_icon(cur_glyph, size=16, color=ACCENT),
                kicker(label),
                ft.Text(cur_label, size=12, color=TEXT_PRIMARY, font_family=FONT_MEDIUM),
                app_icon(PIcons.CARET_DOWN, size=14, color=TEXT_MUTED),
            ],
            spacing=SPACE_2,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        bgcolor=BG_INSET,
        border=ft.Border.all(1, BORDER_CONTROL),
        border_radius=RADIUS_SHARP,
    )
    return ft.PopupMenuButton(
        content=face,
        bgcolor=BG_PANEL,
        items=[
            ft.PopupMenuItem(
                content=ft.Row(
                    [app_icon(g, size=16, color=ACCENT if k == value else TEXT_MUTED),
                     ft.Text(l, size=12, color=TEXT_PRIMARY, font_family=FONT)],
                    spacing=SPACE_2,
                ),
                on_click=lambda e, key=k: on_change(key),
            )
            for k, l, g in options
        ],
    )
