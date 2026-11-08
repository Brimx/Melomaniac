"""Catálogo y helper unificado de iconos Phosphor para Melomaniac.

Provee glifos consistentes en pesos Regular, Fill y Duotone para toda la aplicación,
manteniendo los estilos alineados con los tokens de diseño (`ui/tokens.py`) y un
modelo de interacción moderno (luminancia + micro-escala, sin halos Material).
"""

from __future__ import annotations

from typing import Callable, Optional
import flet as ft

from ui.fonts import FONT_PHOSPHOR, FONT_PHOSPHOR_FILL, FONT_PHOSPHOR_DUOTONE
from ui.tokens import (
    TEXT_PRIMARY,
    TEXT_MUTED,
    MOTION_HOVER_MS,
)


class PIcons:
    """Glifos oficiales de Phosphor Icons (v2.1.2) mapeados a Unicode."""

    # Reproducción y Audio
    PLAY = "\ue3d0"  # play
    PAUSE = "\ue39e"  # pause
    SKIP_BACK = "\ue5a4"  # skip-back
    SKIP_FORWARD = "\ue5a6"  # skip-forward
    SHUFFLE = "\ue422"  # shuffle
    REPEAT = "\ue3f6"  # repeat
    REPEAT_ONCE = "\ue3f8"  # repeat-once
    SPEAKER_HIGH = "\ue44a"  # speaker-high
    SPEAKER_LOW = "\ue44c"  # speaker-low
    SPEAKER_SLASH = "\ue45a"  # speaker-slash
    SPEAKER_NONE = "\ue44e"  # speaker-none
    EQUALIZER = "\uebbc"  # equalizer
    WAVEFORM = "\ue802"  # waveform
    QUEUE = "\ue6ac"  # queue
    DISC = "\ue564"  # disc
    MUSIC_NOTE = "\ue33c"  # music-note
    MUSIC_NOTES = "\ue340"  # music-notes

    # Navegación y Rail
    ARROWS_LEFT_RIGHT = "\ue0a0"  # arrows-left-right (transferencia)
    BOOKS = "\ue758"  # books (biblioteca)
    PLAYLIST = "\ue6aa"  # playlist
    DOWNLOAD_SIMPLE = "\ue20c"  # download-simple (descargas)
    GEAR = "\ue270"  # gear (configuración)
    SIDEBAR_SIMPLE = "\uec24"  # sidebar-simple (toggle rail)

    # Direcciones y Flechas
    ARROW_DOWN = "\ue03e"  # arrow-down
    ARROW_UP = "\ue08e"  # arrow-up
    ARROW_LEFT = "\ue058"  # arrow-left
    ARROW_RIGHT = "\ue06c"  # arrow-right
    CARET_DOWN = "\ue136"  # caret-down
    CARET_UP = "\ue13c"  # caret-up
    CARET_LEFT = "\ue138"  # caret-left
    CARET_RIGHT = "\ue13a"  # caret-right
    ARROW_SQUARE_OUT = "\ue5de"  # arrow-square-out (enlace externo)

    # Acciones y Edición
    MAGNIFYING_GLASS = "\ue30c"  # magnifying-glass (búsqueda)
    PLUS = "\ue3d4"  # plus
    MINUS = "\ue32a"  # minus
    X = "\ue4f6"  # x (cerrar / cancelar)
    TRASH = "\ue4a6"  # trash (eliminar)
    PENCIL_SIMPLE = "\ue3b4"  # pencil-simple (editar)
    ARROWS_CLOCKWISE = "\ue094"  # arrows-clockwise (refrescar / sync)
    FLOPPY_DISK = "\ue248"  # floppy-disk (guardar)
    EXPORT = "\ueaf0"  # export (exportar)
    SLIDERS_HORIZONTAL = "\ue434"  # sliders-horizontal (organizar / filtros)
    LIST = "\ue2f0"  # list
    DOTS_SIX_VERTICAL = "\ueae2"  # dots-six-vertical (drag handle)
    DOTS_THREE_VERTICAL = "\ue208"  # dots-three-vertical (menú contextual)

    # Archivos y Carpetas
    FOLDER = "\ue24a"  # folder
    FOLDER_OPEN = "\ue256"  # folder-open
    FOLDER_PLUS = "\ue258"  # folder-plus

    # Estados y Feedback
    CHECK = "\ue182"  # check
    CHECK_CIRCLE = "\ue184"  # check-circle
    WARNING = "\ue4e0"  # warning
    WARNING_CIRCLE = "\ue4e2"  # warning-circle
    INFO = "\ue2ce"  # info
    QUESTION = "\ue3e8"  # question

    # Seguridad y Accesos
    EYE = "\ue220"  # eye
    EYE_SLASH = "\ue224"  # eye-slash
    LOCK = "\ue2fa"  # lock
    KEY = "\ue2d6"  # key

    # Logos de Plataformas
    APPLE_LOGO = "\ue516"  # apple-logo
    SPOTIFY_LOGO = "\ue66e"  # spotify-logo
    YOUTUBE_LOGO = "\ue4fc"  # youtube-logo

    # Otros
    HEART = "\ue2a8"  # heart
    USER = "\ue4c2"  # user
    USERS = "\ue4d6"  # users
    CLOCK = "\ue19a"  # clock
    CALENDAR = "\ue108"  # calendar
    SHARE_NETWORK = "\ue408"  # share-network
    SPARKLE = "\ue6a2"  # sparkle
    BROADCAST = "\ue0f2"  # broadcast


