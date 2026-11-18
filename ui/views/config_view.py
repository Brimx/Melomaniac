"""
ui/views/config_view.py — Módulo Config: 8 secciones con construcción perezosa
═════════════════════════════════════════════════════════════════════════════
Navegación lateral de 8 secciones (Conexiones, Reproducción, Descargas,
Biblioteca, Accesibilidad, Apariencia, Datos, Acerca de) con construcción bajo
demanda (solo instancia la sección visible), layout responsivo (<1100 px nav
horizontal superior, >1440 px cuerpo centrado y 2 columnas en Acerca de) y
probes de disco/red fuera del hilo UI.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

import flet as ft

from core.config import PLATFORM_ORDER
from services import browser_session, desktop_integration, settings_store, update_checker
from services.authentication import (
    PreFlightResult,
    SEARCH_CACHE_JSON,
    TRANSFER_HISTORY_JSONL,
    preflight_result,
    read_browser_json,
    read_env_values,
    read_spotify_cookies,
)
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    AppSwitch,
    app_dialog,
    app_text,
    close_dialog,
    kicker,
    notify,
    open_external,
    safe_update,
    show_dialog,
)
from ui.theme.fonts import FONT_HEADLINE, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    ACCENT_HALO,
    BG_HOVER,
    BG_LIST,
    BG_PANEL,
    BG_SURFACE,
    BORDER_CONTROL,
    BORDER_MUTED,
    BORDER_ROW,
    FS_META,
    MOTION_HOVER_MS,
    RADIUS_SHARP,
    RADIUS_SM,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_5,
    SUCCESS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
)
from ui.views.config_sections import (
    CONFIG_SECTIONS,
    build_acerca_section,
    build_accesibilidad_section,
    build_apariencia_section,
    build_biblioteca_section,
    build_conexiones_section,
    build_datos_section,
    build_descargas_section,
    build_reproduccion_section,
    cfg_badge,
)

_COMPACT_PAGE_BP = 1100.0
_COMPACT_VIEW_BP = 1010.0
_WIDE_PAGE_BP = 1440.0
_WIDE_VIEW_BP = 1320.0
_BODY_MAX_W = 780
_BODY_WIDE_ACERCA_W = 980


class _StandaloneAuthAdapter:
    """Adaptador mínimo cuando ConfigView abre ConfigWizard sin AuthManager inyectado."""

    def __init__(self, get_service: Callable[[], Any]) -> None:
        self._get_service = get_service
        self.state = None

    @property
    def service(self):
        return self._get_service()

    def state_log_fn(self, _msg: str) -> None:
        pass

    async def check_sessions(self, platforms: list[str]) -> list[PreFlightResult]:
        svc = self._get_service()
        out: list[PreFlightResult] = []
        for plat in platforms:
            out.append(await _probe_platform_session(svc, plat))
        return out

    def ingest_preflight_results(self, _results: list[PreFlightResult]) -> None:
        pass


async def _probe_platform_session(service: Any, platform: str) -> PreFlightResult:
    """Ejecuta init_* de una plataforma sobre ``MusicApiService`` y devuelve ``PreFlightResult``."""
    if service is None:
        return preflight_result(platform, False, "Servicio de API no disponible en este contexto")
    try:
        if platform == "YouTube Music":
            ok = await service.init_youtube()
            return preflight_result(platform, ok, service.youtube_auth_error, service.youtube_session_source)
        if platform == "Apple Music":
            ok = await service.init_apple()
            return preflight_result(platform, ok, service.apple_auth_error, service.apple_session_source)
        if platform == "Spotify":
            ok = await service.init_spotify()
            return preflight_result(platform, ok, service.spotify_auth_error, service.spotify_session_source)
    except Exception as exc:  # noqa: BLE001
        return preflight_result(platform, False, str(exc))
    return preflight_result(platform, False, "Plataforma no soportada")


class ConfigView(ft.Container):
    """Vista completa de Configuración dividida en 8 secciones perezosas."""

    def __init__(
        self,
        page: ft.Page,
        on_change=None,
        on_welcome=None,
        *,
        auth_manager=None,
        service=None,
        library_state=None,
        on_open_wizard=None,
    ) -> None:
        super().__init__(expand=True, bgcolor=BG_LIST, on_size_change=self._on_size_change)
        self._page_ref = page
        self._on_change = on_change
        self._on_welcome = on_welcome
        self._auth_manager = auth_manager
        self._service = service
        self._library_state = library_state
        self._on_open_wizard = on_open_wizard

        self._picker = ft.FilePicker()
        if hasattr(page, "services") and isinstance(page.services, list):
            page.services.append(self._picker)

        self._active_section: str = CONFIG_SECTIONS[0][0]
        self._refresh_gen: int = 0
        self._view_width: float = 0.0
        self._is_compact, self._is_wide = self._compute_breakpoints()

        # Caché en memoria de probes asíncronos (sin I/O en __init__)
        self._browser_profiles: list[Any] = []
        self._unsupported_browsers: list[str] = []
        self._provider_status: dict[str, dict[str, str]] = {
            p: {"kind": "muted", "label": "Sin comprobar", "meta": "Comprobando configuración…"}
            for p in PLATFORM_ORDER
        }
        self._last_cache_text: str = ""
        self._folder_counts: dict[str, int] = {}
        self._data_stats: dict[str, str] = {}

        # Referencias mutables de la sección activa (se reasignan al construir la sección)
        self._provider_ui: dict[str, dict[str, Any]] = {}
        self._browser_dd: ft.Dropdown | None = None
        self._browser_note: ft.Text | None = None
        self._vol_label: ft.Text | None = None
        self._format_dd: ft.Dropdown | None = None
        self._dir_text: ft.Text | None = None
        self._dir_reset_btn: ft.Control | None = None
        self._local_folders_col: ft.Column | None = None
        self._reindex_btn: ft.Control | None = None
        self._reindex_status: ft.Text | None = None
        self._covers_sw: AppSwitch | None = None
        self._cover_refresh_text: ft.Text | None = None
        self._cover_refresh_btn: ft.Control | None = None
        self._cache_text: ft.Text | None = None
        self._cache_btn: ft.Control | None = None
        self._theme_group: ft.RadioGroup | None = None
        self._theme_tiles: dict[str, ft.Container] = {}
        self._search_cache_text: ft.Text | None = None
        self._local_index_text: ft.Text | None = None
        self._history_text: ft.Text | None = None
        self._rebuild_index_btn: ft.Control | None = None
        self._version_text: ft.Text | None = None
        self._update_check_btn: ft.Control | None = None
        self._update_result_col: ft.Column | None = None
        self._shortcut_status_text: ft.Text | None = None
        self._shortcut_location_text: ft.Text | None = None
        self._shortcut_warning_text: ft.Text | None = None
        self._shortcut_actions_row: ft.Row | None = None

        self._nav_items: dict[str, dict[str, Any]] = {}
        self.content = self._build()

    @property
    def auth_manager(self):
        return self._auth_manager

    @auth_manager.setter
    def auth_manager(self, value) -> None:
        self._auth_manager = value

    @property
    def service(self):
        return self._service

    @service.setter
    def service(self, value) -> None:
        self._service = value

    # ── Responsividad y estructura ─────────────────────────────────────

    def _compute_breakpoints(self, view_width: float = 0.0) -> tuple[bool, bool]:
        page_w = float(getattr(self._page_ref, "width", 0) or 0)
        if page_w <= 0:
            win = getattr(self._page_ref, "window", None)
            page_w = float(getattr(win, "width", 0) or 0)
        if view_width > 0:
            compact = view_width < _COMPACT_VIEW_BP or (0 < page_w < _COMPACT_PAGE_BP)
            wide = (view_width > _WIDE_VIEW_BP or page_w > _WIDE_PAGE_BP) and not compact
            return compact, wide
        if page_w > 0:
            return page_w < _COMPACT_PAGE_BP, page_w > _WIDE_PAGE_BP
        return False, False

    def _on_size_change(self, e: ft.LayoutSizeChangeEvent) -> None:
        try:
            w = float(getattr(e, "width", 0) or 0)
        except (TypeError, ValueError):
            return
        if w <= 0:
            return
        self._view_width = w
        compact, wide = self._compute_breakpoints(w)
        if compact == self._is_compact and wide == self._is_wide:
            return
        self._is_compact = compact
        self._is_wide = wide
        self._rebuild_shell_layout()
        if self._active_section == "acerca":
            self._rebuild_active_section()
        safe_update(self)

    def _build_nav_controls(self, *, horizontal: bool) -> ft.Control:
        self._nav_items = {}
        items: list[ft.Control] = []
        for sec_id, num, label, icon_glyph in CONFIG_SECTIONS:
            active = sec_id == self._active_section
            num_txt = app_text(
                num, size=FS_META, color=ACCENT if active else TEXT_MUTED,
                font_family=mono_family("light"), visible=not horizontal,
            )
            ico_ctrl = app_icon(
                icon_glyph, size=15, color=ACCENT if active else TEXT_MUTED, fill=active,
            )
            lbl_txt = app_text(
                label, size=12, color=TEXT_PRIMARY if active else TEXT_MUTED,
                font_family=FONT_HEADLINE if active else FONT_TEXT,
                no_wrap=True,
            )
            row = ft.Row(
                controls=[num_txt, ico_ctrl, lbl_txt] if not horizontal else [ico_ctrl, lbl_txt],
                spacing=SPACE_2 if not horizontal else SPACE_1,
                tight=horizontal,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            btn = ft.Container(
                content=row,
                bgcolor=ACCENT_HALO if (active and horizontal) else (BG_LIST if active else ft.Colors.TRANSPARENT),
                border=(
                    ft.Border.all(0.8, ACCENT if active else BORDER_CONTROL)
                    if horizontal
                    else ft.Border.only(left=ft.BorderSide(2.0, ACCENT if active else ft.Colors.TRANSPARENT))
                ),
                border_radius=RADIUS_SHARP,
                padding=ft.Padding.symmetric(horizontal=10 if horizontal else 12, vertical=7 if horizontal else 9),
                data=sec_id,
                on_click=lambda _e, sid=sec_id: self.select_section(sid),
                animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            )
            self._nav_items[sec_id] = {
                "btn": btn,
                "num": num_txt,
                "icon": ico_ctrl,
                "label": lbl_txt,
            }
            items.append(btn)

        if horizontal:
            return ft.Container(
                padding=ft.Padding.only(bottom=6),
                content=ft.Row(controls=items, wrap=True, spacing=SPACE_2, run_spacing=SPACE_2),
            )
        return ft.Container(
            width=210,
            content=ft.Column(
                controls=[
                    ft.Container(
                        padding=ft.Padding.only(left=12, bottom=4),
                        content=kicker("SECCIONES"),
                    ),
                    *items,
                ],
                spacing=SPACE_1,
                tight=True,
            ),
        )

    def _sync_nav_selection(self) -> None:
        horizontal = self._is_compact
        for sec_id, refs in self._nav_items.items():
            active = sec_id == self._active_section
            btn: ft.Container = refs["btn"]
            btn.bgcolor = ACCENT_HALO if (active and horizontal) else (BG_LIST if active else ft.Colors.TRANSPARENT)
            btn.border = (
                ft.Border.all(0.8, ACCENT if active else BORDER_CONTROL)
                if horizontal
                else ft.Border.only(left=ft.BorderSide(2.0, ACCENT if active else ft.Colors.TRANSPARENT))
            )
            refs["num"].color = ACCENT if active else TEXT_MUTED
            refs["icon"].color = ACCENT if active else TEXT_MUTED
            refs["label"].color = TEXT_PRIMARY if active else TEXT_MUTED
            refs["label"].font_family = FONT_HEADLINE if active else FONT_TEXT

    def _body_max_width(self) -> int:
        if self._is_wide and self._active_section == "acerca":
            return _BODY_WIDE_ACERCA_W
        return _BODY_MAX_W

    def _build_section_controls(self, section_id: str) -> list[ft.Control]:
        if section_id == "conexiones":
            return build_conexiones_section(self)
        if section_id == "reproduccion":
            return build_reproduccion_section(self)
        if section_id == "descargas":
            return build_descargas_section(self)
        if section_id == "biblioteca":
            return build_biblioteca_section(self)
        if section_id == "accesibilidad":
            return build_accesibilidad_section(self)
        if section_id == "apariencia":
            return build_apariencia_section(self)
        if section_id == "datos":
            return build_datos_section(self)
        return build_acerca_section(self, wide=self._is_wide)

    def _rebuild_active_section(self) -> None:
        self._body_col.controls = self._build_section_controls(self._active_section)
        self._body_frame.width = None if self._is_compact else self._body_max_width()
        self._body_align.alignment = ft.Alignment.TOP_CENTER if self._is_wide else ft.Alignment.TOP_LEFT

    def _rebuild_shell_layout(self) -> None:
        self._nav_ctrl = self._build_nav_controls(horizontal=self._is_compact)
        self._body_frame.width = None if self._is_compact else self._body_max_width()
        self._body_align.alignment = ft.Alignment.TOP_CENTER if self._is_wide else ft.Alignment.TOP_LEFT
        if self._is_compact:
            self._layout_host.content = ft.Column(
                controls=[self._nav_ctrl, self._body_scroll],
                spacing=SPACE_3,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        else:
            self._layout_host.content = ft.Row(
                controls=[self._nav_ctrl, self._body_scroll],
                spacing=SPACE_5,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )

    def _build(self) -> ft.Control:
        header = ft.Row(
            controls=[
                app_icon(PIcons.GEAR, size=18, color=TEXT_MUTED),
                app_text("Configuración", size=16, color=TEXT_PRIMARY, font_family=FONT_HEADLINE),
            ],
            spacing=SPACE_2,
        )
        self._body_col = ft.Column(
            controls=self._build_section_controls(self._active_section),
            spacing=SPACE_3,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self._body_frame = ft.Container(
            content=self._body_col,
            width=None if self._is_compact else self._body_max_width(),
        )
        self._body_align = ft.Container(
            content=self._body_frame,
            alignment=ft.Alignment.TOP_CENTER if self._is_wide else ft.Alignment.TOP_LEFT,
        )
        self._body_scroll = ft.Column(
            controls=[self._body_align],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self._layout_host = ft.Container(expand=True)
        self._rebuild_shell_layout()

        return ft.Container(
            expand=True,
            padding=ft.Padding.all(16),
            content=ft.Column(
                controls=[
                    header,
                    ft.Divider(height=1, color=BORDER_MUTED),
                    self._layout_host,
                ],
                spacing=SPACE_3,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

    def select_section(self, section_id: str, *, update: bool = True) -> None:
        """Cambia la sección visible instanciando solo sus controles."""
        valid_ids = {s[0] for s in CONFIG_SECTIONS}
        if section_id not in valid_ids or section_id == self._active_section:
            return
        self._active_section = section_id
        self._sync_nav_selection()
        self._rebuild_active_section()
        self._schedule_probe()
        if update:
            safe_update(self)

    # ── Estado y probes asíncronos ─────────────────────────────────────

    @property
    def _download_dir(self) -> str:
        from engine.downloader import resolve_download_dir

        try:
            return str(resolve_download_dir(settings_store.get_download_dir() or None, create=False))
        except Exception:
            return settings_store.get_download_dir() or ""

    @staticmethod
    def _cache_size_text() -> str:
        from services import cover_cache

        try:
            total = sum(f.stat().st_size for f in cover_cache.CACHE_DIR.iterdir() if f.is_file())
        except OSError:
            return "Sin caché de portadas"
        if not total:
            return "Sin caché de portadas"
        return f"{total / (1024 * 1024):.1f} MB en caché de portadas"

    def _is_mounted(self) -> bool:
        try:
            return self.page is not None
        except RuntimeError:
            return False

    def refresh(self, *, update: bool = True) -> None:
        """Recarga controles visibles desde ``settings_store`` y lanza probe en segundo plano."""
        self._sync_visible_controls()
        if update:
            safe_update(self)
        self._schedule_probe()

    def _sync_visible_controls(self) -> None:
        if self._format_dd is not None:
            self._format_dd.value = settings_store.get_download_format()
        if self._covers_sw is not None:
            self._covers_sw.value = settings_store.get_covers_online()
        if self._dir_text is not None:
            self._dir_text.value = self._download_dir
        if self._dir_reset_btn is not None:
            self._dir_reset_btn.visible = bool(settings_store.get_download_dir())
        if self._active_section == "biblioteca" and self._local_folders_col is not None:
            self._render_local_folders_list()
        if self._active_section == "conexiones":
            for plat in PLATFORM_ORDER:
                self._apply_provider_status_ui(plat)

    def _schedule_probe(self) -> None:
        if self._active_section not in {"conexiones", "biblioteca", "datos", "acerca"}:
            return
        self._refresh_gen += 1
        gen = self._refresh_gen
        try:
            self._page_ref.run_task(self._refresh_async, gen, self._active_section)
        except Exception:
            try:
                asyncio.get_running_loop().create_task(self._refresh_async(gen, self._active_section))
            except Exception:
                pass

    async def _refresh_async(self, gen: int, section: str) -> None:
        if section == "conexiones":
            profiles, unsupported, overrides = await asyncio.to_thread(self._probe_connections)
            if gen != self._refresh_gen or not self._is_mounted() or self._active_section != "conexiones":
                return
            self._browser_profiles = profiles
            self._unsupported_browsers = unsupported
            self._ingest_connection_probe(profiles, overrides)
            self._refresh_connections_ui()
        elif section == "biblioteca":
            cache_text, counts = await asyncio.to_thread(self._probe_library)
            if gen != self._refresh_gen or not self._is_mounted() or self._active_section != "biblioteca":
                return
            self._last_cache_text = cache_text
            self._folder_counts = counts
            if self._cache_text is not None:
                self._cache_text.value = cache_text
            self._render_local_folders_list()
        elif section == "datos":
            stats = await asyncio.to_thread(self._probe_data_stats)
            if gen != self._refresh_gen or not self._is_mounted() or self._active_section != "datos":
                return
            self._data_stats = stats
            if self._search_cache_text is not None:
                self._search_cache_text.value = stats["search_cache"]
            if self._local_index_text is not None:
                self._local_index_text.value = stats["local_index"]
            if self._history_text is not None:
                self._history_text.value = stats["history"]
        elif section == "acerca":
            env, st = await asyncio.to_thread(
                lambda: (desktop_integration.get_runtime_environment(), desktop_integration.get_shortcut_status())
            )
            if gen != self._refresh_gen or not self._is_mounted() or self._active_section != "acerca":
                return
            self._apply_shortcut_ui(env, st)
        safe_update(self)

    # ── Probes síncronos ejecutados en asyncio.to_thread ───────────────

    def _probe_connections(self) -> tuple[list[Any], list[str], dict[str, bool]]:
        profiles = browser_session.detect()
        unsupported = browser_session.unsupported_browsers()
        bj = read_browser_json()
        env = read_env_values()
        sc = read_spotify_cookies()
        sp_c = sc.get("cookies", {}) if isinstance(sc, dict) else {}
        overrides = {
            "YouTube Music": bool(str(bj.get("Cookie", "") or "").strip() or str(bj.get("Authorization", "") or "").strip()),
            "Apple Music": bool(env.get("APPLE_MUSIC_USER_TOKEN", "").strip()),
            "Spotify": bool(isinstance(sp_c, dict) and sp_c.get("sp_dc") and sp_c.get("sp_key")),
        }
        return profiles, unsupported, overrides

    def _ingest_connection_probe(self, profiles: list[Any], overrides: dict[str, bool]) -> None:
        last_results = {
            r.platform: r
            for r in (getattr(self._auth_manager, "_last_results", None) or [])
        }
        prof_label = profiles[0].label if profiles else ""
        for plat in PLATFORM_ORDER:
            res = last_results.get(plat)
            if res is not None:
                self._provider_status[plat] = {
                    "kind": "ok" if res.ok else ("warn" if not res.expired else "err"),
                    "label": "Válida" if res.ok else "Revisar",
                    "meta": f"Origen: {res.source}" if (res.ok and res.source) else (res.error[:90] or "Sin verificar"),
                }
            elif overrides.get(plat):
                self._provider_status[plat] = {
                    "kind": "ok",
                    "label": "Override",
                    "meta": "Credencial manual detectada en config/",
                }
            elif prof_label:
                self._provider_status[plat] = {
                    "kind": "warn",
                    "label": "Navegador",
                    "meta": f"Perfil detectado: {prof_label} · pulsa Verificar",
                }
            else:
                self._provider_status[plat] = {
                    "kind": "muted",
                    "label": "Sin sesión",
                    "meta": "Sin navegador ni override manual detectado",
                }

    def _probe_library(self) -> tuple[str, dict[str, int]]:
        from services.local_index import SYNC_SCAN_LIMIT, quick_count

        counts: dict[str, int] = {}
        for folder in settings_store.get_local_folders():
            try:
                counts[folder] = quick_count(folder, limit=SYNC_SCAN_LIMIT)
            except Exception:
                counts[folder] = 0
        return self._cache_size_text(), counts

    @staticmethod
    def _probe_data_stats() -> dict[str, str]:
        from services.local_index import LOCAL_INDEX_JSON

        def _fmt_bytes(num: int) -> str:
            if num < 1024:
                return f"{num} B"
            if num < 1024 * 1024:
                return f"{num / 1024:.1f} KB"
            return f"{num / (1024 * 1024):.1f} MB"

        search_txt = "Vacía (0 entradas)"
        if SEARCH_CACHE_JSON.is_file():
            try:
                sz = SEARCH_CACHE_JSON.stat().st_size
                raw = json.loads(SEARCH_CACHE_JSON.read_text(encoding="utf-8"))
                n = len(raw) if isinstance(raw, dict) else 0
                search_txt = f"{n} entradas · {_fmt_bytes(sz)}"
            except (OSError, ValueError):
                search_txt = "Archivo ilegible"

        idx_txt = "Sin índice aún"
        if LOCAL_INDEX_JSON.is_file():
            try:
                sz = LOCAL_INDEX_JSON.stat().st_size
                raw = json.loads(LOCAL_INDEX_JSON.read_text(encoding="utf-8"))
                entries = raw.get("entries", raw) if isinstance(raw, dict) else {}
                n = len(entries) if isinstance(entries, dict) else 0
                idx_txt = f"{n} pistas · {_fmt_bytes(sz)}"
            except (OSError, ValueError):
                idx_txt = "Archivo ilegible"

        hist_txt = "0 informes"
        if TRANSFER_HISTORY_JSONL.is_file():
            try:
                with TRANSFER_HISTORY_JSONL.open(encoding="utf-8") as fh:
                    n = sum(1 for ln in fh if ln.strip())
                hist_txt = f"{n} informe" + ("" if n == 1 else "s")
            except OSError:
                hist_txt = "No legible"

        return {"search_cache": search_txt, "local_index": idx_txt, "history": hist_txt}

    # ── Conexiones helpers & handlers ──────────────────────────────────

    def _browser_dropdown_options(self) -> list[tuple[str, str]]:
        opts = [("__auto__", "Automático (navegador primero)")]
        seen = {"__auto__"}
        for prof in self._browser_profiles:
            if prof.label not in seen:
                seen.add(prof.label)
                opts.append((prof.label, prof.label))
        saved = settings_store.get_preferred_browser()
        if saved and saved not in seen:
            opts.append((saved, f"{saved} (guardado)"))
        return opts

    def _browser_note_value(self) -> str:
        if not self._unsupported_browsers:
            return ""
        return "Navegadores sin soporte directo (Flatpak/Snap): " + ", ".join(self._unsupported_browsers[:3])

    def _apply_provider_status_ui(self, platform: str) -> None:
        refs = self._provider_ui.get(platform)
        if not refs:
            return
        st = self._provider_status.get(platform) or {"kind": "muted", "label": "Sin comprobar", "meta": ""}
        refs["badge"].content = cfg_badge(st["label"], st["kind"])
        refs["meta"].value = st["meta"]

    def _refresh_connections_ui(self) -> None:
        for plat in PLATFORM_ORDER:
            self._apply_provider_status_ui(plat)
        if self._browser_dd is not None:
            self._browser_dd.options = [ft.dropdown.Option(key=k, text=t) for k, t in self._browser_dropdown_options()]
            self._browser_dd.value = settings_store.get_preferred_browser() or "__auto__"
        if self._browser_note is not None:
            note = self._browser_note_value()
            self._browser_note.value = note
            self._browser_note.visible = bool(note)

    def _resolve_service(self):
        if self._auth_manager is not None and getattr(self._auth_manager, "service", None) is not None:
            return self._auth_manager.service
        return self._service

    def _on_verify_platform(self, platform: str) -> None:
        try:
            self._page_ref.run_task(self._verify_platform_async, platform)
        except Exception:
            asyncio.create_task(self._verify_platform_async(platform))

    async def _verify_platform_async(self, platform: str) -> None:
        refs = self._provider_ui.get(platform)
        if refs:
            refs["spinner"].visible = True
            refs["verify_btn"].disabled = True
            safe_update(self)
        try:
            browser_session.clear_cache()
            if self._auth_manager is not None:
                results = await self._auth_manager.check_sessions([platform])
                self._auth_manager.ingest_preflight_results(results)
            else:
                svc = self._resolve_service()
                if svc is None:
                    self._provider_status[platform] = {
                        "kind": "warn",
                        "label": "Sin servicio",
                        "meta": "Verificación en vivo requiere servicio inicializado",
                    }
                    self._apply_provider_status_ui(platform)
                    notify(self._page_ref, f"{platform}: servicio no disponible para verificar ahora", kind="error")
                    return
                results = [await _probe_platform_session(svc, platform)]
            for res in results:
                if res.platform != platform:
                    continue
                self._provider_status[platform] = {
                    "kind": "ok" if res.ok else ("err" if res.expired else "warn"),
                    "label": "Válida" if res.ok else "Revisar",
                    "meta": (
                        f"Sesión activa · {res.source}" if (res.ok and res.source)
                        else (res.error[:100] or "Sesión no confirmada")
                    ),
                }
                self._apply_provider_status_ui(platform)
                notify(
                    self._page_ref,
                    f"{platform}: sesión válida" if res.ok else f"{platform}: {res.error[:120] or 'revisar sesión'}",
                    kind="info" if res.ok else "error",
                )
        finally:
            if refs:
                refs["spinner"].visible = False
                refs["verify_btn"].disabled = False
            safe_update(self)

    def _on_open_platform_wizard(self, platform: str | None) -> None:
        if self._on_open_wizard is not None:
            self._on_open_wizard(platform)
            return
        if self._auth_manager is not None:
            self._auth_manager.open_wizard(platform)
            return
        if self._resolve_service() is None:
            notify(self._page_ref, "Asistente no disponible sin servicio inicializado", kind="error")
            return
        from ui.setup.config_wizard import ConfigWizard

        adapter = _StandaloneAuthAdapter(self._resolve_service)
        wiz = ConfigWizard(self._page_ref, adapter, on_saved=self._schedule_probe)  # type: ignore[arg-type]
        wiz.open(initial_platform=platform)

    def _on_preferred_browser_change(self, e=None) -> None:
        val = str(getattr(e.control, "value", "") or "")
        pref = "" if val == "__auto__" else val
        settings_store.set_preferred_browser(pref)
        svc = self._resolve_service()
        if svc is not None:
            for attr in ("youtube_preferred_source", "apple_preferred_source", "spotify_preferred_source"):
                if hasattr(svc, attr):
                    setattr(svc, attr, pref or None)
        browser_session.clear_cache()
        self._changed()

    # ── Reproducción, Descargas, Accesibilidad y Apariencia handlers ───

    def _persist_bool(self, setter: Callable[[bool], None], value: Any) -> None:
        setter(bool(value))
        self._changed()

    def _on_lyrics_provider_change(self, e=None) -> None:
        sel = list(getattr(e.control, "selected", None) or [])
        if sel:
            settings_store.set_lyrics_provider(str(sel[0]))
            self._changed()

    def _on_volume_sliding(self, e=None) -> None:
        pct = max(0, min(100, int(round(float(getattr(e.control, "value", 0) or 0)))))
        if self._vol_label is not None:
            self._vol_label.value = f"{pct} %"
            safe_update(self._vol_label)

    def _on_volume_commit(self, e=None) -> None:
        pct = max(0, min(100, int(round(float(getattr(e.control, "value", 0) or 0)))))
        if self._vol_label is not None:
            self._vol_label.value = f"{pct} %"
            safe_update(self._vol_label)
        settings_store.set_volume(pct / 100.0)
        self._changed()

    def _on_format_change(self, e=None) -> None:
        settings_store.set_download_format(str(getattr(e.control, "value", "") or ""))
        self._changed()

    def _on_fade_change(self, e=None) -> None:
        try:
            sec = int(getattr(e.control, "value", 0) or 0)
        except (TypeError, ValueError):
            sec = 0
        settings_store.set_fade_seconds(sec)
        self._changed()

    async def _on_pick_dir(self, e=None) -> None:
        try:
            current = self._download_dir
            path = await self._picker.get_directory_path(
                dialog_title="Carpeta de descargas",
                initial_directory=current if os.path.isdir(current) else None,
            )
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo abrir el selector: {exc}", kind="error")
            return
        if path:
            settings_store.set_download_dir(path)
            self._sync_visible_controls()
            safe_update(self)
            self._changed()

    def _on_reset_dir(self, e=None) -> None:
        settings_store.set_download_dir("")
        self._sync_visible_controls()
        safe_update(self)
        self._changed()

    def _on_library_sort_change(self, e=None) -> None:
        settings_store.set_library_sort_order(str(getattr(e.control, "value", "") or ""))
        self._changed()

    def _on_theme_change(self, e=None) -> None:
        val = str(getattr(e.control, "value", "sala") or "sala")
        settings_store.set_theme(val)
        for tid, tile in self._theme_tiles.items():
            tile.border = ft.Border.all(0.8, ACCENT if tid == val else BORDER_MUTED)
        safe_update(self._theme_group)
        if val == "sala":
            notify(self._page_ref, "Tema «Sala de escucha» activo")
        else:
            notify(self._page_ref, "Tema guardado · disponible próximamente")
        self._changed()

    # ── Biblioteca handlers (carpetas con confirmación, caché, portadas)

    def _render_local_folders_list(self) -> None:
        if self._local_folders_col is None:
            return
        folders = settings_store.get_local_folders()
        if not folders:
            self._local_folders_col.controls = [
                app_text("No hay carpetas locales configuradas.", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
            ]
            return
        rows: list[ft.Control] = []
        for folder in folders:
            count = self._folder_counts.get(folder)
            count_str = f"{count} pistas" if count is not None else "…"
            row = ft.Container(
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                bgcolor=BG_LIST,
                border=ft.Border.all(0.6, BORDER_ROW),
                border_radius=RADIUS_SM,
                content=ft.Row(
                    controls=[
                        app_icon(PIcons.FOLDER, size=16, color=TEXT_MUTED),
                        app_text(
                            folder, size=11, color=TEXT_PRIMARY, font_family=mono_family("light"),
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
                        ),
                        app_text(count_str, size=FS_META, color=TEXT_MUTED, font_family=mono_family("light")),
                        app_icon_button(
                            PIcons.X,
                            size=14,
                            width=28,
                            height=28,
                            tooltip="Quitar carpeta",
                            on_click=lambda _e, f=folder: self._on_remove_local_folder(f),
                        ),
                    ],
                    spacing=SPACE_2,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            row._isolated = True
            rows.append(row)
        self._local_folders_col.controls = rows

    async def _on_add_local_folder(self, e=None) -> None:
        try:
            path = await self._picker.get_directory_path(dialog_title="Añadir carpeta de música")
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo abrir el selector: {exc}", kind="error")
            return
        if not path:
            return
        added = settings_store.add_local_folder(path)
        if not added:
            notify(self._page_ref, "Esa carpeta ya estaba en Biblioteca")
            return
        self._render_local_folders_list()
        safe_update(self)
        self._schedule_probe()
        self._changed()

    def _on_remove_local_folder(self, folder: str) -> None:
        try:
            self._page_ref.run_task(self._remove_local_folder_async, folder)
        except Exception:
            asyncio.create_task(self._remove_local_folder_async(folder))

    async def _remove_local_folder_async(self, folder: str) -> None:
        confirmed = await self._ask_confirm(
            "Quitar carpeta local",
            f"Se quitará «{folder}» de tu Biblioteca.\n\nNo se borrarán archivos de audio del disco.",
            confirm_label="Quitar",
        )
        if not confirmed:
            return
        if settings_store.remove_local_folder(folder):
            self._folder_counts.pop(folder, None)
            self._render_local_folders_list()
            safe_update(self)
            notify(self._page_ref, "Carpeta quitada de Biblioteca")
            self._changed()

    async def _on_reindex_folders(self, e=None) -> None:
        folders = settings_store.get_local_folders()
        if not folders:
            notify(self._page_ref, "No hay carpetas locales para reindexar")
            return
        from services.local_index import save_shared_index, scan_folder_async

        if self._reindex_btn is not None:
            self._reindex_btn.disabled = True
        if self._reindex_status is not None:
            self._reindex_status.visible = True
            self._reindex_status.value = "Reindexando carpetas…"
        safe_update(self)

        total_tracks = 0
        try:
            for folder in folders:
                tracks = await scan_folder_async(folder)
                self._folder_counts[folder] = len(tracks)
                total_tracks += len(tracks)
            await asyncio.to_thread(save_shared_index)
            if self._reindex_status is not None:
                self._reindex_status.value = f"Índice actualizado · {total_tracks} pistas"
            self._render_local_folders_list()
            notify(self._page_ref, f"Biblioteca reindexada ({total_tracks} pistas)")
        except Exception as exc:  # noqa: BLE001
            if self._reindex_status is not None:
                self._reindex_status.value = f"Error al reindexar: {exc}"
            notify(self._page_ref, f"Error al reindexar: {exc}", kind="error")
        finally:
            if self._reindex_btn is not None:
                self._reindex_btn.disabled = False
            safe_update(self)

    def _on_covers_change(self, e=None) -> None:
        settings_store.set_covers_online(bool(getattr(e.control, "value", False)))
        self._changed()

    async def _on_clear_cache(self, e=None) -> None:
        from services import cover_cache

        def _clear() -> int:
            removed = 0
            try:
                for entry in cover_cache.CACHE_DIR.iterdir():
                    if entry.is_file():
                        entry.unlink(missing_ok=True)
                        removed += 1
            except OSError:
                pass
            return removed

        removed = await asyncio.to_thread(_clear)
        self._schedule_probe()
        notify(
            self._page_ref,
            f"Caché de portadas limpiado ({removed} archivos)" if removed
            else "El caché de portadas ya estaba vacío",
        )

    async def _on_cover_preview(self, e=None) -> None:
        """Calcula qué portadas cambiarían antes de escribir nada."""
        from services.covers import refresh_downloaded_covers

        folder = self._download_dir
        if not folder or not os.path.isdir(folder):
            notify(self._page_ref, "No hay carpeta de descargas configurada", kind="error")
            return
        if self._cover_refresh_btn is not None:
            self._cover_refresh_btn.disabled = True
        if self._cover_refresh_text is not None:
            self._cover_refresh_text.visible = True
            self._cover_refresh_text.value = "Buscando portadas… (puede tardar)"
        safe_update(self)
        try:
            report = await asyncio.to_thread(refresh_downloaded_covers, folder, apply=False)
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo calcular: {exc}", kind="error")
            if self._cover_refresh_btn is not None:
                self._cover_refresh_btn.disabled = False
            safe_update(self)
            return

        pending = len(report.previews)
        if self._cover_refresh_text is not None:
            if not report.total:
                self._cover_refresh_text.value = "No hay archivos de audio en la carpeta"
            elif not pending:
                self._cover_refresh_text.value = (
                    f"{report.total} archivos · {report.unchanged} ya tienen portada · "
                    f"{report.missing} sin artwork"
                )
            else:
                self._cover_refresh_text.value = (
                    f"{pending} de {report.total} archivos cambiarían de portada"
                )
        if pending:
            await self._confirm_cover_apply(folder, report.previews)
        if self._cover_refresh_btn is not None:
            self._cover_refresh_btn.disabled = False
        safe_update(self)

    async def _confirm_cover_apply(self, folder: str, previews: list) -> None:
        from engine.audio_metadata import write_cover

        sources: dict[str, int] = {}
        for _, _, source in previews:
            sources[source or "desconocido"] = sources.get(source or "desconocido", 0) + 1
        detail = ", ".join(f"{n} de {s}" for s, n in sorted(sources.items(), key=lambda kv: -kv[1]))

        confirmed = await self._ask_confirm(
            "Sobrescribir portadas",
            f"Se reemplazará la portada de {len(previews)} archivos en {folder}.\n\n"
            f"Orígenes: {detail}.\n\n¿Continuar?",
            confirm_label="Sobrescribir",
        )
        if not confirmed:
            if self._cover_refresh_text is not None:
                self._cover_refresh_text.value = "Cancelado: no se modificó ningún archivo"
            return

        def _write() -> tuple[int, int]:
            ok = failed = 0
            for path, data, _ in previews:
                if write_cover(path, data):
                    ok += 1
                else:
                    failed += 1
            return ok, failed

        ok, failed = await asyncio.to_thread(_write)
        if self._cover_refresh_text is not None:
            self._cover_refresh_text.value = (
                f"{ok} portadas actualizadas" + (f" · {failed} con error" if failed else "")
            )
        notify(self._page_ref, f"{ok} portadas actualizadas", kind="info" if not failed else "error")

    # ── Datos handlers ─────────────────────────────────────────────────

    async def _on_clear_search_cache(self, e=None) -> None:
        confirmed = await self._ask_confirm(
            "Vaciar caché de búsquedas",
            "Se eliminará config/search_cache.json. Las próximas transferencias volverán a consultar coincidencias.",
            confirm_label="Vaciar",
        )
        if not confirmed:
            return

        def _unlink() -> bool:
            try:
                if SEARCH_CACHE_JSON.is_file():
                    SEARCH_CACHE_JSON.unlink()
                    return True
            except OSError:
                pass
            return False

        removed = await asyncio.to_thread(_unlink)
        self._schedule_probe()
        notify(self._page_ref, "Caché de búsquedas vaciado" if removed else "La caché ya estaba vacía")

    async def _on_rebuild_local_index(self, e=None) -> None:
        from services import local_index as svc_idx

        confirmed = await self._ask_confirm(
            "Reconstruir índice local",
            "Se regenerará config/local_index.json escaneando las carpetas locales configuradas.",
            confirm_label="Reconstruir",
        )
        if not confirmed:
            return
        if self._rebuild_index_btn is not None:
            self._rebuild_index_btn.disabled = True
        safe_update(self)

        def _reset_file() -> None:
            try:
                svc_idx.LOCAL_INDEX_JSON.unlink(missing_ok=True)
            except OSError:
                pass
            svc_idx._index = None

        try:
            await asyncio.to_thread(_reset_file)
            total = 0
            for folder in settings_store.get_local_folders():
                tracks = await svc_idx.scan_folder_async(folder)
                total += len(tracks)
            await asyncio.to_thread(svc_idx.save_shared_index)
            self._schedule_probe()
            notify(self._page_ref, f"Índice local reconstruido ({total} pistas)")
        finally:
            if self._rebuild_index_btn is not None:
                self._rebuild_index_btn.disabled = False
            safe_update(self)

    async def _on_reset_settings(self, e=None) -> None:
        confirmed = await self._ask_confirm(
            "Restablecer ajustes",
            "Se restaurarán las preferencias de Melomaniac a sus valores por defecto.\n\n"
            "No se borrarán credenciales ni archivos descargados.",
            confirm_label="Restablecer",
        )
        if not confirmed:
            return
        await asyncio.to_thread(settings_store.reset_settings)
        self._rebuild_active_section()
        safe_update(self)
        self._schedule_probe()
        notify(self._page_ref, "Ajustes restablecidos")
        self._changed()

    # ── Acerca de, Bienvenida, Accesos directos y Actualizaciones ──────

    def _on_show_welcome(self, e=None) -> None:
        if self._on_welcome is not None:
            self._on_welcome()

    async def _on_install_shortcut(self, e=None) -> None:
        ok, msg = await asyncio.to_thread(desktop_integration.install_shortcut)
        notify(self._page_ref, msg, kind="info" if ok else "error")
        env, st = await asyncio.to_thread(
            lambda: (desktop_integration.get_runtime_environment(), desktop_integration.get_shortcut_status())
        )
        self._apply_shortcut_ui(env, st)
        safe_update(self)

    async def _on_remove_shortcut(self, e=None) -> None:
        ok, msg = await asyncio.to_thread(desktop_integration.remove_shortcut)
        notify(self._page_ref, msg, kind="info" if ok else "error")
        env, st = await asyncio.to_thread(
            lambda: (desktop_integration.get_runtime_environment(), desktop_integration.get_shortcut_status())
        )
        self._apply_shortcut_ui(env, st)
        safe_update(self)

    def _apply_shortcut_ui(
        self,
        env: desktop_integration.RuntimeEnvironment,
        st: desktop_integration.ShortcutStatus,
    ) -> None:
        if self._shortcut_status_text is None:
            return
        self._shortcut_status_text.value = st.summary
        if st.installed and st.is_current:
            self._shortcut_status_text.color = SUCCESS
        elif st.is_broken or (st.installed and not st.is_current):
            self._shortcut_status_text.color = WARNING
        else:
            self._shortcut_status_text.color = TEXT_PRIMARY

        loc = st.location_path or ""
        if st.target_path:
            loc = f"{loc} → {st.target_path}"
        if self._shortcut_location_text is not None:
            self._shortcut_location_text.value = loc
            self._shortcut_location_text.visible = bool(loc)

        warn = st.warning or ""
        if self._shortcut_warning_text is not None:
            self._shortcut_warning_text.value = warn
            self._shortcut_warning_text.visible = bool(warn)

        actions: list[ft.Control] = []
        if env.is_windows_installer:
            actions.append(
                app_text(
                    "Gestionado por el instalador de Windows (Menú Inicio y Escritorio)",
                    size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
                )
            )
        elif st.installed and st.is_current:
            actions.extend([
                _ghost_btn("Reinstalar", PIcons.ARROWS_CLOCKWISE, self._on_install_shortcut, width=130, height=34),
                _ghost_btn("Quitar", PIcons.TRASH, self._on_remove_shortcut, width=110, height=34),
            ])
        elif st.installed and not st.is_current:
            actions.extend([
                _primary_btn("Actualizar acceso directo", PIcons.ARROWS_CLOCKWISE, self._on_install_shortcut, width=220, height=34),
                _ghost_btn("Quitar", PIcons.TRASH, self._on_remove_shortcut, width=110, height=34),
            ])
        else:
            actions.append(
                _primary_btn("Crear acceso directo", PIcons.PLUS, self._on_install_shortcut, width=200, height=34)
            )
        if self._shortcut_actions_row is not None:
            self._shortcut_actions_row.controls = actions

    async def _on_check_updates(self, e=None) -> None:
        if self._update_check_btn is None:
            return
        from ui.components.widgets import set_button_text

        set_button_text(self._update_check_btn, "Buscando…")
        self._update_check_btn.disabled = True
        safe_update(self._update_check_btn)
        try:
            info = await update_checker.check_for_updates()
            self._render_update_result(info)
        finally:
            set_button_text(self._update_check_btn, "Comprobar")
            self._update_check_btn.disabled = False
            safe_update(self._update_check_btn)

    def _render_update_result(self, info: update_checker.UpdateInfo) -> None:
        if self._update_result_col is None:
            return
        ctrls: list[ft.Control] = []
        if not info.checked:
            ctrls.append(
                app_text(
                    f"No se pudo comprobar: {info.error_message or info.instructions}",
                    size=11, color=WARNING, font_family=FONT_TEXT,
                )
            )
        elif info.has_update:
            ctrls.extend([
                app_text(
                    f"¡Nueva versión disponible: v{info.latest_version}!",
                    size=12, color=SUCCESS, font_family=FONT_HEADLINE,
                ),
                app_text(info.instructions, size=11, color=TEXT_PRIMARY, font_family=FONT_TEXT),
                ft.Row(
                    controls=[
                        _primary_btn(
                            "Descargar actualización", PIcons.DOWNLOAD_SIMPLE,
                            lambda _: open_external(self._page_ref, info.download_url or info.release_url),
                            width=210, height=34,
                        ),
                        _ghost_btn(
                            "Ver notas de release", PIcons.ARROW_SQUARE_OUT,
                            lambda _: open_external(self._page_ref, info.release_url),
                            width=180, height=34,
                        ),
                    ],
                    spacing=SPACE_2,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ])
        else:
            ctrls.extend([
                app_text(
                    f"✓ Estás usando la versión más reciente (v{info.current_version}).",
                    size=11, color=SUCCESS, font_family=FONT_TEXT,
                ),
                app_text(info.instructions, size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
            ])
        self._update_result_col.controls = ctrls
        safe_update(self._update_result_col)

    # ── Utilidades comunes ─────────────────────────────────────────────

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass

    async def _ask_confirm(
        self,
        title: str,
        message: str,
        *,
        confirm_label: str = "Confirmar",
    ) -> bool:
        """Diálogo modal de confirmación; devuelve ``False`` si se cancela o descarta."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def _close(value: bool) -> None:
            if not future.done():
                future.set_result(value)
            close_dialog(self._page_ref, dlg)

        dlg = app_dialog(
            title,
            app_text(message, size=12, color=TEXT_MUTED, font_family=FONT_TEXT),
            actions=[
                _ghost_btn("Cancelar", PIcons.X, lambda _: _close(False), width=110, height=34),
                _primary_btn(confirm_label, PIcons.CHECK, lambda _: _close(True), width=150, height=34),
            ],
            width=420,
        )
        dlg.on_dismiss = lambda _: _close(False)
        try:
            show_dialog(self._page_ref, dlg)
        except Exception:
            if hasattr(self._page_ref, "overlay") and isinstance(self._page_ref.overlay, list):
                self._page_ref.overlay.append(dlg)
                dlg.open = True
                safe_update(self._page_ref)
            else:
                return False
        return await future
