"""ui/shell/main_layout.py — Controlador de layout principal de Gestión y ensamblaje raíz."""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import EXPORT_DEST_LABEL
from core.models import LoadState, TransferState
from ui.components.skeletons import PreviewSkeletonRow, SkeletonRow
from ui.components.song_row import ITEM_H
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    app_text_field,
    collapsible_drawer,
    empty_state,
    input_border,
    kicker,
    safe_update,
    set_button_text,
    status_chip,
    tab_underline,
    table_header,
)
from ui.shell.nav_rail import build_nav_rail
from ui.shell.sidebar import build_sidebar
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_INSET,
    BG_INPUT,
    BG_LIST,
    BG_PANEL,
    BG_SURFACE,
    BORDER_CONTROL,
    BORDER_LIGHT,
    BORDER_MUTED,
    BORDER_ROW,
    CHIP_BG,
    DECOR_LINE,
    ERROR_COL,
    FS_META,
    FS_SEC,
    FS_SM,
    MOTION_PANEL_MS,
    ON_ACCENT,
    RADIUS_NONE,
    RADIUS_SHARP,
    RADIUS_SM,
    SPACE_0,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    SPACE_5,
    SUCCESS,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def _platform_icon(name: str) -> str:
    """Devuelve el icono Phosphor correspondiente a la plataforma o fuente."""
    low = (name or "").lower()
    if "youtube" in low:
        return PIcons.YOUTUBE_LOGO
    if "apple" in low:
        return PIcons.APPLE_LOGO
    if "spotify" in low:
        return PIcons.SPOTIFY_LOGO
    if "export" in low:
        return PIcons.EXPORT
    if "pegar" in low:
        return PIcons.LIST_BULLETS
    if "local" in low or "archivo" in low:
        return PIcons.FOLDER
    return PIcons.MUSIC_NOTES


