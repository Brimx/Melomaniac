"""
auth_manager.py — MelomaniacPass v5.0 — ISRC-Master Auth
════════════════════════════════════════════════════════
Centralises ALL credential I/O, pre-flight validation, and the Flet
Configuration Wizard.

Credential contract by platform
────────────────────────────────
• YouTube Music  → editable via ConfigWizard (browser.json)
• Apple Music    → editable via ConfigWizard (.env)

browser.json  (YouTube Music)
    {
        "Accept": "*/*",
        "Authorization": "<SAPISIDHASH …>",
        "Content-Type": "application/json",
        "X-Goog-AuthUser": "0",
        "x-origin": "https://music.youtube.com",
        "Cookie": "<raw cookie string>"
    }

.env  (Apple Music)
    APPLE_AUTH_BEARER="<value>"
    APPLE_MUSIC_USER_TOKEN="<value>"
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Callable, Optional

import flet as ft
import requests
from dotenv import dotenv_values, load_dotenv, set_key

# ── Paths ──────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
BROWSER_JSON = BASE_DIR / "browser.json"
ENV_FILE     = BASE_DIR / ".env"
SPOTIFY_COOKIES_JSON = BASE_DIR / "spotify_cookies.json"

# ── Fixed keys in browser.json ─────────────────────────────────────────
BROWSER_JSON_FIXED: dict[str, str] = {
    "Accept":          "*/*",
    "Content-Type":    "application/json",
    "X-Goog-AuthUser": "0",
    "x-origin":        "https://music.youtube.com",
}

# ── Required .env variable names (exact, ordered) ──────────────────────
ENV_KEYS_APPLE = [
    "APPLE_AUTH_BEARER",
    "APPLE_MUSIC_USER_TOKEN",
]
ENV_KEYS_ALL = ENV_KEYS_APPLE

# ── Design tokens (mirrored from app.py) ───────────────────────────────
_BG_DEEP      = "#FF000000"
_BG_PANEL     = "#FF080808"
_BG_SURFACE   = "#FF111118"
_BG_INPUT     = "#FF16161F"
_CHIP_BG      = "#FF1A1A22"
_BORDER_LIGHT = "#FF3D4455"
_ACCENT       = "#FF4F8BFF"
_ACCENT_HALO  = "#FF2A3F5C"
_SUCCESS      = "#FF00D084"
_WARNING      = "#FFFFA500"
_ERROR_COL    = "#FFFF4444"
_TEXT_PRIMARY = "#FFF2F6FF"
_TEXT_MUTED   = "#FF7A8499"
_TEXT_DIM     = "#FF3D4455"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §1  LOW-LEVEL CREDENTIAL I/O
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def read_browser_json() -> dict:
    """Return the parsed browser.json, or {} if missing/invalid."""
    if not BROWSER_JSON.exists():
        return {}
    try:
        return json.loads(BROWSER_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_browser_json(authorization: str, cookie: str) -> None:
    """
    Write a spec-compliant browser.json, injecting only Authorization
    and Cookie while keeping all fixed fields in exact order.
    """
    data = {
        "Accept":          BROWSER_JSON_FIXED["Accept"],
        "Authorization":   authorization.strip(),
        "Content-Type":    BROWSER_JSON_FIXED["Content-Type"],
        "X-Goog-AuthUser": BROWSER_JSON_FIXED["X-Goog-AuthUser"],
        "x-origin":        BROWSER_JSON_FIXED["x-origin"],
        "Cookie":          cookie.strip(),
    }
    BROWSER_JSON.write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
    )


def read_env_values() -> dict[str, str]:
    """Return current .env values (empty string if key absent)."""
    raw = dotenv_values(str(ENV_FILE)) if ENV_FILE.exists() else {}
    return {k: raw.get(k, "") for k in ENV_KEYS_ALL}


def read_spotify_cookies() -> dict:
    """Return parsed spotify_cookies.json, or {} if missing/invalid."""
    if not SPOTIFY_COOKIES_JSON.exists():
        return {}
    try:
        return json.loads(SPOTIFY_COOKIES_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_spotify_cookies(identifier: str, sp_dc: str, sp_key: str) -> None:
    """
    Write spotify_cookies.json with the dump format expected by
    spotapi Login.from_cookies: {"identifier": ..., "cookies": {sp_dc, sp_key}}.
    """
    data = {
        "identifier": identifier.strip(),
        "cookies": {
            "sp_dc":  sp_dc.strip(),
            "sp_key": sp_key.strip(),
        },
    }
    SPOTIFY_COOKIES_JSON.write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
    )


def write_env_values(values: dict[str, str]) -> None:
    """
    Upsert keys in .env, maintaining the required comment headers and order.
    Creates the file if it does not exist.
    """
    if not ENV_FILE.exists():
        ENV_FILE.write_text(
            "# APPLE MUSIC\n"
            + "\n".join(f'{k}=""' for k in ENV_KEYS_APPLE)
            + "\n",
            encoding="utf-8",
        )
    for key, val in values.items():
        if key in ENV_KEYS_ALL:
            set_key(str(ENV_FILE), key, val, quote_mode="always")
    # Hot-reload into the running process's os.environ
    load_dotenv(str(ENV_FILE), override=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §2  PRE-FLIGHT VALIDATORS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AuthFailureCode:
    """Códigos de sesión para UI / diagnóstico (Global Auth Check)."""
    YT_EXPIRED      = "YT_EXPIRED"
    APPLE_EXPIRED   = "APPLE_EXPIRED"
    SPOTIFY_EXPIRED = "SPOTIFY_EXPIRED"


class PreFlightResult:
    """Holds the outcome of a single platform pre-flight check."""

    def __init__(self, platform: str):
        self.platform = platform
        self.ok       = False
        self.error    = ""
        self.expired  = False
        self.code     = ""

    def __repr__(self) -> str:
        status = "OK" if self.ok else f"FAIL({'EXPIRED' if self.expired else self.error[:30]})"
        return f"<PreFlight {self.platform}: {status}>"


def _preflight_youtube() -> PreFlightResult:
    r  = PreFlightResult("YouTube Music")
    bj = read_browser_json()
    if not bj.get("Authorization") or not bj.get("Cookie"):
        r.error   = "browser.json: falta Authorization o Cookie"
        r.expired = True
        return r
    if not bj.get("Authorization", "").startswith("SAPISIDHASH"):
        r.error   = "Authorization no comienza con 'SAPISIDHASH'"
        r.expired = True
        return r
    try:
        from ytmusicapi import YTMusic  # pylint: disable=import-outside-toplevel
        ytm = YTMusic(str(BROWSER_JSON))
        ytm.get_history()
        r.ok = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        msg       = str(exc).lower()
        r.code    = AuthFailureCode.YT_EXPIRED
        r.expired = True
        r.error   = (
            "401 — token expirado o inválido"
            if any(k in msg for k in ("401", "unauthorized", "sign in", "cookie", "parse"))
            else str(exc)[:200]
        )
    return r


def _preflight_apple() -> PreFlightResult:
    r      = PreFlightResult("Apple Music")
    env    = read_env_values()
    bearer = env.get("APPLE_AUTH_BEARER", "").strip()
    utok   = env.get("APPLE_MUSIC_USER_TOKEN", "").strip()

    if not bearer or not utok:
        r.error = "APPLE_AUTH_BEARER or APPLE_MUSIC_USER_TOKEN missing"
        return r

    full_bearer = bearer if bearer.startswith("Bearer ") else f"Bearer {bearer}"
    hdrs = {
        "Authorization":            full_bearer,
        "media-user-token":         utok,
        "x-apple-music-user-token": utok,
        "Origin":  "https://music.apple.com",
        "Referer": "https://music.apple.com/",
        "Accept":  "application/json",
    }
    try:
        resp = requests.get(
            "https://amp-api.music.apple.com/v1/me/storefront",
            headers=hdrs, timeout=8,
        )
        if resp.status_code == 401:
            r.expired = True
            r.code    = AuthFailureCode.APPLE_EXPIRED
            r.error   = "401 — Apple Music token expired"
            return r
        if resp.status_code != 200:
            r.error = f"Unexpected HTTP {resp.status_code}"
            return r
        sf  = resp.json().get("data", [{}])[0].get("id", "us")
        cat = requests.get(
            f"https://api.music.apple.com/v1/catalog/{sf}/search",
            params={"term": "a", "types": "songs", "limit": 1},
            headers=hdrs,
            timeout=8,
        )
        if cat.status_code == 401:
            r.expired = True
            r.code    = AuthFailureCode.APPLE_EXPIRED
            r.error   = "401 — catálogo rechazó media-user-token"
            return r
        if cat.status_code == 200:
            r.ok = True
        else:
            r.code  = AuthFailureCode.APPLE_EXPIRED
            r.error = f"Catálogo HTTP {cat.status_code}"
    except requests.RequestException as exc:
        r.error = str(exc)
    return r


def _preflight_spotify() -> PreFlightResult:
    r = PreFlightResult("Spotify")
    sc = read_spotify_cookies()
    cookies = sc.get("cookies", {})
    if isinstance(cookies, str):
        cookies = {}
    if not sc.get("identifier") or not cookies.get("sp_dc") or not cookies.get("sp_key"):
        r.error   = "spotify_cookies.json: falta identifier, sp_dc o sp_key"
        r.expired = True
        return r
    try:
        from spotapi import Login, Config  # pylint: disable=import-outside-toplevel
        from spotapi.utils.logger import NoopLogger  # pylint: disable=import-outside-toplevel
        cfg   = Config(logger=NoopLogger())
        login = Login.from_cookies(sc, cfg)
        if not login.logged_in:
            r.error   = "cookies inválidas (login falló)"
            r.expired = True
            return r
        r.ok = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        msg    = str(exc).lower()
        r.code = AuthFailureCode.SPOTIFY_EXPIRED
        r.expired = True
        r.error = (
            "401 — cookies expiradas o inválidas"
            if any(k in msg for k in ("401", "unauthorized", "invalid", "cookie", "login"))
            else str(exc)[:200]
        )
    return r


def auth_failure_tooltip(r: PreFlightResult) -> str:
    """Texto para Tooltip en la barra superior (sesión caída)."""
    if r.ok:
        return ""
    hints = {
        "YouTube Music": "browser.json: Cookie + Authorization (SAPISIDHASH)",
        "Apple Music":   ".env: APPLE_AUTH_BEARER + APPLE_MUSIC_USER_TOKEN",
        "Spotify":       "spotify_cookies.json: identifier + sp_dc + sp_key",
    }
    tag = f"[{r.code}] " if r.code else ""
    return f"{tag}{hints.get(r.platform, r.platform)} · {r.error}"[:500]


async def run_preflight() -> list[PreFlightResult]:
    """
    Run all pre-flight checks in parallel using asyncio.gather().
    Returns [yt_result, am_result, sp_result].
    """
    results = await asyncio.gather(
        asyncio.to_thread(_preflight_youtube),
        asyncio.to_thread(_preflight_apple),
        asyncio.to_thread(_preflight_spotify),
        return_exceptions=True,
    )
    out: list[PreFlightResult] = []
    platforms = ["YouTube Music", "Apple Music", "Spotify"]
    for plat, res in zip(platforms, results):
        if isinstance(res, Exception):
            r       = PreFlightResult(plat)
            r.error = str(res)
            out.append(r)
        else:
            out.append(res)
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §4  FLET CONFIGURATION WIZARD
#     Tab 0 — YouTube Music  : fully editable  (browser.json)
#     Tab 1 — Apple Music    : fully editable  (.env)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ConfigWizard:
    """
    Flet overlay dialog for platform credential management.

    YouTube Music and Apple Music panels are fully editable.
    "Guardar y Aplicar" writes YouTube Music (browser.json) and
    Apple Music (.env).
    """

    # Platform name → panel index
    _PLATFORM_INDEX = {
        "YouTube Music": 0,
        "Apple Music":   1,
        "Spotify":       2,
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

        # YouTube Music field refs (editable)
        self._yt_auth:   Optional[ft.TextField] = None
        self._yt_cookie: Optional[ft.TextField] = None

        # Apple Music field refs (editable)
        self._am_fields: dict[str, ft.TextField] = {}

        # Spotify field refs (editable)
        self._sp_identifier: Optional[ft.TextField] = None
        self._sp_dc:         Optional[ft.TextField] = None
        self._sp_key:        Optional[ft.TextField] = None

    # ── Dialog lifecycle ───────────────────────────────────────────────

    def _show_dialog(self, dlg: ft.AlertDialog) -> None:
        self.page.show_dialog(dlg)

    def _dismiss_dialog(self, dlg: ft.AlertDialog) -> None:
        if dlg is None:
            return
        try:
            dlg.open = False
            self.page.update()
        except Exception:
            pass

    def _safe_dialog_update(self) -> None:
        try:
            if self._dlg is not None and getattr(self._dlg, "open", False):
                self._dlg.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # ── Tab management ─────────────────────────────────────────────────

    def _resolve_initial_tab(
        self, failed_platforms: set[str], initial_platform: Optional[str]
    ) -> int:
        if initial_platform and initial_platform in self._PLATFORM_INDEX:
            return self._PLATFORM_INDEX[initial_platform]
        if failed_platforms:
            order = ["YouTube Music", "Apple Music", "Spotify"]
            return next(
                (self._PLATFORM_INDEX[p] for p in order if p in failed_platforms), 0
            )
        return 0

    def _apply_tab_selection(self, idx: int) -> None:
        if not self._tab_panels or not self._tab_buttons:
            return
        idx = max(0, min(idx, len(self._tab_panels) - 1))
        self._active_tab_idx = idx
        if self._panel_holder is not None:
            self._panel_holder.content = self._tab_panels[idx]
        tab_order = ["YouTube Music", "Apple Music", "Spotify"]
        for i, btn in enumerate(self._tab_buttons):
            is_warn      = tab_order[i] in self._failed_platforms
            col_active   = _WARNING if is_warn else _TEXT_PRIMARY
            col_inactive = _WARNING if is_warn else _TEXT_MUTED
            btn.bgcolor  = "#14FFFFFF" if i == idx else "transparent"
            row = btn.content
            row.controls[0].color  = col_active   if i == idx else col_inactive
            row.controls[1].color  = col_active   if i == idx else col_inactive
            row.controls[1].font_family = (
                "IBM Plex Sans SemiBold" if i == idx else "IBM Plex Sans"
            )
        self._safe_dialog_update()
        try:
            self.page.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

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
        color = _WARNING if warn else (
            _TEXT_PRIMARY if idx == self._active_tab_idx else _TEXT_MUTED
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
            bgcolor="#14FFFFFF" if idx == self._active_tab_idx else "transparent",
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
        self._is_saving = True

        async def _save_and_close() -> None:
            try:
                await asyncio.to_thread(self._apply_save)
                self._close_wizard()
                if self.on_saved:
                    self.on_saved()
            except Exception as ex:  # pylint: disable=broad-exception-caught
                print(f"[ConfigWizard] Error al guardar: {ex}")
            finally:
                self._is_saving = False

        asyncio.create_task(_save_and_close())

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

        self._tab_panels       = []
        self._tab_buttons      = []
        self._panel_holder     = None
        self._failed_platforms = set()
        self._active_tab_idx   = 0

        if results:
            for r in results:
                if not r.ok:
                    self._failed_platforms.add(r.platform)

        _initial_idx = self._resolve_initial_tab(self._failed_platforms, initial_platform)
        self._active_tab_idx = _initial_idx

        panels = [
            self._panel_youtube(warn="YouTube Music" in self._failed_platforms),
            self._panel_apple(warn="Apple Music" in self._failed_platforms),
            self._panel_spotify(warn="Spotify" in self._failed_platforms),
        ]
        self._tab_panels   = panels
        self._panel_holder = ft.Container(content=panels[_initial_idx], expand=True)

        TAB_LABELS = [
            ("YouTube Music", ft.Icons.MUSIC_VIDEO,  ft.Icons.WARNING_AMBER_ROUNDED, "YouTube Music"),
            ("Apple Music",   ft.Icons.APPLE,        ft.Icons.WARNING_AMBER_ROUNDED, "Apple Music"),
            ("Spotify",       ft.Icons.MUSIC_NOTE,   ft.Icons.WARNING_AMBER_ROUNDED, "Spotify"),
        ]
        self._tab_buttons = [
            self._make_tab_btn(i, lbl, ico_ok, ico_warn, plat)
            for i, (lbl, ico_ok, ico_warn, plat) in enumerate(TAB_LABELS)
        ]

        body = ft.Column(
            controls=[
                ft.Container(
                    content=ft.Row(controls=self._tab_buttons, spacing=4),
                    bgcolor="#08FFFFFF",
                    border_radius=10,
                    padding=ft.Padding.all(4),
                    border=ft.Border.all(0.8, "#14FFFFFF"),
                ),
                ft.Container(
                    content=self._panel_holder,
                    expand=True,
                    bgcolor=_BG_SURFACE,
                    border_radius=8,
                    padding=ft.Padding.all(0),
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                ),
            ],
            spacing=8,
            expand=True,
        )

        self._dlg = ft.AlertDialog(
            modal=True,
            scrollable=False,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS, color=_ACCENT, size=18),
                    ft.Text(
                        "Configuración de Credenciales",
                        size=14, font_family="IBM Plex Sans Bold",
                        color=_TEXT_PRIMARY,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=body,
                width=620,
                height=480,
                bgcolor=_BG_SURFACE,
                border_radius=10,
                padding=ft.Padding.all(8),
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            ),
            actions=[
                ft.TextButton(
                    "Guardar y Aplicar",
                    icon=ft.Icons.SAVE_OUTLINED,
                    on_click=self._on_save_click,
                    style=ft.ButtonStyle(color={ft.ControlState.DEFAULT: _ACCENT}),
                ),
                ft.TextButton(
                    "Cerrar",
                    on_click=self._on_close_click,
                    style=ft.ButtonStyle(color={ft.ControlState.DEFAULT: _TEXT_MUTED}),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=_BG_PANEL,
            shape=ft.RoundedRectangleBorder(radius=14),
        )
        self._apply_tab_selection(_initial_idx)
        self._show_dialog(self._dlg)

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
                            str(num), size=10, color=_ACCENT,
                            font_family="IBM Plex Sans Bold",
                        ),
                        bgcolor="#18FFFFFF",
                        border_radius=20,
                        width=20, height=20,
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                label, size=11, color=_TEXT_PRIMARY,
                                font_family="IBM Plex Sans Bold",
                            ),
                            ft.Text(
                                body, size=11, color=_TEXT_MUTED,
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
            bgcolor="#0AFFFFFF",
            border=ft.Border.all(0.8, "#14FFFFFF"),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        )

    # ── Tab 0: YouTube Music (editable) ───────────────────────────────

    def _panel_youtube(self, warn: bool = False) -> ft.Container:
        bj = read_browser_json()
        self._yt_auth = ft.TextField(
            label="Authorization (SAPISIDHASH …)",
            value=bj.get("Authorization", ""),
            multiline=True, min_lines=2, max_lines=3,
            **self._field_style(),
        )
        self._yt_cookie = ft.TextField(
            label="Cookie",
            value=bj.get("Cookie", ""),
            multiline=True, min_lines=4, max_lines=6,
            **self._field_style(),
        )
        hint = (
            self._warn_banner(
                "Token expirado. Actualiza Authorization y Cookie desde "
                "music.youtube.com → DevTools → Network."
            ) if warn else ft.Container(height=0)
        )
        instructions = self._instructions_box([
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
        ])
        return ft.Container(
            content=ft.Column(
                controls=[
                    hint,
                    instructions,
                    self._section("BROWSER.JSON — CAMPOS VARIABLES"),
                    self._yt_auth,
                    self._yt_cookie,
                    self._fixed_note(
                        "Los campos fijos (Accept, Content-Type, X-Goog-AuthUser, x-origin) "
                        "se escriben automáticamente."
                    ),
                ],
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

        controls.append(self._instructions_box([
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
        ]))

        controls.append(self._section("APPLE MUSIC — .env"))
        for key in ENV_KEYS_APPLE:
            tf = ft.TextField(
                label=key,
                value=env.get(key, ""),
                password=True,
                can_reveal_password=True,
                **self._field_style(),
            )
            self._am_fields[key] = tf
            controls.append(tf)

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
        self._sp_identifier = ft.TextField(
            label="Identifier (email o username)",
            value=sc.get("identifier", ""),
            **self._field_style(),
        )
        self._sp_dc = ft.TextField(
            label="sp_dc",
            value=cookies.get("sp_dc", ""),
            password=True, can_reveal_password=True,
            **self._field_style(),
        )
        self._sp_key = ft.TextField(
            label="sp_key",
            value=cookies.get("sp_key", ""),
            password=True, can_reveal_password=True,
            **self._field_style(),
        )
        hint = (
            self._warn_banner(
                "Cookies expiradas. Actualiza identifier, sp_dc y sp_key desde "
                "open.spotify.com → DevTools → Application."
            ) if warn else ft.Container(height=0)
        )
        instructions = self._instructions_box([
            ("Abre Spotify Web y pulsa F12",
             "Ve a open.spotify.com e inicia sesión."),
            ("Ve a Application → Cookies",
             "En DevTools abre la pestaña Application → Storage → Cookies "
             "→ https://open.spotify.com."),
            ("Copia sp_dc y sp_key",
             "Busca las cookies sp_dc y sp_key y copia sus valores en los "
             "campos correspondientes."),
            ("Pega el identifier",
             "Escribe tu email o username de Spotify en el campo identifier."),
        ])
        return ft.Container(
            content=ft.Column(
                controls=[
                    hint,
                    instructions,
                    self._section("SPOTIFY_COOKIES.JSON — CAMPOS VARIABLES"),
                    self._sp_identifier,
                    self._sp_dc,
                    self._sp_key,
                    self._fixed_note(
                        "Solo se necesitan cookies para crear playlists; "
                        "la búsqueda y el fetch de playlists públicas funcionan sin login."
                    ),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=ft.Padding.all(12),
            expand=True,
        )

    # ── Save logic (YouTube Music + Apple Music + Spotify) ─────────────

    def _apply_save(self) -> None:
        # browser.json — YouTube Music
        if self._yt_auth and self._yt_cookie:
            write_browser_json(
                self._yt_auth.value   or "",
                self._yt_cookie.value or "",
            )
        # .env — Apple Music only
        am_vals = {k: tf.value or "" for k, tf in self._am_fields.items()}
        if am_vals:
            write_env_values(am_vals)
        # spotify_cookies.json — Spotify
        if getattr(self, "_sp_identifier", None) and getattr(self, "_sp_dc", None) \
                and getattr(self, "_sp_key", None):
            write_spotify_cookies(
                self._sp_identifier.value or "",
                self._sp_dc.value  or "",
                self._sp_key.value or "",
            )

    # ── UI helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _field_style() -> dict:
        return {
            "bgcolor":              "#08FFFFFF",
            "border_color":        "#18FFFFFF",
            "focused_border_color": _ACCENT,
            "label_style":  ft.TextStyle(color=_TEXT_MUTED, size=10, font_family="IBM Plex Sans"),
            "text_style":   ft.TextStyle(color=_TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
            "border_radius": 8,
        }

    @staticmethod
    def _section(text: str) -> ft.Text:
        return ft.Text(
            text, size=8, color=_TEXT_DIM,
            font_family="IBM Plex Sans Bold",
            style=ft.TextStyle(letter_spacing=1.2),
        )

    @staticmethod
    def _fixed_note(text: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(text, size=9, color=_TEXT_DIM, font_family="IBM Plex Sans"),
            bgcolor="#06FFFFFF",
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

    @staticmethod
    def _warn_banner(text: str) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=_WARNING, size=14),
                    ft.Text(
                        text, size=10, color=_WARNING,
                        font_family="IBM Plex Sans", expand=True,
                    ),
                ],
                spacing=6,
            ),
            bgcolor="#120C0000",
            border=ft.Border.all(0.8, "#30FFA500"),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §5  AUTH MANAGER  (service-level coordinator)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AuthManager:
    """
    High-level auth coordinator used by app.py.

    1. run_startup_check()        — parallel pre-flight on all platforms.
    2. open_wizard(platform)      — opens ConfigWizard (routes to correct tab).
    3. refresh_session_icons()    — revalidates and updates UI icons.
    4. reload_credentials()       — hot-reloads .env / browser.json and re-inits services.
    """

    def __init__(self, page: ft.Page, service, state) -> None:
        self.page    = page
        self.service = service

        # Accept AppState or a bound _log method (compatibility)
        if hasattr(state, "notify"):
            self.state        = state
            self.state_log_fn = state._log
        elif callable(state) and getattr(state, "__self__", None) is not None \
                and hasattr(state.__self__, "notify"):
            self.state        = state.__self__
            self.state_log_fn = state
        else:
            raise TypeError(
                "AuthManager(page, service, state): el tercer argumento debe ser "
                "el objeto AppState (p. ej. state), no state._log ni otro valor."
            )

        self._wizard = ConfigWizard(
            page, auth_manager=self, on_saved=self._on_wizard_saved
        )
        self._last_results: list[PreFlightResult] = []
        self._reload_task:  Optional[asyncio.Task] = None  # tracked for hard_cleanup

    # ── Pre-flight / session management ───────────────────────────────

    async def check_all_sessions(self) -> list[PreFlightResult]:
        """Parallel pre-flight on all platforms."""
        return await run_preflight()

    def ingest_preflight_results(self, results: list[PreFlightResult]) -> None:
        """Cache results, update AppState auth flags and notify the UI."""
        self._last_results = results
        self._sync_auth_ui_state(results)
        self.state.notify()

    async def refresh_session_icons(self) -> list[PreFlightResult]:
        """Re-run check_all_sessions and push results to the UI icons."""
        results = await self.check_all_sessions()
        self.ingest_preflight_results(results)
        return results

    def _sync_auth_ui_state(self, results: list[PreFlightResult]) -> None:
        for r in results:
            self.state.auth_session_ok[r.platform]   = r.ok
            self.state.auth_session_hint[r.platform] = (
                "" if r.ok else auth_failure_tooltip(r)
            )

    async def run_startup_check(self) -> list[PreFlightResult]:
        """
        Parallel pre-flight + conditional service init.
        Expired platforms open the wizard on their respective tab.
        """
        self.state_log_fn("[INFO] Pre-flight: verificando credenciales…")
        results = await self.check_all_sessions()
        self._last_results = results
        self._sync_auth_ui_state(results)

        need_wizard_for: list[str] = []

        for r in results:
            if r.ok:
                self.state_log_fn(f"[INFO]  ✓ {r.platform}: OK")
            elif r.expired:
                need_wizard_for.append(r.platform)
                self.state_log_fn(
                    f"[ERROR] ⚠ {r.platform}: credenciales expiradas — "
                    "actualiza las credenciales en la configuración"
                )
            else:
                self.state_log_fn(f"[WARN]  – {r.platform}: {r.error}")

        await self._init_passing_services(results)

        if need_wizard_for:
            first_fail = need_wizard_for[0]

            async def _open_wizard_deferred() -> None:
                await asyncio.sleep(random.uniform(2.0, 4.0))
                self.open_wizard(first_fail)

            asyncio.create_task(_open_wizard_deferred())

        return results

    async def _init_passing_services(self, results: list[PreFlightResult]) -> None:
        tasks = []
        for r in results:
            if r.ok:
                if r.platform == "YouTube Music":
                    tasks.append(self.service.init_youtube())
                elif r.platform == "Apple Music":
                    tasks.append(self.service.init_apple())
                elif r.platform == "Spotify":
                    tasks.append(self.service.init_spotify())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── Wizard ─────────────────────────────────────────────────────────

    def open_wizard(self, platform: Optional[str] = None) -> None:
        """
        Open the ConfigWizard.
        If *platform* is given, the corresponding tab is shown first.
        """
        def _go() -> None:
            try:
                self._wizard.open(self._last_results or None, initial_platform=platform)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                self.state_log_fn(f"[ERROR] Wizard: {ex}")

        try:
            asyncio.get_running_loop().call_soon(_go)
        except RuntimeError:
            _go()

    def _on_wizard_saved(self) -> None:
        """Called by ConfigWizard after the user clicks 'Guardar y Aplicar'."""
        self._reload_task = asyncio.create_task(self.reload_credentials())

    # ── Hot-reload ─────────────────────────────────────────────────────

    async def reload_credentials(self) -> None:
        """
        Hot-reload credentials from disk and reinitialise all services.
        No process restart required.
        """
        self.state_log_fn("[INFO] Recargando credenciales…")
        load_dotenv(str(ENV_FILE), override=True)

        init_results = await asyncio.gather(
            self.service.init_youtube(),
            self.service.init_apple(),
            self.service.init_spotify(),
            return_exceptions=True,
        )
        for plat, res in zip(["YouTube Music", "Apple Music", "Spotify"], init_results):
            if res is True:
                self.state_log_fn(f"[SUCCESS] ✓ {plat}: reconectado")
            else:
                self.state_log_fn(f"[ERROR]   – {plat}: {res}")

        chk = await self.check_all_sessions()
        self._last_results = chk
        self._sync_auth_ui_state(chk)
        self.state.notify()