def _resolve_font_family(fill: bool = False, duotone: bool = False) -> str:
    """Resuelve la familia de fuente según el peso solicitado."""
    if duotone:
        return FONT_PHOSPHOR_DUOTONE
    if fill:
        return FONT_PHOSPHOR_FILL
    return FONT_PHOSPHOR


def app_icon(
    glyph: str,
    size: float = 18,
    color: Optional[str] = None,
    fill: bool = False,
    duotone: bool = False,
    tooltip: Optional[str] = None,
    animate_opacity: Optional[int] = None,
) -> ft.Text:
    """Crea un glifo tipográfico de Phosphor alineado y estilizado.

    Args:
        glyph: Constante Unicode de `PIcons` (ej. `PIcons.PLAY`).
        size: Tamaño del glifo en píxeles (default 18).
        color: Color del icono (por defecto `TEXT_PRIMARY`).
        fill: True para usar la variante sólida/rellena.
        duotone: True para la variante Duotone.
        tooltip: Texto accesible de ayuda flotante.
        animate_opacity: Duración en ms para fundido suave (o None).
    """
    family = _resolve_font_family(fill=fill, duotone=duotone)
    return ft.Text(
        glyph,
        font_family=family,
        size=size,
        color=color or TEXT_PRIMARY,
        text_align=ft.TextAlign.CENTER,
        tooltip=tooltip,
        animate_opacity=animate_opacity,
    )


def app_icon_button(
    glyph: str,
    on_click: Optional[Callable] = None,
    fill: bool = False,
    duotone: bool = False,
    size: float = 18,
    icon_color: Optional[str] = None,
    hover_color_icon: Optional[str] = None,
    hover_scale: float = 1.08,
    tooltip: Optional[str] = None,
    width: Optional[float] = None,
    height: Optional[float] = None,
    padding: Optional[ft.Padding] = None,
    bgcolor: Optional[str] = None,
    border_radius: Optional[float | ft.BorderRadius] = None,
    visible: bool = True,
    on_hover: Optional[Callable] = None,
) -> ft.Container:
    """Crea un botón interactivo sin halos Material, con luminancia y micro-escala.

    Args:
        glyph: Constante Unicode de `PIcons`.
        on_click: Callback al hacer clic.
        fill: True para silueta sólida.
        duotone: True para duotone.
        size: Tamaño óptico del glifo.
        icon_color: Color en reposo (por defecto `TEXT_MUTED`).
        hover_color_icon: Color en hover (por defecto `TEXT_PRIMARY`).
        hover_scale: Escala en hover (default 1.08; usar 1.0 para desactivar).
        tooltip: Etiqueta accesible.
        width: Ancho del contenedor clickeable (None = tamaño intrínseco).
        height: Alto del contenedor clickeable (None = tamaño intrínseco).
        padding: Padding interno opcional.
        bgcolor: Fondo opcional.
        border_radius: Radio de borde opcional.
        on_hover: Callback adicional de hover si se requiere.
    """
    base_color = icon_color or TEXT_MUTED
    target_hover_color = hover_color_icon or TEXT_PRIMARY

    text_ctrl = app_icon(
        glyph=glyph,
        size=size,
        color=base_color,
        fill=fill,
        duotone=duotone,
    )

    def _handle_hover(e: ft.HoverEvent) -> None:
        is_h = e.data == "true" or e.data is True
        if hover_scale != 1.0:
            btn.scale = hover_scale if is_h else 1.0
        text_ctrl.color = target_hover_color if is_h else base_color
        if on_hover:
            on_hover(e)
        if btn.page:
            btn.update()

    btn = ft.Container(
        content=text_ctrl,
        alignment=ft.Alignment(0, 0),
        width=width,
        height=height,
        padding=padding,
        bgcolor=bgcolor,
        border_radius=border_radius,
        tooltip=tooltip,
        visible=visible,
        scale=1.0,
        animate_scale=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT) if hover_scale != 1.0 else None,
        on_click=on_click,
        on_hover=_handle_hover,
    )
    # Guarda referencia al texto interno para mutaciones dinámicas de icono/color
    btn.icon_text = text_ctrl  # type: ignore[attr-defined]
    return btn
