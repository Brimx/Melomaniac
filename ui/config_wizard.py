"""Flet credential configuration wizard.

This module owns the credential-editing dialog. File persistence and
authentication checks are provided by services.authentication.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

import flet as ft

from core.config import PLATFORM_ORDER
from ui.tokens import (
    BG_PANEL, BG_SURFACE, BG_INPUT, BORDER_LIGHT,
    ACCENT, SUCCESS, WARNING, ERROR_COL,
    TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM,
    OVERLAY_06, OVERLAY_08, OVERLAY_10, OVERLAY_14, OVERLAY_18,
    WARN_BG, WARN_BORDER,
)
from ui.widgets import DialogMixin, dialog_action
from services.authentication import (
    ENV_KEYS_APPLE,
    PreFlightResult,
    read_browser_json,
    read_env_values,
    read_spotify_cookies,
    write_browser_json,
    write_env_values,
    write_spotify_cookies,
)

# ── Platform order and tab metadata (single source of truth) ───────────
PLATFORM_TAB_META = {

    "YouTube Music": (ft.Icons.MUSIC_VIDEO, ft.Icons.WARNING_AMBER_ROUNDED),
    "Apple Music":   (ft.Icons.APPLE, ft.Icons.WARNING_AMBER_ROUNDED),
    "Spotify":       (ft.Icons.MUSIC_NOTE, ft.Icons.WARNING_AMBER_ROUNDED),
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
    {"key": "sp_dc", "password": True, "can_reveal_password": True},
    {"key": "sp_key", "password": True, "can_reveal_password": True},
)

YOUTUBE_INSTRUCTIONS = (
    ("Abre YouTube Music y pulsa F12",
     "Ve a la pestaña Network en DevTools."),
    ("Filtra por \"browse\"",
     "Escribe browse en la barra de filtro de Network."),
    ("Selecciona el POST de mayor peso",
     "Busca una solicitud con método POST (habitualmente browsing o browse)."),
    ("Extrae Authorization",
     "En Headers → Request Headers copia el valor completo de Authorization "
     "(empieza con SAPISIDHASH …)."),
    ("Extrae Cookie",
     "En la misma solicitud copia el valor completo del header Cookie."),
)

APPLE_INSTRUCTIONS = (
    ("Abre Apple Music Web y pulsa F12",
     "Ve a music.apple.com y abre DevTools."),
    ("Filtra en Network por \"catalog\"",
     "Escribe catalog en la barra de filtro de la pestaña Network."),
    ("Selecciona el GET de mayor peso",
     "Abre la solicitud GET más pesada y ve a Headers → Request Headers."),
    ("Extrae Authorization (Bearer)",
     "Copia el valor completo de Authorization (Bearer eyJ…) "
     "y pégalo en APPLE_AUTH_BEARER."),
    ("Extrae Media-User-Token",
     "Copia el valor del header media-user-token (o x-apple-music-user-token) "
     "y pégalo en APPLE_MUSIC_USER_TOKEN."),
)

SPOTIFY_INSTRUCTIONS = (
    ("Abre Spotify Web y pulsa F12",
     "Ve a open.spotify.com e inicia sesión."),
    ("Ve a Application → Cookies",
     "En DevTools abre Application → Storage → Cookies "
     "→ https://open.spotify.com."),
    ("Copia sp_dc y sp_key",
     "Busca las cookies sp_dc y sp_key y copia sus valores en los "
     "campos correspondientes."),
    ("Pega el identifier",
     "Escribe tu email o username de Spotify en el campo identifier."),
)

class ConfigWizard(DialogMixin):
    """
    Flet overlay dialog for platform credential management.

    All platform panels are editable.
    "Guardar y Aplicar" writes config/browser.json, config/.env, and
    config/spotify_cookies.json using the corresponding platform fields.
    """

    # Platform name → panel index
    _PLATFORM_INDEX = {
        platform: index
        for index, platform in enumerate(PLATFORM_ORDER)
    }

    def __init__(
        self,
        page: ft.Page,
        auth_manager: "AuthManager",
        on_saved: Optional[Callable[[], None]] = None,
    ) -> None:
        self.page          = page
        self._auth_manager = auth_manager
        self.on_saved      = on_saved

        self._dlg: Optional[ft.AlertDialog]    = None
        self._tab_panels:  list[ft.Container]  = []
        self._tab_buttons: list[ft.Container]  = []
        self._panel_holder: Optional[ft.Container] = None
        self._failed_platforms: set[str]       = set()
        self._active_tab_idx: int              = 0
        self._is_saving: bool                  = False

        # Editable fields grouped by platform. Each value is a distinct
        # TextField; the dictionaries only provide stable references.
        self._yt_fields: dict[str, ft.TextField] = {}
        self._am_fields: dict[str, ft.TextField] = {}
        self._sp_fields: dict[str, ft.TextField] = {}

        # Save controls
        self._save_button: Optional[ft.TextButton] = None
        self._save_error: Optional[ft.Text] = None

    # ── Dialog lifecycle ───────────────────────────────────────────────

    def _show_dialog(self, dlg: ft.AlertDialog) -> None:
        # Compat: ciclo de vida canónico en DialogMixin.
        self.show_dialog(self.page, dlg)

    def _dismiss_dialog(self, dlg: ft.AlertDialog) -> None:
        # Compat: ciclo de vida canónico en DialogMixin.
        self.close_dialog(self.page, dlg)

    def _safe_dialog_update(self) -> None:
        try:
            if self._dlg is not None and getattr(self._dlg, "open", False):
                self._dlg.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def _safe_page_update(self) -> None:
        try:
            self.page.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def _reset_state(self) -> None:
        """Clear dialog, panel, and field references before rebuilding it."""
        self._tab_panels = []
        self._tab_buttons = []
        self._panel_holder = None
        self._failed_platforms = set()
        self._active_tab_idx = 0
        self._yt_fields = {}
        self._am_fields = {}
        self._sp_fields = {}
        self._save_button = None
        self._save_error = None

    # ── Tab management ─────────────────────────────────────────────────

    def _resolve_initial_tab(
        self, failed_platforms: set[str], initial_platform: Optional[str]
    ) -> int:
        if initial_platform and initial_platform in self._PLATFORM_INDEX:
            return self._PLATFORM_INDEX[initial_platform]
        if failed_platforms:
            return next(
                (self._PLATFORM_INDEX[p] for p in PLATFORM_ORDER if p in failed_platforms),
                0,
            )
        return 0

    def _apply_tab_selection(self, idx: int) -> None:
        if not self._tab_panels or not self._tab_buttons:
            return
        idx = max(0, min(idx, len(self._tab_panels) - 1))
        self._active_tab_idx = idx
        if self._panel_holder is not None:
            self._panel_holder.content = self._tab_panels[idx]
        for i, btn in enumerate(self._tab_buttons):
            is_warn      = PLATFORM_ORDER[i] in self._failed_platforms
            col_active   = WARNING if is_warn else TEXT_PRIMARY
            col_inactive = WARNING if is_warn else TEXT_MUTED
            btn.bgcolor  = OVERLAY_14 if i == idx else "transparent"
            row = btn.content
            row.controls[0].color  = col_active   if i == idx else col_inactive
            row.controls[1].color  = col_active   if i == idx else col_inactive
            row.controls[1].font_family = (
                "IBM Plex Sans SemiBold" if i == idx else "IBM Plex Sans"
            )
        self._safe_dialog_update()
        self._safe_page_update()

    def _on_tab_click(self, e: ft.ControlEvent) -> None:
        try:
            idx = int(getattr(e.control, "data", "0"))
        except (TypeError, ValueError):
            idx = 0
        self._apply_tab_selection(idx)

    def _make_tab_btn(
        self,
        idx: int,
        label: str,
        icon_ok: str,
        icon_warn: str,
        platform: str,
    ) -> ft.Container:
        warn  = platform in self._failed_platforms
        icon  = icon_warn if warn else icon_ok
        color = WARNING if warn else (
            TEXT_PRIMARY if idx == self._active_tab_idx else TEXT_MUTED
        )
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(icon, color=color, size=13),
                    ft.Text(
                        label, size=11, color=color,
                        font_family=(
                            "IBM Plex Sans SemiBold"
                            if idx == self._active_tab_idx else "IBM Plex Sans"
                        ),
                    ),
                ],
                spacing=6, tight=True,
            ),
            bgcolor=OVERLAY_14 if idx == self._active_tab_idx else "transparent",
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            data=str(idx),
            ink=True,
            on_click=self._on_tab_click,
        )

    # ── Action handlers ────────────────────────────────────────────────

    def _on_close_click(self, _e: ft.ControlEvent) -> None:
        self._close_wizard()

    def _on_save_click(self, _e: ft.ControlEvent) -> None:
        if self._is_saving:
            return

        try:
            # Flet controls belong to the UI thread. Capture primitive values
            # before moving the disk writes to a worker thread.
            values = self._collect_form_values()
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self._show_save_error(ex)
            return

        self._set_saving(True)

        async def _save_and_close() -> None:
            try:
                await asyncio.to_thread(self._apply_save, values)
                self._close_wizard()
                if self.on_saved:
                    self.on_saved()
            except Exception as ex:  # pylint: disable=broad-exception-caught
                self._show_save_error(ex)
                self._auth_manager.state_log_fn(
                    f"[ERROR] Configuración: no se pudo guardar: {ex}"
                )
            finally:
                self._set_saving(False)

        asyncio.create_task(_save_and_close())

    def _set_saving(self, saving: bool) -> None:
        self._is_saving = saving
        if self._save_button is not None:
            self._save_button.disabled = saving
        if saving and self._save_error is not None:
            self._save_error.value = ""
            self._save_error.visible = False
        self._safe_dialog_update()

    def _show_save_error(self, error: Exception) -> None:
        if self._save_error is not None:
            self._save_error.value = f"No se pudo guardar: {str(error)[:240]}"
            self._save_error.visible = True
        self._safe_dialog_update()

    def _collect_form_values(self) -> dict[str, dict[str, str]]:
        """Read all editable controls while still on the UI thread."""
        return {
            "youtube": {
                key: field.value or ""
                for key, field in self._yt_fields.items()
            },
            "apple": {
                key: field.value or ""
                for key, field in self._am_fields.items()
            },
            "spotify": {
                key: field.value or ""
                for key, field in self._sp_fields.items()
            },
        }

    # ── Public API ─────────────────────────────────────────────────────

    def open(
        self,
        results: Optional[list[PreFlightResult]] = None,
        initial_platform: Optional[str] = None,
    ) -> None:
        """
        Show the wizard.
        • Highlights failed platforms if *results* is supplied.
        • If *initial_platform* is given, that tab is shown first.
        """
        if self._dlg is not None and getattr(self._dlg, "open", False):
            if initial_platform and initial_platform in self._PLATFORM_INDEX:
                self._apply_tab_selection(self._PLATFORM_INDEX[initial_platform])
            return

        if self._dlg is not None:
            try:
                self._dismiss_dialog(self._dlg)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._dlg = None

        self._reset_state()
        self._failed_platforms = {
            result.platform
            for result in (results or [])
            if not result.ok
        }

        _initial_idx = self._resolve_initial_tab(self._failed_platforms, initial_platform)
        self._active_tab_idx = _initial_idx

        panels = self._build_panels()
        self._tab_panels   = panels
        self._panel_holder = ft.Container(content=panels[_initial_idx], expand=True)

        self._tab_buttons = self._build_tab_buttons()
        body = self._build_dialog_body()
        self._dlg = self._build_dialog(body)
        self._apply_tab_selection(_initial_idx)
        self._show_dialog(self._dlg)

    def _build_panels(self) -> list[ft.Container]:
        builders = {
            "YouTube Music": self._panel_youtube,
            "Apple Music":   self._panel_apple,
            "Spotify":       self._panel_spotify,
        }
        return [
            builders[platform](warn=platform in self._failed_platforms)
            for platform in PLATFORM_ORDER
        ]

    def _build_tab_buttons(self) -> list[ft.Container]:
        return [
            self._make_tab_btn(
                index,
                platform,
                *PLATFORM_TAB_META[platform],
                platform,
            )
            for index, platform in enumerate(PLATFORM_ORDER)
        ]

    def _build_dialog_body(self) -> ft.Column:
        return ft.Column(
            controls=[
                ft.Container(
                    content=ft.Row(controls=self._tab_buttons, spacing=4),
                    bgcolor=OVERLAY_08,
                    border_radius=10,
                    padding=ft.Padding.all(4),
                    border=ft.Border.all(0.8, OVERLAY_14),
                ),
                ft.Container(
                    content=self._panel_holder,
                    expand=True,
                    bgcolor=BG_SURFACE,
                    border_radius=8,
                    padding=ft.Padding.all(0),
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                ),
            ],
            spacing=8,
            expand=True,
        )

    def _build_dialog(self, body: ft.Column) -> ft.AlertDialog:
        self._save_error = ft.Text(
            "",
            size=10,
            color=ERROR_COL,
            font_family="IBM Plex Sans",
            visible=False,
        )
        body.controls.append(self._save_error)

        self._save_button = dialog_action(
            "Guardar y Aplicar",
            self._on_save_click,
            kind="primary",
            icon=ft.Icons.SAVE_OUTLINED,
        )
        return ft.AlertDialog(
            modal=True,
            scrollable=False,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS, color=ACCENT, size=18),
                    ft.Text(
                        "Configuración de Credenciales",
                        size=14, font_family="IBM Plex Sans Bold",
                        color=TEXT_PRIMARY,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=body,
                width=620,
                height=480,
                bgcolor=BG_SURFACE,
                border_radius=10,
                padding=ft.Padding.all(8),
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            ),
            actions=[
                self._save_button,
                dialog_action("Cerrar", self._on_close_click, kind="muted"),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=BG_PANEL,
            shape=ft.RoundedRectangleBorder(radius=14),
        )

    def _close_wizard(self) -> None:
        try:
            if self._dlg is not None:
                self._dismiss_dialog(self._dlg)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        finally:
            self._dlg = None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Panel builders
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _instructions_box(steps: list[tuple[str, str]]) -> ft.Container:
        """Renders a numbered instruction box above the credential fields."""
        def _step(num: int, label: str, body: str) -> ft.Row:
            return ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Text(
                            str(num), size=10, color=ACCENT,
                            font_family="IBM Plex Sans Bold",
                        ),
                        bgcolor=OVERLAY_18,
                        border_radius=20,
                        width=20, height=20,
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                label, size=11, color=TEXT_PRIMARY,
                                font_family="IBM Plex Sans Bold",
                            ),
                            ft.Text(
                                body, size=11, color=TEXT_MUTED,
                                font_family="IBM Plex Sans",
                            ),
                        ],
                        spacing=1, tight=True, expand=True,
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

    def _make_field(
        self,
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
        """Delega en widgets.app_text_field canónico."""
        from ui.widgets import app_text_field
        return app_text_field(
            label=label,
            value=value,
            password=password,
            can_reveal_password=can_reveal_password,
            multiline=multiline,
            min_lines=min_lines,
            max_lines=max_lines,
            expand=expand,
        )

    def _make_expandable_field(
        self,
        label: str,
        value: str = "",
        *,
        min_lines: int,
        max_lines: int,
        password: bool = False,
        can_reveal_password: bool = False,
    ) -> tuple[ft.Row, ft.TextField]:
        """Create a compact multiline field with an expand/collapse button."""
        field = self._make_field(
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
            toggle.icon = ft.Icons.EXPAND_LESS if expanded else ft.Icons.EXPAND_MORE
            toggle.tooltip = "Contraer campo" if expanded else "Expandir campo"
            self._safe_dialog_update()

        toggle = ft.IconButton(
            icon=ft.Icons.EXPAND_MORE,
            icon_color=TEXT_MUTED,
            icon_size=16,
            padding=ft.Padding.all(0),
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

    def _build_field_controls(
        self,
        specs: tuple[dict[str, object], ...],
        values: dict[str, str],
        registry: dict[str, ft.TextField],
    ) -> list[ft.Control]:
        """Build controls from declarative specs and register their fields."""
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
                control, field = self._make_expandable_field(
                    label=label,
                    value=value,
                    min_lines=min_lines,
                    max_lines=max_lines,
                    password=password,
                    can_reveal_password=can_reveal,
                )
            else:
                field = self._make_field(
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

            registry[key] = field
            controls.append(control)
        return controls

    # ── Tab 0: YouTube Music (editable) ───────────────────────────────

    def _panel_youtube(self, warn: bool = False) -> ft.Container:
        bj = read_browser_json()
        self._yt_fields = {}
        fields = self._build_field_controls(
            YOUTUBE_FIELD_SPECS,
            bj,
            self._yt_fields,
        )
        controls: list[ft.Control] = []
        if warn:
            controls.append(self._warn_banner(
                "Token expirado. Actualiza Authorization y Cookie desde "
                "music.youtube.com → DevTools → Network."
            ))
        controls.extend([
            self._instructions_box(YOUTUBE_INSTRUCTIONS),
            self._section("BROWSER.JSON — CAMPOS VARIABLES"),
            *fields,
            self._fixed_note(
                "Los campos fijos (Accept, Content-Type, X-Goog-AuthUser, x-origin) "
                "se escriben automáticamente."
            ),
        ])
        return ft.Container(
            content=ft.Column(
                controls=controls,
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=ft.Padding.all(12),
            expand=True,
        )

    # ── Tab 1: Apple Music (editable) ─────────────────────────────────

    def _panel_apple(self, warn: bool = False) -> ft.Container:
        env = read_env_values()
        self._am_fields = {}
        controls: list[ft.Control] = []

        if warn:
            controls.append(self._warn_banner(
                "Token expirado. Actualiza APPLE_AUTH_BEARER y APPLE_MUSIC_USER_TOKEN."
            ))

        controls.append(self._instructions_box(APPLE_INSTRUCTIONS))

        controls.append(self._section("APPLE MUSIC — .env"))
        controls.extend(self._build_field_controls(
            APPLE_FIELD_SPECS,
            env,
            self._am_fields,
        ))

        controls.append(self._fixed_note(
            'APPLE_AUTH_BEARER puede tener o no el prefijo "Bearer "; '
            "la app lo normaliza automáticamente."
        ))
        return ft.Container(
            content=ft.Column(controls, spacing=10, scroll=ft.ScrollMode.AUTO),
            padding=ft.Padding.all(12),
            expand=True,
        )

    # ── Tab 2: Spotify (editable) ─────────────────────────────────────

    def _panel_spotify(self, warn: bool = False) -> ft.Container:
        sc = read_spotify_cookies()
        cookies = sc.get("cookies", {})
        if isinstance(cookies, str):
            cookies = {}
        values = {
            "identifier": sc.get("identifier", ""),
            "sp_dc": cookies.get("sp_dc", ""),
            "sp_key": cookies.get("sp_key", ""),
        }
        self._sp_fields = {}
        fields = self._build_field_controls(
            SPOTIFY_FIELD_SPECS,
            values,
            self._sp_fields,
        )
        controls: list[ft.Control] = []
        if warn:
            controls.append(self._warn_banner(
                "Cookies expiradas. Actualiza identifier, sp_dc y sp_key desde "
                "open.spotify.com → DevTools → Application."
            ))
        controls.extend([
            self._instructions_box(SPOTIFY_INSTRUCTIONS),
            self._section("SPOTIFY_COOKIES.JSON — CAMPOS VARIABLES"),
            *fields,
            self._fixed_note(
                "Solo se necesitan cookies para crear playlists; "
                "la búsqueda y el fetch de playlists públicas funcionan sin login."
            ),
        ])
        return ft.Container(
            content=ft.Column(
                controls=controls,
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=ft.Padding.all(12),
            expand=True,
        )

    # ── Save logic (YouTube Music + Apple Music + Spotify) ─────────────

    def _apply_save(self, values: dict[str, dict[str, str]]) -> None:
        """Persist primitive form values; this method is safe for a worker thread."""
        yt_vals = values.get("youtube", {})
        if "Authorization" in yt_vals and "Cookie" in yt_vals:
            write_browser_json(
                yt_vals["Authorization"],
                yt_vals["Cookie"],
            )

        am_vals = values.get("apple", {})
        if am_vals:
            write_env_values(am_vals)

        sp_vals = values.get("spotify", {})
        if {"identifier", "sp_dc", "sp_key"}.issubset(sp_vals):
            write_spotify_cookies(
                sp_vals["identifier"],
                sp_vals["sp_dc"],
                sp_vals["sp_key"],
            )

    # ── UI helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _field_style() -> dict:
        # Compat: ahora canónico en widgets.app_text_field. Se mantiene
        # para llamadas externas, pero _make_field ya no lo usa.
        from ui.widgets import app_text_field
        probe = app_text_field(label="x")
        return {
            "bgcolor": probe.bgcolor,
            "border_color": probe.border_color,
            "focused_border_color": probe.focused_border_color,
            "label_style": probe.label_style,
            "text_style": probe.text_style,
            "border_radius": probe.border_radius,
        }

    @staticmethod
    def _section(text: str) -> ft.Text:
        from ui.widgets import section_label
        return section_label(text)

    @staticmethod
    def _fixed_note(text: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(text, size=9, color=TEXT_DIM, font_family="IBM Plex Sans"),
            bgcolor=OVERLAY_06,
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

    @staticmethod
    def _warn_banner(text: str) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=WARNING, size=14),
                    ft.Text(
                        text, size=10, color=WARNING,
                        font_family="IBM Plex Sans", expand=True,
                    ),
                ],
                spacing=6,
            ),
            bgcolor=WARN_BG,
            border=ft.Border.all(0.8, WARN_BORDER),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

__all__ = ["ConfigWizard"]
