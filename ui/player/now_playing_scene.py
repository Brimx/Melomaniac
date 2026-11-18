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
import base64
import io
import math
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import unquote, urlparse

import flet as ft
import flet.canvas as cv
from PIL import Image

from core.playback import PlayableTrack, RepeatMode
from engine.covers import upscale_cover_url
from engine.parsers import local_cover_data_uri
from services import cover_cache, settings_store
from ui.components.song_row import ITEM_H, _thumb_content_for
from ui.components.widgets import (
    app_text,
    empty_state,
    fmt_ms as _fmt_ms,
    is_mounted,
    safe_update,
    section_label,
    tab_underline,
)
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
    ACCENT_HOVER,
    BG_DEEP,
    BG_INPUT,
    BG_PANEL,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    DECOR_LINE,
    FS_META,
    MOTION_HOVER_MS,
    MOTION_PANEL_MS,
    PLAYER_PROGRESS_ANIM_MS,
    PLAYER_PROGRESS_H,
    RADIUS_PILL,
    RADIUS_SHARP,
    RADIUS_SM,
    SIDEBAR_BG,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    SPACE_7,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

_OLED_MAX_Y = 0.0136
_AMBIENT_FADE_MS = 24_000
_DISC_ROTATION_MS = 1_800
_COVER_SIZE = 340
_DISC_SIZE = 300
_DISC_PEEK = 72
_LEFT_COL_W = 540
_RIGHT_COL_W = 680
_ASCII_RAMP = " .:-=+*#%@"
_ASCII_COLS = 96
_ASCII_ROWS = 48


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


def _open_cover(source: str | Path | bytes) -> Any:
    """Abre una portada desde bytes (data URI decodificado) o una ruta local."""
    if isinstance(source, (bytes, bytearray)):
        try:
            return Image.open(io.BytesIO(bytes(source)))
        except Exception:
            return None
    path = Path(source)
    if not path.is_file():
        return None
    try:
        return Image.open(path)
    except Exception:
        return None


def _decode_data_uri(data_uri: str) -> bytes | None:
    """Decodifica un ``data:image/...`` a bytes (``None`` si viene malformado)."""
    try:
        header, _, payload = data_uri.partition(",")
        if not payload:
            return None
        if ";base64" in header:
            return base64.b64decode(payload)
        return unquote(payload).encode("latin-1")
    except Exception:
        return None


def extract_ambient_pair(image: str | Path | bytes) -> tuple[str, str] | None:
    """Extrae 2 colores dominantes con mayor separación de tono y tope OLED.

    Acepta una ruta local o los bytes de una portada (el caso de las pistas
    locales, cuyo ``img_url`` es un data URI). Devuelve ``None`` si la portada
    no existe, falla o es prácticamente acromática (p. ej. blanco y negro),
    caso en que el ambiente permanece apagado.
    """
    opened = _open_cover(image)
    if opened is None:
        return None
    try:
        with opened as img:
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


_ASCII_OLED_COLOR = cap_oled_luminance((200, 200, 200))


