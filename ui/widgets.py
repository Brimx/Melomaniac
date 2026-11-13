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
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import flet as ft

from ui.fonts import (
    FONT_HEADLINE, FONT_HEADLINE_BOLD,
    FONT_TEXT, FONT_TEXT_MEDIUM, FONT_TEXT_SEMI, mono_family,
)
from ui.icons import app_icon, PIcons

from ui.tokens import (
    TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM,
    ACCENT, ACCENT_HOVER, ACCENT_DIM, ACCENT_HALO, BG_HOVER,
    BG_DEEP, BG_SURFACE, BORDER_ROW,
    SUCCESS, ERROR_COL, WARNING, BORDER_MUTED, BORDER_LIGHT,
    BG_INPUT, BG_PANEL, WARN_BG, WARN_BORDER,
    RADIUS_INPUT, RADIUS_BUTTON, RADIUS_DIALOG,
    MOTION_HOVER_MS,
)

FONT = FONT_TEXT
FONT_BOLD = FONT_HEADLINE_BOLD
FONT_MEDIUM = FONT_TEXT_MEDIUM
FONT_SEMI = FONT_TEXT_SEMI

_DEBOUNCE_TASKS: dict[Any, asyncio.Task] = {}


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
            content=app_icon(value, size=16, color=TEXT_DIM),
            alignment=ft.Alignment.CENTER,
            width=20, height=height,
        )
    return value