class _RegistroDrawer:
    """Adaptador de telemetría sobre ``collapsible_drawer`` («REGISTRO · N eventos»)."""

    def __init__(self, page: ft.Page) -> None:
        self._page = page
        self._tab = "log"
        self._log_lines: list[str] = []
        self._monitor_text = ft.Text(
            "Sin operaciones activas.",
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
        )
        self._pm_text = ft.Text(
            "Sin incidencias registradas.",
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
        )
        self._log_list = ft.ListView(
            spacing=SPACE_1,
            auto_scroll=True,
            height=130,
        )
        self._tabs_wrap = ft.Container()
        self._body_panel = ft.Container(content=self._log_list)
        self._pm_export_btn = _ghost_btn(
            "Exportar informe",
            PIcons.EXPORT,
            lambda _: self._export_postmortem(),
            height=30,
        )
        self._pm_export_btn.visible = False
        self._pm_rows: list[dict] = []
        self._pm_meta: dict[str, object] = {}

        def _render_tabs() -> None:
            self._tabs_wrap.content = tab_underline(
                [("log", "Registro"), ("monitor", "Monitor"), ("postmortem", "Post-mortem")],
                self._tab,
                self._set_tab,
            )

        self._render_tabs = _render_tabs
        _render_tabs()

        drawer_content = ft.Column(
            controls=[
                ft.Row(
                    [self._tabs_wrap, ft.Container(expand=True), self._pm_export_btn],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._body_panel,
            ],
            spacing=SPACE_2,
            tight=True,
        )
        self.container = collapsible_drawer(
            "REGISTRO",
            "0 eventos",
            drawer_content,
            expanded=False,
        )
        # Referencias internas del collapsible_drawer para actualizar subtítulo y expansión
        col = self.container.content
        header = col.controls[0]
        self._subtitle_ctrl = header.content.controls[1]
        self._body_wrap = col.controls[1]
        self._caret_ctrl = header.content.controls[3]

    def _set_tab(self, key: str) -> None:
        self._tab = key
        self._render_tabs()
        if key == "log":
            self._body_panel.content = self._log_list
            self._pm_export_btn.visible = False
        elif key == "monitor":
            self._body_panel.content = self._monitor_text
            self._pm_export_btn.visible = False
        else:
            self._body_panel.content = self._pm_text
            self._pm_export_btn.visible = bool(self._pm_rows)
        safe_update(self._tabs_wrap)
        safe_update(self._body_panel)
        safe_update(self._pm_export_btn)

    def _export_postmortem(self) -> None:
        try:
            from datetime import datetime
            from pathlib import Path
            import json

            out_dir = Path("config")
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = out_dir / f"postmortem_{stamp}.json"
            payload = {"meta": self._pm_meta, "failed": self._pm_rows}
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def show_postmortem(self) -> None:
        self._body_wrap.visible = True
        self._caret_ctrl.value = PIcons.CARET_UP
        self._set_tab("postmortem")
        safe_update(self._body_wrap)
        safe_update(self._caret_ctrl)

    def clear_postmortem(self) -> None:
        self._pm_rows = []
        self._pm_meta = {}
        self._pm_text.value = "Sin incidencias registradas."
        self._pm_export_btn.visible = False
        safe_update(self._pm_text)
        safe_update(self._pm_export_btn)

    def sync_mode(self) -> None:
        pass

    def update_counters(
        self,
        detected: int,
        candidates: int,
        processed: int,
        ok: int,
        not_found: int,
        errors: int,
        *,
        eta_str: str = "",
        active_platform: str = "",
    ) -> None:
        parts = [
            f"Detectadas: {detected or '—'}",
            f"Candidatas: {candidates or '—'}",
            f"Procesadas: {processed or '—'}",
            f"OK: {ok or '—'}",
            f"Sin match: {not_found or '—'}",
            f"Errores: {errors or '—'}",
        ]
        if active_platform:
            parts.append(f"Plataforma: {active_platform}")
        if eta_str:
            parts.append(f"ETA: {eta_str}")
        val = " · ".join(parts)
        if self._monitor_text.value != val:
            self._monitor_text.value = val
            safe_update(self._monitor_text)

    def update_log(self, log_lines: list[str]) -> None:
        lines = list(log_lines or ())
        sub = f"{len(lines)} eventos"
        if self._subtitle_ctrl.value != sub:
            self._subtitle_ctrl.value = sub
            safe_update(self._subtitle_ctrl)
        if lines == self._log_lines:
            return
        self._log_lines = lines
        self._log_list.controls = [
            ft.Text(
                line,
                size=FS_META,
                color=TEXT_MUTED,
                font_family=mono_family("light"),
                selectable=True,
            )
            for line in lines[-80:]
        ]
        safe_update(self._log_list)

    def update_postmortem(
        self,
        failed_tracks,
        *,
        destination: str = "",
        confirmed: int = 0,
        attempted: int = 0,
        playlist_url: str = "",
    ) -> None:
        rows = list(failed_tracks or ())
        self._pm_rows = rows
        self._pm_meta = {
            "destination": destination,
            "confirmed": confirmed,
            "attempted": attempted,
            "playlist_url": playlist_url,
        }
        if not rows:
            self._pm_text.value = (
                f"Destino: {destination or '—'} · Confirmadas: {confirmed}/{attempted} · Sin fallos."
            )
        else:
            preview = [
                f"• {r.get('name', '?')} — {r.get('artist', '?')} ({r.get('reason', 'sin detalle')})"
                for r in rows[:20]
            ]
            self._pm_text.value = (
                f"Destino: {destination or '—'} · Confirmadas: {confirmed}/{attempted} · "
                f"Fallidas: {len(rows)}\n" + "\n".join(preview)
            )
        self._pm_export_btn.visible = bool(rows) and self._tab == "postmortem"
        safe_update(self._pm_text)
        safe_update(self._pm_export_btn)


def compute_contextual_actions_state(
    state, *, net_blocked: bool = False
) -> dict[str, dict[str, object]]:
    """Calcula visibilidad, estado habilitado y tooltip de la barra contextual.

    Mantiene la regla de 2.10: mostrar/habilitar solo acciones aplicables a la
    fuente cargada (por ejemplo, ocultar ``Descargar`` si todas las pistas ya
    son archivos de audio locales en disco, y ocultar la barra completa cuando
    no hay playlist cargada).
    """
    tracks = list(getattr(state, "tracks", []) or [])
    load_state = getattr(state, "load_state", LoadState.IDLE)
    is_ready = load_state == LoadState.READY and len(tracks) > 0
    is_transferring = getattr(state, "transfer_state", TransferState.IDLE) == TransferState.RUNNING
    download_busy = getattr(state, "download_state", TransferState.IDLE) == TransferState.RUNNING
    all_local_audio = bool(tracks) and all(bool(getattr(t, "source_path", "")) for t in tracks)

    return {
        "transfer": {
            "visible": is_ready,
            "disabled": (not is_ready) or is_transferring or net_blocked,
            "tooltip": (
                "Transferencia en curso"
                if is_transferring
                else (
                    "Sin conexión (rate limit activo)"
                    if net_blocked
                    else "Elegir destino y transferir la selección"
                )
            ),
        },
        "organize": {
            "visible": is_ready,
            "disabled": not is_ready,
            "tooltip": "Ordenar o agrupar las pistas cargadas",
        },
        "split": {
            "visible": is_ready,
            "disabled": not is_ready,
            "tooltip": "Dividir la playlist por criterio o grupos manuales",
        },
        "reorganize": {
            "visible": is_ready,
            "disabled": not is_ready,
            "tooltip": "Volver a aplicar los criterios de organización",
        },
        "download": {
            "visible": is_ready and not all_local_audio,
            "disabled": (not is_ready) or all_local_audio,
            "tooltip": (
                "Añadir pistas seleccionadas a la cola de descargas"
                if download_busy
                else "Descargar pistas seleccionadas con yt-dlp"
            ),
        },
        "export": {
            "visible": is_ready,
            "disabled": not is_ready,
            "tooltip": "Exportar a archivo o añadir a una playlist destino",
        },
    }


def _make_segmented_control(
    current: str,
    options: list[tuple[str, str]],
    *,
    dd_width: int,
    on_change=None,
) -> ft.Control:
    """Construye un ``SegmentedButton`` con fallback a ``Dropdown`` si falta en runtime."""
    try:
        return ft.SegmentedButton(
            selected=[current],
            allow_empty_selection=False,
            allow_multiple_selection=False,
            show_selected_icon=False,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                color={ft.ControlState.SELECTED: ON_ACCENT, ft.ControlState.DEFAULT: TEXT_MUTED},
            ),
            segments=[
                ft.Segment(value=val, label=ft.Text(label, size=11, font_family=FONT_HEADLINE))
                for val, label in options
            ],
            on_change=on_change,
        )
    except Exception:
        return ft.Dropdown(
            value=current,
            width=dd_width,
            height=38,
            bgcolor=BG_INPUT,
            border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            options=[ft.dropdown.Option(val, label) for val, label in options],
            on_select=on_change,
        )


