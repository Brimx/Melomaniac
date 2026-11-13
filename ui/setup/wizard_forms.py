"""Specs declarativos y constructores de formularios para ConfigWizard."""

from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from services.authentication import ENV_KEYS_APPLE
from ui.components.widgets import (
    app_text_field,
    input_border,
    section_label,
)
from ui.theme.fonts import FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    OVERLAY_06,
    OVERLAY_10,
    OVERLAY_14,
    OVERLAY_18,
    SUCCESS,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARN_BG,
    WARN_BORDER,
    WARNING,
)

# ── Platform order and tab metadata (single source of truth) ───────────
PLATFORM_TAB_META = {
    "YouTube Music": (PIcons.YOUTUBE_LOGO, PIcons.WARNING),
    "Apple Music": (PIcons.APPLE_LOGO, PIcons.WARNING),
    "Spotify": (PIcons.SPOTIFY_LOGO, PIcons.WARNING),
}

PLATFORM_LOGIN_URLS = {
    "YouTube Music": "https://music.youtube.com",
    "Apple Music": "https://music.apple.com",
    "Spotify": "https://open.spotify.com",
}

PLATFORM_MANUAL_LABELS = {
    "YouTube Music": "config/browser.json",
    "Apple Music": "config/.env",
    "Spotify": "config/spotify_cookies.json",
}

# ── Declarative wizard field and instruction definitions ──────────────
YOUTUBE_FIELD_SPECS = (
    {
        "key": "Authorization",
        "label": "Authorization (SAPISIDHASH …)",
        "multiline": True,
        "expandable": True,
        "min_lines": 2,
        "max_lines": 3,
    },
    {
        "key": "Cookie",
        "label": "Cookie",
        "multiline": True,
        "expandable": True,
        "min_lines": 4,
        "max_lines": 6,
    },
)

APPLE_FIELD_SPECS = tuple(
    {
        "key": key,
        "password": True,
        "can_reveal_password": True,
    }
    for key in ENV_KEYS_APPLE
)

SPOTIFY_FIELD_SPECS = (
    {"key": "identifier", "label": "Identifier (email o username)"},
    {"key": "sp_dc", "password": True, "can_reveal_password": True, "saved_badge": True},
    {"key": "sp_key", "password": True, "can_reveal_password": True, "saved_badge": True},
)

YOUTUBE_INSTRUCTIONS = (
    (
        "Abre YouTube Music y pulsa F12",
        "Ve a la pestaña Network en DevTools.",
    ),
    (
        'Filtra por "browse"',
        "Escribe browse en la barra de filtro de Network.",
    ),
    (
        "Selecciona el POST de mayor peso",
        "Busca una solicitud con método POST (habitualmente browsing o browse).",
    ),
    (
        "Extrae Authorization",
        "En Headers → Request Headers copia el valor completo de Authorization "
        "(empieza con SAPISIDHASH …).",
    ),
    (
        "Extrae Cookie",
        "En la misma solicitud copia el valor completo del header Cookie.",
    ),
)

APPLE_INSTRUCTIONS = (
    (
        "Abre Apple Music Web y pulsa F12",
        "Ve a music.apple.com y abre DevTools.",
    ),
    (
        'Filtra en Network por "catalog"',
        "Escribe catalog en la barra de filtro de la pestaña Network.",
    ),
    (
        "Selecciona el GET de mayor peso",
        "Abre la solicitud GET más pesada y ve a Headers → Request Headers.",
    ),
    (
        "Extrae Authorization (Bearer)",
        "Copia el valor completo de Authorization (Bearer eyJ…) "
        "y pégalo en APPLE_AUTH_BEARER.",
    ),
    (
        "Extrae Media-User-Token",
        "Copia el valor del header media-user-token (o x-apple-music-user-token) "
        "y pégalo en APPLE_MUSIC_USER_TOKEN.",
    ),
)

SPOTIFY_INSTRUCTIONS = (
    (
        "Abre Spotify Web y pulsa F12",
        "Ve a open.spotify.com e inicia sesión.",
    ),
    (
        "Ve a Application → Cookies",
        "En DevTools abre Application → Storage → Cookies "
        "→ https://open.spotify.com.",
    ),
    (
        "Copia sp_dc y sp_key",
        "Busca las cookies sp_dc y sp_key y copia sus valores en los "
        "campos correspondientes.",
    ),
    (
        "Pega el identifier",
        "Escribe tu email o username de Spotify en el campo identifier.",
    ),
)