def input_border(
    border_color: str = BORDER_LIGHT,
    focused_color: str = ACCENT,
    radius: float = RADIUS_INPUT,
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
        border=input_border(BORDER_LIGHT, ACCENT, RADIUS_INPUT),
        filled=True,
        fill_color=BG_INPUT,
        dense=True,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        hint_style=ft.TextStyle(color=TEXT_DIM, size=11),
        label_style=ft.TextStyle(color=TEXT_MUTED, size=10, font_family=FONT_HEADLINE),
        floating_label_style=ft.TextStyle(color=ACCENT, size=10, font_family=FONT_HEADLINE),
    )


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
    border_radius: float = RADIUS_INPUT,
    **kwargs,
) -> ft.TextField:
    """TextField canónico OLED. Base: playlist_meta (radius 10, dense).

    Unifica playlist_meta._field_style + wizard._field_style/_make_field
    + main_ui paste/id/name fields y se alinea con InputDecorationTheme.
    """
    # Traduce props deprecadas si algún caller aún las pasa
    _bc = kwargs.pop("border_color", BORDER_LIGHT)
    _fbc = kwargs.pop("focused_border_color", ACCENT)
    _br = kwargs.pop("border", None)
    _bg = kwargs.pop("bgcolor", BG_INPUT)
    border = _br if _br is not None else input_border(_bc, _fbc, border_radius)

    # La altura del campo es la referencia para centrar sus iconos.
    _icon_h = kwargs.get("height")

    prefix_icon = kwargs.pop("prefix_icon", None)
    if prefix_icon:
        kwargs["prefix_icon"] = _as_icon(prefix_icon, _icon_h)

    suffix_icon = kwargs.pop("suffix_icon", None)
    if suffix_icon:
        kwargs["suffix_icon"] = _as_icon(suffix_icon, _icon_h)
    return ft.TextField(
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
        hint_style=kwargs.pop("hint_style", ft.TextStyle(color=TEXT_DIM, size=11)),
        label_style=kwargs.pop(
            "label_style",
            ft.TextStyle(color=TEXT_MUTED, size=10, font_family=FONT_HEADLINE),
        ),
        text_style=text_style or ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
        text_size=kwargs.pop("text_size", 12),
        dense=kwargs.pop("dense", True),
        content_padding=content_padding or ft.Padding.symmetric(horizontal=12, vertical=8),
        **kwargs,
    )


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
            spacing=6,
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
    radius: float = RADIUS_DIALOG,
) -> ft.AlertDialog:
    """AlertDialog canónico OLED: modal + BG_PANEL + radius 14."""
    if isinstance(title, str):
        ic = icon or PIcons.INFO
        is_glyph = bool(ic and len(ic) == 1 and ord(ic) >= 0xE000)
        ico_ctrl = app_icon(ic, color=ACCENT, size=18) if is_glyph else ft.Icon(ic, color=ACCENT, size=18)
        title_ctrl: ft.Control = ft.Row(
            controls=[
                ico_ctrl,
                ft.Text(title, size=14, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            ],
            spacing=8,
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
    _bc = kwargs.pop("border_color", BORDER_LIGHT)
    _fbc = kwargs.pop("focused_border_color", ACCENT)
    _br = kwargs.pop("border", None)
    _bg = kwargs.pop("bgcolor", BG_INPUT)
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
        border=_br if _br is not None else input_border(_bc, _fbc, RADIUS_INPUT),
        label_style=kwargs.pop(
            "label_style",
            ft.TextStyle(color=TEXT_MUTED, size=10, font_family=FONT_HEADLINE),
        ),
        text_style=kwargs.pop(
            "text_style",
            ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
        ),
        text_size=kwargs.pop("text_size", 12),
        **kwargs,
    )


class DialogMixin:
    """Ciclo de vida único para diálogos: show/close."""

    def show_dialog(self, page: ft.Page, dlg: ft.AlertDialog) -> None:
        page.show_dialog(dlg)

    def close_dialog(self, page: ft.Page, dlg: ft.AlertDialog | None) -> None:
        if dlg is None:
            return
        try:
            dlg.open = False
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
    from ui.tokens import BG_PANEL as _BG_PANEL, ERROR_COL as _ERR
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
        ft.Text(message, size=10, color=accent, font_family=mono_family("light"), opacity=1.0),
    ]
    if action is not None:
        row_controls.extend([ft.Container(expand=True), action])
    return ft.Container(
        content=ft.Row(row_controls, spacing=6,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=WARN_BG if kind == "warning" else BG_PANEL,
        border=ft.Border.all(0.8, WARN_BORDER if kind == "warning" else accent),
        border_radius=RADIUS_INPUT,
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
        text, size=9, color=TEXT_DIM,
        font_family=FONT_HEADLINE_BOLD,
        style=ft.TextStyle(letter_spacing=1.4),
        opacity=1.0,
    )


def section_label(text: str) -> ft.Text:
    """Alias público de _section_label para reutilizar sin guion bajo."""
    return _section_label(text)


def _primary_btn(text: str, icon: str, on_click, width=None, height=None) -> ft.Button:
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
            [app_icon(icon, size=18, color=TEXT_PRIMARY), ft.Text(text, opacity=1.0, font_family=FONT_HEADLINE)],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            spacing=8,
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
            color={ft.ControlState.DEFAULT: TEXT_PRIMARY, ft.ControlState.DISABLED: TEXT_MUTED},
            elevation={ft.ControlState.DEFAULT: 0, ft.ControlState.HOVERED: 6},
            shadow_color={ft.ControlState.HOVERED: ACCENT_HALO},
            shape=ft.RoundedRectangleBorder(radius=RADIUS_BUTTON),
            padding=ft.Padding.symmetric(horizontal=16, vertical=pad_v),
            animation_duration=MOTION_HOVER_MS,
        ),
        width=width,
        height=height,
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
            spacing=8,
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
                ft.ControlState.DEFAULT: ft.BorderSide(0.8, BORDER_MUTED),
                ft.ControlState.HOVERED: ft.BorderSide(0.8, ACCENT),
            },
            shape=ft.RoundedRectangleBorder(radius=RADIUS_BUTTON),
            padding=ft.Padding.symmetric(horizontal=14, vertical=pad_v),
            animation_duration=MOTION_HOVER_MS,
        ),
        width=width,
        height=height,
    )


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
        "pending":              (PIcons.CLOCK,            TEXT_DIM,  False),
        "local_pending":        (PIcons.FOLDER,           WARNING,   False),
        "revision_necesaria":   (PIcons.WARNING,          WARNING,   True),
    }
    ico, col, fill = icons.get(status, (PIcons.CLOCK, TEXT_DIM, False))
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
) -> ft.PopupMenuButton:
    """Botón «⋮» OLED con menú desplegable anclado nativamente al disparador."""
    btn = ft.PopupMenuButton(
        content=ft.Container(
            content=app_icon(icon, size=icon_size, color=icon_color),
            width=width,
            height=height,
            alignment=ft.Alignment.CENTER,
            border_radius=RADIUS_INPUT,
        ),
        items=build_popup_menu_items(_resolve_actions(actions)),
        tooltip=tooltip,
        bgcolor=BG_SURFACE,
        shadow_color=ACCENT_HALO,
        elevation=6,
        shape=ft.RoundedRectangleBorder(radius=RADIUS_INPUT),
        menu_padding=ft.Padding.symmetric(vertical=4),
        padding=ft.Padding.all(0),
        visible=visible,
    )

    def _refresh_items(_e=None) -> None:
        if callable(actions):
            btn.items = build_popup_menu_items(_resolve_actions(actions))
            try:
                btn.update()
            except Exception:
                pass

    btn.on_open = _refresh_items
    btn.refresh_items = _refresh_items  # type: ignore[attr-defined]
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