class LayoutController:
    """Controlador de construcción y sincronización de layout para ``PlaylistManagerUI``."""

    def __init__(self, ui) -> None:
        self._ui = ui
        self._last_chip_counts: tuple[int, int, int] | None = None
        self._last_footer_btn_label: str = "Transferir"

    def _make_order_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton para elegir Artista-Título vs Título-Artista."""
        return _make_segmented_control(
            current,
            [("artist-title", "Artista - Título"), ("title-artist", "Título - Artista")],
            dd_width=200,
            on_change=on_change,
        )

    def _make_mode_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton Lista|Doble|Preview."""
        return _make_segmented_control(
            current,
            [("lista", "Lista"), ("doble", "Doble"), ("preview", "Preview")],
            dd_width=180,
            on_change=on_change,
        )

    def _make_cono_empty(self, title: str, subtitle: str, visible: bool = True) -> ft.Container:
        """Empty con icono para placeholders de módulo y preview."""
        return ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=app_icon(PIcons.WRENCH, size=52, color=DECOR_LINE),
                        bgcolor=CHIP_BG,
                        border=ft.Border.all(0.8, BORDER_LIGHT),
                        border_radius=RADIUS_SM,
                        padding=ft.Padding.all(20),
                    ),
                    ft.Text(
                        title,
                        size=18,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_BOLD,
                        opacity=1.0,
                    ),
                    ft.Text(
                        subtitle,
                        size=12,
                        color=TEXT_MUTED,
                        font_family=FONT_TEXT,
                        opacity=1.0,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        "Función en desarrollo",
                        size=11,
                        color=TEXT_MUTED,
                        font_family=FONT_TEXT,
                        opacity=0.8,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=SPACE_2,
            ),
            alignment=ft.Alignment.CENTER,
            visible=visible,
            expand=True,
        )

    def _build_sidebar(self) -> None:
        build_sidebar(self._ui)

    def _build_content(self) -> None:
        ui = self._ui
        self._build_mode_bar()
        ui._footer_bar = self._build_footer_bar()
        header_bar = self._build_content_header()
        ui._contextual_bar = self._build_contextual_bar()
        ui._catalog_panel = ui._catalog.build_catalog_panel()
        ui._col_headers = self._build_col_headers()
        self._build_list_and_preview_panels()
        ui._telemetry = _RegistroDrawer(ui.page)
        self._assemble_content_area(header_bar, ui._col_headers)

    def _build_contextual_bar(self) -> ft.Container:
        """Barra contextual unificada encima de la tabla."""
        ui = self._ui
        ui._ctx_organize_btn = _ghost_btn(
            "Organizar", PIcons.SLIDERS_HORIZONTAL, ui._on_organize, height=34
        )
        ui._ctx_split_btn = _ghost_btn(
            "Dividir", PIcons.GIT_BRANCH, ui._on_split, height=34
        )
        ui._ctx_reorganize_btn = _ghost_btn(
            "Reorganizar", PIcons.ARROWS_CLOCKWISE, ui._on_reorganize, height=34, disabled=True
        )
        ui._ctx_download_btn = _ghost_btn(
            "Descargar", PIcons.DOWNLOAD_SIMPLE, lambda _: ui._on_batch_download(), height=34
        )
        ui._ctx_export_btn = _ghost_btn(
            "Exportar",
            PIcons.EXPORT,
            lambda _: asyncio.create_task(ui._open_export_dialog()),
            height=34,
        )
        ui._ctx_transfer_btn = ui._footer_transfer_btn
        ui._transfer_btn = ui._footer_transfer_btn
        ui._organize_btn = ui._ctx_organize_btn
        ui._split_btn = ui._ctx_split_btn
        ui._reorganize_btn = ui._ctx_reorganize_btn
        ui._ctx_action_buttons = {
            "transfer": ui._footer_transfer_btn,
            "organize": ui._ctx_organize_btn,
            "split": ui._ctx_split_btn,
            "reorganize": ui._ctx_reorganize_btn,
            "download": ui._ctx_download_btn,
            "export": ui._ctx_export_btn,
        }
        # Colocar Exportar y Descargar en el pie junto al primario Transferir
        footer_row = ui._footer_bar.content
        footer_row.controls = [
            footer_row.controls[0],
            ui._ctx_export_btn,
            ui._ctx_download_btn,
            ui._footer_transfer_btn,
        ]

        actions_row = ft.Row(
            controls=[
                ui._mode_seg,
                ui._ctx_organize_btn,
                ui._ctx_reorganize_btn,
                ui._ctx_split_btn,
                ui._division_popup,
                ui._edit_division_btn,
            ],
            spacing=SPACE_2,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        ui._status_chips_row = ft.Row(
            controls=[
                status_chip("0 LISTA", kind="ok"),
                status_chip("0 REVISAR", kind="warn"),
                status_chip("0 SIN MATCH", kind="err"),
            ],
            spacing=SPACE_1,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        right_row = ft.Row(
            controls=[
                ui._search_field,
                ui._select_all_chk,
                ui._status_chips_row,
            ],
            spacing=SPACE_2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        bar = ft.Container(
            content=ft.Row(
                controls=[actions_row, ft.Container(expand=True), right_row],
                spacing=SPACE_2,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_PANEL,
            border=ft.Border.all(0.8, BORDER_CONTROL),
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_2),
            visible=False,
        )
        ui._contextual_bar = bar
        self._apply_contextual_actions_state(compute_contextual_actions_state(ui.state))
        return bar

    def _apply_contextual_actions_state(
        self, states: dict[str, dict[str, object]]
    ) -> None:
        """Aplica visibilidad, habilitación, tooltips y sincroniza las 3 franjas."""
        buttons = getattr(self._ui, "_ctx_action_buttons", {})
        any_visible = False
        for key, btn in buttons.items():
            spec = states.get(key)
            if not spec:
                continue
            vis = bool(spec.get("visible", True))
            btn.visible = vis
            if vis:
                any_visible = True
            btn.disabled = bool(spec.get("disabled", False))
            tip = str(spec.get("tooltip", "") or "")
            if tip:
                btn.tooltip = tip
        bar = getattr(self._ui, "_contextual_bar", None)
        if bar is not None:
            catalog_open = bool(getattr(self._ui, "_catalog_open", False))
            bar.visible = any_visible and not catalog_open
        self._sync_workflow_ui(states)

    def _build_content_header(self) -> ft.Container:
        ui = self._ui

        def _make_step_tab(num: str, label: str):
            num_txt = ft.Text(
                num,
                size=FS_META,
                color=TEXT_MUTED,
                font_family=mono_family("light"),
                opacity=1.0,
            )
            lbl_txt = ft.Text(
                label,
                size=FS_SM,
                color=TEXT_MUTED,
                font_family=FONT_HEADLINE,
                opacity=1.0,
            )
            box = ft.Container(
                content=ft.Row(
                    controls=[num_txt, lbl_txt],
                    spacing=SPACE_2,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                expand=True,
                bgcolor=BG_PANEL,
                border=ft.Border(bottom=ft.BorderSide(2, BORDER_MUTED)),
                padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_2),
            )
            return box, num_txt, lbl_txt

        step_specs = [
            ("01", "Origen"),
            ("02", "Revisar"),
            ("03", "Destino"),
            ("04", "Transferir"),
        ]
        ui._step_items = [_make_step_tab(n, t) for n, t in step_specs]
        ui._steps_strip = ft.Row(
            controls=[box for box, _, _ in ui._step_items],
            spacing=SPACE_0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        ui._playlist_title = ft.Text(
            "Cargar una playlist",
            size=FS_SEC,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            opacity=1.0,
        )
        ui._track_count = ft.Text(
            "", size=FS_SM, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0
        )
        ui._search_field = app_text_field(
            hint_text="Buscar título, artista…",
            prefix_icon=PIcons.MAGNIFYING_GLASS,
            width=210,
            height=34,
            content_padding=ft.Padding.symmetric(horizontal=SPACE_2, vertical=0),
            on_change=ui._on_search_change,
        )
        ui._merge_btn = _ghost_btn(
            "Fusionar",
            PIcons.QUEUE,
            lambda _: ui._open_merge_dialog(),
            height=34,
        )
        ui._merge_btn.tooltip = "Fusionar varias playlists o fuentes locales sin duplicados"
        ui._catalog_search_btn = _ghost_btn(
            "Catálogo",
            PIcons.GLOBE,
            lambda _: ui._on_toggle_catalog_search(),
            height=34,
        )
        ui._catalog_search_btn.tooltip = "Buscar canciones, álbumes o artistas en plataformas"
        ui._segment_dd = ft.Dropdown(
            width=160,
            height=36,
            bgcolor=BG_INPUT,
            border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=FS_SM, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=SPACE_2, vertical=0),
            on_select=lambda e: ui.state.set_active_segment(e.control.value),
            visible=False,
            hint_text="Segmento...",
        )

        ui._auth_yt = app_icon_button(
            glyph=PIcons.YOUTUBE_LOGO,
            icon_color=DECOR_LINE,
            size=16,
            tooltip="YouTube Music · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(ui._on_auth_probe("YouTube Music")),
            width=26,
            height=26,
        )
        ui._auth_am = app_icon_button(
            glyph=PIcons.APPLE_LOGO,
            icon_color=DECOR_LINE,
            size=16,
            tooltip="Apple Music · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(ui._on_auth_probe("Apple Music")),
            width=26,
            height=26,
        )
        ui._auth_sp = app_icon_button(
            glyph=PIcons.SPOTIFY_LOGO,
            icon_color=DECOR_LINE,
            size=16,
            tooltip="Spotify · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(ui._on_auth_probe("Spotify")),
            width=26,
            height=26,
        )
        ui._auth_strip = ft.Row(
            controls=[ui._auth_yt, ui._auth_am, ui._auth_sp],
            spacing=SPACE_1,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        ui._select_all_chk = ft.Checkbox(
            label="Todo",
            label_style=ft.TextStyle(color=TEXT_MUTED, size=FS_META, font_family=FONT_HEADLINE),
            fill_color={ft.ControlState.SELECTED: ACCENT},
            check_color=ON_ACCENT,
            border_side=ft.BorderSide(1.5, DECOR_LINE),
            on_change=lambda _: ui.state.toggle_select_all(),
        )

        ui._content_progress_bar = ft.ProgressBar(
            value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=RADIUS_SHARP
        )
        ui._content_prog_label = ft.Text(
            "", size=FS_META, color=TEXT_MUTED, font_family=mono_family("light"), opacity=0.8
        )
        ui._content_eta_label = ft.Text(
            "", size=FS_META, color=TEXT_MUTED, font_family=mono_family("light"), opacity=0.65
        )
        ui._content_progress = ft.Container(
            content=ft.Column(
                [
                    ui._content_progress_bar,
                    ft.Row(
                        [ui._content_prog_label, ft.Container(expand=True), ui._content_eta_label],
                        spacing=SPACE_0,
                    ),
                ],
                spacing=SPACE_1,
            ),
            visible=False,
            bgcolor=BG_INSET,
            border=ft.Border.all(0.8, BORDER_CONTROL),
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_2),
        )

        ui._clear_session_btn = app_icon_button(
            glyph=PIcons.TRASH,
            icon_color=DECOR_LINE,
            size=16,
            tooltip="Limpiar playlist cargada",
            on_click=ui._on_clear_session,
            width=28,
            height=28,
        )

        ui._route_src_icon = app_icon(
            _platform_icon(ui.state.source), size=18, color=ACCENT
        )
        ui._route_dst_icon = app_icon(
            _platform_icon(ui.state.destination), size=18, color=TEXT_MUTED
        )
        ui._route_src_text = ft.Text(
            f"{ui.state.source} · Sin cargar",
            size=FS_SM,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        ui._route_pipe_text = ft.Text(
            "0 pistas",
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
        )
        ui._route_dst_text = ft.Text(
            ui.state.destination or "Elegir al transferir",
            size=FS_SM,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        src_card = ft.Container(
            content=ft.Row(
                controls=[
                    ui._route_src_icon,
                    ft.Column(
                        controls=[kicker("ORIGEN"), ui._route_src_text],
                        spacing=SPACE_0,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=SPACE_2,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_ROW),
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_2),
            expand=True,
        )
        mid_arrow = ft.Column(
            controls=[
                app_icon(PIcons.ARROW_RIGHT, size=16, color=ACCENT),
                ui._route_pipe_text,
            ],
            spacing=SPACE_0,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )
        ui._route_dst_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ui._route_dst_icon,
                    ft.Column(
                        controls=[kicker("DESTINO"), ui._route_dst_text],
                        spacing=SPACE_0,
                        tight=True,
                        expand=True,
                    ),
                    app_icon(PIcons.CARET_DOWN, size=14, color=TEXT_MUTED),
                ],
                spacing=SPACE_2,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_CONTROL),
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_2),
            tooltip="Elegir plataforma destino y transferir",
            on_click=self._on_route_dest_click,
            expand=True,
        )
        ui._route_bar = ft.Row(
            controls=[src_card, mid_arrow, ui._route_dst_btn],
            spacing=SPACE_3,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        title_row = ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        kicker("GESTIÓN"),
                        ft.Row(
                            controls=[ui._playlist_title, ui._track_count],
                            spacing=SPACE_2,
                            vertical_alignment=ft.CrossAxisAlignment.BASELINE,
                        ),
                    ],
                    spacing=SPACE_0,
                    tight=True,
                    expand=True,
                ),
                ui._auth_strip,
                ui._merge_btn,
                ui._catalog_search_btn,
                ui._segment_dd,
                ui._clear_session_btn,
            ],
            spacing=SPACE_2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ft.Container(
            content=ft.Column(
                controls=[ui._steps_strip, title_row, ui._route_bar],
                spacing=SPACE_2,
                tight=True,
            ),
        )

    def _on_route_dest_click(self, _e=None) -> None:
        ui = self._ui
        s = ui.state
        if getattr(s, "load_state", LoadState.IDLE) != LoadState.READY or not getattr(
            s, "tracks", None
        ):
            return
        if getattr(s, "transfer_state", TransferState.IDLE) == TransferState.RUNNING:
            return
        ui._on_transfer(None)

    def _build_col_headers(self) -> ft.Container:
        ui = self._ui
        hdr = table_header(
            [
                ("", None),
                ("Nº", None),
                ("PORTADA", None),
                ("TÍTULO / ARTISTA", 3),
                ("ÁLBUM", 2),
                ("DUR.", None),
                ("ESTADO", None),
            ]
        )
        cells = hdr.content.controls
        cells[0].width = 32
        cells[1].width = 32
        cells[2].width = 55
        cells[5].width = 48
        cells[6].width = 64
        ui._album_col_header = cells[4]
        hdr.bgcolor = BG_INSET
        hdr.padding = ft.Padding.symmetric(horizontal=SPACE_4, vertical=SPACE_2)
        hdr.border_radius = ft.BorderRadius.only(
            top_left=RADIUS_SHARP,
            top_right=RADIUS_SHARP,
            bottom_left=RADIUS_NONE,
            bottom_right=RADIUS_NONE,
        )
        hdr.border = ft.Border.all(0.8, BORDER_CONTROL)
        hdr.clip_behavior = ft.ClipBehavior.ANTI_ALIAS
        hdr.visible = False
        return hdr

    def _sync_queue_album_column(self, queue_open: bool, *, update: bool = True) -> None:
        """Oculta la columna Álbum en Gestión cuando el panel de cola está abierto (D26)."""
        ui = self._ui
        show_album = not bool(queue_open)
        album_hdr = getattr(ui, "_album_col_header", None)
        if album_hdr is not None and album_hdr.visible != show_album:
            album_hdr.visible = show_album
            if update:
                try:
                    album_hdr.update()
                except Exception:
                    pass
        lv = getattr(ui, "_list_view", None)
        if lv is None:
            return
        for row in getattr(lv, "controls", ()):
            setter = getattr(row, "set_album_visible", None)
            if callable(setter):
                setter(show_album, update=update)

    def _build_list_and_preview_panels(self) -> None:
        ui = self._ui
        ui._list_view = ft.ListView(
            item_extent=ITEM_H,
            cache_extent=ITEM_H * 8,
            spacing=SPACE_0,
            expand=True,
            padding=ft.Padding.only(bottom=20),
        )
        _sf = dict(left=0, top=0, right=0, bottom=0)
        ui._list_view_wrap = ft.Container(content=ui._list_view, bgcolor=BG_LIST, visible=False, **_sf)
        init_cnt = ui._calc_skeleton_count() if hasattr(ui, "_calc_skeleton_count") else ui.SKELETON_COUNT
        ui._skeletons = [SkeletonRow(i) for i in range(init_cnt)]
        ui._skeleton_view = ft.ListView(
            item_extent=ITEM_H,
            cache_extent=ITEM_H * 8,
            spacing=SPACE_0,
            expand=True,
            controls=ui._skeletons,
            visible=True,
        )
        ui._skeleton_view_wrap = ft.Container(
            content=ui._skeleton_view, bgcolor=BG_LIST, visible=False, **_sf
        )

        empty_col = empty_state(
            PIcons.PLAYLIST,
            "Carga una playlist",
            subtitle="Introduce el ID o la URL en el panel izquierdo y pulsa «Cargar».",
            action_label="Buscar en catálogo",
            on_action=lambda _: ui._on_toggle_catalog_search(),
        )
        ui._empty_hint_text = empty_col.controls[2]
        ui._empty_state = ft.Container(
            bgcolor=BG_LIST,
            content=empty_col,
            padding=ft.Padding.all(SPACE_5),
            alignment=ft.Alignment.CENTER_LEFT,
            visible=True,
            **_sf,
        )

        ui._error_text = ft.Text("", size=13, color=ERROR_COL, font_family=FONT_TEXT, opacity=1.0)
        ui._error_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(
                [
                    app_icon(PIcons.WARNING_CIRCLE, size=48, color=ERROR_COL, fill=True),
                    ui._error_text,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=SPACE_2,
            ),
            alignment=ft.Alignment.CENTER,
            visible=False,
            **_sf,
        )

        ui._preview_list_view = ft.ListView(
            item_extent=ITEM_H,
            cache_extent=ITEM_H * 8,
            spacing=SPACE_0,
            expand=True,
            padding=ft.Padding.only(bottom=20),
        )
        ui._preview_count = ft.Text(
            "", size=FS_META, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0
        )
        ui._preview_empty = self._make_cono_empty(
            "Preview vacío", "Marca canciones y organiza para ver cómo se va a pasar.", visible=False
        )
        ui._preview_empty_wrap = ft.Container(
            content=ui._preview_empty, bgcolor=BG_LIST, visible=False, **_sf
        )
        ui._preview_view_wrap = ft.Container(
            content=ui._preview_list_view, bgcolor=BG_LIST, visible=False, **_sf
        )
        ui._preview_skeletons = [PreviewSkeletonRow(i) for i in range(len(ui._skeletons))]
        ui._preview_skeleton_view = ft.ListView(
            item_extent=ITEM_H,
            cache_extent=ITEM_H * 8,
            spacing=SPACE_0,
            expand=True,
            controls=ui._preview_skeletons,
            visible=True,
        )
        ui._preview_skeleton_wrap = ft.Container(
            content=ui._preview_skeleton_view, bgcolor=BG_LIST, visible=False, **_sf
        )
        ui._preview_stack = ft.Stack(
            controls=[ui._preview_view_wrap, ui._preview_skeleton_wrap, ui._preview_empty_wrap],
            expand=True,
        )
        ui._preview_header_text = ft.Text(
            "Preview — cómo se va a pasar",
            size=11,
            color=TEXT_MUTED,
            font_family=FONT_HEADLINE_BOLD,
        )
        ui._preview_header_icon = app_icon(PIcons.EYE, size=14, color=TEXT_MUTED)
        ui._preview_panel = ft.Container(
            expand=True,
            bgcolor=BG_LIST,
            border=ft.Border.all(0.8, BORDER_CONTROL),
            border_radius=ft.BorderRadius.only(
                top_left=RADIUS_NONE,
                top_right=RADIUS_NONE,
                bottom_left=RADIUS_SHARP,
                bottom_right=RADIUS_SHARP,
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ui._preview_header_icon,
                            ui._preview_header_text,
                            ft.Container(expand=True),
                            ui._preview_count,
                        ],
                        spacing=SPACE_1,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                    ui._preview_stack,
                ],
                spacing=SPACE_2,
                expand=True,
            ),
            padding=ft.Padding.all(SPACE_2),
            visible=True,
        )

        list_stack = ft.Stack(
            controls=[ui._list_view_wrap, ui._skeleton_view_wrap, ui._empty_state, ui._error_state],
            expand=True,
        )
        ui._lista_header_text = ft.Text(
            "Lista editable", size=FS_META, color=TEXT_MUTED, font_family=FONT_HEADLINE_BOLD
        )
        ui._lista_header_icon = app_icon(PIcons.LIST_BULLETS, size=14, color=TEXT_MUTED)
        ui._lista_subheader = ft.Container(
            content=ft.Column(
                [
                    ft.Row([ui._lista_header_icon, ui._lista_header_text], spacing=SPACE_1),
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                ],
                spacing=SPACE_1,
                tight=True,
            ),
            padding=ft.Padding.only(left=SPACE_2, right=SPACE_2, top=SPACE_1),
            visible=False,
        )
        ui._lista_panel = ft.Container(
            expand=True,
            bgcolor=BG_LIST,
            border=ft.Border.all(0.8, BORDER_CONTROL),
            border_radius=ft.BorderRadius.only(
                top_left=RADIUS_NONE,
                top_right=RADIUS_NONE,
                bottom_left=RADIUS_SHARP,
                bottom_right=RADIUS_SHARP,
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    ui._lista_subheader,
                    list_stack,
                ],
                spacing=SPACE_0,
                expand=True,
            ),
            padding=ft.Padding.symmetric(horizontal=SPACE_1, vertical=0),
            visible=True,
        )

    def _build_mode_bar(self) -> None:
        ui = self._ui

        def _on_mode_change(e):
            try:
                v = (
                    list(e.control.selected)[0]
                    if hasattr(e.control, "selected") and e.control.selected
                    else e.control.value
                )
            except Exception:
                v = getattr(e.control, "value", "lista")
            ui.state.set_dual_mode(v)

        ui._mode_seg = self._make_mode_segmented(
            getattr(ui.state, "dual_mode", "lista"), on_change=_on_mode_change
        )
        ui._division_btn_text = ft.Text("División 1", size=FS_META, color=TEXT_MUTED, font_family=FONT_HEADLINE)
        ui._division_label = ft.Text("División", size=FS_META, color=TEXT_MUTED, font_family=FONT_HEADLINE_BOLD)
        ui._division_popup = ft.PopupMenuButton(
            content=ft.Row(
                [
                    app_icon(PIcons.FUNNEL, size=14, color=DECOR_LINE),
                    ui._division_btn_text,
                    app_icon(PIcons.CARET_DOWN, size=14, color=DECOR_LINE),
                ],
                spacing=SPACE_1,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            items=[],
            style=ft.ButtonStyle(
                padding=ft.Padding.symmetric(horizontal=SPACE_2, vertical=6),
                side={ft.ControlState.DEFAULT: ft.BorderSide(0.8, BORDER_CONTROL)},
                shape=ft.RoundedRectangleBorder(radius=RADIUS_SHARP),
                bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
            ),
            tooltip="División activa — cambia preview",
            visible=False,
        )
        ui._division_col = ft.Column([ui._division_label, ui._division_popup], spacing=SPACE_0, tight=True)
        ui._partition_dd = None
        ui._partition_btn = _ghost_btn(
            "Todos", PIcons.FUNNEL, lambda _: ui._open_partition_picker()
        )
        ui._partition_btn.visible = False
        ui._partition_btn.tooltip = "Seleccionar divisiones para transferir — multi §7"
        ui._edit_division_btn = _ghost_btn(
            "Editar", PIcons.PENCIL_SIMPLE, lambda _: ui._open_edit_division_dialog()
        )
        ui._edit_division_btn.visible = False
        ui._edit_division_btn.tooltip = (
            "Editar división — quitar/agregar entre ORIGINAL y División activa §4"
        )
        ui._mode_bar = None

    def _build_footer_bar(self) -> ft.Container:
        """Pie fijo de 56 px con resumen de selección/estado y acción primaria."""
        ui = self._ui
        ui._footer_summary = ft.Text(
            "Sin pistas cargadas · Elige una plataforma o archivo local",
            size=FS_SM,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            opacity=1.0,
        )
        ui._footer_sub = ft.Text(
            f"Destino: {ui.state.destination}",
            size=FS_META,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            opacity=1.0,
        )
        ui._footer_transfer_btn = _primary_btn(
            "Transferir",
            PIcons.ARROWS_LEFT_RIGHT,
            ui._on_transfer,
            height=36,
            disabled=True,
        )
        ui._footer_transfer_btn.tooltip = "Elegir destino y transferir la selección"
        return ft.Container(
            height=56,
            bgcolor=BG_PANEL,
            border=ft.Border.only(top=ft.BorderSide(1, BORDER_CONTROL)),
            padding=ft.Padding.symmetric(horizontal=SPACE_4, vertical=SPACE_2),
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[ui._footer_summary, ui._footer_sub],
                        spacing=SPACE_1,
                        alignment=ft.MainAxisAlignment.CENTER,
                        expand=True,
                    ),
                    ui._footer_transfer_btn,
                ],
                spacing=SPACE_3,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            visible=True,
        )

    def _sync_workflow_ui(self, states: dict[str, dict[str, object]] | None = None) -> None:
        """Sincroniza tira de pasos, ruta origen->destino, chips y pie fijo 56 px."""
        ui = self._ui
        s = ui.state
        tracks = list(getattr(s, "tracks", []) or [])
        load_state = getattr(s, "load_state", LoadState.IDLE)
        transfer_state = getattr(s, "transfer_state", TransferState.IDLE)
        has_tracks = len(tracks) > 0 and load_state == LoadState.READY
        is_transferring = transfer_state == TransferState.RUNNING
        has_report = getattr(s, "last_report", None) is not None
        catalog_open = bool(getattr(ui, "_catalog_open", False))

        # 1. Tira de pasos: 01 Origen -> 02 Revisar -> 03 Destino -> 04 Transferir
        step_items = getattr(ui, "_step_items", None)
        if step_items and len(step_items) == 4:
            if not has_tracks:
                active_idx = 0
            elif is_transferring or has_report:
                active_idx = 3
            elif getattr(s, "destination_confirmed", False):
                active_idx = 2
            else:
                active_idx = 1
            for idx, (box, num_txt, lbl_txt) in enumerate(step_items):
                if idx < active_idx or (idx == 3 and has_report and not is_transferring):
                    box.bgcolor = BG_PANEL
                    box.border = ft.Border(bottom=ft.BorderSide(2, SUCCESS))
                    num_txt.color = SUCCESS
                    lbl_txt.color = TEXT_PRIMARY
                elif idx == active_idx:
                    box.bgcolor = BG_LIST
                    box.border = ft.Border(bottom=ft.BorderSide(2, ACCENT))
                    num_txt.color = ACCENT
                    lbl_txt.color = TEXT_PRIMARY
                else:
                    box.bgcolor = BG_PANEL
                    box.border = ft.Border(bottom=ft.BorderSide(2, BORDER_MUTED))
                    num_txt.color = TEXT_MUTED
                    lbl_txt.color = TEXT_MUTED

        # 2. Ruta origen -> destino
        src_name = getattr(s, "source", "") or "Origen"
        pl_name = getattr(s, "playlist_name", "") or "Sin cargar"
        dst_name = getattr(s, "destination", "") or "Elegir al transferir"
        route_src_icon = getattr(ui, "_route_src_icon", None)
        if route_src_icon is not None:
            route_src_icon.value = _platform_icon(src_name)
        route_dst_icon = getattr(ui, "_route_dst_icon", None)
        if route_dst_icon is not None:
            route_dst_icon.value = _platform_icon(dst_name)
            route_dst_icon.color = ACCENT if has_tracks else TEXT_MUTED
        route_src = getattr(ui, "_route_src_text", None)
        if route_src is not None:
            route_src.value = f"{src_name} · {pl_name}" if has_tracks else f"{src_name} · Sin playlist"
        route_pipe = getattr(ui, "_route_pipe_text", None)
        if route_pipe is not None:
            route_pipe.value = f"{len(tracks)} pistas" if has_tracks else "0 pistas"
        route_dst = getattr(ui, "_route_dst_text", None)
        if route_dst is not None:
            route_dst.value = dst_name
        route_dst_btn = getattr(ui, "_route_dst_btn", None)
        if route_dst_btn is not None:
            route_dst_btn.border = ft.Border.all(
                0.8, ACCENT if has_tracks and not is_transferring else BORDER_CONTROL
            )

        # 3. Chips LISTA / REVISAR / SIN MATCH
        rev_cnt = sum(
            1 for t in tracks if getattr(t, "transfer_status", "pending") == "revision_necesaria"
        )
        no_cnt = sum(
            1
            for t in tracks
            if getattr(t, "transfer_status", "pending") in ("not_found", "error")
        )
        ok_cnt = max(0, len(tracks) - rev_cnt - no_cnt)
        chips_row = getattr(ui, "_status_chips_row", None)
        counts = (ok_cnt, rev_cnt, no_cnt)
        if chips_row is not None and self._last_chip_counts != counts:
            self._last_chip_counts = counts
            chips_row.controls = [
                status_chip(f"{ok_cnt} LISTA", kind="ok" if ok_cnt else "neutral"),
                status_chip(f"{rev_cnt} REVISAR", kind="warn" if rev_cnt else "neutral"),
                status_chip(f"{no_cnt} SIN MATCH", kind="err" if no_cnt else "neutral"),
            ]

        # 4. Subcabecera de lista editable solo en vista doble/preview
        subheader = getattr(ui, "_lista_subheader", None)
        preview_panel = getattr(ui, "_preview_panel", None)
        if subheader is not None and preview_panel is not None:
            subheader.visible = bool(getattr(preview_panel, "visible", False))

        # 5. Pie fijo 56 px
        footer_bar = getattr(ui, "_footer_bar", None)
        if footer_bar is not None:
            footer_bar.visible = not catalog_open
        footer_summary = getattr(ui, "_footer_summary", None)
        if footer_summary is not None:
            if load_state in (LoadState.LOADING_META, LoadState.LOADING_TRACKS):
                footer_summary.value = "Cargando pistas…"
                footer_summary.color = TEXT_MUTED
            elif load_state == LoadState.ERROR:
                footer_summary.value = "Error al cargar · Revisa origen o sesión"
                footer_summary.color = ERROR_COL
            elif has_tracks:
                sel_cnt = sum(1 for t in tracks if getattr(t, "selected", False))
                total_ms = sum(int(getattr(t, "duration_ms", 0) or 0) for t in tracks)
                dur_part = ""
                if total_ms > 0:
                    mins = total_ms // 60000
                    dur_part = f" · {mins} min"
                parts = [f"{sel_cnt} de {len(tracks)} seleccionadas{dur_part}"]
                if rev_cnt:
                    parts.append(f"{rev_cnt} revisar")
                if no_cnt:
                    parts.append(f"{no_cnt} sin match")
                footer_summary.value = " · ".join(parts)
                footer_summary.color = TEXT_PRIMARY
            else:
                footer_summary.value = "Sin pistas cargadas · Elige una plataforma o archivo local"
                footer_summary.color = TEXT_MUTED

        footer_sub = getattr(ui, "_footer_sub", None)
        if footer_sub is not None:
            dual_mode = getattr(s, "dual_mode", "lista")
            mode_note = " · Solo vista previa" if dual_mode == "preview" else ""
            footer_sub.value = f"Destino: {dst_name}{mode_note}"

        footer_btn = getattr(ui, "_footer_transfer_btn", None)
        if footer_btn is not None:
            spec = (states or {}).get("transfer") or {}
            footer_btn.disabled = bool(spec.get("disabled", (not has_tracks) or is_transferring))
            tip = str(spec.get("tooltip", "") or "")
            if tip:
                footer_btn.tooltip = tip
            desired_label = (
                "Transfiriendo…"
                if is_transferring
                else ("Exportar" if dst_name == EXPORT_DEST_LABEL else "Transferir")
            )
            if self._last_footer_btn_label != desired_label:
                self._last_footer_btn_label = desired_label
                btn_icon = PIcons.EXPORT if desired_label == "Exportar" else PIcons.ARROWS_LEFT_RIGHT
                set_button_text(footer_btn, desired_label, icon=btn_icon)

    def _assemble_content_area(self, header_bar: ft.Control, col_headers: ft.Container) -> None:
        ui = self._ui
        ui._dual_row = ft.Row(
            controls=[ui._lista_panel, ui._preview_panel],
            spacing=SPACE_2,
            expand=True,
            visible=True,
        )
        ui._preview_panel.visible = False
        ui._lista_panel.visible = True
        ui._list_area = ft.Container(
            content=ui._dual_row,
            expand=True,
            margin=ft.Margin.only(top=-SPACE_2),
            animate_opacity=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_scale=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            opacity=1.0,
            scale=1.0,
        )
        ui._content = ft.Container(
            expand=True,
            bgcolor=BG_LIST,
            padding=ft.Padding.only(left=SPACE_5, right=SPACE_5, top=SPACE_3, bottom=SPACE_2),
            content=ft.Column(
                controls=[
                    ui._content_progress,
                    header_bar,
                    ui._contextual_bar,
                    ui._catalog_panel,
                    col_headers,
                    ui._list_area,
                    ui._footer_bar,
                    ui._telemetry.container,
                ],
                spacing=SPACE_2,
                expand=True,
            ),
        )
        self._sync_workflow_ui()

    def _build_nav_rail(self) -> None:
        build_nav_rail(self._ui)

    def _build_modules_and_root(self) -> None:
        ui = self._ui
        ui._panel_gestion = ft.Row(
            controls=[ui._sidebar, ui._content],
            spacing=SPACE_0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        try:
            from services.library_state import LibraryState
            from ui.views.library_view import LibraryView

            lib_state = LibraryState(service=getattr(ui.state, "service", None))
            ui.library_state = lib_state  # type: ignore[attr-defined]
            ui._panel_biblioteca = LibraryView(
                ui.page,
                lib_state,
                getattr(ui.state, "service", None),
                app_state=ui.state,
                on_open_downloads=lambda: ui._select_module(2),
                on_open_gestion=ui._open_folder_in_gestion,
            )
        except Exception as exc:
            try:
                ui.state.log(f"[WARN] Biblioteca fallback: {exc}")
            except Exception:
                pass
            ui._panel_biblioteca = ft.Container(
                expand=True,
                bgcolor=BG_LIST,
                alignment=ft.Alignment.CENTER,
                content=self._make_cono_empty(
                    "Biblioteca", "Colección local, historial 20 y portadas — pronto.", visible=True
                ),
            )

        ui._auto_play_download_ids = set()
        from services.mpris_service import MPRISService
        from ui.player.player_bar import PlayerBar
        from ui.player.player_queue import PlayerQueuePanel

        ui.player_bar = PlayerBar(
            ui.page,
            ui.state,
            on_toggle_queue=lambda: ui.player_queue.toggle(),
        )
        ui.player_bar.attach_mpris(MPRISService())
        ui.player_queue = PlayerQueuePanel(
            ui.page,
            ui.player_bar,
            on_open_change=self._sync_queue_album_column,
        )
        self._sync_queue_album_column(ui.player_queue.is_open, update=False)
        ui.player_bar.log_audio_services_count("init")
        if hasattr(ui, "_panel_biblioteca") and ui._panel_biblioteca is not None:
            ui._panel_biblioteca.player_bar = ui.player_bar

        lib_for_picker = getattr(ui, "library_state", None)
        if lib_for_picker is not None and hasattr(lib_for_picker, "subscribe"):
            def _on_library_change() -> None:
                try:
                    ui._refresh_playlist_picker(ui.state.source)
                except Exception:
                    pass

            lib_for_picker.subscribe(_on_library_change)
            try:
                ui._refresh_playlist_picker(ui.state.source)
            except Exception:
                pass

        try:
            from ui.views.downloads_view import DownloadsView

            ui._panel_descargas = DownloadsView(
                ui.page, ui.state, library_state=getattr(ui, "library_state", None)
            )
            ui._panel_descargas.player_bar = ui.player_bar  # type: ignore[attr-defined]
        except Exception as exc:
            try:
                ui.state.log(f"[WARN] Descargas fallback: {exc}")
            except Exception:
                pass
            ui._panel_descargas = ft.Container(
                expand=True,
                bgcolor=BG_LIST,
                alignment=ft.Alignment.CENTER,
                content=self._make_cono_empty(
                    "Descargas", "yt-dlp + progreso — no disponible.", visible=True
                ),
            )

        try:
            from ui.views.config_view import ConfigView

            ui._panel_config = ConfigView(
                ui.page,
                on_change=ui._on_config_changed,
                on_welcome=lambda: ui.on_show_welcome and ui.on_show_welcome(),
            )
        except Exception as exc:
            try:
                ui.state.log(f"[WARN] Config fallback: {exc}")
            except Exception:
                pass
            ui._panel_config = ft.Container(
                expand=True,
                bgcolor=BG_LIST,
                alignment=ft.Alignment.CENTER,
                content=self._make_cono_empty(
                    "Configuración", "Preferencias no disponibles.", visible=True
                ),
            )

        ui._module_panels = [
            ui._panel_gestion,
            ui._panel_biblioteca,
            ui._panel_descargas,
            ui._panel_config,
        ]
        for i, p in enumerate(ui._module_panels):
            p.visible = (i == 0)
        ui._module_stack = ft.Stack(
            controls=ui._module_panels,
            expand=True,
            fit=ft.StackFit.EXPAND,
        )

        # Layout principal: rail flotante + módulo + cola lateral por empuje, y reproductor abajo.
        ui.root = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ui._nav_rail,
                            ui._module_stack,
                            ui.player_queue,
                        ],
                        spacing=SPACE_0,
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    ui.player_bar,
                ],
                spacing=SPACE_0,
                expand=True,
            ),
            bgcolor=BG_LIST,
            expand=True,
        )
