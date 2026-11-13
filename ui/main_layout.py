"""ui/main_layout.py — Construcción de controles: selectores, barra lateral y contenido principal.

Mixin de `PlaylistManagerUI` (ui/main_ui.py): los métodos se mezclan en la clase
y comparten su `self`.
"""

from __future__ import annotations

import asyncio
import flet as ft

from ui.theme.tokens import (
    BG_DEEP,
    BG_HOVER,
    MOTION_HOVER_MS,
    RADIUS_BUTTON,
    RADIUS_INPUT,
    RADIUS_RAIL,
    RADIUS_RAIL_ITEM,
    RAIL_ANIM_MS,
    RAIL_BRAND_W,
    RAIL_HEADER_H,
    RAIL_ITEM_H,
    RAIL_ITEM_PAD_V,
    RAIL_ITEM_SPACING,
    RAIL_LABEL_W,
    RAIL_MARGIN,
    RAIL_PAD_H,
    RAIL_PAD_V,
    RAIL_VERSION_H,
    RAIL_VERSION_H_COLLAPSED,
    RAIL_W_COLLAPSED,
    RAIL_W_EXPANDED,
    ACCENT,
    ACCENT_HALO,
    BG_INPUT,
    BG_LIST,
    BG_PANEL,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    ERROR_COL,
    MOTION_PANEL_MS,
    SIDEBAR_BG,
    SUCCESS,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
)
from ui.components.song_row import ITEM_H
from ui.components.skeletons import (
    PreviewSkeletonRow,
    SkeletonRow,
)
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    _section_label,
    app_text,
    app_text_field,
    input_border,
    organize_dropdown,
    safe_update,
)
from ui.theme.icons import FONT_PHOSPHOR, FONT_PHOSPHOR_FILL, PIcons, app_icon, app_icon_button
from ui.theme.fonts import (
    FONT_HEADLINE_MEDIUM,
    FONT_HEADLINE,
    FONT_HEADLINE_BOLD,
    FONT_TEXT,
    brand_family,
    mono_family,
)
from ui.components.telemetry import TelemetryDrawer
from core.config import APP_VERSION, EXPORT_DEST_LABEL
from core.models import LoadState, TransferState
from core.state import AppState


