"""
ui/player/now_playing_scene.py — Escena ampliada «Reproduciendo»
═══════════════════════════════════════════════════════════════════════
Vista a pantalla completa sobre ``page.overlay`` abierta al pulsar la
miniatura de ``PlayerBar``:
- Ambiente OLED Tier B: Pillow extrae hasta 4 dominantes fuera del bucle,
  elige los 2 de mayor separación tonal, aplica el tope OLED (Y ≤ 0.0136)
  una sola vez y realiza cross-fade por opacidad GPU entre dos ``Stack``
  estáticos con ``RadialGradient``. Se pausa al cerrar la escena, perder el
  foco de ventana o con ``reduce_motion`` activo.
- Disco de vinilo dividido en capa estática de surcos + capa giratoria de
  baja frecuencia (``SweepGradient`` + sello) encadenada en ``on_animation_end``.
- Columna derecha compartiendo ``Cola`` (``build_queue_row``) y ``Letra``
  (``LyricsPane``).
"""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import flet as ft
import flet.canvas as cv
from PIL import Image

from core.playback import PlayableTrack, RepeatMode
from services import cover_cache, settings_store
from ui.components.song_row import ITEM_H
from ui.components.widgets import app_text, fmt_ms as _fmt_ms, is_mounted, safe_update, section_label
from ui.player.player_queue import (
    LyricsPane,
    _TAB_COLA,
    _TAB_LETRA,
    _VALID_TABS,
    _load_queue_prefs,
    _read_setting,
    _save_queue_prefs,
    build_queue_row,
)
from ui.theme.fonts import (
    FONT_HEADLINE,
    FONT_HEADLINE_BOLD,
    FONT_PHOSPHOR,
    FONT_PHOSPHOR_FILL,
    FONT_TEXT,
    font_family_for,
    mono_family,
)
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    ACCENT_DIM,
    ACCENT_HALO,
    BG_DEEP,
    BG_PANEL,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    MOTION_HOVER_MS,
    MOTION_PANEL_MS,
    PLAYER_PROGRESS_ANIM_MS,
    PLAYER_PROGRESS_H,
    RADIUS_BUTTON,
    RADIUS_DIALOG,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

_OLED_MAX_Y = 0.0136
_AMBIENT_FADE_MS = 24_000
_DISC_ROTATION_MS = 6_000
_COVER_SIZE = 252
_DISC_SIZE = 228
_DISC_PEEK = 58


def _rgb_luminance_y(rgb: tuple[int, int, int]) -> float:
    """Luminancia relativa sRGB en [0, 1]."""
    channels: list[float] = []
    for v in rgb:
        c = max(0.0, min(255.0, float(v))) / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _rgb_hue_deg(rgb: tuple[int, int, int]) -> float:
    """Tono HSV en grados [0, 360)."""
    r, g, b = (v / 255.0 for v in rgb)
    hi, lo = max(r, g, b), min(r, g, b)
    delta = hi - lo
    if delta <= 0:
        return 0.0
    if hi == r:
        hue = ((g - b) / delta) % 6.0
    elif hi == g:
        hue = (b - r) / delta + 2.0
    else:
        hue = (r - g) / delta + 4.0
    return (hue * 60.0) % 360.0


def cap_oled_luminance(rgb: tuple[int, int, int], *, max_y: float = _OLED_MAX_Y) -> str:
    """Aplica el tope OLED una sola vez al color y devuelve ``#FFRRGGBB``."""
    t = 1.0
    r, g, b = rgb
    while t > 0.02:
        cand = (int(round(r * t)), int(round(g * t)), int(round(b * t)))
        if _rgb_luminance_y(cand) <= max_y:
            return f"#FF{cand[0]:02x}{cand[1]:02x}{cand[2]:02x}"
        t -= 0.04
    cand = (int(round(r * 0.02)), int(round(g * 0.02)), int(round(b * 0.02)))
    return f"#FF{cand[0]:02x}{cand[1]:02x}{cand[2]:02x}"


def extract_ambient_pair(image_path: str | Path) -> tuple[str, str] | None:
    """Extrae 2 colores dominantes con mayor separación de tono y tope OLED.

    Devuelve ``None`` si la portada no existe, falla o es prácticamente acromática
    (p. ej. blanco y negro), caso en que el ambiente permanece apagado.
    """
    path = Path(image_path)
    if not path.is_file():
        return None
    try:
        with Image.open(path) as img:
            quant = (
                img.convert("RGB")
                .resize((64, 64), Image.Resampling.BILINEAR)
                .quantize(8, method=Image.Quantize.MEDIANCUT)
                .convert("RGB")
            )
            counts = quant.getcolors(4096) or []
    except Exception:
        return None

    counts.sort(key=lambda item: item[0], reverse=True)
    palette: list[tuple[int, int, int]] = []
    for _count, color in counts:
        if not isinstance(color, tuple) or len(color) < 3:
            continue
        rgb = (int(color[0]), int(color[1]), int(color[2]))
        avg = sum(rgb) / 3.0
        chroma = max(rgb) - min(rgb)
        lum = _rgb_luminance_y(rgb)
        if 20.0 < avg < 235.0 and chroma > 6 and 0.004 < lum < 0.85:
            palette.append(rgb)
            if len(palette) >= 4:
                break

    if len(palette) < 2:
        return None

    best_pair = (palette[0], palette[1])
    best_sep = -1.0
    for i, c1 in enumerate(palette):
        h1 = _rgb_hue_deg(c1)
        for c2 in palette[i + 1:]:
            diff = abs(h1 - _rgb_hue_deg(c2))
            sep = min(diff, 360.0 - diff)
            if sep > best_sep:
                best_sep = sep
                best_pair = (c1, c2)

    return (
        cap_oled_luminance(best_pair[0]),
        cap_oled_luminance(best_pair[1]),
    )