def extract_ascii_art(
    image: str | Path | bytes,
    *,
    cols: int = _ASCII_COLS,
    rows: int = _ASCII_ROWS,
) -> str | None:
    """Genera representación ASCII estática ``cols×rows`` desde una portada local."""
    opened = _open_cover(image)
    if opened is None:
        return None
    try:
        with opened as img:
            gray = img.convert("L").resize((cols, rows), Image.Resampling.LANCZOS)
            px = gray.load()
    except Exception:
        return None
    if px is None:
        return None
    n_ramp = len(_ASCII_RAMP) - 1
    return "\n".join(
        "".join(_ASCII_RAMP[px[x, y] * n_ramp // 255] for x in range(cols))
        for y in range(rows)
    )


def _resolve_local_cover(img_url: str) -> bytes | Path | None:
    raw = upscale_cover_url((img_url or "").strip())
    if not raw:
        return None
    if raw.startswith("data:"):
        return _decode_data_uri(raw)
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


def _display_cover_src(img_url: str, audio_path: str = "") -> str:
    """Fuente de la portada a resolución de la escena.

    - URL de CDN de Google: se sube a ``=s1000`` con ``upscale_cover_url``.
    - data URI local (96 px de las filas): se re-extrae del audio a alta
      resolución; ampliar el data URI de fila se ve borroso.
    - Ruta local o asset: se devuelve tal cual.
    """
    src = (img_url or "").strip()
    if src.startswith("data:") and audio_path:
        hi = local_cover_data_uri(audio_path, target_px=_COVER_SIZE * 2)
        if hi:
            return hi
    return upscale_cover_url(src)


class _VinylDisc(ft.Stack):
    """Vinilo en 2 capas: surcos estáticos (0 CPU/frame) + reflejo giratorio GPU."""

    def __init__(self, size: int = _DISC_SIZE) -> None:
        self._size = size
        self._angle = 0.0
        self._spin_start_angle = 0.0
        self._spin_started_at = 0.0
        self._spinning = False
        self._should_spin = False

        center = size / 2.0
        groove_shapes: list[cv.Shape] = [
            cv.Circle(center, center, center - 1, ft.Paint(color=BG_PANEL)),
            cv.Circle(
                center,
                center,
                center - 1,
                ft.Paint(color=BORDER_MUTED, stroke_width=1.2, style=ft.PaintingStyle.STROKE),
            ),
        ]
        # Surcos estáticos: dibujar una sola vez evita moiré al rotar
        r = center - 7.0
        ring_idx = 0
        min_r = size * 0.21
        while r > min_r:
            col = SIDEBAR_BG if ring_idx % 2 == 0 else BG_INPUT
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
            cv.Circle(center, center, label_r + 2.0, ft.Paint(color=ACCENT_HALO)),
            cv.Circle(center, center, label_r, ft.Paint(color=ACCENT_DIM)),
            cv.Circle(center, center, max(4.0, size * 0.025), ft.Paint(color=BG_DEEP)),
        ])

        static_canvas = cv.Canvas(shapes=groove_shapes, width=size, height=size)

        seal_dot = ft.Container(
            width=max(8, int(size * 0.045)),
            height=max(8, int(size * 0.045)),
            border_radius=RADIUS_PILL,
            bgcolor=ft.Colors.with_opacity(0.33, TEXT_PRIMARY),
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
                    ft.Colors.TRANSPARENT,
                    ft.Colors.with_opacity(0.15, TEXT_PRIMARY),
                    ft.Colors.TRANSPARENT,
                    ft.Colors.with_opacity(0.10, ACCENT_HOVER),
                    ft.Colors.TRANSPARENT,
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
        elif self._spinning:
            self._spinning = False
            elapsed_ms = max(0.0, (time.monotonic() - self._spin_started_at) * 1000.0)
            frac = min(1.0, elapsed_ms / max(1, _DISC_ROTATION_MS))
            self._angle = self._spin_start_angle + frac * math.tau
            self._rotator.animate_rotation = None
            self._rotator.rotate = ft.Rotate(self._angle, alignment=ft.Alignment.CENTER)
            safe_update(self._rotator)

    def _advance_rotation(self) -> None:
        if not self._should_spin or not is_mounted(self):
            self._spinning = False
            return
        self._spin_start_angle = self._angle
        self._spin_started_at = time.monotonic()
        self._angle += math.tau
        self._rotator.animate_rotation = ft.Animation(
            _DISC_ROTATION_MS, ft.AnimationCurve.LINEAR
        )
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
        self._ascii_cache: dict[str, str | None] = {}
        self._rendered_cover_url: str | None = None
        self._needs_refresh = False
        self._last_track_sig: Any = None
        self._last_transport_sig: Any = None
        self._last_vol_sig: Any = None
        self._last_queue_sig: Any = None
        self._show_disc_setting = True
        self._show_amb_setting = True
        self._reduce_motion_setting = False
        self._show_right_setting = True
        self._reload_motion_settings()
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
        self._cover_thumb = ft.Container(
            width=_COVER_SIZE,
            height=_COVER_SIZE,
            border_radius=RADIUS_SM,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            alignment=ft.Alignment.CENTER,
            content=_thumb_content_for("", box=_COVER_SIZE, icon_size=56),
        )
        self._format_badge_txt = app_text(
            "", size=FS_META, color=TEXT_MUTED, font_family=mono_family("medium")
        )
        self._format_badge = ft.Container(
            bgcolor=CHIP_BG,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_SM,
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
            border_radius=RADIUS_SM,
            alignment=ft.Alignment.CENTER,
            content=ft.Stack(
                controls=[
                    self._cover_thumb,
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

        self._right_toggle_btn = app_icon_button(
            PIcons.SIDEBAR_SIMPLE,
            size=18,
            icon_color=TEXT_MUTED,
            tooltip="Mostrar / ocultar Letra y Cola",
            on_click=lambda _e: self._toggle_right_panel(),
            width=34,
            height=34,
        )

        left_col = ft.Container(
            width=_LEFT_COL_W,
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
                        spacing=SPACE_3,
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
                        spacing=SPACE_4,
                        alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        controls=[self._mute_btn, self._vol_slider, self._right_toggle_btn],
                        spacing=SPACE_2,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=SPACE_1,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

        # ── Columna derecha compartiendo Cola y Letra ──────────────────
        self._tab_items = [(_TAB_LETRA, "Letra"), (_TAB_COLA, "Cola")]
        self._tab_bar = ft.Container(
            content=tab_underline(self._tab_items, self._active_tab, self.set_tab),
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
            alignment=ft.Alignment.CENTER_LEFT,
            padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_4),
            content=empty_state(
                PIcons.QUEUE,
                "Cola vacía",
                "Reproduce una pista desde Descargas o Biblioteca Local.",
            ),
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
            spacing=SPACE_2,
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

        self._right_col = ft.Container(
            width=_RIGHT_COL_W,
            padding=ft.Padding.symmetric(vertical=24),
            content=ft.Column(
                controls=[
                    self._tab_bar,
                    ft.Divider(height=1, color=BORDER_MUTED),
                    ft.Stack(
                        controls=[self._cola_view, self._letra_view],
                        expand=True,
                        fit=ft.StackFit.EXPAND,
                    ),
                ],
                spacing=SPACE_3,
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
            border_radius=RADIUS_SHARP,
        )

        body = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=56, vertical=28),
            content=ft.Row(
                controls=[left_col, self._right_col],
                spacing=SPACE_7,
                expand=True,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        self._ascii_txt = app_text(
            "",
            size=FS_META,
            color=_ASCII_OLED_COLOR,
            font_family=mono_family("regular"),
            no_wrap=True,
            selectable=False,
        )
        self._ascii_layer = ft.Container(
            left=0,
            top=0,
            bottom=0,
            width=520,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            alignment=ft.Alignment.CENTER_LEFT,
            visible=False,
            content=self._ascii_txt,
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
                    self._ascii_layer,
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

    def _reload_motion_settings(self) -> None:
        self._show_disc_setting = bool(_read_setting("get_scene_show_disc", "scene_show_disc", True))
        self._show_amb_setting = bool(_read_setting("get_scene_ambient_bg", "scene_ambient_bg", True))
        self._reduce_motion_setting = bool(_read_setting("get_reduce_motion", "reduce_motion", False))
        self._show_right_setting = bool(
            _read_setting("get_scene_right_open", "scene_right_panel_open", True)
        )

    def open(self) -> None:
        if self._disposed:
            return
        self._open_gen += 1
        gen = self._open_gen
        self._is_open = True
        self._window_focused = True
        self._install_page_hooks()
        self._reload_motion_settings()

        overlay = getattr(self._page_ref, "overlay", None)
        if isinstance(overlay, list) and self not in overlay:
            overlay.append(self)

        reduce = self._reduce_motion_setting
        self.animate_opacity = None if reduce else ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT)
        self.visible = True
        self.opacity = 1.0 if reduce else 0.0
        self.refresh()
        self._apply_right_panel()
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
            and self._show_amb_setting
            and not self._reduce_motion_setting
        )

    def _should_spin_disc(self) -> bool:
        return (
            self._is_open
            and self._window_focused
            and bool(getattr(self.player_bar, "is_playing", False))
            and self._show_disc_setting
            and not self._reduce_motion_setting
        )

    def _sync_motion_state(self) -> None:
        self._reload_motion_settings()
        show_disc = self._show_disc_setting
        self._disc_holder.visible = show_disc
        self._disc.set_spinning(self._should_spin_disc())

        show_amb = self._show_amb_setting
        reduce = self._reduce_motion_setting
        self._ascii_layer.visible = show_amb and bool(self._ascii_txt.value)
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

    def _toggle_right_panel(self) -> None:
        """Muestra u oculta el panel derecho (Letra/Cola) y persiste la elección."""
        self._show_right_setting = not self._show_right_setting
        setter = getattr(settings_store, "set_scene_right_open", None)
        if callable(setter):
            try:
                setter(self._show_right_setting)
            except Exception:
                pass
        self._apply_right_panel()

    def _apply_right_panel(self) -> None:
        self._right_col.visible = self._show_right_setting
        active = self._show_right_setting
        self._right_toggle_btn.icon_text.color = ACCENT if active else TEXT_MUTED
        self._right_toggle_btn.icon_text.font_family = (
            FONT_PHOSPHOR_FILL if active else FONT_PHOSPHOR
        )
        safe_update(self._right_col)
        safe_update(self._right_toggle_btn)

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

    def _apply_ascii_art(self, ascii_art: str | None) -> None:
        self._ascii_txt.value = ascii_art or ""
        self._ascii_layer.visible = bool(ascii_art) and self._show_amb_setting
        safe_update(self._ascii_layer)

    def _refresh_ambient_for_cover(self, img_url: str) -> None:
        if not self._is_open:
            return
        if img_url == self._last_cover_url:
            return
        self._last_cover_url = img_url
        self._amb_gen += 1
        gen = self._amb_gen
        if not img_url or not self._show_amb_setting:
            self._apply_ascii_art(None)
            self._apply_ambient_pair(None)
            return
        if img_url in self._amb_cache:
            self._apply_ascii_art(self._ascii_cache.get(img_url))
            self._apply_ambient_pair(self._amb_cache[img_url])
            return

        async def _load() -> None:
            def _worker() -> tuple[tuple[str, str] | None, str | None]:
                path = _resolve_local_cover(img_url)
                if path is None:
                    return None, None
                return extract_ambient_pair(path), extract_ascii_art(path)

            pair, ascii_art = await asyncio.to_thread(_worker)
            if gen != self._amb_gen or self._disposed:
                return
            self._amb_cache[img_url] = pair
            self._ascii_cache[img_url] = ascii_art
            self._apply_ascii_art(ascii_art)
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
        self._tab_bar.content = tab_underline(self._tab_items, self._active_tab, self.set_tab)
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
        if not self._is_open:
            self._needs_refresh = True
            return
        self._needs_refresh = False

        q = self.player_bar.queue
        items = q.ordered_tracks()
        cur: PlayableTrack | None = q.current
        playing = bool(getattr(self.player_bar, "is_playing", False))
        has_queue = bool(q.order)

        track_sig = (
            (
                cur.path,
                cur.track.id,
                cur.track.name,
                cur.track.artist,
                cur.track.album,
                cur.track.img_url,
                cur.badge_text,
                bool(getattr(cur, "is_stream", False)),
            )
            if cur is not None
            else None
        )
        if track_sig != self._last_track_sig:
            self._last_track_sig = track_sig
            if cur is not None:
                is_stream = bool(getattr(cur, "is_stream", False))
                tr = cur.track
                title = tr.name or os.path.basename(cur.path)
                artist = tr.artist or ("Artista desconocido" if is_stream else "Archivo local")
                sub = f"{artist} — {tr.album}" if tr.album else artist
                self._title_txt.value = title
                self._title_txt.font_family = font_family_for(title, "bold")
                self._artist_txt.value = sub
                self._artist_txt.font_family = font_family_for(sub)
                if tr.img_url != self._rendered_cover_url:
                    self._rendered_cover_url = tr.img_url
                    self._cover_thumb.content = _thumb_content_for(
                        _display_cover_src(tr.img_url, cur.path),
                        box=_COVER_SIZE,
                        icon_size=56,
                    )
                    safe_update(self._cover_thumb)
                if is_stream:
                    self._format_badge.visible = False
                else:
                    self._format_badge_txt.value = cur.badge_text
                    self._format_badge.visible = True
                self._refresh_ambient_for_cover(tr.img_url)
            else:
                self._title_txt.value = "Sin reproducción"
                self._title_txt.font_family = FONT_HEADLINE_BOLD
                self._artist_txt.value = "Selecciona una pista descargada"
                self._artist_txt.font_family = FONT_TEXT
                if self._rendered_cover_url is not None:
                    self._rendered_cover_url = None
                    self._cover_thumb.content = _thumb_content_for(
                        "", box=_COVER_SIZE, icon_size=56
                    )
                    safe_update(self._cover_thumb)
                self._format_badge.visible = False
                self._refresh_ambient_for_cover("")
            safe_update(self._title_txt)
            safe_update(self._artist_txt)
            safe_update(self._format_badge)
            self._letra_view.refresh_track()

        transport_sig = (playing, has_queue, q.shuffle, q.repeat)
        if transport_sig != self._last_transport_sig:
            self._last_transport_sig = transport_sig
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

            safe_update(self._play_btn)
            safe_update(self._prev_btn)
            safe_update(self._next_btn)
            safe_update(self._shuffle_btn)
            safe_update(self._repeat_btn)

        muted = bool(getattr(self.player_bar, "_muted", False))
        vol = float(getattr(self.player_bar, "_volume", 1.0))
        vol_sig = (muted, round(vol, 4))
        if vol_sig != self._last_vol_sig:
            self._last_vol_sig = vol_sig
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
            safe_update(self._vol_slider)
            safe_update(self._mute_btn)

        queue_sig = (
            tuple((id(pt), pt.path, pt.track.id) for pt in items),
            id(cur) if cur is not None else None,
        )
        if queue_sig != self._last_queue_sig:
            self._last_queue_sig = queue_sig
            self._queue_count_txt.value = f"{len(items)} {'pista' if len(items) == 1 else 'pistas'}"
            self._queue_list.controls = [
                build_queue_row(idx, pt, pt is cur, self.player_bar, reorderable=True)
                for idx, pt in enumerate(items)
            ]
            self._queue_empty.visible = not items
            self._queue_list.visible = bool(items)
            safe_update(self._queue_count_txt)
            safe_update(self._queue_list)
            safe_update(self._queue_empty)

        self._disc_holder.visible = self._show_disc_setting
        self._disc.set_spinning(self._should_spin_disc())
        if not self._scrubbing:
            pos = int(getattr(self.player_bar, "position_ms", 0) or 0)
            dur = int(getattr(self.player_bar, "duration_ms", 0) or 0)
            self._update_progress(pos, dur)

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