def instructions_box(steps: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> ft.Container:
    """Renderiza una caja numerada de instrucciones encima de los campos."""

    def _step(num: int, label: str, body: str) -> ft.Row:
        return ft.Row(
            controls=[
                ft.Container(
                    content=ft.Text(
                        str(num),
                        size=10,
                        color=ACCENT,
                        font_family=FONT_HEADLINE_BOLD,
                    ),
                    bgcolor=OVERLAY_18,
                    border_radius=20,
                    width=20,
                    height=20,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Column(
                    controls=[
                        ft.Text(
                            label,
                            size=11,
                            color=TEXT_PRIMARY,
                            font_family=FONT_HEADLINE_BOLD,
                        ),
                        ft.Text(
                            body,
                            size=11,
                            color=TEXT_MUTED,
                            font_family=FONT_TEXT,
                        ),
                    ],
                    spacing=1,
                    tight=True,
                    expand=True,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    rows = [_step(i + 1, lbl, txt) for i, (lbl, txt) in enumerate(steps)]
    return ft.Container(
        content=ft.Column(rows, spacing=8),
        bgcolor=OVERLAY_10,
        border=ft.Border.all(0.8, OVERLAY_14),
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
    )


def make_wizard_field(
    label: str,
    value: str = "",
    *,
    password: bool = False,
    can_reveal_password: bool = False,
    multiline: bool = False,
    min_lines: Optional[int] = None,
    max_lines: Optional[int] = None,
    expand: bool = False,
) -> ft.TextField:
    """Construye un TextField estilizado con tipografía monoespaciada."""
    return app_text_field(
        label=label,
        value=value,
        password=password,
        can_reveal_password=can_reveal_password,
        multiline=multiline,
        min_lines=min_lines,
        max_lines=max_lines,
        expand=expand,
        text_style=ft.TextStyle(
            color=TEXT_PRIMARY,
            size=12,
            font_family=mono_family(),
        ),
    )


def make_expandable_field(
    label: str,
    value: str = "",
    *,
    min_lines: int,
    max_lines: int,
    password: bool = False,
    can_reveal_password: bool = False,
    on_toggle_update: Optional[Callable[[], None]] = None,
) -> tuple[ft.Row, ft.TextField]:
    """Crea un campo multilínea compacto con botón para expandir o contraer."""
    field = make_wizard_field(
        label=label,
        value=value,
        password=password,
        can_reveal_password=can_reveal_password,
        multiline=True,
        min_lines=1,
        max_lines=1,
        expand=True,
    )
    expanded = False

    def _toggle(_e: ft.ControlEvent) -> None:
        nonlocal expanded
        expanded = not expanded
        field.min_lines = min_lines if expanded else 1
        field.max_lines = max_lines if expanded else 1
        toggle.content.value = PIcons.CARET_UP if expanded else PIcons.CARET_DOWN
        toggle.tooltip = "Contraer campo" if expanded else "Expandir campo"
        if on_toggle_update is not None:
            on_toggle_update()

    toggle = app_icon_button(
        glyph=PIcons.CARET_DOWN,
        icon_color=TEXT_MUTED,
        size=16,
        tooltip="Expandir campo",
        on_click=_toggle,
    )
    return (
        ft.Row(
            controls=[field, toggle],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        ),
        field,
    )


def build_field_controls(
    specs: tuple[dict[str, object], ...],
    values: dict[str, str],
    registry: dict[str, ft.TextField],
    *,
    on_toggle_update: Optional[Callable[[], None]] = None,
) -> list[ft.Control]:
    """Construye los controles del formulario a partir de sus specs declarativos."""
    controls: list[ft.Control] = []
    for spec in specs:
        key = str(spec["key"])
        label = str(spec.get("label", key))
        raw_value = values.get(key, "") or ""
        value = raw_value if isinstance(raw_value, str) else str(raw_value)
        password = bool(spec.get("password", False))
        can_reveal = bool(spec.get("can_reveal_password", False))

        if bool(spec.get("expandable", False)):
            min_lines = int(spec.get("min_lines", 2))
            max_lines = int(spec.get("max_lines", min_lines))
            control, field = make_expandable_field(
                label=label,
                value=value,
                min_lines=min_lines,
                max_lines=max_lines,
                password=password,
                can_reveal_password=can_reveal,
                on_toggle_update=on_toggle_update,
            )
        else:
            field = make_wizard_field(
                label=label,
                value=value,
                password=password,
                can_reveal_password=can_reveal,
                multiline=bool(spec.get("multiline", False)),
                min_lines=(
                    int(spec["min_lines"])
                    if spec.get("min_lines") is not None else None
                ),
                max_lines=(
                    int(spec["max_lines"])
                    if spec.get("max_lines") is not None else None
                ),
            )
            control = field

        if bool(spec.get("saved_badge", False)) and value.strip():
            field.helper = "Sesión guardada"
            field.helper_style = ft.TextStyle(color=SUCCESS, size=10, font_family=FONT_TEXT)

        registry[key] = field
        controls.append(control)
    return controls


def field_style() -> dict:
    """Compatibilidad con consultas externas de estilo de campo del wizard."""
    probe = app_text_field(label="x")
    return {
        "bgcolor": probe.bgcolor,
        "border": input_border(),
        "label_style": probe.label_style,
        "text_style": probe.text_style,
    }


def section_header(text: str) -> ft.Text:
    """Cabecera de sección reutilizable para formularios del wizard."""
    return section_label(text)


def fixed_note(text: str) -> ft.Container:
    """Nota informativa secundaria al pie de un formulario."""
    return ft.Container(
        content=ft.Text(text, size=9, color=TEXT_DIM, font_family=FONT_TEXT),
        bgcolor=OVERLAY_06,
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
    )


def warn_banner(text: str) -> ft.Container:
    """Banner de advertencia para formularios de credenciales."""
    return ft.Container(
        content=ft.Row(
            controls=[
                app_icon(PIcons.WARNING, color=WARNING, size=14),
                ft.Text(
                    text,
                    size=10,
                    color=WARNING,
                    font_family=FONT_TEXT,
                    expand=True,
                ),
            ],
            spacing=6,
        ),
        bgcolor=WARN_BG,
        border=ft.Border.all(0.8, WARN_BORDER),
        border_radius=8,
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
    )


__all__ = [
    "APPLE_FIELD_SPECS",
    "APPLE_INSTRUCTIONS",
    "PLATFORM_LOGIN_URLS",
    "PLATFORM_MANUAL_LABELS",
    "PLATFORM_TAB_META",
    "SPOTIFY_FIELD_SPECS",
    "SPOTIFY_INSTRUCTIONS",
    "YOUTUBE_FIELD_SPECS",
    "YOUTUBE_INSTRUCTIONS",
    "build_field_controls",
    "field_style",
    "fixed_note",
    "instructions_box",
    "make_expandable_field",
    "make_wizard_field",
    "section_header",
    "warn_banner",
]