def _resolve_local_cover(img_url: str) -> Path | None:
    raw = (img_url or "").strip()
    if not raw:
        return None
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        p = Path(unquote(parsed.path))
        return p if p.is_file() else None
    if not raw.startswith(("http://", "https://")):
        p = Path(raw)
        return p if p.is_file() else None
    cached = cover_cache.cached_path_for_url(raw)
    if cached is not None and cached.is_file():
        return cached
    if bool(getattr(settings_store, "get_covers_online", lambda: True)()):
        return cover_cache.ensure_cached(raw)
    return None


class _VinylDisc(ft.Stack):
    """Vinilo en 2 capas: surcos estáticos (0 CPU/frame) + reflejo giratorio GPU."""

    def __init__(self, size: int = _DISC_SIZE) -> None:
        self._size = size
        self._angle = 0.0
        self._spinning = False
        self._should_spin = False

        center = size / 2.0
        groove_shapes: list[cv.Shape] = [
            cv.Circle(center, center, center - 1, ft.Paint(color="#FF050507")),
            cv.Circle(
                center,
                center,
                center - 1,
                ft.Paint(color="#FF1D1D26", stroke_width=1.2, style=ft.PaintingStyle.STROKE),
            ),
        ]
        # Surcos estáticos: dibujar una sola vez evita moiré al rotar
        r = center - 7.0
        ring_idx = 0
        min_r = size * 0.21
        while r > min_r:
            col = "#FF0D0D14" if ring_idx % 2 == 0 else "#FF15151E"
            groove_shapes.append(
                cv.Circle(
                    center,
                    center,
                    r,
                    ft.Paint(color=col, stroke_width=1.0, style=ft.PaintingStyle.STROKE),
                )
            )
            r -= 3.2
            ring_idx += 1

        label_r = size * 0.165
        groove_shapes.extend([
            cv.Circle(center, center, label_r + 2.0, ft.Paint(color="#FF0B1430")),
            cv.Circle(center, center, label_r, ft.Paint(color=ACCENT_DIM)),
            cv.Circle(center, center, max(4.0, size * 0.025), ft.Paint(color=BG_DEEP)),
        ])

        static_canvas = cv.Canvas(shapes=groove_shapes, width=size, height=size)

        seal_dot = ft.Container(
            width=max(8, int(size * 0.045)),
            height=max(8, int(size * 0.045)),
            border_radius=99,
            bgcolor="#55F2F6FF",
            left=int(center - label_r * 0.58),
            top=int(center - label_r * 0.58),
        )
        self._rotator = ft.Container(
            width=size,
            height=size,
            shape=ft.BoxShape.CIRCLE,
            gradient=ft.SweepGradient(
                center=ft.Alignment.CENTER,
                colors=[
                    "#00000000",
                    "#26FFFFFF",
                    "#00000000",
                    "#18A0BEFF",
                    "#00000000",
                ],
                stops=[0.0, 0.10, 0.22, 0.62, 0.74],
            ),
            content=ft.Stack(controls=[seal_dot], width=size, height=size),
            rotate=ft.Rotate(0.0, alignment=ft.Alignment.CENTER),
            animate_rotation=ft.Animation(_DISC_ROTATION_MS, ft.AnimationCurve.LINEAR),
            on_animation_end=self._on_spin_end,
        )

        super().__init__(
            width=size,
            height=size,
            controls=[static_canvas, self._rotator],
        )

    def set_spinning(self, active: bool) -> None:
        self._should_spin = bool(active)
        if self._should_spin:
            if not self._spinning:
                self._spinning = True
                self._advance_rotation()
        else:
            self._spinning = False

    def _advance_rotation(self) -> None:
        if not self._should_spin or not is_mounted(self):
            self._spinning = False
            return
        self._angle += math.tau
        self._rotator.rotate = ft.Rotate(self._angle, alignment=ft.Alignment.CENTER)
        if not safe_update(self._rotator):
            self._spinning = False

    def _on_spin_end(self, _e=None) -> None:
        if self._should_spin:
            self._advance_rotation()
        else:
            self._spinning = False