def compute_contextual_actions_state(
    state, *, net_blocked: bool = False
) -> dict[str, dict[str, object]]:
    """Calcula visibilidad, estado habilitado y tooltip de la barra contextual.

    Mantiene la regla de 2.10: mostrar/habilitar solo acciones aplicables a la
    fuente cargada (por ejemplo, ocultar ``Descargar`` si todas las pistas ya
    son archivos de audio locales en disco, y ocultar acciones de pista cuando
    no hay playlist cargada salvo ``Fusionar``).
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
        "merge": {
            "visible": True,
            "disabled": is_transferring,
            "tooltip": "Fusionar varias playlists o fuentes locales sin duplicados",
        },
        "download": {
            "visible": is_ready and not all_local_audio,
            "disabled": (not is_ready) or all_local_audio or download_busy,
            "tooltip": (
                "Descarga en curso"
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


class LayoutMixin:
    """Construcción de controles: selectores, barra lateral y contenido principal."""

    def _make_order_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton para elegir Artista-Título vs Título-Artista (reusa tokens)."""
        # Fallback a Dropdown si SegmentedButton no está disponible en runtime
        try:
            return ft.SegmentedButton(
                selected=[current],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="artist-title", label=ft.Text("Artista - Título", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="title-artist", label=ft.Text("Título - Artista", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=on_change,
            )
        except Exception:
            # fallback Dropdown compacto
            dd = ft.Dropdown(
                value=current,
                width=200, height=38,
                bgcolor=BG_INPUT, border=input_border(),
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                options=[
                    ft.dropdown.Option("artist-title", "Artista - Título"),
                    ft.dropdown.Option("title-artist", "Título - Artista"),
                ],
                on_select=on_change,  # compat
            )
            return dd

    def _make_mode_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton Lista|Doble|Preview — modo doble solo al Organizar/Dividir."""
        try:
            return ft.SegmentedButton(
                selected=[current],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="lista", label=ft.Text("Lista", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="doble", label=ft.Text("Doble", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="preview", label=ft.Text("Preview", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=on_change,
            )
        except Exception:
            return ft.Dropdown(
                value=current,
                width=180, height=38,
                bgcolor=BG_INPUT, border=input_border(),
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                options=[
                    ft.dropdown.Option("lista", "Lista"),
                    ft.dropdown.Option("doble", "Doble"),
                    ft.dropdown.Option("preview", "Preview"),
                ],
                on_select=on_change,
            )

    def _make_cono_empty(self, title: str, subtitle: str, visible: bool = True) -> ft.Container:
        """Empty con icono cono para Biblioteca/Descargas/Preview — reusa tokens."""
        return ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(controls=[
                ft.Container(content=app_icon(PIcons.WRENCH, size=52, color=TEXT_DIM),
                             bgcolor=CHIP_BG, border=ft.Border.all(0.8, BORDER_LIGHT),
                             border_radius=20, padding=ft.Padding.all(20)),
                ft.Text(title, size=18, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD, opacity=1.0),
                ft.Text(subtitle, size=12, color=TEXT_MUTED, font_family=FONT_TEXT, opacity=1.0, text_align=ft.TextAlign.CENTER),
                ft.Text("Función en desarrollo", size=11, color=TEXT_DIM, font_family=FONT_TEXT, opacity=0.8),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=visible,
            expand=True,
        )

    def _build_sidebar(self) -> None:
        s = self.state

        self.btn_wizard = app_icon_button(
            PIcons.GEAR, icon_color=TEXT_DIM, hover_color_icon=TEXT_PRIMARY, size=16,
            tooltip="Configurar credenciales", on_click=self._on_open_wizard,
            width=28, height=28,
        )
        logo = ft.Column([
            ft.Row([
                ft.Container(
                    content=app_icon(PIcons.MUSIC_NOTES, color=ACCENT, size=20, fill=True),
                    bgcolor=ACCENT_HALO, border_radius=8, padding=ft.Padding.all(6),
                ),
                ft.Column([
                    ft.Text(spans=[
                        ft.TextSpan("Melomaniac", ft.TextStyle(size=20,
                                                               color=TEXT_PRIMARY, font_family=brand_family("bold"))),
                    ], opacity=1.0),
                    ft.Text(f"v{APP_VERSION}", size=9, color=TEXT_DIM, font_family=FONT_TEXT,
                            style=ft.TextStyle(letter_spacing=0.8), opacity=1.0),
                ], spacing=0, tight=True, expand=True),
                self.btn_wizard,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=0)

        _dd_style = dict(
            bgcolor=BG_INPUT, border=input_border(),
            label_style=ft.TextStyle(color=TEXT_MUTED, size=10, font_family=FONT_HEADLINE),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=8, vertical=0),
            expand=True,
        )

        def _on_src_select(e) -> None:
            val = e.control.value
            s.set_source(val)
            if val == "Archivo Local":
                asyncio.create_task(self._do_local_pick())
            elif val == "Pegar Texto":
                self._open_paste_dialog()
            elif val in AppState.PLATFORMS:
                asyncio.create_task(self._refresh_auth_live(val))
                self._refresh_playlist_picker(val)

        def _on_dst_select(e) -> None:
            val = e.control.value
            s.set_destination(val)
            if val == EXPORT_DEST_LABEL:
                # Export local no requiere sesión
                if isinstance(self._transfer_btn.content, ft.Row) and len(self._transfer_btn.content.controls) >= 2:
                    self._transfer_btn.content.controls[0].value = PIcons.EXPORT
                    self._transfer_btn.content.controls[1].value = "Exportar"
                elif hasattr(self._transfer_btn.content, "value"):
                    self._transfer_btn.content.value = "Exportar"
                self._transfer_btn.update()
                return
            else:
                # restaura icono transfer si venía de export
                try:
                    if isinstance(self._transfer_btn.content, ft.Row) and len(self._transfer_btn.content.controls) >= 2:
                        self._transfer_btn.content.controls[0].value = PIcons.ARROWS_LEFT_RIGHT
                        self._transfer_btn.content.controls[1].value = "Transferir"
                    elif hasattr(self._transfer_btn.content, "value"):
                        self._transfer_btn.content.value = "Transferir"
                except Exception:
                    pass
            if val in AppState.PLATFORMS:
                asyncio.create_task(self._refresh_auth_live(val))

        self._src_dd = ft.Dropdown(
            label="Origen", value=s.source,
            options=[ft.dropdown.Option(key=p, text=p) for p in AppState.SOURCE_OPTIONS],
            on_select=_on_src_select, **_dd_style,
        )
        # El destino definitivo se elige con preflight dentro del diálogo de
        # transferencia; este control se conserva oculto por compatibilidad.
        _dst_opts = [*AppState.PLATFORMS, EXPORT_DEST_LABEL]
        self._dst_dd = ft.Dropdown(
            label="Destino", value=s.destination,
            options=[ft.dropdown.Option(key=p, text=p) for p in _dst_opts],
            on_select=_on_dst_select, visible=False, **_dd_style,
        )
        self._status_badge      = ft.Text("", size=10, color=SUCCESS, font_family=mono_family(), opacity=1.0)
        self._dest_session_warn = ft.Text("", size=9, color=ERROR_COL, font_family=FONT_TEXT, visible=False)

        platform_section = ft.Column([
            _section_label("PLATAFORMAS"),
            ft.Row([self._src_dd, self._dst_dd], spacing=8),
            self._status_badge,
            self._dest_session_warn,
        ], spacing=8)

        self._id_clear_btn = app_icon_button(
            PIcons.X, icon_color=TEXT_DIM, hover_color_icon=TEXT_PRIMARY, size=12,
            width=24, height=24,
            tooltip="Limpiar ID", visible=False,
            on_click=self._on_clear_id,
        )

        def _on_id_change(_):
            self._id_clear_btn.visible = bool(self._id_field.value)
            self._id_clear_btn.update()

        self._id_field = app_text_field(
            label="ID o URL de la Playlist",
            hint_text="pl.xxxx  /  PLxxxx  /  URL",
            suffix=self._id_clear_btn,
            on_change=_on_id_change,
            on_submit=lambda e: asyncio.create_task(self._do_cloud_load(e)),
        )

        # Selector de playlists ya sincronizadas. Es la vía principal: elegir
        # una playlist de la biblioteca no exige pegar un ID. El campo de
        # ID/URL queda detrás de «Usar URL o ID» como alternativa avanzada, y
        # se rellena en cada cambio de origen con lo que haya en caché (sin
        # disparar sincronización: eso haría tres llamadas al abrir Gestión).
        # Mismo estilo que los dropdowns de plataforma/destino del sidebar,
        # sin ``expand``: aquí vive en una Column, donde expandiría en alto.
        _dd_col_style = {k: v for k, v in _dd_style.items() if k != "expand"}
        self._playlist_pick = ft.Dropdown(
            label="Playlist de la biblioteca", value="", options=[],
            on_select=self._on_playlist_pick, **_dd_col_style,
        )
        self._playlist_pick.visible = False
        self._playlist_help = app_text(
            "", size=9, color=TEXT_DIM, font_family=FONT_TEXT, visible=False,
        )
        self._url_toggle_label = ft.Text(
            "Usar URL o ID", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE,
        )
        self._use_url_btn = ft.TextButton(
            content=ft.Row([
                app_icon(PIcons.LINK, size=13, color=TEXT_MUTED),
                self._url_toggle_label,
            ], spacing=6, tight=True),
            on_click=self._on_toggle_url_field,
        )
        self._url_mode = False

        self._playlist_section = ft.Column([
            _section_label("PLAYLIST"),
            self._playlist_pick,
            self._playlist_help,
            self._id_field,
            self._use_url_btn,
        ], spacing=8, visible=(s.source not in AppState.LOCAL_SOURCES))
        # Estado inicial sin tocar la página: aún no existe durante el build.
        self._id_field.visible = False
        self._playlist_help.value = (
            "Sin playlists sincronizadas: usa «Usar URL o ID» o sincroniza "
            "la plataforma en Biblioteca."
        )
        self._playlist_help.visible = True
        self._playlist_divider = ft.Divider(
            height=1, color=BORDER_MUTED, thickness=0.5,
            visible=(s.source not in AppState.LOCAL_SOURCES),
        )

        _BTN_W, _BTN_H = 129, 44
        self._load_btn = _primary_btn("Cargar", PIcons.DOWNLOAD_SIMPLE, self._on_load, width=_BTN_W, height=_BTN_H)
        self._merge_sidebar_btn = _ghost_btn(
            "Fusionar", PIcons.QUEUE, lambda _: self._open_merge_dialog(), width=_BTN_W, height=_BTN_H,
        )

        actions = ft.Column([
            ft.Row([self._load_btn, self._merge_sidebar_btn], spacing=6),
        ], spacing=6)

        self._rl_banner = ft.Container(
            content=ft.Row([
                app_icon(PIcons.CLOCK, color=WARNING, size=14),
                ft.Text("", size=10, color=WARNING, font_family=mono_family("light"), opacity=1.0),
            ], spacing=6),
            bgcolor=BG_PANEL, border=ft.Border.all(0.8, WARNING),
            border_radius=8, padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            visible=False,
        )

        self._progress_bar = ft.ProgressBar(value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=4)
        self._progress_row = ft.Container(
            content=ft.Column([
                self._progress_bar,
                ft.Row([ft.Text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0)],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ], spacing=4),
            visible=False,
            border=ft.Border.all(0.8, BORDER_LIGHT), border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

        self._telemetry = TelemetryDrawer(self.page, sidebar_width=300)

        fixed_top = ft.Column(controls=[
            logo,
            ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
            platform_section,
            ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
            self._playlist_section,
            self._playlist_divider,
            _section_label("ACCIONES"),
            actions,
        ], spacing=12)

        scrollable_bottom = ft.Column(controls=[
            self._rl_banner,
            self._telemetry.container,
        ], spacing=12, scroll=ft.ScrollMode.ADAPTIVE, expand=True)

        sidebar_col   = ft.Column(controls=[fixed_top, scrollable_bottom], spacing=12, expand=True)
        sidebar_stack = ft.Stack(controls=[sidebar_col, self._telemetry.handle], expand=True)

        self._sidebar = ft.Container(
            width=300, padding=ft.Padding.all(18),
            bgcolor=SIDEBAR_BG, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            border_radius=ft.BorderRadius.only(top_right=14, bottom_right=14),
            border=ft.Border.only(right=ft.BorderSide(1, BORDER_LIGHT)),
            content=sidebar_stack,
        )

    def _build_content(self) -> None:
        header_bar = self._build_content_header()
        self._contextual_bar = self._build_contextual_bar()
        self._catalog_panel = self._build_catalog_panel()
        self._col_headers = self._build_col_headers()
        self._build_list_and_preview_panels()
        self._build_mode_bar()
        self._assemble_content_area(header_bar, self._col_headers)

    def _build_contextual_bar(self) -> ft.Container:
        """Barra de acciones contextuales encima de la tabla (Bloque 2.10)."""
        self._ctx_transfer_btn = _primary_btn(
            "Transferir", PIcons.ARROWS_LEFT_RIGHT, self._on_transfer, height=34,
        )
        self._ctx_organize_btn = _ghost_btn(
            "Organizar", PIcons.SLIDERS_HORIZONTAL, self._on_organize, height=34,
        )
        self._ctx_split_btn = _ghost_btn(
            "Dividir", PIcons.GIT_BRANCH, self._on_split, height=34,
        )
        self._ctx_reorganize_btn = _ghost_btn(
            "Reorganizar", PIcons.ARROWS_CLOCKWISE, self._on_reorganize, height=34, disabled=True,
        )
        self._ctx_download_btn = _ghost_btn(
            "Descargar", PIcons.DOWNLOAD_SIMPLE, lambda _: self._on_batch_download(), height=34,
        )
        self._ctx_export_btn = _ghost_btn(
            "Exportar", PIcons.EXPORT,
            lambda _: asyncio.create_task(self._open_export_dialog()), height=34,
        )
        self._transfer_btn = self._ctx_transfer_btn
        self._organize_btn = self._ctx_organize_btn
        self._split_btn = self._ctx_split_btn
        self._reorganize_btn = self._ctx_reorganize_btn
        self._ctx_action_buttons = {
            "transfer": self._ctx_transfer_btn,
            "organize": self._ctx_organize_btn,
            "split": self._ctx_split_btn,
            "download": self._ctx_download_btn,
            "export": self._ctx_export_btn,
        }
        row = ft.Row(
            controls=[
                self._ctx_transfer_btn,
                self._ctx_organize_btn,
                self._ctx_split_btn,
                self._ctx_reorganize_btn,
                self._ctx_download_btn,
                self._ctx_export_btn,
            ],
            spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        bar = ft.Container(
            content=row,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        )
        self._apply_contextual_actions_state(
            compute_contextual_actions_state(self.state)
        )
        return bar

    def _apply_contextual_actions_state(
        self, states: dict[str, dict[str, object]]
    ) -> None:
        """Aplica visibilidad, habilitación y tooltips a la barra contextual."""
        buttons = getattr(self, "_ctx_action_buttons", {})
        for key, btn in buttons.items():
            spec = states.get(key)
            if not spec:
                continue
            btn.visible = bool(spec.get("visible", True))
            btn.disabled = bool(spec.get("disabled", False))
            tip = str(spec.get("tooltip", "") or "")
            if tip:
                btn.tooltip = tip

    def _build_catalog_panel(self) -> ft.Container:
        """Panel de búsqueda en catálogo con pestañas Canciones/Álbumes/Artistas (2.11)."""
        default_plat = (
            self.state.source
            if self.state.source in AppState.PLATFORMS
            else AppState.PLATFORMS[0]
        )
        self._catalog_platform_dd = organize_dropdown(
            [(p, p) for p in AppState.PLATFORMS],
            default_plat,
            "Plataforma",
            width=170,
            height=38,
            on_select=self._on_catalog_platform_change,
        )
        self._catalog_query_field = app_text_field(
            hint_text="Buscar canciones, álbumes o artistas…",
            prefix_icon=PIcons.MAGNIFYING_GLASS,
            expand=True,
            height=38,
            on_submit=lambda _: asyncio.create_task(self._on_catalog_search_submit()),
        )
        self._catalog_run_btn = _primary_btn(
            "Buscar",
            PIcons.MAGNIFYING_GLASS,
            lambda _: asyncio.create_task(self._on_catalog_search_submit()),
            height=38,
        )
        self._catalog_close_btn = app_icon_button(
            glyph=PIcons.X,
            icon_color=TEXT_MUTED,
            hover_color_icon=TEXT_PRIMARY,
            size=16,
            tooltip="Volver a la tabla de playlist",
            on_click=lambda _: self._toggle_catalog_panel(False),
            width=32,
            height=32,
        )
        try:
            self._catalog_tabs: ft.Control = ft.SegmentedButton(
                selected=["songs"],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="songs", label=ft.Text("Canciones", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="albums", label=ft.Text("Álbumes", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="artists", label=ft.Text("Artistas", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=self._on_catalog_tab_change,
            )
        except Exception:
            self._catalog_tabs = organize_dropdown(
                [("songs", "Canciones"), ("albums", "Álbumes"), ("artists", "Artistas")],
                "songs",
                "Tipo",
                width=160,
                height=38,
                on_select=self._on_catalog_tab_change,
            )

        self._catalog_detail_title = ft.Text(
            "", size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD, expand=True,
        )
        self._catalog_detail_load_btn = _primary_btn(
            "Cargar en Gestión", PIcons.PLAYLIST, lambda _: self._on_catalog_detail_load(), height=32,
        )
        self._catalog_detail_dl_btn = _ghost_btn(
            "Descargar", PIcons.DOWNLOAD_SIMPLE, lambda _: self._on_catalog_detail_download(), height=32,
        )
        self._catalog_detail_add_btn = _ghost_btn(
            "Añadir a playlist", PIcons.PLUS,
            lambda _: asyncio.create_task(self._on_catalog_detail_add()), height=32,
        )
        self._catalog_detail_bar = ft.Container(
            visible=False,
            bgcolor=CHIP_BG,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            content=ft.Row(
                [
                    _ghost_btn("Volver", PIcons.ARROW_LEFT, lambda _: self._on_catalog_back(), height=32),
                    self._catalog_detail_title,
                    self._catalog_detail_load_btn,
                    self._catalog_detail_dl_btn,
                    self._catalog_detail_add_btn,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self._catalog_status = ft.Text(
            "Elige una pestaña y escribe una búsqueda para explorar el catálogo.",
            size=11,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
        )
        self._catalog_results_lv = ft.ListView(
            spacing=6, expand=True, padding=ft.Padding.only(bottom=12),
        )
        return ft.Container(
            visible=False,
            expand=True,
            bgcolor=BG_LIST,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.all(14),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self._catalog_platform_dd,
                            self._catalog_query_field,
                            self._catalog_run_btn,
                            self._catalog_close_btn,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [self._catalog_tabs, ft.Container(expand=True), self._catalog_status],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._catalog_detail_bar,
                    self._catalog_results_lv,
                ],
                spacing=10,
                expand=True,
            ),
        )

    def _build_content_header(self) -> ft.Row:
        self._playlist_title = ft.Text(
            "Cargar una playlist", size=22,
            color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD, opacity=1.0,
        )
        self._track_count = ft.Text("", size=12, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0)
        self._search_field = app_text_field(
            hint_text="Buscar título, artista…",
            prefix_icon=PIcons.MAGNIFYING_GLASS,
            width=240, height=36,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            on_change=self._on_search_change,
        )
        self._catalog_search_btn = _ghost_btn(
            "Catálogo",
            PIcons.GLOBE,
            lambda _: self._on_toggle_catalog_search(),
            height=36,
        )
        self._catalog_search_btn.tooltip = "Buscar canciones, álbumes o artistas en plataformas"
        self._segment_dd = ft.Dropdown(
            width=160, height=38,
            bgcolor=BG_INPUT, border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            on_select=lambda e: self.state.set_active_segment(e.control.value),
            visible=False,
            hint_text="Segmento..."
        )

        self._auth_yt = app_icon_button(
            glyph=PIcons.YOUTUBE_LOGO,
            icon_color=TEXT_DIM,
            size=17,
            tooltip="YouTube Music · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(self._on_auth_probe("YouTube Music")),
            width=28, height=28,
        )
        self._auth_am = app_icon_button(
            glyph=PIcons.APPLE_LOGO,
            icon_color=TEXT_DIM,
            size=17,
            tooltip="Apple Music · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(self._on_auth_probe("Apple Music")),
            width=28, height=28,
        )
        self._auth_sp = app_icon_button(
            glyph=PIcons.SPOTIFY_LOGO,
            icon_color=TEXT_DIM,
            size=17,
            tooltip="Spotify · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(self._on_auth_probe("Spotify")),
            width=28, height=28,
        )
        self._auth_strip = ft.Row(controls=[self._auth_yt, self._auth_am, self._auth_sp],
                                  spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        self._select_all_chk = ft.Checkbox(
            label="Todo",
            label_style=ft.TextStyle(color=TEXT_MUTED, size=11, font_family=FONT_HEADLINE),
            fill_color={ft.ControlState.SELECTED: ACCENT},
            check_color=TEXT_PRIMARY,
            border_side=ft.BorderSide(1.5, TEXT_DIM),
            on_change=lambda _: self.state.toggle_select_all(),
        )

        self._content_progress_bar = ft.ProgressBar(value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=4)
        self._content_prog_label   = ft.Text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=0.6)
        self._content_eta_label    = ft.Text("", size=10, color=TEXT_DIM,   font_family=mono_family("light"), opacity=0.45)
        self._content_progress = ft.Container(
            content=ft.Column([
                self._content_progress_bar,
                ft.Row([self._content_prog_label, ft.Container(expand=True), self._content_eta_label], spacing=0),
            ], spacing=4),
            visible=False,
            border=ft.Border.all(0.8, BORDER_LIGHT), border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

        self._clear_session_btn = app_icon_button(
            glyph=PIcons.TRASH,
            icon_color=TEXT_DIM,
            size=16,
            tooltip="Limpiar playlist cargada",
            on_click=self._on_clear_session,
            width=28, height=28,
        )

        # header sin selector de partición (movido junto a Vista §)
        header_bar = ft.Row(controls=[
            ft.Column([self._playlist_title, self._track_count], spacing=2),
            ft.Container(expand=True),
            self._auth_strip, self._search_field, self._catalog_search_btn,
            self._select_all_chk, self._clear_session_btn,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

        return header_bar

    def _build_col_headers(self) -> ft.Container:
        def _col_header(text, width=None, expand=False, center=False):
            align = ft.Alignment.CENTER if center else ft.Alignment.CENTER_LEFT
            ctrl  = ft.Text(text, size=9, color=TEXT_DIM, font_family=FONT_HEADLINE_BOLD,
                            style=ft.TextStyle(letter_spacing=0.8),
                            text_align=ft.TextAlign.CENTER if center else ft.TextAlign.LEFT, opacity=1.0)
            return ft.Container(content=ctrl, width=width, expand=expand, alignment=align)

        self._album_col_header = _col_header("ÁLBUM", expand=2)
        col_headers = ft.Container(
            content=ft.Row(controls=[
                _col_header("#",               width=32, center=True),
                _col_header("PORTADA",         width=55, center=True),
                _col_header("TÍTULO / ARTISTA", expand=3),
                self._album_col_header,
                _col_header("DUR.",            width=48, center=True),
                _col_header("",                width=26, center=True),
                _col_header("SEL.",            width=32, center=True),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            bgcolor=CHIP_BG, border_radius=12,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        return col_headers

    def _sync_queue_album_column(self, queue_open: bool, *, update: bool = True) -> None:
        """Oculta la columna Álbum en Gestión cuando el panel de cola está abierto (D26)."""
        show_album = not bool(queue_open)
        album_hdr = getattr(self, "_album_col_header", None)
        if album_hdr is not None and album_hdr.visible != show_album:
            album_hdr.visible = show_album
            if update:
                try:
                    album_hdr.update()
                except Exception:
                    pass
        lv = getattr(self, "_list_view", None)
        if lv is None:
            return
        for row in getattr(lv, "controls", ()):
            setter = getattr(row, "set_album_visible", None)
            if callable(setter):
                setter(show_album, update=update)

    def _build_list_and_preview_panels(self) -> None:
        self._list_view = ft.ListView(item_extent=ITEM_H, cache_extent=ITEM_H * 8, spacing=0, expand=True,
                                      padding=ft.Padding.only(bottom=20))
        _sf = dict(left=0, top=0, right=0, bottom=0)
        self._list_view_wrap     = ft.Container(content=self._list_view, bgcolor=BG_LIST, visible=False, **_sf)
        # skeleton count responsive (se recalcula en _calc_skeleton_count; inicial 14)
        self._skeletons          = [SkeletonRow(i) for i in range(self._calc_skeleton_count() if hasattr(self, '_calc_skeleton_count') else self.SKELETON_COUNT)]
        self._skeleton_view      = ft.ListView(item_extent=ITEM_H, cache_extent=ITEM_H * 8, spacing=0, expand=True,
                                               controls=self._skeletons, visible=True)
        self._skeleton_view_wrap = ft.Container(content=self._skeleton_view, bgcolor=BG_LIST, visible=False, **_sf)

        self._empty_hint_text = ft.Text(
            "Introduce el ID o la URL en el panel izquierdo y pulsa «Cargar».",
            size=12, color=TEXT_DIM, font_family=FONT_TEXT, opacity=1.0, text_align=ft.TextAlign.CENTER,
        )
        self._empty_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(controls=[
                ft.Container(content=app_icon(PIcons.PLAYLIST, size=52, color=TEXT_DIM),
                             bgcolor=CHIP_BG, border=ft.Border.all(0.8, BORDER_LIGHT),
                             border_radius=20, padding=ft.Padding.all(20)),
                ft.Text("Carga una playlist", size=20, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD,
                        opacity=1.0),
                ft.Text("Sin playlist cargada", size=14, color=TEXT_MUTED, font_family=FONT_HEADLINE,
                        opacity=1.0),
                self._empty_hint_text,
                _ghost_btn(
                    "Buscar en catálogo",
                    PIcons.MAGNIFYING_GLASS,
                    lambda _: self._on_toggle_catalog_search(),
                    height=34,
                ),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=True, **_sf,
        )

        self._error_text  = ft.Text("", size=13, color=ERROR_COL, font_family=FONT_TEXT, opacity=1.0)
        self._error_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column([app_icon(PIcons.WARNING_CIRCLE, size=48, color=ERROR_COL, fill=True), self._error_text],
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=False, **_sf,
        )

        # Preview readonly (derecha del doble)
        self._preview_list_view = ft.ListView(item_extent=ITEM_H, cache_extent=ITEM_H * 8, spacing=0, expand=True,
                                              padding=ft.Padding.only(bottom=20))
        self._preview_count = ft.Text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0)
        self._preview_empty = self._make_cono_empty("Preview vacío", "Marca canciones y organiza para ver cómo se va a pasar.", visible=False)
        self._preview_empty_wrap = ft.Container(content=self._preview_empty, bgcolor=BG_LIST, visible=False, **_sf)
        self._preview_view_wrap = ft.Container(content=self._preview_list_view, bgcolor=BG_LIST, visible=False, **_sf)
        # skeleton preview espejo (mismo cnt, sin Estado/Sel)
        _init_cnt = len(self._skeletons) if hasattr(self, '_skeletons') and self._skeletons else self._calc_skeleton_count()
        self._preview_skeletons = [PreviewSkeletonRow(i) for i in range(_init_cnt)]
        self._preview_skeleton_view = ft.ListView(item_extent=ITEM_H, cache_extent=ITEM_H * 8, spacing=0, expand=True, controls=self._preview_skeletons, visible=True)
        self._preview_skeleton_wrap = ft.Container(content=self._preview_skeleton_view, bgcolor=BG_LIST, visible=False, **_sf)
        self._preview_stack = ft.Stack(controls=[self._preview_view_wrap, self._preview_skeleton_wrap, self._preview_empty_wrap], expand=True)
        self._preview_header_text = ft.Text("Preview — cómo se va a pasar", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE_BOLD)
        self._preview_header_icon = app_icon(PIcons.EYE, size=14, color=TEXT_MUTED)
        self._preview_panel = ft.Container(
            expand=True, bgcolor=BG_LIST, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column([
                ft.Row([
                    self._preview_header_icon,
                    self._preview_header_text,
                    ft.Container(expand=True),
                    self._preview_count,
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                self._preview_stack,
            ], spacing=8, expand=True),
            padding=ft.Padding.all(10), visible=True,
        )

        # Lista panel (izq) — SOURCE (base) cuando dual, RESULT cuando single
        list_stack = ft.Stack(controls=[
            self._list_view_wrap, self._skeleton_view_wrap, self._empty_state, self._error_state,
        ], expand=True)
        self._lista_header_text = ft.Text("Lista editable", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE_BOLD)
        self._lista_header_icon = app_icon(PIcons.LIST_BULLETS, size=14, color=TEXT_MUTED)
        self._lista_panel = ft.Container(
            expand=True, bgcolor=BG_LIST, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column([
                ft.Row([
                    self._lista_header_icon,
                    self._lista_header_text,
                ], spacing=6),
                ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                list_stack,
            ], spacing=8, expand=True),
            padding=ft.Padding.all(10), visible=True,
        )

    def _build_mode_bar(self) -> None:
        # Modo Lista|Doble|Preview (SegmentedButton principal, solo visible cuando hay dual)
        def _on_mode_change(e):
            try:
                v = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                v = getattr(e.control, "value", "lista")
            self.state.set_dual_mode(v)

        self._mode_seg = self._make_mode_segmented(getattr(self.state, "dual_mode", "lista"), on_change=_on_mode_change)
        # División activa — Outlined + PopupMenu dinámico 2..5 junto a Vista (reemplaza Dropdown)
        self._division_btn_text = ft.Text("División 1", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE)
        self._division_label = ft.Text("División", size=9, color=TEXT_DIM, font_family=FONT_HEADLINE_BOLD)
        self._division_popup = ft.PopupMenuButton(
            content=ft.Row([
                app_icon(PIcons.FUNNEL, size=14, color=TEXT_DIM),
                self._division_btn_text,
                app_icon(PIcons.CARET_DOWN, size=14, color=TEXT_DIM),
            ], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            items=[],
            style=ft.ButtonStyle(
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                side={ft.ControlState.DEFAULT: ft.BorderSide(0.7, BORDER_LIGHT)},
                shape=ft.RoundedRectangleBorder(radius=8),
                bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
            ),
            tooltip="División activa — cambia preview",
            visible=False,
        )
        self._division_col = ft.Column([self._division_label, self._division_popup], spacing=0, tight=True)
        # Compat: dropdown antiguo eliminado, botón Todos oculto (dropdown cumple papel)
        self._partition_dd = None
        self._partition_btn = _ghost_btn(
            "Todos",
            PIcons.FUNNEL,
            lambda _: self._open_partition_picker(),
        )
        self._partition_btn.visible = False
        self._partition_btn.tooltip = "Seleccionar divisiones para transferir — multi §7"
        self._edit_division_btn = _ghost_btn(
            "Editar",
            PIcons.PENCIL_SIMPLE,
            lambda _: self._open_edit_division_dialog(),
        )
        self._edit_division_btn.visible = False
        self._edit_division_btn.tooltip = "Editar división — quitar/agregar entre ORIGINAL y División activa §4 (animación nativa)"
        self._mode_bar = ft.Row([
            ft.Text("Vista:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
            self._mode_seg,
            ft.VerticalDivider(width=1, color=BORDER_MUTED),
            app_icon(PIcons.GIT_BRANCH, size=14, color=TEXT_DIM),
            self._division_col,
            self._edit_division_btn,
            ft.Container(expand=True),
            app_icon_button(glyph=PIcons.X, size=14, icon_color=TEXT_DIM, tooltip="Cerrar doble vista",
                            on_click=lambda _: self.state.set_show_dual(False),
                            width=24, height=24),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=False)

    def _assemble_content_area(self, header_bar: ft.Row, col_headers: ft.Container) -> None:
        # Doble fila — Row persistente con ambos paneles (cookbook: evita reparent, preserva virtualización ListView)
        # _lista_panel y _preview_panel viven siempre dentro de _dual_row; se controla via visible, no moviendo hijos.
        self._dual_row = ft.Row(
            controls=[self._lista_panel, self._preview_panel],
            spacing=10, expand=True, visible=True,
        )
        # estado inicial: solo lista visible (show_dual=False)
        self._preview_panel.visible = False
        self._lista_panel.visible = True
        # list_area siempre contiene _dual_row (estable, animación cookbook)
        self._list_area = ft.Container(
            content=self._dual_row, expand=True,
            animate_opacity=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_scale=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            opacity=1.0, scale=1.0,
        )
        list_area = self._list_area

        self._content = ft.Container(
            expand=True, bgcolor=BG_LIST, padding=ft.Padding.all(24),
            content=ft.Column(
                controls=[
                    self._content_progress,
                    header_bar,
                    self._contextual_bar,
                    self._catalog_panel,
                    col_headers,
                    self._mode_bar,
                    list_area,
                ],
                spacing=10,
                expand=True,
            ),
        )

    def _build_nav_rail(self) -> None:
        # ──────────────────────────────────────────────────────────────
        # NAVIGATION RAIL FLOTANTE — docked, desplaza el contenido
        # Shell con margen exterior y radio de diálogo; ítems con radio de botón.
        # ──────────────────────────────────────────────────────────────
        self._current_module = 0  # 0=Gestión, 1=Biblioteca, 2=Descargas, 3=Config
        self._rail_collapsed = True  # arranca docked: solo iconos
        self._rail_w_collapsed = RAIL_W_COLLAPSED
        self._rail_w_expanded = RAIL_W_EXPANDED
        # metadata destinos (label, icon, selected_icon)
        self._rail_dests = [
            ("Gestión", PIcons.ARROWS_LEFT_RIGHT, PIcons.ARROWS_LEFT_RIGHT),
            ("Biblioteca", PIcons.BOOKS, PIcons.BOOKS),
            ("Descargas", PIcons.DOWNLOAD_SIMPLE, PIcons.DOWNLOAD_SIMPLE),
            ("Config", PIcons.GEAR, PIcons.GEAR),
        ]
        self._rail_label_boxes: list[ft.Container] = []
        self._rail_item_refs: list[ft.Container] = []

        def _select_module(idx: int) -> None:
            if idx == self._current_module:
                return
            prev_idx = self._current_module
            self._current_module = idx
            try:
                refreshed_self = False
                prev_panel = (
                    self._module_panels[prev_idx]
                    if 0 <= prev_idx < len(self._module_panels)
                    else None
                )
                curr_panel = (
                    self._module_panels[idx]
                    if 0 <= idx < len(self._module_panels)
                    else None
                )
                with ft.context.disable_auto_update():
                    for i, panel in enumerate(self._module_panels):
                        panel.visible = (i == idx)
                    self._refresh_rail_selection()
                # Publica el cambio visual de pestaña antes de refrescar contenido diferido.
                safe_update(prev_panel)
                safe_update(self._nav_rail)
                if curr_panel is not None and hasattr(curr_panel, "refresh"):
                    try:
                        curr_panel.refresh(update=False)
                    except TypeError:
                        curr_panel.refresh()
                        refreshed_self = True
                if idx == 0:
                    self._sync_skeleton_count()
                    if getattr(self, "_gestion_dirty", False):
                        self._on_state_changed()
                        refreshed_self = True
                if not refreshed_self:
                    if not safe_update(curr_panel):
                        safe_update(self._module_stack)
            except Exception:
                pass

        # Biblioteca y otras vistas necesitan cambiar de módulo después del
        # arranque (p. ej. «Crear playlist» desde una carpeta local).
        self._select_module = _select_module

        def _on_rail_item_click(e):
            try:
                idx = int(e.control.data)
            except Exception:
                idx = 0
            _select_module(idx)

        # refs para animación header/version
        self._rail_header_brand: ft.Text | None = None
        self._rail_header_brand_box: ft.Container | None = None
        self._rail_header_box: ft.Container | None = None
        self._rail_header_row: ft.Row | None = None
        self._rail_version_text: ft.Text | None = None
        self._rail_version_box: ft.Container | None = None

        # builder de items (Container con animate + hover reactivo) — Headline para labels, Notch para brand
        def _rail_item(idx: int, label: str, icon_out: str, icon_sel: str) -> ft.Container:
            is_sel = (idx == self._current_module)
            # Stack Sans Headline para labels (§ Headline: labels/botones)
            txt = ft.Text(
                label, size=13, color=TEXT_PRIMARY if is_sel else TEXT_MUTED,
                font_family=FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE,
                expand=True, no_wrap=True, max_lines=1,
                overflow=ft.TextOverflow.CLIP,
            )
            # Solo animan ancho/margen/opacidad de la caja: el texto permanece
            # montado y recortado (clip), nunca visible/invisible, para que el
            # colapso sea un recorte progresivo y no un salto.
            label_box = ft.Container(
                content=txt,
                width=RAIL_LABEL_W if not self._rail_collapsed else 0,
                height=20,
                margin=ft.Margin.only(left=12) if not self._rail_collapsed else ft.Margin.only(left=0),
                opacity=1.0 if not self._rail_collapsed else 0.0,
                alignment=ft.Alignment.CENTER_LEFT,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
                animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            )
            self._rail_label_boxes.append(label_box)
            icon = app_icon(icon_sel if is_sel else icon_out, size=20, color=ACCENT if is_sel else TEXT_DIM, fill=is_sel)
            # Row fija: el icono nunca se recentra; solo se recorta el label.
            row = ft.Row(
                [icon, label_box],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
                alignment=ft.MainAxisAlignment.START,
            )

            cont = ft.Container(
                content=row,
                bgcolor=ACCENT_HALO if is_sel else ft.Colors.TRANSPARENT,
                border=ft.Border.all(0.8, ACCENT if is_sel else ft.Colors.TRANSPARENT),
                border_radius=RADIUS_RAIL_ITEM,
                padding=ft.Padding.symmetric(
                    horizontal=12,
                    vertical=RAIL_ITEM_PAD_V,
                ),
                height=RAIL_ITEM_H,
                data=str(idx),
                tooltip=label if self._rail_collapsed else None,
                on_click=_on_rail_item_click,
                animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
                alignment=ft.Alignment.CENTER_LEFT,
            )

            def _on_hover(e: ft.HoverEvent) -> None:
                is_currently_sel = (idx == self._current_module)
                if is_currently_sel:
                    return
                is_h = e.data == "true" or e.data is True
                cont.bgcolor = BG_HOVER if is_h else ft.Colors.TRANSPARENT
                cont.border = ft.Border.all(0.8, ACCENT if is_h else ft.Colors.TRANSPARENT)
                icon.color = TEXT_PRIMARY if is_h else TEXT_DIM
                txt.color = TEXT_PRIMARY if is_h else TEXT_MUTED
                if cont.page:
                    cont.update()

            cont.on_hover = _on_hover
            self._rail_item_refs.append(cont)
            return cont

        # toggle colapso: ancho del shell + ancho/opacidad de labels en paralelo
        def _toggle_rail(_e=None):
            self._rail_collapsed = not self._rail_collapsed
            expanded = not self._rail_collapsed
            try:
                self._nav_rail.width = self._rail_w_expanded if expanded else self._rail_w_collapsed
                self._rail_toggle_btn.tooltip = "Colapsar" if expanded else "Expandir"
                # header brand: caja que crece/decrece y se desvanece
                if self._rail_header_brand_box is not None:
                    self._rail_header_brand_box.width = RAIL_BRAND_W if expanded else 0
                    self._rail_header_brand_box.margin = ft.Margin.only(left=8) if expanded else ft.Margin.only(left=0)
                    self._rail_header_brand_box.opacity = 1.0 if expanded else 0.0
                if self._rail_header_box is not None:
                    self._rail_header_box.alignment = ft.Alignment.CENTER_LEFT
                if self._rail_version_text is not None:
                    self._rail_version_text.opacity = 0.7 if expanded else 0.0
                if self._rail_version_box is not None:
                    # En compacto no deja espacio muerto bajo Config.
                    self._rail_version_box.height = RAIL_VERSION_H if expanded else RAIL_VERSION_H_COLLAPSED
                # items: solo cambian ancho/margen/opacidad del label (animados).
                # Sin invertir visible/alineación/padding en el mismo parche:
                # esas propiedades no se animan y provocaban el salto brusco.
                for i, label_box in enumerate(self._rail_label_boxes):
                    label_box.width = RAIL_LABEL_W if expanded else 0
                    label_box.margin = ft.Margin.only(left=12) if expanded else ft.Margin.only(left=0)
                    label_box.opacity = 1.0 if expanded else 0.0
                    try:
                        cont = self._rail_item_refs[i]
                        cont.tooltip = None if expanded else self._rail_dests[i][0]
                    except Exception:
                        pass
                self._nav_rail.update()
            except Exception:
                pass

        self._rail_toggle_btn = app_icon_button(
            PIcons.SIDEBAR_SIMPLE,
            size=18,
            icon_color=TEXT_MUTED,
            hover_color_icon=TEXT_PRIMARY,
            tooltip="Expandir" if self._rail_collapsed else "Colapsar",
            on_click=_toggle_rail,
            width=32,
            height=32,
        )
        # header del rail con toggle — Stack Sans Notch Bold, tamaño similar a sidebar (14-16)
        self._rail_header_brand = ft.Text(
            "Melomaniac", size=20, color=TEXT_PRIMARY,
            font_family=brand_family("bold"),
            visible=True,
            expand=True, no_wrap=True, max_lines=1,
            overflow=ft.TextOverflow.CLIP,
        )
        self._rail_header_brand_box = ft.Container(
            content=self._rail_header_brand,
            width=RAIL_BRAND_W if not self._rail_collapsed else 0,
            height=20,
            margin=ft.Margin.only(left=8) if not self._rail_collapsed else ft.Margin.only(left=0),
            opacity=1.0 if not self._rail_collapsed else 0.0,
            alignment=ft.Alignment.CENTER_LEFT,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        self._rail_header_row = ft.Row(
            [self._rail_toggle_btn, self._rail_header_brand_box],
            spacing=0, height=RAIL_HEADER_H, tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.START,
        )
        self._rail_header_box = ft.Container(
            content=self._rail_header_row,
            height=RAIL_HEADER_H,
            alignment=ft.Alignment.CENTER_LEFT,
        )
        rail_header = self._rail_header_box
        # Grupo principal: Gestión, Biblioteca y Descargas
        rail_items = [
            _rail_item(i, lbl, ico, sel)
            for i, (lbl, ico, sel) in enumerate(self._rail_dests[:3])
        ]
        # Config queda separado al fondo, antes de la versión
        config_item = _rail_item(3, *self._rail_dests[3])

        # helper para refrescar selección; el update lo hace el caller una sola vez
        def _refresh_rail_selection():
            for i, cont in enumerate(self._rail_item_refs):
                is_sel = (i == self._current_module)
                try:
                    cont.bgcolor = ACCENT_HALO if is_sel else ft.Colors.TRANSPARENT
                    cont.border = ft.Border.all(0.8, ACCENT if is_sel else ft.Colors.TRANSPARENT)
                    row = cont.content  # Row([icon, label_box])
                    if row and len(row.controls) >= 2:
                        icon_ctrl = row.controls[0]
                        label_box = row.controls[1]
                        lbl_ctrl = label_box.content if isinstance(label_box, ft.Container) else None
                        glyph = self._rail_dests[i][2] if is_sel else self._rail_dests[i][1]
                        icon_ctrl.value = glyph
                        icon_ctrl.font_family = FONT_PHOSPHOR_FILL if is_sel else FONT_PHOSPHOR
                        icon_ctrl.color = ACCENT if is_sel else TEXT_DIM
                        if lbl_ctrl:
                            lbl_ctrl.color = TEXT_PRIMARY if is_sel else TEXT_MUTED
                            lbl_ctrl.font_family = FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE
                except Exception:
                    pass
        self._refresh_rail_selection = _refresh_rail_selection  # type: ignore

        # versión — IBM Plex Mono Light 300 (datos técnicos § Mono)
        self._rail_version_text = ft.Text(
            f"v{APP_VERSION}", size=8, color=TEXT_DIM,
            font_family=mono_family("light"),
            visible=True,
            height=RAIL_VERSION_H, no_wrap=True, max_lines=1,
            opacity=0.7 if not self._rail_collapsed else 0.0,
            animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            text_align=ft.TextAlign.CENTER,
        )
        self._rail_version_box = ft.Container(
            content=self._rail_version_text,
            height=RAIL_VERSION_H if not self._rail_collapsed else RAIL_VERSION_H_COLLAPSED,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        rail_bottom = ft.Column(
            controls=[config_item, self._rail_version_box],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        # Shell flotante: margen respecto a la ventana + radio de diálogo
        self._nav_rail = ft.Container(
            width=self._rail_w_expanded if not self._rail_collapsed else self._rail_w_collapsed,
            margin=ft.Margin.all(RAIL_MARGIN),
            bgcolor=SIDEBAR_BG,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_RAIL,
            padding=ft.Padding.symmetric(horizontal=RAIL_PAD_H, vertical=RAIL_PAD_V),
            shadow=ft.BoxShadow(
                blur_radius=18,
                color=ft.Colors.with_opacity(0.45, BG_DEEP),
                offset=ft.Offset(0, 4),
            ),
            animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                controls=[
                    rail_header,
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                    ft.Column(
                        controls=rail_items,
                        spacing=RAIL_ITEM_SPACING,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    ft.Container(expand=True),
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                    rail_bottom,
                ],
                spacing=10, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

    def _build_modules_and_root(self) -> None:
        # Paneles de módulos — Gestión es Row([sidebar, content]); Biblioteca y Descargas tienen vistas propias
        self._panel_gestion = ft.Row(controls=[self._sidebar, self._content], spacing=0, expand=True,
                                     vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        # Biblioteca: LibraryView con selector de fuente (plataformas + Local)
        try:
            from services.library_state import LibraryState
            from ui.library_view import LibraryView
            # library_state con inyección service (respeta core->services)
            lib_state = LibraryState(service=getattr(self.state, "service", None) or getattr(self.state, "service", None))
            # si service aún None (init temprano), se inyectará luego via app.py/ui.library_state
            self.library_state = lib_state  # type: ignore
            self._panel_biblioteca = LibraryView(
                self.page, lib_state,
                getattr(self.state, "service", None) or getattr(self.state, "service", None),
                app_state=self.state,
                on_open_downloads=lambda: self._select_module(2),
                on_open_gestion=self._open_folder_in_gestion,
            )
        except Exception as exc:
            # fallback placeholder no rompe app
            try:
                self.state.log(f"[WARN] Biblioteca fallback: {exc}")  # type: ignore
            except Exception:
                pass
            self._panel_biblioteca = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                                  content=self._make_cono_empty("Biblioteca", "Colección local, historial 20 y portadas — pronto.", visible=True))
        self._auto_play_download_ids: set[str] = set()
        from services.mpris_service import MPRISService
        from ui.player_bar import PlayerBar
        from ui.player_queue import PlayerQueuePanel

        self.player_bar = PlayerBar(
            self.page,
            self.state,
            on_toggle_queue=lambda: self.player_queue.toggle(),
        )
        self.player_bar.attach_mpris(MPRISService())
        self.player_queue = PlayerQueuePanel(
            self.page,
            self.player_bar,
            on_open_change=self._sync_queue_album_column,
        )
        self._sync_queue_album_column(self.player_queue.is_open, update=False)
        self.player_bar.log_audio_services_count("init")
        if hasattr(self, "_panel_biblioteca") and self._panel_biblioteca is not None:
            self._panel_biblioteca.player_bar = self.player_bar

        # El selector de playlists de Gestión se rellena cuando Biblioteca
        # termina de sincronizar, sin acoplar ambas vistas.
        lib_for_picker = getattr(self, "library_state", None)
        if lib_for_picker is not None and hasattr(lib_for_picker, "subscribe"):
            def _on_library_change() -> None:
                try:
                    self._refresh_playlist_picker(self.state.source)
                except Exception:
                    pass

            lib_for_picker.subscribe(_on_library_change)
            # Primer relleno con lo que ya haya en caché para el origen actual.
            try:
                self._refresh_playlist_picker(self.state.source)
            except Exception:
                pass

        try:
            from ui.downloads_view import DownloadsView
            self._panel_descargas = DownloadsView(self.page, self.state, library_state=getattr(self, "library_state", None))
            self._panel_descargas.player_bar = self.player_bar  # type: ignore[attr-defined]
        except Exception as exc:
            try:
                self.state.log(f"[WARN] Descargas fallback: {exc}")  # type: ignore
            except Exception:
                pass
            self._panel_descargas = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                                 content=self._make_cono_empty("Descargas", "yt-dlp + progreso — no disponible.", visible=True))
        # Config: preferencias persistentes. El wizard de credenciales sigue
        # abriéndose desde el engranaje del panel izquierdo.
        try:
            from ui.config_view import ConfigView
            self._panel_config = ConfigView(
                self.page, on_change=self._on_config_changed,
                on_welcome=lambda: self.on_show_welcome and self.on_show_welcome(),
            )
        except Exception as exc:
            try:
                self.state.log(f"[WARN] Config fallback: {exc}")  # type: ignore
            except Exception:
                pass
            self._panel_config = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                              content=self._make_cono_empty("Configuración", "Preferencias no disponibles.", visible=True))
        self._module_panels = [self._panel_gestion, self._panel_biblioteca, self._panel_descargas, self._panel_config]
        # apila módulos, solo uno visible a la vez
        for i, p in enumerate(self._module_panels):
            p.visible = (i == 0)
        # El stack debe entregar toda la altura a cada panel para que los
        # estados vacíos puedan centrar su contenido también verticalmente.
        self._module_stack = ft.Stack(
            controls=self._module_panels,
            expand=True,
            fit=ft.StackFit.EXPAND,
        )

        # ──────────────────────────────────────────────────────────────
        # ENSAMBLAJE DE LAYOUT RAÍZ
        # ──────────────────────────────────────────────────────────────
        # Layout principal: rail flotante + módulo + cola lateral por empuje,
        # y barra de reproducción fija abajo.
        self.root = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self._nav_rail,
                            self._module_stack,
                            self.player_queue,
                        ],
                        spacing=0,
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    self.player_bar,
                ],
                spacing=0, expand=True,
            ),
            bgcolor=BG_LIST, expand=True,
        )
