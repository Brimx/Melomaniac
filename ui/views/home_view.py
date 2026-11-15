"""ui/views/home_view.py — Inicio 3.1: hero compacto 176 px, Continuar, Conexiones y Recientes con lomo obi."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

import flet as ft

from core.config import LOCAL_LIBRARY_LABEL, PLATFORM_ORDER
from core.library_models import PlaylistSummary
from ui.components.song_row import _thumb_content_for
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    apply_paper_relief,
    fmt_ms,
    obi_strip,
    safe_update,
    status_chip,
)
from ui.theme.fonts import (
    FONT_HEADLINE,
    FONT_HEADLINE_BOLD,
    FONT_HEADLINE_MEDIUM,
    FONT_TEXT,
    brand_family,
    font_family_for,
    mono_family,
)
from ui.theme.icons import PIcons, app_icon
from ui.theme.tokens import (
    ACCENT,
    BG_HOVER,
    BG_LIST,
    BG_PANEL,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    BORDER_ROW,
    FS_BODY,
    FS_META,
    FS_PAGE,
    FS_ROW,
    FS_SEC,
    FS_SM,
    HOME_CONN_ROW_H,
    HOME_CONTINUAR_ART,
    HOME_HERO_H,
    MOTION_HOVER_MS,
    PAPER_BG,
    PAPER_LIFT,
    PAPER_LIFT_SM,
    RADIUS_SHARP,
    SEAL_COLOR,
    SPACE_0,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    SPACE_5,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

_PLATFORM_META: tuple[tuple[str, str, str], ...] = (
    ("YouTube Music", PIcons.YOUTUBE_LOGO, "YOUTUBE"),
    ("Apple Music", PIcons.APPLE_LOGO, "APPLE"),
    ("Spotify", PIcons.SPOTIFY_LOGO, "SPOTIFY"),
)
_OBI_LABELS: dict[str, str] = {
    "YouTube Music": "YOUTUBE",
    "Apple Music": "APPLE",
    "Spotify": "SPOTIFY",
    LOCAL_LIBRARY_LABEL: "LOCAL",
}
_RECENT_LIMIT = 6
_RECENT_CARD_W = 136


def _greeting_for_hour(hour: int | None = None) -> str:
    h = datetime.now().hour if hour is None else hour
    if 5 <= h < 12:
        return "Buenos días."
    if 12 <= h < 20:
        return "Buenas tardes."
    return "Buenas noches."


def _section_header(num: str, title: str, trailing: ft.Control | None = None) -> ft.Container:
    controls: list[ft.Control] = [
        ft.Text(
            num,
            size=FS_META,
            color=ACCENT,
            font_family=mono_family("regular"),
            no_wrap=True,
        ),
        ft.Text(
            title,
            size=FS_SEC,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_MEDIUM,
            no_wrap=True,
        ),
        ft.Container(expand=True),
    ]
    if trailing is not None:
        controls.append(trailing)
    return ft.Container(
        content=ft.Row(
            controls=controls,
            spacing=SPACE_3,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.only(bottom=SPACE_2),
        border=ft.Border.only(bottom=ft.BorderSide(0.8, BORDER_MUTED)),
    )


class HomeView(ft.Container):
    """Vista Inicio (3.1): hero compacto, Continuar, Conexiones y Recientes con obi."""

    def __init__(
        self,
        page: ft.Page,
        app_state: Any,
        *,
        library_state: Any = None,
        player_bar: Any = None,
        on_navigate: Optional[Callable[[int], None]] = None,
        on_open_playlist: Optional[Callable[[PlaylistSummary], None]] = None,
        on_open_wizard: Optional[Callable[[Optional[str]], None]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self.app_state = app_state
        self.library_state = library_state
        self.player_bar = player_bar
        self._on_navigate = on_navigate
        self._on_open_playlist = on_open_playlist
        self._on_open_wizard = on_open_wizard
        self._home_dirty = False
        self._subscribed_lib = None
        self._subscribed_player = None

        self._build_ui()
        self._bind_sources()
        self.refresh(update=False)

    # ── Suscripciones y ciclo de vida ──────────────────────────────────

    def _bind_sources(self) -> None:
        if self.app_state is not None and hasattr(self.app_state, "subscribe"):
            self.app_state.subscribe(self._on_external_change)
        self.set_library_state(self.library_state)
        self.set_player_bar(self.player_bar)

    def set_library_state(self, library_state: Any) -> None:
        if library_state is self._subscribed_lib:
            return
        self.library_state = library_state
        if library_state is not None and hasattr(library_state, "subscribe"):
            library_state.subscribe(self._on_external_change)
            self._subscribed_lib = library_state

    def set_player_bar(self, player_bar: Any) -> None:
        if player_bar is self._subscribed_player:
            return
        if self._subscribed_player is not None:
            if hasattr(self._subscribed_player, "unsubscribe"):
                self._subscribed_player.unsubscribe(self._on_player_change)
            if hasattr(self._subscribed_player, "unsubscribe_position"):
                self._subscribed_player.unsubscribe_position(self._on_player_position)
        self.player_bar = player_bar
        if player_bar is not None:
            if hasattr(player_bar, "subscribe"):
                player_bar.subscribe(self._on_player_change)
            if hasattr(player_bar, "subscribe_position"):
                player_bar.subscribe_position(self._on_player_position)
            self._subscribed_player = player_bar

    def will_unmount(self) -> None:
        if self._subscribed_player is not None:
            if hasattr(self._subscribed_player, "unsubscribe"):
                self._subscribed_player.unsubscribe(self._on_player_change)
            if hasattr(self._subscribed_player, "unsubscribe_position"):
                self._subscribed_player.unsubscribe_position(self._on_player_position)
            self._subscribed_player = None
        super().will_unmount()

    def _on_external_change(self) -> None:
        if not self.visible:
            self._home_dirty = True
            return
        self.refresh(update=True)

    def _on_player_change(self) -> None:
        if not self.visible:
            self._home_dirty = True
            return
        self._sync_continuar(update=True)

    def _on_player_position(self, pos_ms: int, dur_ms: int) -> None:
        if not self.visible:
            return
        self._sync_progress_bar(pos_ms, dur_ms, update=True)

    # ── Construcción estática ──────────────────────────────────────────

    def _build_ui(self) -> None:
        self._hero = self._build_hero()

        # 01 Continuar reproducción (2 hojas apiladas a 3 px y 6 px + canto 2 px)
        self._cont_cover_wrap = ft.Container(
            width=HOME_CONTINUAR_ART,
            height=HOME_CONTINUAR_ART,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_SHARP,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=_thumb_content_for("", box=HOME_CONTINUAR_ART, icon_size=32),
        )
        self._cont_obi_wrap = ft.Container(
            left=0,
            top=12,
            bottom=12,
            content=obi_strip("LOCAL"),
        )
        self._cont_sleeve = ft.Stack(
            controls=[self._cont_cover_wrap, self._cont_obi_wrap],
            width=HOME_CONTINUAR_ART,
            height=HOME_CONTINUAR_ART,
        )
        self._cont_kicker = ft.Text(
            "TOCADISCOS EN REPOSO",
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("regular"),
            style=ft.TextStyle(letter_spacing=1.2),
            no_wrap=True,
        )
        self._cont_title = ft.Text(
            "Nada reproduciéndose",
            size=FS_SEC,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._cont_sub = ft.Text(
            "Abre una playlist local o desde Biblioteca para empezar.",
            size=FS_SM,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._cont_progress = ft.ProgressBar(
            value=0.0,
            height=2,
            color=ACCENT,
            bgcolor=BORDER_MUTED,
        )
        self._cont_pos_txt = ft.Text(
            "0:00", size=FS_META, color=TEXT_MUTED, font_family=mono_family("light")
        )
        self._cont_dur_txt = ft.Text(
            "0:00", size=FS_META, color=TEXT_MUTED, font_family=mono_family("light")
        )
        self._cont_primary_btn = _primary_btn(
            "Continuar",
            PIcons.PLAY,
            self._on_continuar_click,
            height=34,
        )
        self._cont_scene_btn = _ghost_btn(
            "Escena",
            PIcons.ARROW_SQUARE_OUT,
            self._on_open_scene,
            height=34,
        )
        self._cont_scene_btn.visible = False

        self._platter_card = ft.Container(
            bgcolor=BG_PANEL,
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.all(SPACE_3),
            content=ft.Row(
                controls=[
                    self._cont_sleeve,
                    ft.Column(
                        controls=[
                            self._cont_kicker,
                            self._cont_title,
                            self._cont_sub,
                            ft.Container(height=2),
                            self._cont_progress,
                            ft.Row(
                                controls=[
                                    self._cont_pos_txt,
                                    ft.Container(expand=True),
                                    self._cont_dur_txt,
                                ],
                                spacing=SPACE_0,
                            ),
                            ft.Container(height=2),
                            ft.Row(
                                controls=[self._cont_primary_btn, self._cont_scene_btn],
                                spacing=SPACE_2,
                            ),
                        ],
                        spacing=SPACE_1,
                        expand=True,
                        tight=True,
                    ),
                ],
                spacing=SPACE_4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        apply_paper_relief(
            self._platter_card,
            layers=2,
            lift=PAPER_LIFT,
            border_color=BORDER_MUTED,
            canto_color=BORDER_LIGHT,
        )

        continuar_col = ft.Column(
            controls=[
                _section_header("01", "Continuar reproducción"),
                self._platter_card,
            ],
            spacing=SPACE_3,
            expand=7,
            tight=True,
        )

        # 02 Estado rápido de 3 conexiones (36 px por fila)
        self._conn_rows_col = ft.Column(spacing=SPACE_0, tight=True)
        conn_col = ft.Column(
            controls=[
                _section_header(
                    "02",
                    "Conexiones",
                    trailing=_ghost_btn(
                        "Configurar",
                        PIcons.GEAR,
                        lambda _: self._go(3),
                        height=28,
                    ),
                ),
                self._conn_rows_col,
            ],
            spacing=SPACE_3,
            expand=5,
            tight=True,
        )

        top_grid = ft.Row(
            controls=[continuar_col, conn_col],
            spacing=SPACE_5,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        # 03 Recientes con lomo obi
        self._recents_row = ft.Row(
            spacing=SPACE_4,
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
        recents_sec = ft.Column(
            controls=[
                _section_header(
                    "03",
                    "Recientes",
                    trailing=_ghost_btn(
                        "Ver biblioteca",
                        PIcons.ARROW_RIGHT,
                        lambda _: self._go(1),
                        height=30,
                    ),
                ),
                self._recents_row,
            ],
            spacing=SPACE_3,
            tight=True,
        )

        # 04 Empezar (accesos rápidos)
        starters_sec = ft.Column(
            controls=[
                _section_header("04", "Empezar"),
                ft.Row(
                    controls=[
                        self._quick_ficha(
                            "01",
                            "Cargar una playlist",
                            "Por URL, ID o desde una lista sincronizada.",
                            PIcons.LINK,
                            0,
                        ),
                        self._quick_ficha(
                            "02",
                            "Explorar estantes",
                            "Playlists sincronizadas y música local.",
                            PIcons.BOOKS,
                            1,
                        ),
                        self._quick_ficha(
                            "03",
                            "Descargar con yt-dlp",
                            "Cola paralela de audio con pausa real.",
                            PIcons.DOWNLOAD_SIMPLE,
                            2,
                        ),
                        self._quick_ficha(
                            "04",
                            "Revisar sesiones",
                            "Navegador de origen y verificación.",
                            PIcons.GEAR,
                            3,
                        ),
                    ],
                    spacing=SPACE_4,
                ),
            ],
            spacing=SPACE_3,
            tight=True,
        )

        body = ft.Column(
            controls=[
                self._hero,
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=SPACE_5, vertical=SPACE_4),
                    content=ft.Column(
                        controls=[top_grid, recents_sec, starters_sec],
                        spacing=SPACE_4,
                        tight=True,
                    ),
                ),
            ],
            spacing=SPACE_0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        self.content = body

    def _build_hero(self) -> ft.Container:
        self._hero_kicker = ft.Text(
            "SALA DE ESCUCHA · INICIO",
            size=FS_META,
            color=TEXT_MUTED,
            font_family=mono_family("regular"),
            style=ft.TextStyle(letter_spacing=1.4),
        )
        hanko = ft.Container(
            width=18,
            height=18,
            bgcolor=SEAL_COLOR,
            border_radius=RADIUS_SHARP,
            rotate=ft.Rotate(-0.07),
            alignment=ft.Alignment.CENTER,
            content=ft.Text(
                "音",
                size=11,
                color=PAPER_BG,
                font_family=brand_family("bold"),
            ),
        )
        self._hero_title = ft.Text(
            _greeting_for_hour(),
            size=FS_PAGE,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            no_wrap=True,
        )
        self._hero_status_txt = ft.Text(
            "Listo para transferir, explorar o escuchar.",
            size=FS_SM,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        left = ft.Column(
            controls=[
                ft.Row([self._hero_kicker, hanko], spacing=SPACE_2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self._hero_title,
                self._hero_status_txt,
            ],
            spacing=SPACE_1,
            alignment=ft.MainAxisAlignment.END,
            expand=True,
        )
        actions = ft.Row(
            controls=[
                _primary_btn("Gestionar playlist", PIcons.ARROWS_LEFT_RIGHT, lambda _: self._go(0), height=34),
                _ghost_btn("Biblioteca", PIcons.BOOKS, lambda _: self._go(1), height=34),
            ],
            spacing=SPACE_2,
            vertical_alignment=ft.CrossAxisAlignment.END,
        )
        return ft.Container(
            height=HOME_HERO_H,
            bgcolor=BG_PANEL,
            padding=ft.Padding.symmetric(horizontal=SPACE_5, vertical=SPACE_4),
            border=ft.Border.only(bottom=ft.BorderSide(1.0, BORDER_LIGHT)),
            content=ft.Row(
                controls=[left, actions],
                spacing=SPACE_4,
                vertical_alignment=ft.CrossAxisAlignment.END,
            ),
        )

    def _quick_ficha(
        self,
        num: str,
        title: str,
        desc: str,
        icon: str,
        target_module: int,
    ) -> ft.Container:
        card = ft.Container(
            expand=True,
            bgcolor=BG_PANEL,
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.all(SPACE_4),
            ink=True,
            on_click=lambda _, idx=target_module: self._go(idx),
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text(num, size=FS_SEC, color=TEXT_MUTED, font_family=mono_family("light")),
                            ft.Container(expand=True),
                            app_icon(icon, size=18, color=ACCENT),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        title,
                        size=FS_BODY,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_MEDIUM,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(
                        desc,
                        size=FS_SM,
                        color=TEXT_MUTED,
                        font_family=FONT_TEXT,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=SPACE_2,
                tight=True,
            ),
        )
        apply_paper_relief(card, layers=1, lift=PAPER_LIFT_SM, border_color=BORDER_MUTED, canto_color=BORDER_LIGHT)

        def _on_hover(e: ft.HoverEvent) -> None:
            h = e.data == "true" or e.data is True
            card.bgcolor = BG_SURFACE if h else BG_PANEL
            safe_update(card)

        card.on_hover = _on_hover
        return card

    # ── Sincronización en memoria (cero I/O síncrono) ──────────────────

    def refresh(self, *, update: bool = True) -> None:
        self._home_dirty = False
        self._hero_title.value = _greeting_for_hour()
        self._sync_hero_status()
        self._sync_continuar(update=False)
        self._sync_connections(update=False)
        self._sync_recents(update=False)
        if update:
            safe_update(self)

    def _sync_hero_status(self) -> None:
        s = self.app_state
        if s is None:
            return
        auth_ok = getattr(s, "auth_session_ok", {}) or {}
        pending = [p for p, ok in auth_ok.items() if ok is False]
        dl_total = int(getattr(s, "download_total", 0) or 0)
        dl_prog = int(getattr(s, "download_progress", 0) or 0)
        parts: list[str] = []
        if dl_total > dl_prog:
            parts.append(f"{dl_total - dl_prog} descarga(s) en curso")
        if pending:
            parts.append(f"{', '.join(pending)} requiere revisar sesión")
        self._hero_status_txt.value = (
            " · ".join(parts) if parts else "Listo para transferir, explorar o escuchar."
        )

    def _sync_continuar(self, *, update: bool = True) -> None:
        pb = self.player_bar
        current = getattr(getattr(pb, "queue", None), "current", None) if pb is not None else None
        if current is None:
            self._cont_kicker.value = "TOCADISCOS EN REPOSO"
            self._cont_title.value = "Nada reproduciéndose"
            self._cont_sub.value = "Elige una pista en Biblioteca o Gestión."
            self._cont_cover_wrap.content = _thumb_content_for("", box=HOME_CONTINUAR_ART, icon_size=32)
            self._cont_obi_wrap.content = obi_strip("LOCAL")
            self._cont_progress.value = 0.0
            self._cont_pos_txt.value = "0:00"
            self._cont_dur_txt.value = "0:00"
            self._cont_scene_btn.visible = False
            self._set_primary_btn_label("Explorar Biblioteca", PIcons.BOOKS)
        else:
            playing = bool(getattr(pb, "is_playing", False))
            self._cont_kicker.value = "REPRODUCIENDO" if playing else "EN PAUSA"
            self._cont_title.value = getattr(current, "title", "") or getattr(current, "name", "") or "Pista actual"
            artist = getattr(current, "artist", "") or ""
            album = getattr(current, "album", "") or ""
            self._cont_sub.value = f"{artist} · {album}".strip(" ·") or "Audio local"
            img_url = getattr(current, "img_url", "") or ""
            self._cont_cover_wrap.content = _thumb_content_for(img_url, box=HOME_CONTINUAR_ART, icon_size=32)
            plat = getattr(current, "platform", "") or "LOCAL"
            self._cont_obi_wrap.content = obi_strip(_OBI_LABELS.get(plat, plat[:8]))
            pos_ms = int(getattr(pb, "position_ms", 0) or 0)
            dur_ms = int(getattr(pb, "duration_ms", 0) or 0)
            self._sync_progress_bar(pos_ms, dur_ms, update=False)
            self._cont_scene_btn.visible = True
            self._set_primary_btn_label(
                "Pausar" if playing else "Continuar",
                PIcons.PAUSE if playing else PIcons.PLAY,
            )
        if update:
            safe_update(self._platter_card)

    def _set_primary_btn_label(self, label: str, icon: str) -> None:
        from ui.components.widgets import set_button_text

        set_button_text(self._cont_primary_btn, label, icon=icon)

    def _sync_progress_bar(self, pos_ms: int, dur_ms: int, *, update: bool = True) -> None:
        frac = min(1.0, max(0.0, pos_ms / dur_ms)) if dur_ms > 0 else 0.0
        self._cont_progress.value = frac
        self._cont_pos_txt.value = fmt_ms(pos_ms)
        self._cont_dur_txt.value = fmt_ms(dur_ms)
        if update:
            safe_update(self._cont_progress)
            safe_update(self._cont_pos_txt)
            safe_update(self._cont_dur_txt)

    def _sync_connections(self, *, update: bool = True) -> None:
        s = self.app_state
        auth_ok = getattr(s, "auth_session_ok", {}) or {}
        auth_hint = getattr(s, "auth_session_hint", {}) or {}
        rows: list[ft.Control] = []
        for idx, (plat, icon_glyph, _short) in enumerate(_PLATFORM_META):
            ok = auth_ok.get(plat)
            hint = str(auth_hint.get(plat) or "").strip()
            if ok is True:
                chip = status_chip("Válida", kind="ok", icon=PIcons.CHECK_CIRCLE)
                sub = hint or "Sesión activa"
            elif ok is False:
                chip = status_chip("Revisar", kind="warn", icon=PIcons.WARNING)
                sub = hint or "Requiere verificación"
            else:
                chip = status_chip("Sin verificar", kind="neutral")
                sub = hint or "Pendiente de comprobación"

            border_bottom = (
                ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW))
                if idx < len(_PLATFORM_META) - 1
                else None
            )
            row = ft.Container(
                height=HOME_CONN_ROW_H,
                padding=ft.Padding.symmetric(horizontal=SPACE_1),
                border=border_bottom,
                ink=True,
                on_click=lambda _, p=plat, is_bad=(ok is False): self._on_connection_click(p, is_bad),
                content=ft.Row(
                    controls=[
                        app_icon(icon_glyph, size=18, color=TEXT_PRIMARY if ok else TEXT_MUTED),
                        ft.Column(
                            controls=[
                                ft.Text(
                                    plat,
                                    size=FS_SM,
                                    color=TEXT_PRIMARY,
                                    font_family=FONT_HEADLINE_MEDIUM,
                                    no_wrap=True,
                                ),
                                ft.Text(
                                    sub,
                                    size=FS_META,
                                    color=TEXT_MUTED,
                                    font_family=mono_family("light"),
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            spacing=SPACE_0,
                            expand=True,
                            tight=True,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        chip,
                    ],
                    spacing=SPACE_3,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            rows.append(row)
        self._conn_rows_col.controls = rows
        if update:
            safe_update(self._conn_rows_col)

    def _collect_recent_summaries(self) -> list[PlaylistSummary]:
        lib = self.library_state
        if lib is None:
            return []
        collected: list[PlaylistSummary] = []
        seen_keys: set[tuple[str, str]] = set()
        for plat in (*PLATFORM_ORDER, LOCAL_LIBRARY_LABEL):
            try:
                items = lib.get_playlists(plat) or []
            except Exception:
                items = []
            for item in items:
                key = (item.platform, item.id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                collected.append(item)
                if len(collected) >= _RECENT_LIMIT:
                    return collected
        return collected

    def _sync_recents(self, *, update: bool = True) -> None:
        summaries = self._collect_recent_summaries()
        if not summaries:
            empty = ft.Container(
                expand=True,
                bgcolor=BG_PANEL,
                border=ft.Border.all(0.8, BORDER_MUTED),
                border_radius=RADIUS_SHARP,
                padding=ft.Padding.symmetric(horizontal=SPACE_4, vertical=SPACE_3),
                content=ft.Row(
                    controls=[
                        app_icon(PIcons.DISC, size=20, color=TEXT_MUTED),
                        ft.Text(
                            "Aún no hay estantes en memoria. Sincroniza una plataforma en Biblioteca.",
                            size=FS_SM,
                            color=TEXT_MUTED,
                            font_family=FONT_TEXT,
                            expand=True,
                        ),
                        _ghost_btn("Ir a Biblioteca", PIcons.BOOKS, lambda _: self._go(1), height=30),
                    ],
                    spacing=SPACE_3,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            self._recents_row.controls = [empty]
        else:
            self._recents_row.controls = [self._build_recent_card(s) for s in summaries]
        if update:
            safe_update(self._recents_row)

    def _build_recent_card(self, summary: PlaylistSummary) -> ft.Container:
        cover_url = summary.cover_urls[0] if summary.cover_urls else ""
        obi_lbl = _OBI_LABELS.get(summary.platform, summary.platform[:8])
        cover_box = ft.Container(
            width=_RECENT_CARD_W,
            height=_RECENT_CARD_W,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_SHARP,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=_thumb_content_for(cover_url, box=_RECENT_CARD_W, icon_size=28),
        )
        sleeve = ft.Stack(
            controls=[
                cover_box,
                ft.Container(left=0, top=12, bottom=12, content=obi_strip(obi_lbl)),
            ],
            width=_RECENT_CARD_W,
            height=_RECENT_CARD_W,
        )
        apply_paper_relief(
            cover_box,
            layers=1,
            lift=PAPER_LIFT_SM,
            border_color=BORDER_MUTED,
            canto_color=BORDER_LIGHT,
        )

        count_txt = (
            f"{summary.track_count} pistas"
            if summary.track_count is not None
            else "Abrir para ver"
        )
        card = ft.Container(
            width=_RECENT_CARD_W,
            ink=True,
            on_click=lambda _, s=summary: self._open_recent(s),
            content=ft.Column(
                controls=[
                    sleeve,
                    ft.Text(
                        summary.name,
                        size=FS_SM,
                        color=TEXT_PRIMARY,
                        font_family=font_family_for(summary.name, "semibold"),
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(
                        f"{summary.platform} · {count_txt}",
                        size=FS_META,
                        color=TEXT_MUTED,
                        font_family=mono_family("light"),
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=SPACE_1,
                tight=True,
            ),
        )
        return card

    # ── Acciones ───────────────────────────────────────────────────────

    def _go(self, module_idx: int) -> None:
        if self._on_navigate is not None:
            self._on_navigate(module_idx)

    def _on_continuar_click(self, _e=None) -> None:
        pb = self.player_bar
        current = getattr(getattr(pb, "queue", None), "current", None) if pb is not None else None
        if current is None or pb is None:
            self._go(1)
            return
        toggle = getattr(pb, "toggle_play_pause", None) or getattr(
            pb, "_on_play_pause_click", None
        )
        if callable(toggle):
            toggle()

    def _on_open_scene(self, _e=None) -> None:
        pb = self.player_bar
        if pb is not None and hasattr(pb, "open_now_playing_scene"):
            pb.open_now_playing_scene()

    def _on_connection_click(self, platform: str, is_bad: bool) -> None:
        if is_bad and self._on_open_wizard is not None:
            self._on_open_wizard(platform)
            return
        self._go(3)

    def _open_recent(self, summary: PlaylistSummary) -> None:
        if self._on_open_playlist is not None:
            self._on_open_playlist(summary)
        else:
            self._go(1)
