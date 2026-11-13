"""Flet credential configuration wizard.

This module owns the credential-editing dialog. File persistence and
authentication checks are provided by services.authentication.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ui.setup.auth_manager import AuthManager

import flet as ft

from core.config import PLATFORM_ORDER
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_HEADLINE_SEMI, FONT_TEXT
from ui.theme.icons import PIcons, app_icon
from ui.theme.tokens import (
    BG_PANEL, BG_SURFACE,
    ACCENT, SUCCESS, WARNING, ERROR_COL,
    TEXT_PRIMARY, TEXT_MUTED,
    OVERLAY_06, OVERLAY_08, OVERLAY_14,
)
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    app_text,
    close_dialog,
    dialog_action,
    open_external,
    organize_dropdown,
    show_dialog,
)
from ui.setup.wizard_forms import (
    APPLE_FIELD_SPECS,
    APPLE_INSTRUCTIONS,
    PLATFORM_LOGIN_URLS,
    PLATFORM_MANUAL_LABELS,
    PLATFORM_TAB_META,
    SPOTIFY_FIELD_SPECS,
    SPOTIFY_INSTRUCTIONS,
    YOUTUBE_FIELD_SPECS,
    YOUTUBE_INSTRUCTIONS,
    build_field_controls,
    fixed_note,
    instructions_box,
    section_header,
)
from services import browser_session
from services.authentication import (
    PreFlightResult,
    read_browser_json,
    read_env_values,
    read_spotify_cookies,
    write_browser_json,
    write_env_values,
    write_spotify_cookies,
)


class ConfigWizard:
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
        self._results_by_platform: dict[str, PreFlightResult] = {}
        self._status_rows: dict[str, ft.Row] = {}
        self._source_dropdowns: dict[str, ft.Dropdown] = {}

        # Save controls
        self._save_button: Optional[ft.TextButton] = None
        self._save_error: Optional[ft.Text] = None

    # ── Dialog lifecycle ───────────────────────────────────────────────

    def _show_dialog(self, dlg: ft.AlertDialog) -> None:
        show_dialog(self.page, dlg)

    def _dismiss_dialog(self, dlg: ft.AlertDialog) -> None:
        close_dialog(self.page, dlg)

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
        self._results_by_platform = {}
        self._status_rows = {}
        self._source_dropdowns = {}
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
                FONT_HEADLINE_SEMI if i == idx else FONT_HEADLINE
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
                    app_icon(icon, color=color, size=13),
                    ft.Text(
                        label, size=11, color=color,
                        font_family=(
                            FONT_HEADLINE_SEMI
                            if idx == self._active_tab_idx else FONT_HEADLINE
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
        for result in (results or []):
            self._results_by_platform[result.platform] = result
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
            font_family=FONT_TEXT,
            visible=False,
        )
        body.controls.append(self._save_error)

        self._save_button = dialog_action(
            "Guardar y Aplicar",
            self._on_save_click,
            kind="primary",
            icon=PIcons.FLOPPY_DISK,
        )
        return ft.AlertDialog(
            modal=True,
            scrollable=False,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            title=ft.Row(
                controls=[
                    app_icon(PIcons.GEAR, color=ACCENT, size=18),
                    ft.Text(
                        "Configuración de Credenciales",
                        size=14, font_family=FONT_HEADLINE_BOLD,
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

    # ── Status & browser session header ────────────────────────────────

    def _current_source_info(self, platform: str) -> tuple[bool, str, str]:
        """Devuelve ``(ok, source, error)`` actual de la plataforma."""
        res = self._results_by_platform.get(platform)
        svc = getattr(self._auth_manager, "service", None)
        attr_map = {
            "YouTube Music": ("youtube_session_source", "youtube_auth_error"),
            "Apple Music":   ("apple_session_source", "apple_auth_error"),
            "Spotify":       ("spotify_session_source", "spotify_auth_error"),
        }
        src_attr, err_attr = attr_map.get(platform, ("", ""))
        svc_src = str(getattr(svc, src_attr, "") or "") if svc else ""
        svc_err = str(getattr(svc, err_attr, "") or "") if svc else ""
        if res is not None:
            return (res.ok, res.source or svc_src, res.error or svc_err)
        st = getattr(self._auth_manager, "state", None)
        ok = bool(st.auth_session_ok.get(platform, False)) if st else bool(svc_src)
        return (ok, svc_src, svc_err)

    def _set_preferred_source(self, platform: str, value: Optional[str]) -> None:
        svc = getattr(self._auth_manager, "service", None)
        if svc is None:
            return
        pref = None if not value or value == "__auto__" else value
        if platform == "YouTube Music":
            svc.youtube_preferred_source = pref
        elif platform == "Apple Music":
            svc.apple_preferred_source = pref
        elif platform == "Spotify":
            svc.spotify_preferred_source = pref

    def _update_status_row(self, platform: str) -> None:
        row = self._status_rows.get(platform)
        if row is None:
            return
        ok, source, error = self._current_source_info(platform)
        icon_ctrl = row.controls[0]
        text_ctrl = row.controls[1]
        if ok:
            icon_ctrl.value = PIcons.CHECK_CIRCLE
            icon_ctrl.color = SUCCESS
            src_label = source or "sesión activa"
            text_ctrl.value = f"Verificado con {src_label}"
            text_ctrl.color = SUCCESS
        else:
            icon_ctrl.value = PIcons.WARNING
            icon_ctrl.color = WARNING
            text_ctrl.value = error or "Sesión no verificada — inicia sesión en el navegador y pulsa Reintentar"
            text_ctrl.color = WARNING

    def _on_retry_platform(self, platform: str) -> None:
        dd = self._source_dropdowns.get(platform)
        if dd is not None:
            self._set_preferred_source(platform, dd.value)

        async def _run() -> None:
            browser_session.clear_cache()
            results = await self._auth_manager.check_sessions([platform])
            self._auth_manager.ingest_preflight_results(results)
            for r in results:
                self._results_by_platform[r.platform] = r
                if r.ok:
                    self._failed_platforms.discard(r.platform)
                else:
                    self._failed_platforms.add(r.platform)
            self._update_status_row(platform)
            self._apply_tab_selection(self._active_tab_idx)

        try:
            asyncio.create_task(_run())
        except RuntimeError:
            pass

    def _build_browser_session_header(
        self,
        platform: str,
        advanced_body: ft.Control,
    ) -> list[ft.Control]:
        """Construye selector de origen, línea de estado, botones y sección Avanzado plegable."""
        profiles = browser_session.detect()
        manual_key = PLATFORM_MANUAL_LABELS[platform]
        options: list[tuple[str, str]] = [("__auto__", "Automático (navegador primero)")]
        for prof in profiles:
            options.append((prof.label, prof.label))
        options.append((manual_key, f"Manual ({manual_key})"))

        ok, active_src, _ = self._current_source_info(platform)
        option_keys = {k for k, _ in options}
        default_val = active_src if (ok and active_src in option_keys) else "__auto__"

        dd = organize_dropdown(
            options=options,
            value=default_val,
            label="Origen de sesión",
            width=320,
        )
        dd.on_select = lambda e, p=platform: self._set_preferred_source(p, e.control.value)
        self._source_dropdowns[platform] = dd

        status_icon = app_icon(PIcons.INFO, size=15, color=TEXT_MUTED)
        status_text = app_text("", size=11, color=TEXT_MUTED, expand=True)
        status_row = ft.Row(
            controls=[status_icon, status_text],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._status_rows[platform] = status_row
        self._update_status_row(platform)

        login_url = PLATFORM_LOGIN_URLS[platform]
        actions_row = ft.Row(
            controls=[
                _primary_btn(
                    "Abrir y iniciar sesión",
                    PIcons.ARROW_SQUARE_OUT,
                    lambda _e, u=login_url: open_external(self.page, u),
                    height=38,
                ),
                _ghost_btn(
                    "Reintentar",
                    PIcons.ARROWS_CLOCKWISE,
                    lambda _e, p=platform: self._on_retry_platform(p),
                    height=38,
                ),
            ],
            spacing=8,
            wrap=True,
        )

        unsupported = browser_session.unsupported_browsers()
        controls: list[ft.Control] = [
            dd,
            ft.Container(
                content=status_row,
                bgcolor=OVERLAY_08,
                border=ft.Border.all(0.8, OVERLAY_14),
                border_radius=8,
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            ),
            actions_row,
        ]
        if unsupported:
            controls.append(
                fixed_note(
                    f"Navegadores en Flatpak/Snap no soportados por yt-dlp: {', '.join(unsupported)}. "
                    "Usa Firefox o el modo Avanzado."
                )
            )

        adv_container = ft.Container(content=advanced_body, visible=False, padding=ft.Padding.only(top=6))
        adv_icon = app_icon(PIcons.CARET_DOWN, size=16, color=TEXT_MUTED)
        adv_label = app_text("Avanzado (credenciales manuales)", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE)

        def _toggle_adv(_e: ft.ControlEvent) -> None:
            adv_container.visible = not adv_container.visible
            adv_icon.value = PIcons.CARET_UP if adv_container.visible else PIcons.CARET_DOWN
            self._safe_dialog_update()

        adv_header = ft.Container(
            content=ft.Row(
                controls=[adv_icon, adv_label],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=OVERLAY_06,
            border=ft.Border.all(0.8, OVERLAY_14),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            ink=True,
            on_click=_toggle_adv,
        )
        controls.extend([adv_header, adv_container])
        return controls

    # ── Tab 0: YouTube Music (editable) ───────────────────────────────

    def _panel_youtube(self, warn: bool = False) -> ft.Container:
        bj = read_browser_json()
        self._yt_fields = {}
        fields = build_field_controls(
            YOUTUBE_FIELD_SPECS,
            bj,
            self._yt_fields,
            on_toggle_update=self._safe_dialog_update,
        )
        adv_controls: list[ft.Control] = [
            instructions_box(YOUTUBE_INSTRUCTIONS),
            section_header("BROWSER.JSON — CAMPOS VARIABLES"),
            *fields,
            fixed_note(
                "Los campos fijos (Accept, Content-Type, X-Goog-AuthUser, x-origin) "
                "se escriben automáticamente; SAPISIDHASH se recalcula en cada lectura."
            ),
        ]
        controls = self._build_browser_session_header(
            "YouTube Music",
            ft.Column(controls=adv_controls, spacing=10),
        )
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
        adv_controls: list[ft.Control] = [
            instructions_box(APPLE_INSTRUCTIONS),
            section_header("APPLE MUSIC — .env"),
            *build_field_controls(
                APPLE_FIELD_SPECS,
                env,
                self._am_fields,
                on_toggle_update=self._safe_dialog_update,
            ),
            fixed_note(
                'APPLE_AUTH_BEARER puede tener o no el prefijo "Bearer "; '
                "la app lo normaliza automáticamente."
            ),
        ]
        controls = self._build_browser_session_header(
            "Apple Music",
            ft.Column(controls=adv_controls, spacing=10),
        )
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
        fields = build_field_controls(
            SPOTIFY_FIELD_SPECS,
            values,
            self._sp_fields,
            on_toggle_update=self._safe_dialog_update,
        )
        adv_controls: list[ft.Control] = [
            instructions_box(SPOTIFY_INSTRUCTIONS),
            section_header("SPOTIFY_COOKIES.JSON — CAMPOS VARIABLES"),
            *fields,
            fixed_note(
                "Solo se necesitan cookies para crear playlists y leer tu biblioteca; "
                "la búsqueda y el fetch de playlists públicas funcionan sin login."
            ),
        ]
        controls = self._build_browser_session_header(
            "Spotify",
            ft.Column(controls=adv_controls, spacing=10),
        )
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

__all__ = ["ConfigWizard"]
