"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.5.0                               ║
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

Sistema de Diseño:
    Implementa un sistema de tokens de diseño consistente con:
    - Paleta de colores optimizada para OLED (negros profundos)
    - Tipografía IBM Plex Sans con pesos y tamaños específicos
    - Animaciones sutiles (120ms) para feedback táctil
    - Estados interactivos (default, hover, pressed, disabled)
    - Elevación y sombras para jerarquía visual

Autor: Melomaniac Team
Versión: 4.5.0
Fecha: 2026
"""

from __future__ import annotations

import flet as ft

from ui.fonts import (
    FONT_HEADLINE, FONT_HEADLINE_BOLD,
    FONT_TEXT, FONT_TEXT_MEDIUM, FONT_TEXT_SEMI,
)

from ui.tokens import (
    TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM,
    ACCENT, ACCENT_HOVER, ACCENT_DIM, ACCENT_HALO, BG_HOVER,
    SUCCESS, ERROR_COL, WARNING, BORDER_MUTED, BORDER_LIGHT,
    BG_INPUT, BG_PANEL,
)

FONT = FONT_TEXT
FONT_BOLD = FONT_HEADLINE_BOLD
FONT_MEDIUM = FONT_TEXT_MEDIUM
FONT_SEMI = FONT_TEXT_SEMI


def app_text(
    text: str,
    size: int = 12,
    color: str = TEXT_PRIMARY,
    font_family: str = FONT,
    **kwargs,
) -> ft.Text:
    """Texto canónico para no repetir font_family/size/color inline."""
    return ft.Text(text, size=size, color=color, font_family=font_family, **kwargs)


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
    **kwargs,
) -> ft.TextField:
    """TextField canónico OLED. Base: playlist_meta (radius 10, dense).

    Unifica playlist_meta._field_style + wizard._field_style/_make_field
    + main_ui paste/id/name fields.
    """
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
        bgcolor=BG_INPUT,
        border_color=BORDER_LIGHT,
        focused_border_color=ACCENT,
        hint_style=ft.TextStyle(color=TEXT_DIM, size=11),
        label_style=ft.TextStyle(color=TEXT_MUTED, size=10, font_family=FONT_HEADLINE),
        text_style=text_style or ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
        text_size=12,
        dense=True,
        border_radius=10,
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
    radius: float = 14,
) -> ft.AlertDialog:
    """AlertDialog canónico OLED: modal + BG_PANEL + radius 14."""
    if isinstance(title, str):
        title_ctrl: ft.Control = ft.Row(
            controls=[
                ft.Icon(icon or ft.Icons.INFO_OUTLINED, color=ACCENT, size=18),
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
) -> ft.Dropdown:
    """Dropdown canónico para Organizar/Dividir. Unifica main_ui x2."""
    from ui.tokens import BG_INPUT as _IN, BORDER_LIGHT as _BL, ACCENT as _AC
    from ui.tokens import TEXT_PRIMARY as _TP, TEXT_MUTED as _TM
    return ft.Dropdown(
        options=[ft.dropdown.Option(key=k, text=t) for k, t in options],
        value=value, label=label, width=width,
        bgcolor=_IN, border_color=_BL, focused_border_color=_AC,
        label_style=ft.TextStyle(color=_TM, size=11, font_family=FONT_HEADLINE),
        text_style=ft.TextStyle(color=_TP, size=12, font_family=FONT_TEXT),
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
    
    Args:
        text: Texto del botón.
        icon: Nombre del icono de Flet (ej: ft.Icons.PLAY_ARROW).
        on_click: Callback ejecutado al hacer clic.
        width: Ancho opcional del botón en píxeles.
        height: Alto opcional del botón en píxeles.
    
    Returns:
        Componente ft.Button configurado con estilos primarios.
    
    Example:
        >>> btn = _primary_btn(
        ...     "Transferir",
        ...     ft.Icons.UPLOAD,
        ...     on_click=lambda e: print("Transferir")
        ... )
    
    Note:
        La elevación en hover (6px) con sombra de halo proporciona feedback
        táctil visual que indica interactividad. La animación de 120ms
        es suficientemente rápida para sentirse responsiva sin ser abrupta.
    """
    return ft.Button(
        content=ft.Text(text, opacity=1.0, font_family=FONT_HEADLINE),
        icon=icon,
        on_click=on_click,
        style=ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DEFAULT: ACCENT,
                ft.ControlState.HOVERED: ACCENT_HOVER,
                ft.ControlState.PRESSED: ACCENT_DIM,
            },
            color=TEXT_PRIMARY,
            elevation={ft.ControlState.DEFAULT: 0, ft.ControlState.HOVERED: 6},
            shadow_color={ft.ControlState.HOVERED: ACCENT_HALO},
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            animation_duration=120,
        ),
        width=width,
        height=height,
    )


def _ghost_btn(text: str, icon: str, on_click, width=None, height=None, disabled: bool = False) -> ft.OutlinedButton:
    """
    Crea un botón secundario con borde y fondo transparente.
    
    Botón de acción secundaria con énfasis visual reducido:
    - Default: Borde gris oscuro, texto muted
    - Hover: Borde azul acento, texto primary
    - Disabled: Opacidad reducida, no interactivo
    
    Args:
        text: Texto del botón.
        icon: Nombre del icono de Flet.
        on_click: Callback ejecutado al hacer clic.
        width: Ancho opcional del botón en píxeles.
        height: Alto opcional del botón en píxeles.
        disabled: Si True, el botón se renderiza deshabilitado.
    
    Returns:
        Componente ft.OutlinedButton configurado con estilos ghost.
    
    Example:
        >>> btn = _ghost_btn(
        ...     "Cancelar",
        ...     ft.Icons.CLOSE,
        ...     on_click=lambda e: print("Cancelar"),
        ...     disabled=False
        ... )
    
    Note:
        Los botones ghost son ideales para acciones secundarias o
        destructivas que no deben competir visualmente con la acción
        primaria. El cambio de borde a acento en hover proporciona
        feedback claro sin ser intrusivo.
    """
    return ft.OutlinedButton(
        content=ft.Text(text, opacity=1.0, font_family=FONT_HEADLINE),
        icon=icon,
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
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            animation_duration=120,
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
        "found":                (ft.Icons.CHECK_CIRCLE_OUTLINE,  SUCCESS),
        "not_found":            (ft.Icons.CANCEL_OUTLINED,        ERROR_COL),
        "searching":            (ft.Icons.LOOP,                   ACCENT),
        "transferred":          (ft.Icons.CLOUD_DONE_OUTLINED,    SUCCESS),
        "error":                (ft.Icons.ERROR_OUTLINE,          ERROR_COL),
        "pending":              (ft.Icons.RADIO_BUTTON_UNCHECKED, TEXT_DIM),
        "local_pending":        (ft.Icons.FOLDER_OPEN_OUTLINED,   WARNING),
        "revision_necesaria":   (ft.Icons.FLAG_OUTLINED,          WARNING),
    }
    ico, col = icons.get(status, (ft.Icons.RADIO_BUTTON_UNCHECKED, TEXT_DIM))
    return ft.Icon(ico, color=col, size=15)