class NowPlayingScene(ft.Container):
    """Escena a pantalla completa con ambiente OLED, vinilo y Cola/Letra."""

    def __init__(self, page: ft.Page, player_bar: Any) -> None:
        self._page_ref = page
        self.player_bar = player_bar
        self._is_open = False
        self._window_focused = True
        self._disposed = False
        self._open_gen = 0
        self._amb_gen = 0
        self._amb_phase = False
        self._amb_cycling = False
        self._last_cover_url: str | None = None
        self._amb_active_pair: tuple[str, str] | None = None
        self._amb_cache: dict[str, tuple[str, str] | None] = {}
        self._track_width = 360.0
        self._scrubbing = False
        self._scrub_ms = 0
        self._prev_win_handler: Any = None
        self._prev_key_handler: Any = None
        self._handlers_installed = False

        _, init_tab = _load_queue_prefs()
        self._active_tab = init_tab if init_tab in _VALID_TABS else _TAB_LETRA

        # ── Ambiente OLED Tier B (2 capas Stack estáticas + fade de opacidad) ──
        self._amb_a1 = ft.Container(expand=True)
        self._amb_a2 = ft.Container(expand=True)
        self._amb_b1 = ft.Container(expand=True)
        self._amb_b2 = ft.Container(expand=True)
        self._amb_a = ft.Stack(
            expand=True,
            opacity=1.0,
            animate_opacity=ft.Animation(_AMBIENT_FADE_MS, ft.AnimationCurve.EASE_IN_OUT),
            on_animation_end=self._on_ambient_cycle_end,
            controls=[self._amb_a1, self._amb_a2],
        )
        self._amb_b = ft.Stack(
            expand=True,
            opacity=0.0,
            animate_opacity=ft.Animation(_AMBIENT_FADE_MS, ft.AnimationCurve.EASE_IN_OUT),
            controls=[self._amb_b1, self._amb_b2],
        )
        self._amb_host = ft.Stack(
            expand=True,
            visible=False,
            controls=[self._amb_a, self._amb_b],
        )

        # ── Columna izquierda: funda + disco + controles ───────────────
        self._disc = _VinylDisc(_DISC_SIZE)
        self._disc_holder = ft.Container(
            left=(_COVER_SIZE - _DISC_SIZE) / 2,
            top=0,
            content=self._disc,
        )
        self._cover_fallback = app_icon(PIcons.DISC, size=56, color=TEXT_DIM, fill=True)
        self._cover_img = ft.Image(
            src="",
            width=_COVER_SIZE,
            height=_COVER_SIZE,
            fit=ft.BoxFit.COVER,
            border_radius=RADIUS_DIALOG,
            visible=False,
        )
        self._format_badge_txt = app_text(
            "", size=10, color=TEXT_MUTED, font_family=mono_family("medium")
        )
        self._format_badge = ft.Container(
            bgcolor=CHIP_BG,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            right=10,
            bottom=10,
            visible=False,
            content=self._format_badge_txt,
        )
        self._cover_box = ft.Container(
            width=_COVER_SIZE,
            height=_COVER_SIZE,
            top=_DISC_PEEK,
            left=0,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(1.0, BORDER_LIGHT),
            border_radius=RADIUS_DIALOG,
            alignment=ft.Alignment.CENTER,
            content=ft.Stack(
                controls=[
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment.CENTER,
                        content=self._cover_fallback,
                    ),
                    self._cover_img,
                    self._format_badge,
                ],
                expand=True,
            ),
        )
        self._sleeve_stack = ft.Stack(
            width=_COVER_SIZE,
            height=_COVER_SIZE + _DISC_PEEK,
            clip_behavior=ft.ClipBehavior.NONE,
            controls=[self._disc_holder, self._cover_box],
        )

        self._title_txt = app_text(
            "Sin reproducción",
            size=20,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._artist_txt = app_text(
            "Selecciona una pista",
            size=13,
            color=TEXT_MUTED,
            font_family=FONT_TEXT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        self._pos_txt = app_text("0:00", size=11, color=TEXT_MUTED, font_family=mono_family("light"))
        self._rem_txt = app_text("-0:00", size=11, color=TEXT_MUTED, font_family=mono_family("light"))
        self._progress_fill = ft.Container(
            width=0,
            height=PLAYER_PROGRESS_H,
            bgcolor=ACCENT,
            border_radius=PLAYER_PROGRESS_H / 2,
            animate=ft.Animation(PLAYER_PROGRESS_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        self._progress_track = ft.Container(
            height=PLAYER_PROGRESS_H,
            bgcolor=BORDER_MUTED,
            border_radius=PLAYER_PROGRESS_H / 2,
            alignment=ft.Alignment.CENTER_LEFT,
            on_size_change=self._on_track_size_change,
            content=self._progress_fill,
            expand=True,
        )
        self._progress_gesture = ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap_down=self._on_progress_tap,
            on_horizontal_drag_start=self._on_scrub_start,
            on_horizontal_drag_update=self._on_scrub_update,
            on_horizontal_drag_end=self._on_scrub_end,
            content=ft.Container(
                padding=ft.Padding.symmetric(vertical=8),
                content=self._progress_track,
                expand=True,
            ),
            expand=True,
        )

        self._shuffle_btn = app_icon_button(
            PIcons.SHUFFLE,
            size=18,
            icon_color=TEXT_MUTED,
            tooltip="Aleatorio",
            on_click=self.player_bar._on_shuffle_click,
            width=36,
            height=36,
        )
        self._prev_btn = app_icon_button(
            PIcons.SKIP_BACK,
            size=22,
            icon_color=TEXT_PRIMARY,
            tooltip="Anterior",
            on_click=self.player_bar._on_prev_click,
            width=40,
            height=40,
        )
        self._play_btn = app_icon_button(
            PIcons.PLAY,
            fill=True,
            size=34,
            icon_color=ACCENT,
            hover_color_icon=ACCENT,
            hover_scale=1.1,
            tooltip="Reproducir / Pausar",
            on_click=self.player_bar._on_play_pause_click,
            width=54,
            height=54,
        )
        self._next_btn = app_icon_button(
            PIcons.SKIP_FORWARD,
            size=22,
            icon_color=TEXT_PRIMARY,
            tooltip="Siguiente",
            on_click=self.player_bar._on_next_click,
            width=40,
            height=40,
        )
        self._repeat_btn = app_icon_button(
            PIcons.REPEAT,
            size=18,
            icon_color=TEXT_MUTED,
            tooltip="Repetir",
            on_click=self.player_bar._on_repeat_click,
            width=36,
            height=36,
        )

        self._mute_btn = app_icon_button(
            PIcons.SPEAKER_HIGH,
            size=18,
            icon_color=TEXT_MUTED,
            tooltip="Silenciar / Activar sonido",
            on_click=self.player_bar._on_mute_click,
            width=34,
            height=34,
        )
        self._vol_slider = ft.Slider(
            min=0.0,
            max=1.0,
            value=0.0 if self.player_bar._muted else self.player_bar._volume,
            active_color=ACCENT,
            inactive_color=BG_SURFACE,
            expand=True,
            on_change=self._on_volume_change,
            on_change_end=self._on_volume_change_end,
        )

        left_col = ft.Container(
            width=400,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                controls=[
                    ft.Container(
                        alignment=ft.Alignment.CENTER,
                        content=self._sleeve_stack,
                    ),
                    ft.Container(height=16),
                    self._title_txt,
                    self._artist_txt,
                    ft.Container(height=10),
                    ft.Row(
                        controls=[self._pos_txt, self._progress_gesture, self._rem_txt],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        controls=[
                            self._shuffle_btn,
                            self._prev_btn,
                            self._play_btn,
                            self._next_btn,
                            self._repeat_btn,
                        ],
                        spacing=14,
                        alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        controls=[self._mute_btn, self._vol_slider],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

        # ── Columna derecha compartiendo Cola y Letra ──────────────────
        self._tab_refs: dict[str, tuple[ft.Container, ft.Text, ft.Text]] = {}
        tab_controls: list[ft.Control] = []
        for tab_key, label_text, glyph in (
            (_TAB_LETRA, "Letra", PIcons.MUSIC_NOTES),
            (_TAB_COLA, "Cola", PIcons.QUEUE),
        ):
            ico = app_icon(glyph, size=14, color=TEXT_DIM, fill=False)
            lbl = app_text(label_text, size=12, color=TEXT_MUTED, font_family=FONT_HEADLINE)
            btn = ft.Container(
                expand=True,
                height=34,
                border_radius=RADIUS_BUTTON - 2,
                padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                alignment=ft.Alignment.CENTER,
                ink=True,
                animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
                on_click=lambda _e, key=tab_key: self.set_tab(key),
                content=ft.Row(
                    controls=[ico, lbl],
                    spacing=6,
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
            )
            self._tab_refs[tab_key] = (btn, ico, lbl)
            tab_controls.append(btn)

        tab_bar = ft.Container(
            width=240,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_BUTTON,
            padding=ft.Padding.all(2),
            content=ft.Row(controls=tab_controls, spacing=4),
        )

        self._queue_count_txt = app_text("0 pistas", size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._queue_list = ft.ReorderableListView(
            item_extent=ITEM_H,
            build_controls_on_demand=True,
            show_default_drag_handles=False,
            on_reorder=self._on_reorder,
            expand=True,
            padding=ft.Padding.symmetric(horizontal=4, vertical=4),
        )
        self._queue_empty = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=app_text("Cola vacía", size=13, color=TEXT_MUTED, font_family=FONT_TEXT),
        )
        self._cola_view = ft.Column(
            controls=[
                ft.Row(
                    controls=[section_label("COLA DE REPRODUCCIÓN"), self._queue_count_txt],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Stack(
                    controls=[self._queue_list, self._queue_empty],
                    expand=True,
                    fit=ft.StackFit.EXPAND,
                ),
            ],
            spacing=8,
            expand=True,
            visible=(self._active_tab == _TAB_COLA),
        )

        self._letra_view = LyricsPane(
            page,
            player_bar,
            large=True,
            initially_visible=False,
        )
        self._letra_view.visible = (self._active_tab == _TAB_LETRA)

        right_col = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(vertical=24),
            content=ft.Column(
                controls=[
                    tab_bar,
                    ft.Divider(height=1, color=BORDER_MUTED),
                    ft.Stack(
                        controls=[self._cola_view, self._letra_view],
                        expand=True,
                        fit=ft.StackFit.EXPAND,
                    ),
                ],
                spacing=12,
                expand=True,
            ),
        )

        close_btn = app_icon_button(
            PIcons.X,
            size=20,
            icon_color=TEXT_MUTED,
            hover_color_icon=TEXT_PRIMARY,
            tooltip="Cerrar (Esc)",
            on_click=lambda _e: self.close(),
            width=40,
            height=40,
            bgcolor=BG_PANEL,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_BUTTON,
        )

        body = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=56, vertical=28),
            content=ft.Row(
                controls=[left_col, right_col],
                spacing=52,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        super().__init__(
            left=0,
            top=0,
            right=0,
            bottom=0,
            bgcolor=BG_DEEP,
            opacity=0.0,
            visible=False,
            animate_opacity=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            content=ft.Stack(
                expand=True,
                fit=ft.StackFit.EXPAND,
                controls=[
                    self._amb_host,
                    body,
                    ft.Container(left=24, top=20, content=close_btn),
                ],
            ),
        )

        self._apply_tab_styles()
        self.player_bar.subscribe(self.refresh)
        sub_pos = getattr(self.player_bar, "subscribe_position", None)
        if callable(sub_pos):
            sub_pos(self._on_position)

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def active_tab(self) -> str:
        return self._active_tab

    # ── Ciclo de vida: abrir, cerrar, foco ventana, Escape ─────────────

    def _install_page_hooks(self) -> None:
        if self._handlers_installed:
            return
        self._handlers_installed = True
        win = getattr(self._page_ref, "window", None)
        if win is not None:
            self._prev_win_handler = getattr(win, "on_event", None)
            win.on_event = self._on_window_event
        self._prev_key_handler = getattr(self._page_ref, "on_keyboard_event", None)
        self._page_ref.on_keyboard_event = self._on_keyboard_event

    def _uninstall_page_hooks(self) -> None:
        if not self._handlers_installed:
            return
        self._handlers_installed = False
        win = getattr(self._page_ref, "window", None)
        if win is not None and getattr(win, "on_event", None) == self._on_window_event:
            win.on_event = self._prev_win_handler
        if getattr(self._page_ref, "on_keyboard_event", None) == self._on_keyboard_event:
            self._page_ref.on_keyboard_event = self._prev_key_handler

    def _on_window_event(self, e: Any) -> None:
        if callable(self._prev_win_handler):
            try:
                self._prev_win_handler(e)
            except Exception:
                pass
        ev_type = getattr(e, "type", None) or getattr(e, "data", None)
        ev_name = str(getattr(ev_type, "value", ev_type) or "").lower()
        if ev_name in ("blur", "hide", "minimize"):
            self._window_focused = False
            self._sync_motion_state()
        elif ev_name in ("focus", "show", "restore"):
            self._window_focused = True
            self._sync_motion_state()

    def _on_keyboard_event(self, e: ft.KeyboardEvent) -> None:
        if self._is_open and getattr(e, "key", None) == "Escape":
            self.close()
            return
        if callable(self._prev_key_handler):
            self._prev_key_handler(e)

    def open(self) -> None:
        if self._disposed:
            return
        self._open_gen += 1
        gen = self._open_gen
        self._is_open = True
        self._window_focused = True
        self._install_page_hooks()

        overlay = getattr(self._page_ref, "overlay", None)
        if isinstance(overlay, list) and self not in overlay:
            overlay.append(self)

        reduce = bool(_read_setting("get_reduce_motion", "reduce_motion", False))
        self.animate_opacity = None if reduce else ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT)
        self.visible = True
        self.opacity = 1.0 if reduce else 0.0
        self.refresh()
        self._letra_view.set_active_visibility(self._active_tab == _TAB_LETRA)
        self._safe_page_update()

        if reduce:
            self._sync_motion_state()
        else:
            run_task = getattr(self._page_ref, "run_task", None)
            if callable(run_task):
                try:
                    run_task(self._reveal_open, gen)
                    return
                except Exception:
                    pass
            self.opacity = 1.0
            self._sync_motion_state()
            self._safe_update()

    async def _reveal_open(self, gen: int) -> None:
        await asyncio.sleep(0.03)
        if gen != self._open_gen or not self._is_open:
            return
        self.opacity = 1.0
        self._sync_motion_state()
        self._safe_update()

    def close(self) -> None:
        if not self._is_open:
            return
        self._open_gen += 1
        gen = self._open_gen
        self._is_open = False
        self._letra_view.set_active_visibility(False)
        self._sync_motion_state()

        reduce = bool(_read_setting("get_reduce_motion", "reduce_motion", False))
        self.opacity = 0.0
        self._safe_update()
        if reduce:
            self.visible = False
            self._safe_update()
            return
        run_task = getattr(self._page_ref, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._hide_after_close, gen)
                return
            except Exception:
                pass
        self.visible = False
        self._safe_update()

    async def _hide_after_close(self, gen: int) -> None:
        await asyncio.sleep(MOTION_PANEL_MS / 1000)
        if gen != self._open_gen or self._is_open:
            return
        self.visible = False
        self._safe_update()

    def dispose(self) -> None:
        self._disposed = True
        self._is_open = False
        self._disc.set_spinning(False)
        self._uninstall_page_hooks()
        self.player_bar.unsubscribe(self.refresh)
        unsub_pos = getattr(self.player_bar, "unsubscribe_position", None)
        if callable(unsub_pos):
            unsub_pos(self._on_position)
        self._letra_view.dispose()

    # ── Ambiente OLED Tier B y estado de movimiento ────────────────────

    def _should_animate_ambient(self) -> bool:
        return (
            self._is_open
            and self._window_focused
            and self._amb_active_pair is not None
            and bool(_read_setting("get_scene_ambient_bg", "scene_ambient_bg", True))
            and not bool(_read_setting("get_reduce_motion", "reduce_motion", False))
        )

    def _should_spin_disc(self) -> bool:
        return (
            self._is_open
            and self._window_focused
            and bool(getattr(self.player_bar, "is_playing", False))
            and bool(_read_setting("get_scene_show_disc", "scene_show_disc", True))
            and not bool(_read_setting("get_reduce_motion", "reduce_motion", False))
        )

    def _sync_motion_state(self) -> None:
        show_disc = bool(_read_setting("get_scene_show_disc", "scene_show_disc", True))
        self._disc_holder.visible = show_disc
        self._disc.set_spinning(self._should_spin_disc())

        show_amb = bool(_read_setting("get_scene_ambient_bg", "scene_ambient_bg", True))
        reduce = bool(_read_setting("get_reduce_motion", "reduce_motion", False))
        if not show_amb or self._amb_active_pair is None:
            self._amb_host.visible = False
            self._amb_cycling = False
        else:
            self._amb_host.visible = True
            if reduce:
                self._amb_cycling = False
                self._amb_a.animate_opacity = None
                self._amb_b.animate_opacity = None
                self._amb_a.opacity = 0.5
                self._amb_b.opacity = 0.5
            else:
                anim = ft.Animation(_AMBIENT_FADE_MS, ft.AnimationCurve.EASE_IN_OUT)
                self._amb_a.animate_opacity = anim
                self._amb_b.animate_opacity = anim
                if self._should_animate_ambient():
                    if not self._amb_cycling:
                        self._trigger_ambient_step()
                else:
                    self._amb_cycling = False
        self._safe_update()

    def _apply_ambient_pair(self, pair: tuple[str, str] | None) -> None:
        self._amb_active_pair = pair
        if pair is None:
            self._amb_host.visible = False
            self._safe_update()
            return
        c1, c2 = pair
        self._amb_a1.gradient = ft.RadialGradient(
            center=ft.Alignment(-0.6, 0.7),
            radius=0.95,
            colors=[c1, ft.Colors.TRANSPARENT],
            stops=[0.0, 1.0],
        )
        self._amb_a2.gradient = ft.RadialGradient(
            center=ft.Alignment(0.7, -0.75),
            radius=0.85,
            colors=[c2, ft.Colors.TRANSPARENT],
            stops=[0.0, 1.0],
        )
        self._amb_b1.gradient = ft.RadialGradient(
            center=ft.Alignment(0.65, 0.65),
            radius=0.95,
            colors=[c2, ft.Colors.TRANSPARENT],
            stops=[0.0, 1.0],
        )
        self._amb_b2.gradient = ft.RadialGradient(
            center=ft.Alignment(-0.65, -0.7),
            radius=0.85,
            colors=[c1, ft.Colors.TRANSPARENT],
            stops=[0.0, 1.0],
        )
        self._sync_motion_state()

    def _trigger_ambient_step(self) -> None:
        if not self._should_animate_ambient():
            self._amb_cycling = False
            return
        self._amb_cycling = True
        self._amb_phase = not self._amb_phase
        self._amb_a.opacity = 0.0 if self._amb_phase else 1.0
        self._amb_b.opacity = 1.0 if self._amb_phase else 0.0
        safe_update(self._amb_a)
        safe_update(self._amb_b)

    def _on_ambient_cycle_end(self, _e=None) -> None:
        if self._should_animate_ambient():
            self._trigger_ambient_step()
        else:
            self._amb_cycling = False

    def _refresh_ambient_for_cover(self, img_url: str) -> None:
        if img_url == self._last_cover_url:
            return
        self._last_cover_url = img_url
        self._amb_gen += 1
        gen = self._amb_gen
        if not img_url or not bool(_read_setting("get_scene_ambient_bg", "scene_ambient_bg", True)):
            self._apply_ambient_pair(None)
            return
        if img_url in self._amb_cache:
            self._apply_ambient_pair(self._amb_cache[img_url])
            return

        async def _load() -> None:
            def _worker() -> tuple[str, str] | None:
                path = _resolve_local_cover(img_url)
                if path is None:
                    return None
                return extract_ambient_pair(path)

            pair = await asyncio.to_thread(_worker)
            if gen != self._amb_gen or self._disposed:
                return
            self._amb_cache[img_url] = pair
            self._apply_ambient_pair(pair)

        try:
            asyncio.get_running_loop().create_task(_load())
        except RuntimeError:
            run_task = getattr(self._page_ref, "run_task", None)
            if callable(run_task):
                try:
                    run_task(_load)
                except Exception:
                    pass

    # ── Pestañas Cola/Letra y sincronización de barra ──────────────────

    def _apply_tab_styles(self) -> None:
        for tab_key, (btn, ico, lbl) in self._tab_refs.items():
            active = tab_key == self._active_tab
            btn.bgcolor = ACCENT_HALO if active else ft.Colors.TRANSPARENT
            btn.border = ft.Border.all(0.8, ACCENT if active else ft.Colors.TRANSPARENT)
            ico.color = ACCENT if active else TEXT_DIM
            ico.font_family = FONT_PHOSPHOR_FILL if active else FONT_PHOSPHOR
            lbl.color = TEXT_PRIMARY if active else TEXT_MUTED
            lbl.font_family = FONT_HEADLINE_BOLD if active else FONT_HEADLINE
        self._cola_view.visible = self._active_tab == _TAB_COLA
        self._letra_view.visible = self._active_tab == _TAB_LETRA

    def set_tab(self, tab: str) -> None:
        normalized = str(tab or _TAB_LETRA).lower()
        if normalized not in _VALID_TABS:
            normalized = _TAB_LETRA
        self._active_tab = normalized
        _save_queue_prefs(tab=normalized)
        self._apply_tab_styles()
        self._letra_view.set_active_visibility(self._is_open and self._active_tab == _TAB_LETRA)
        self._safe_update()

    def _on_reorder(self, e: ft.OnReorderEvent) -> None:
        old_idx = getattr(e, "old_index", None)
        new_idx = getattr(e, "new_index", None)
        if old_idx is None or new_idx is None:
            return
        self.player_bar.move_queue_item(int(old_idx), int(new_idx))

    def refresh(self) -> None:
        q = self.player_bar.queue
        items = q.ordered_tracks()
        cur: PlayableTrack | None = q.current
        playing = bool(getattr(self.player_bar, "is_playing", False))
        has_queue = bool(q.order)

        if cur is not None:
            tr = cur.track
            title = tr.name or os.path.basename(cur.path)
            artist = tr.artist or "Archivo local"
            sub = f"{artist} — {tr.album}" if tr.album else artist
            self._title_txt.value = title
            self._title_txt.font_family = font_family_for(title, "bold")
            self._artist_txt.value = sub
            self._artist_txt.font_family = font_family_for(sub)
            if tr.img_url:
                self._cover_img.src = tr.img_url
                self._cover_img.visible = True
                self._cover_fallback.visible = False
            else:
                self._cover_img.visible = False
                self._cover_fallback.visible = True
            self._format_badge_txt.value = cur.badge_text
            self._format_badge.visible = True
            self._refresh_ambient_for_cover(tr.img_url)
        else:
            self._title_txt.value = "Sin reproducción"
            self._title_txt.font_family = FONT_HEADLINE_BOLD
            self._artist_txt.value = "Selecciona una pista descargada"
            self._artist_txt.font_family = FONT_TEXT
            self._cover_img.visible = False
            self._cover_fallback.visible = True
            self._format_badge.visible = False
            self._refresh_ambient_for_cover("")

        self._play_btn.icon_text.value = PIcons.PAUSE if playing else PIcons.PLAY
        self._play_btn.icon_text.font_family = FONT_PHOSPHOR_FILL
        self._play_btn.disabled = not has_queue
        self._play_btn.opacity = 0.4 if not has_queue else 1.0
        self._prev_btn.disabled = not has_queue
        self._prev_btn.opacity = 0.4 if not has_queue else 1.0
        self._next_btn.disabled = not has_queue
        self._next_btn.opacity = 0.4 if not has_queue else 1.0

        self._shuffle_btn.icon_text.color = ACCENT if q.shuffle else TEXT_MUTED
        self._shuffle_btn.icon_text.font_family = FONT_PHOSPHOR_FILL if q.shuffle else FONT_PHOSPHOR

        if q.repeat == RepeatMode.ONE:
            self._repeat_btn.icon_text.value = PIcons.REPEAT_ONCE
            self._repeat_btn.icon_text.font_family = FONT_PHOSPHOR_FILL
            self._repeat_btn.icon_text.color = ACCENT
        elif q.repeat == RepeatMode.ALL:
            self._repeat_btn.icon_text.value = PIcons.REPEAT
            self._repeat_btn.icon_text.font_family = FONT_PHOSPHOR_FILL
            self._repeat_btn.icon_text.color = ACCENT
        else:
            self._repeat_btn.icon_text.value = PIcons.REPEAT
            self._repeat_btn.icon_text.font_family = FONT_PHOSPHOR
            self._repeat_btn.icon_text.color = TEXT_MUTED

        muted = bool(getattr(self.player_bar, "_muted", False))
        vol = float(getattr(self.player_bar, "_volume", 1.0))
        self._vol_slider.value = 0.0 if muted else vol
        if muted or vol <= 0.0:
            self._mute_btn.icon_text.value = PIcons.SPEAKER_SLASH
            self._mute_btn.icon_text.color = ACCENT if muted else TEXT_MUTED
        elif vol < 0.5:
            self._mute_btn.icon_text.value = PIcons.SPEAKER_LOW
            self._mute_btn.icon_text.color = TEXT_MUTED
        else:
            self._mute_btn.icon_text.value = PIcons.SPEAKER_HIGH
            self._mute_btn.icon_text.color = TEXT_MUTED

        self._queue_count_txt.value = f"{len(items)} {'pista' if len(items) == 1 else 'pistas'}"
        self._queue_list.controls = [
            build_queue_row(idx, pt, pt is cur, self.player_bar, reorderable=True)
            for idx, pt in enumerate(items)
        ]
        self._queue_empty.visible = not items
        self._queue_list.visible = bool(items)

        self._disc_holder.visible = bool(_read_setting("get_scene_show_disc", "scene_show_disc", True))
        self._disc.set_spinning(self._should_spin_disc())
        self._letra_view.refresh_track()
        if not self._scrubbing:
            pos = int(getattr(self.player_bar, "position_ms", 0) or 0)
            dur = int(getattr(self.player_bar, "duration_ms", 0) or 0)
            self._update_progress(pos, dur)
        self._safe_update()

    # ── Barra de progreso y volumen locales ────────────────────────────

    def _on_position(self, pos_ms: int, dur_ms: int) -> None:
        if not self._is_open or self._scrubbing:
            return
        self._update_progress(pos_ms, dur_ms)

    def _update_progress(self, pos_ms: int, dur_ms: int) -> None:
        frac = max(0.0, min(1.0, pos_ms / dur_ms)) if dur_ms > 0 else 0.0
        self._progress_fill.width = frac * self._track_width
        self._pos_txt.value = _fmt_ms(pos_ms)
        rem = max(0, dur_ms - pos_ms)
        self._rem_txt.value = f"-{_fmt_ms(rem)}" if dur_ms > 0 else "-0:00"
        safe_update(self._progress_fill)
        safe_update(self._pos_txt)
        safe_update(self._rem_txt)

    def _on_track_size_change(self, e: ft.LayoutSizeChangeEvent) -> None:
        try:
            w = float(e.width or 0.0)
        except (TypeError, ValueError):
            w = 0.0
        if w > 0:
            self._track_width = w
            pos = self._scrub_ms if self._scrubbing else int(getattr(self.player_bar, "position_ms", 0) or 0)
            dur = int(getattr(self.player_bar, "duration_ms", 0) or 0)
            self._update_progress(pos, dur)

    def _x_to_ms(self, local_x: float) -> int:
        dur = int(getattr(self.player_bar, "duration_ms", 0) or 0)
        if dur <= 0 or self._track_width <= 0:
            return 0
        frac = max(0.0, min(1.0, local_x / self._track_width))
        return int(frac * dur)

    def _on_progress_tap(self, e: ft.TapEvent) -> None:
        pos = getattr(e, "local_position", None)
        x = float(getattr(pos, "x", 0.0) or 0.0)
        self.player_bar.seek_to(self._x_to_ms(x))

    def _on_scrub_start(self, _e) -> None:
        if int(getattr(self.player_bar, "duration_ms", 0) or 0) <= 0:
            return
        self._scrubbing = True
        self._scrub_ms = int(getattr(self.player_bar, "position_ms", 0) or 0)
        self._progress_fill.animate = None

    def _on_scrub_update(self, e: ft.DragUpdateEvent) -> None:
        if not self._scrubbing:
            return
        pos = getattr(e, "local_position", None)
        x = float(getattr(pos, "x", 0.0) or 0.0)
        self._scrub_ms = self._x_to_ms(x)
        dur = int(getattr(self.player_bar, "duration_ms", 0) or 0)
        self._update_progress(self._scrub_ms, dur)

    def _on_scrub_end(self, _e) -> None:
        if not self._scrubbing:
            return
        target = self._scrub_ms
        self._scrubbing = False
        self._progress_fill.animate = ft.Animation(
            PLAYER_PROGRESS_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT
        )
        self.player_bar.seek_to(target)

    def _on_volume_change(self, e: ft.ControlEvent) -> None:
        try:
            val = float(e.control.value or 0.0)
        except (TypeError, ValueError):
            val = 1.0
        self.player_bar.set_volume_level(val, persist=False)

    def _on_volume_change_end(self, e: ft.ControlEvent) -> None:
        try:
            val = float(e.control.value or 0.0)
        except (TypeError, ValueError):
            val = 1.0
        self.player_bar.set_volume_level(val, persist=True)

    def _safe_update(self) -> None:
        safe_update(self)

    def _safe_page_update(self) -> None:
        upd = getattr(self._page_ref, "update", None)
        if callable(upd):
            try:
                upd()
            except Exception:
                pass
