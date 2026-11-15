"""
ui/player_queue.py — Panel lateral derecho de cola reordenable y letra
══════════════════════════════════════════════════════════════════════
Vive como 3er hijo del ``Row`` raíz (modo empuje, ``0 ↔ PLAYER_QUEUE_W``)
con animación de ancho ``MOTION_PANEL_MS`` y borde izquierdo sutil.
La pestaña ``Cola`` usa ``ft.ReorderableListView`` con ``item_extent=ITEM_H``
y ``build_controls_on_demand=True``; la pestaña ``Letra`` muestra un
placeholder limpio hasta el Bloque 4.6.
"""

from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections.abc import Callable
import os
from typing import Any

import flet as ft

from core.playback import PlayableTrack
from services import settings_store
from ui.theme.fonts import (
    FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_PHOSPHOR, FONT_PHOSPHOR_FILL,
    FONT_TEXT, font_family_for, mono_family,
)
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.components.song_row import ITEM_H
from ui.theme.tokens import (
    ACCENT, ACCENT_HALO, BG_PANEL, BG_SURFACE, BORDER_MUTED,
    CHIP_BG, MOTION_ENTER_MS, MOTION_HOVER_MS, MOTION_PANEL_MS, PLAYER_QUEUE_W,
    RADIUS_BUTTON, RADIUS_INPUT, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.components.widgets import (
    _ghost_btn,
    app_text,
    is_mounted,
    safe_update,
    section_label,
)

_KEY_QUEUE_OPEN = "queue_panel_open"
_KEY_QUEUE_TAB = "queue_panel_tab"
_TAB_COLA = "cola"
_TAB_LETRA = "letra"
_VALID_TABS = (_TAB_COLA, _TAB_LETRA)
_TAB_SPECS = (
    (_TAB_COLA, "Cola", PIcons.QUEUE),
    (_TAB_LETRA, "Letra", PIcons.MUSIC_NOTES),
)
_SOURCE_LABELS = {
    "local_lrc": "Archivo .lrc",
    "embedded": "Etiqueta embebida",
    "cache": "Caché local",
    "lrclib": "LRCLIB",
}


def _read_setting(getter_name: str, key: str, default: Any) -> Any:
    getter = getattr(settings_store, getter_name, None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    try:
        return settings_store.load_settings().get(key, default)
    except Exception:
        return default


def _is_reduce_motion() -> bool:
    return bool(_read_setting("get_reduce_motion", "reduce_motion", False))


def _lyrics_enabled() -> bool:
    if not bool(_read_setting("get_lyrics_enabled", "lyrics_enabled", True)):
        return False
    prov = str(_read_setting("get_lyrics_provider", "lyrics_provider", "lrclib") or "lrclib").lower()
    return prov not in ("off", "none", "disabled")


def _lyrics_allow_network() -> bool:
    prov = str(_read_setting("get_lyrics_provider", "lyrics_provider", "lrclib") or "lrclib").lower()
    return prov not in ("off", "none", "local", "disabled")


def _import_lyrics_service() -> Any:
    try:
        from services import lyrics as lyrics_svc
        return lyrics_svc
    except ImportError:
        return None


def _active_line_index(lines: list[Any], position_ms: int, lyrics_svc: Any = None) -> int:
    if not lines:
        return -1
    if lyrics_svc is not None:
        fn = getattr(lyrics_svc, "active_line_index", None)
        if callable(fn):
            try:
                return int(fn(lines, position_ms))
            except Exception:
                pass
    times = [int(getattr(line, "time_ms", 0)) for line in lines]
    pos = bisect_right(times, int(position_ms)) - 1
    return pos if pos >= 0 else -1


def _schedule_coro(page: ft.Page | None, coro: Any) -> None:
    if not asyncio.iscoroutine(coro):
        return
    try:
        asyncio.get_running_loop().create_task(coro)
        return
    except RuntimeError:
        pass
    run_task = getattr(page, "run_task", None)
    if callable(run_task):
        try:
            async def _runner() -> None:
                await coro

            run_task(_runner)
            return
        except Exception:
            pass
    coro.close()


def build_queue_row(
    queue_pos: int,
    item: PlayableTrack,
    is_current: bool,
    player_bar: Any,
    *,
    reorderable: bool = True,
) -> ft.Container:
    """Fila reutilizable de la cola de reproducción (panel D26 y escena)."""
    tr = item.track
    title = tr.name or os.path.basename(item.path)
    artist = tr.artist or "Archivo local"
    ext_badge = (item.container or "AUDIO").upper()
    stable_key = f"qrow_{id(item)}_{item.path}"

    leading_controls: list[ft.Control] = []
    if reorderable:
        leading_controls.append(
            ft.ReorderableDragHandle(
                mouse_cursor=ft.MouseCursor.GRAB,
                content=ft.Container(
                    width=24,
                    height=ITEM_H - 12,
                    alignment=ft.Alignment.CENTER,
                    content=app_icon(PIcons.DOTS_SIX_VERTICAL, size=16, color=TEXT_DIM),
                ),
            )
        )

    lead_icon = app_icon(
        PIcons.WAVEFORM if is_current else PIcons.PLAY,
        size=14,
        color=ACCENT if is_current else TEXT_MUTED,
        fill=is_current,
    )
    lead_btn = ft.Container(
        width=26,
        height=26,
        border_radius=RADIUS_BUTTON,
        alignment=ft.Alignment.CENTER,
        tooltip="Reproducir esta pista",
        on_click=lambda _e, pos=queue_pos: player_bar.select_queue_index(pos),
        content=lead_icon,
    )

    meta = ft.Container(
        expand=True,
        ink=True,
        on_click=lambda _e, pos=queue_pos: player_bar.select_queue_index(pos),
        content=ft.Column(
            controls=[
                app_text(
                    title,
                    size=12,
                    color=ACCENT if is_current else TEXT_PRIMARY,
                    font_family=font_family_for(title, "bold" if is_current else "regular"),
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                app_text(
                    artist,
                    size=10,
                    color=TEXT_MUTED,
                    font_family=font_family_for(artist),
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
            spacing=1,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
    )

    fmt_chip = ft.Container(
        bgcolor=CHIP_BG,
        border=ft.Border.all(0.6, BORDER_MUTED),
        border_radius=4,
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
        tooltip=item.stream_tooltip,
        content=app_text(
            ext_badge,
            size=9,
            color=TEXT_MUTED,
            font_family=mono_family("medium"),
        ),
    )

    remove_btn = app_icon_button(
        PIcons.X,
        size=14,
        icon_color=TEXT_DIM,
        hover_color_icon=ACCENT,
        tooltip="Quitar de la cola",
        on_click=lambda _e, pos=queue_pos: player_bar.remove_queue_index(pos),
        width=24,
        height=24,
    )

    return ft.Container(
        key=stable_key,
        height=ITEM_H,
        bgcolor=ACCENT_HALO if is_current else BG_SURFACE,
        border=ft.Border.all(0.6, ACCENT if is_current else BORDER_MUTED),
        border_radius=RADIUS_INPUT,
        padding=ft.Padding.symmetric(horizontal=6, vertical=4),
        margin=ft.Margin.only(bottom=4),
        content=ft.Row(
            controls=[*leading_controls, lead_btn, meta, fmt_chip, remove_btn],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


class LyricsPane(ft.Container):
    """Vista de letras (sincronizadas/planas/vacío) compartida por panel y escena."""

    def __init__(
        self,
        page: ft.Page,
        player_bar: Any,
        *,
        large: bool = False,
        initially_visible: bool = False,
    ) -> None:
        super().__init__(expand=True, visible=initially_visible)
        self._page_ref = page
        self.player_bar = player_bar
        self._large = large
        self._actively_visible = initially_visible
        self._fetch_gen = 0
        self._loaded_key: tuple | None = None
        self._status = "empty"  # empty | loading | synced | plain | instrumental | none | error
        self._result: Any = None
        self._error_msg = ""
        self._active_idx = -1
        self._line_controls: list[tuple[ft.Container, ft.Text, str, int]] = []

        cache = getattr(player_bar, "_lyrics_cache", None)
        self._cache: dict[tuple, Any] = cache if isinstance(cache, dict) else {}

        self._scroll_col = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            spacing=12 if large else 8,
            expand=True,
        )
        self._state_box = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.symmetric(horizontal=16),
        )
        self._source_txt = app_text(
            "Fuente · —",
            size=10,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
        )
        self._footer = ft.Container(
            padding=ft.Padding.only(top=6),
            border=ft.Border.only(top=ft.BorderSide(0.6, BORDER_MUTED)),
            content=ft.Row(
                controls=[self._source_txt],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            visible=False,
        )
        self.content = ft.Column(
            controls=[
                ft.Stack(
                    controls=[self._scroll_col, self._state_box],
                    expand=True,
                    fit=ft.StackFit.EXPAND,
                ),
                self._footer,
            ],
            spacing=4,
            expand=True,
        )

        sub_pos = getattr(player_bar, "subscribe_position", None)
        if callable(sub_pos):
            sub_pos(self._on_position)
        self._render_state("empty")

    @property
    def actively_visible(self) -> bool:
        return self._actively_visible

    def dispose(self) -> None:
        unsub_pos = getattr(self.player_bar, "unsubscribe_position", None)
        if callable(unsub_pos):
            unsub_pos(self._on_position)

    def set_active_visibility(self, visible_flag: bool) -> None:
        self._actively_visible = bool(visible_flag)
        if self._actively_visible:
            self.refresh_track()
            pos_ms = int(getattr(self.player_bar, "position_ms", 0) or 0)
            self.sync_position(pos_ms, force=True)

    @staticmethod
    def _track_key(cur: PlayableTrack | None) -> tuple | None:
        if cur is None:
            return None
        tr = cur.track
        return (cur.path, tr.id, tr.name, tr.artist)

    def refresh_track(self, *, force_reload: bool = False) -> None:
        if not _lyrics_enabled():
            self._fetch_gen += 1
            self._loaded_key = None
            self._result = None
            self._render_state("disabled")
            return

        cur = self.player_bar.queue.current
        key = self._track_key(cur)
        if key is None:
            self._fetch_gen += 1
            self._loaded_key = None
            self._result = None
            self._render_state("empty")
            return

        if not force_reload and key == self._loaded_key and self._status != "empty":
            return

        # Invalida cualquier consulta en vuelo de la pista anterior, aunque la
        # nueva se resuelva desde caché o la carga se difiera.
        self._fetch_gen += 1

        if not force_reload and key in self._cache:
            self._loaded_key = key
            self._apply_result(self._cache[key])
            return

        if not self._actively_visible and not force_reload:
            return

        self._loaded_key = key
        self._start_fetch(cur, key)

    def _start_fetch(self, cur: PlayableTrack, key: tuple) -> None:
        lyrics_svc = _import_lyrics_service()
        if lyrics_svc is None:
            self._result = None
            self._render_state("none")
            return

        gen = self._fetch_gen = self._fetch_gen + 1
        self._render_state("loading")

        tr = cur.track
        dur_ms = int(cur.duration_ms or getattr(self.player_bar, "duration_ms", 0) or 0)
        allow_net = _lyrics_allow_network()

        async def _load() -> None:
            try:
                res = await asyncio.to_thread(
                    lyrics_svc.get_lyrics,
                    tr,
                    audio_path=cur.path,
                    title=tr.name,
                    artist=tr.artist,
                    album=tr.album,
                    duration_ms=dur_ms,
                    allow_network=allow_net,
                )
            except Exception as exc:
                if gen != self._fetch_gen:
                    return
                self._error_msg = str(exc) or "Error consultando letras."
                self._render_state("error")
                return

            if gen != self._fetch_gen:
                return
            if res is not None and not getattr(res, "error", ""):
                self._cache[key] = res
            self._apply_result(res)

        try:
            asyncio.get_running_loop().create_task(_load())
        except RuntimeError:
            run_task = getattr(self._page_ref, "run_task", None)
            if callable(run_task):
                try:
                    run_task(_load)
                except Exception:
                    self._render_state("none")

    def _apply_result(self, res: Any) -> None:
        self._result = res
        if res is None:
            self._render_state("none")
            return
        err = getattr(res, "error", "")
        if err:
            self._error_msg = str(err)
            self._render_state("error")
            return
        if getattr(res, "instrumental", False):
            self._render_state("instrumental")
            return
        lines = list(getattr(res, "lines", []) or [])
        plain_text = str(getattr(res, "plain_text", "") or "").strip()
        if getattr(res, "synced", False) and lines:
            self._render_synced(lines, str(getattr(res, "source", "") or ""))
            pos_ms = int(getattr(self.player_bar, "position_ms", 0) or 0)
            self.sync_position(pos_ms, force=True)
            return
        if plain_text or lines:
            plain_lines = (
                [ln for ln in plain_text.splitlines() if ln.strip()]
                if plain_text
                else [str(getattr(l, "text", "") or "") for l in lines if str(getattr(l, "text", "") or "").strip()]
            )
            if plain_lines:
                self._render_plain(plain_lines, str(getattr(res, "source", "") or ""))
                return
        self._render_state("none")

    def _render_state(self, kind: str) -> None:
        self._status = kind
        self._active_idx = -1
        self._line_controls.clear()
        self._scroll_col.controls = []
        self._scroll_col.visible = False
        self._state_box.visible = True
        self._footer.visible = False

        if kind == "empty":
            icon, title, sub = PIcons.MUSIC_NOTES, "Sin reproducción", "Reproduce una pista para ver su letra."
            extra = None
        elif kind == "disabled":
            icon, title, sub = PIcons.MUSIC_NOTES, "Letras desactivadas", "Actívalas en Configuración → Reproducción."
            extra = None
        elif kind == "loading":
            icon, title, sub = PIcons.ARROWS_CLOCKWISE, "Buscando letra…", "Consultando archivo local y proveedor."
            extra = None
        elif kind == "instrumental":
            icon, title, sub = PIcons.DISC, "Instrumental", "Esta pista está marcada como instrumental."
            extra = None
        elif kind == "error":
            icon, title, sub = PIcons.WARNING, "No se pudo obtener la letra", self._error_msg or "Error de red o proveedor."
            extra = _ghost_btn("Reintentar", PIcons.ARROWS_CLOCKWISE, lambda _e: self.refresh_track(force_reload=True), height=32)
        else:
            icon, title, sub = PIcons.MUSIC_NOTES, "Sin letra disponible", "Ni el archivo ni el proveedor la tienen."
            extra = _ghost_btn("Reintentar", PIcons.ARROWS_CLOCKWISE, lambda _e: self.refresh_track(force_reload=True), height=32)

        col_controls: list[ft.Control] = [
            app_icon(icon, size=36, color=ACCENT if kind == "loading" else TEXT_DIM, fill=(kind != "loading")),
            app_text(title, size=14 if self._large else 13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD, text_align=ft.TextAlign.CENTER),
            app_text(sub, size=12 if self._large else 11, color=TEXT_MUTED, font_family=FONT_TEXT, text_align=ft.TextAlign.CENTER),
        ]
        if extra is not None:
            col_controls.append(ft.Container(padding=ft.Padding.only(top=6), content=extra))

        self._state_box.content = ft.Column(
            controls=col_controls,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=6,
            tight=True,
        )
        self._safe_update()

    def _render_plain(self, lines: list[str], source: str) -> None:
        self._status = "plain"
        self._active_idx = -1
        self._line_controls.clear()
        size = 19 if self._large else 13
        self._scroll_col.controls = [
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=4, vertical=4),
                content=app_text(
                    line,
                    size=size,
                    color=TEXT_PRIMARY,
                    font_family=font_family_for(line),
                    opacity=0.88,
                ),
            )
            for line in lines
        ]
        self._scroll_col.visible = True
        self._state_box.visible = False
        self._source_txt.value = f"Fuente · {_SOURCE_LABELS.get(source, source or 'Local')}"
        self._footer.visible = True
        self._safe_update()

    def _render_synced(self, lines: list[Any], source: str) -> None:
        self._status = "synced"
        self._active_idx = -1
        self._line_controls.clear()
        size = 24 if self._large else 15
        pad_v = 96 if self._large else 44
        reduce = _is_reduce_motion()
        seek_fn = getattr(self.player_bar, "seek_to", None)

        controls: list[ft.Control] = [ft.Container(height=pad_v)]
        for idx, line in enumerate(lines):
            text = str(getattr(line, "text", "") or "").strip() or "♪"
            time_ms = int(getattr(line, "time_ms", 0) or 0)
            txt = app_text(
                text,
                size=size,
                color=TEXT_MUTED,
                font_family=font_family_for(text),
            )
            row = ft.Container(
                key=ft.ScrollKey(f"lyr_{idx}"),
                opacity=0.35,
                animate_opacity=None if reduce else ft.Animation(MOTION_ENTER_MS, ft.AnimationCurve.EASE_OUT),
                padding=ft.Padding.symmetric(horizontal=6, vertical=4 if self._large else 2),
                border_radius=RADIUS_BUTTON - 2,
                ink=callable(seek_fn),
                on_click=(lambda _e, ms=time_ms: seek_fn(ms)) if callable(seek_fn) else None,
                content=txt,
            )
            self._line_controls.append((row, txt, text, time_ms))
            controls.append(row)
        controls.append(ft.Container(height=pad_v))

        self._scroll_col.controls = controls
        self._scroll_col.visible = True
        self._state_box.visible = False
        self._source_txt.value = f"Fuente · {_SOURCE_LABELS.get(source, source or 'Sincronizada')}"
        self._footer.visible = True
        self._safe_update()

    def _on_position(self, pos_ms: int, _dur_ms: int) -> None:
        if not self._actively_visible:
            return
        self.sync_position(pos_ms, force=False)

    def sync_position(self, pos_ms: int, *, force: bool = False) -> None:
        if self._status != "synced" or not self._line_controls:
            return
        lines = getattr(self._result, "lines", None) or []
        idx = _active_line_index(lines, pos_ms, _import_lyrics_service())
        if idx == self._active_idx and not force:
            return
        self._active_idx = idx
        for i, (row, txt, raw_text, _ms) in enumerate(self._line_controls):
            if idx < 0:
                row.opacity = 0.45
                txt.color = TEXT_MUTED
                txt.font_family = font_family_for(raw_text)
                continue
            dist = abs(i - idx)
            if dist == 0:
                row.opacity = 1.0
                txt.color = TEXT_PRIMARY
                txt.font_family = font_family_for(raw_text, "bold")
            elif dist == 1:
                row.opacity = 0.55
                txt.color = TEXT_PRIMARY
                txt.font_family = font_family_for(raw_text)
            elif dist == 2:
                row.opacity = 0.34
                txt.color = TEXT_MUTED
                txt.font_family = font_family_for(raw_text)
            else:
                row.opacity = 0.22
                txt.color = TEXT_MUTED
                txt.font_family = font_family_for(raw_text)

        self._safe_update()

        if 0 <= idx < len(self._line_controls) and self._actively_visible and is_mounted(self._scroll_col):
            dur = 0 if (force or _is_reduce_motion()) else MOTION_PANEL_MS
            try:
                coro = self._scroll_col.scroll_to(
                    scroll_key=f"lyr_{idx}",
                    duration=dur,
                    curve=ft.AnimationCurve.EASE_IN_OUT,
                )
                _schedule_coro(self._page_ref, coro)
            except Exception:
                pass

    def _safe_update(self) -> None:
        safe_update(self)


def _load_queue_prefs() -> tuple[bool, str]:
    """Lee estado abierto y pestaña activa del panel desde ``settings_store``."""
    getter_open = getattr(settings_store, "get_queue_open", None)
    getter_tab = getattr(settings_store, "get_queue_tab", None)
    if callable(getter_open) and callable(getter_tab):
        try:
            tab = str(getter_tab() or _TAB_COLA).lower()
            return bool(getter_open()), tab if tab in _VALID_TABS else _TAB_COLA
        except Exception:
            return False, _TAB_COLA
    try:
        data = settings_store.load_settings()
    except Exception:
        return False, _TAB_COLA
    is_open = bool(data.get(_KEY_QUEUE_OPEN, False))
    tab = str(data.get(_KEY_QUEUE_TAB, _TAB_COLA) or _TAB_COLA).lower()
    return is_open, tab if tab in _VALID_TABS else _TAB_COLA


def _save_queue_prefs(*, is_open: bool | None = None, tab: str | None = None) -> None:
    """Persiste estado abierto y pestaña activa del panel en ``settings_store``."""
    remaining_open = is_open
    remaining_tab = tab if tab in _VALID_TABS else None

    if remaining_open is not None:
        setter_open = getattr(settings_store, "set_queue_open", None)
        if callable(setter_open):
            try:
                setter_open(bool(remaining_open))
                remaining_open = None
            except Exception:
                pass
    if remaining_tab is not None:
        setter_tab = getattr(settings_store, "set_queue_tab", None)
        if callable(setter_tab):
            try:
                setter_tab(remaining_tab)
                remaining_tab = None
            except Exception:
                pass

    if remaining_open is None and remaining_tab is None:
        return
    try:
        data = settings_store.load_settings()
        changed = False
        if remaining_open is not None and data.get(_KEY_QUEUE_OPEN) != bool(remaining_open):
            data[_KEY_QUEUE_OPEN] = bool(remaining_open)
            changed = True
        if remaining_tab is not None and data.get(_KEY_QUEUE_TAB) != remaining_tab:
            data[_KEY_QUEUE_TAB] = remaining_tab
            changed = True
        if changed:
            settings_store.save_settings(data)
    except Exception:
        pass


class PlayerQueuePanel(ft.Container):
    """Panel lateral derecho de cola y letra (modo empuje ``0 ↔ PLAYER_QUEUE_W``)."""

    def __init__(
        self,
        page: ft.Page,
        player_bar,
        *,
        on_open_change: Callable[[bool], None] | None = None,
    ) -> None:
        init_open, init_tab = _load_queue_prefs()
        super().__init__(
            width=PLAYER_QUEUE_W if init_open else 0,
            bgcolor=BG_PANEL,
            border=ft.Border.only(left=ft.BorderSide(0.8, BORDER_MUTED)),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            animate=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            visible=init_open,
        )
        self._page_ref = page
        self.player_bar = player_bar
        self._on_open_change = on_open_change
        self._is_open: bool = init_open
        self._active_tab: str = init_tab
        self._open_gen: int = 0

        self._count_txt = app_text(
            "0 pistas", size=11, color=TEXT_MUTED, font_family=FONT_TEXT
        )
        self._clear_btn = _ghost_btn(
            "Limpiar",
            PIcons.TRASH,
            lambda _e: self.player_bar.clear_queue(),
            height=30,
        )
        self._close_btn = app_icon_button(
            PIcons.X,
            size=16,
            icon_color=TEXT_MUTED,
            tooltip="Cerrar panel",
            on_click=lambda _e: self.set_open(False),
            width=30,
            height=30,
        )

        self._tab_refs: dict[str, tuple[ft.Container, ft.Text, ft.Text]] = {}
        tab_controls: list[ft.Control] = []
        for tab_key, label_text, glyph in _TAB_SPECS:
            ico = app_icon(glyph, size=14, color=TEXT_DIM, fill=False)
            lbl = app_text(label_text, size=12, color=TEXT_MUTED, font_family=FONT_HEADLINE)
            btn = ft.Container(
                expand=True,
                height=30,
                border_radius=RADIUS_BUTTON - 2,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
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

        self._tab_bar = ft.Container(
            expand=True,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_BUTTON,
            padding=ft.Padding.all(2),
            content=ft.Row(
                controls=tab_controls,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        self._list_view = ft.ReorderableListView(
            item_extent=ITEM_H,
            build_controls_on_demand=True,
            show_default_drag_handles=False,
            on_reorder=self._on_reorder,
            expand=True,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )

        self._empty_box = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                controls=[
                    app_icon(PIcons.QUEUE, size=40, color=TEXT_DIM, fill=True),
                    app_text(
                        "Cola vacía",
                        size=13,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_BOLD,
                    ),
                    app_text(
                        "Reproduce una pista desde Descargas o Biblioteca Local",
                        size=11,
                        color=TEXT_MUTED,
                        font_family=FONT_TEXT,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
        )

        self._cola_view = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                section_label("COLA DE REPRODUCCIÓN"),
                                self._count_txt,
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        self._clear_btn,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Stack(
                    controls=[self._list_view, self._empty_box],
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
            large=False,
            initially_visible=(init_open and self._active_tab == _TAB_LETRA),
        )
        self._letra_view.visible = (self._active_tab == _TAB_LETRA)

        self._body_box = ft.Container(
            width=PLAYER_QUEUE_W,
            opacity=1.0 if init_open else 0.0,
            animate_opacity=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            padding=ft.Padding.all(12),
            expand=True,
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[self._tab_bar, self._close_btn],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=BORDER_MUTED),
                    ft.Stack(
                        controls=[self._cola_view, self._letra_view],
                        expand=True,
                        fit=ft.StackFit.EXPAND,
                    ),
                ],
                spacing=8,
                expand=True,
            ),
        )
        self.content = self._body_box
        self._apply_tab_styles()
        self.player_bar.subscribe(self.refresh)
        self.refresh()

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def active_tab(self) -> str:
        return self._active_tab

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
        normalized = str(tab or _TAB_COLA).lower()
        if normalized not in _VALID_TABS:
            normalized = _TAB_COLA
        self._active_tab = normalized
        _save_queue_prefs(tab=normalized)
        self._apply_tab_styles()
        self._letra_view.set_active_visibility(self._is_open and self._active_tab == _TAB_LETRA)
        try:
            self.update()
        except Exception:
            pass

    def toggle(self) -> None:
        self.set_open(not self._is_open)

    def set_open(self, open_state: bool) -> None:
        target = bool(open_state)
        self._is_open = target
        _save_queue_prefs(is_open=target)
        self._letra_view.set_active_visibility(target and self._active_tab == _TAB_LETRA)
        if callable(self._on_open_change):
            try:
                self._on_open_change(target)
            except Exception:
                pass
        self._open_gen += 1
        gen = self._open_gen
        if target:
            if self.visible and self.width == PLAYER_QUEUE_W and self._body_box.opacity == 1.0:
                return
            # Primero se monta en el árbol con ancho 0; la animación de entrada
            # parte del frame siguiente para interpolar ancho 0 -> PLAYER_QUEUE_W.
            self.width = 0
            self._body_box.opacity = 0.0
            self.visible = True
            try:
                self.update()
            except Exception:
                pass
            try:
                self._page_ref.run_task(self._reveal_open, gen)
            except Exception:
                self._apply_open_state()
        else:
            self.width = 0
            self._body_box.opacity = 0.0
            try:
                self.update()
            except Exception:
                pass
            try:
                self._page_ref.run_task(self._hide_after_close, gen)
            except Exception:
                self.visible = False
                try:
                    self.update()
                except Exception:
                    pass

    def _apply_open_state(self) -> None:
        self.width = PLAYER_QUEUE_W
        self._body_box.opacity = 1.0
        try:
            self.update()
        except Exception:
            pass

    async def _reveal_open(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._open_gen or not self._is_open:
            return
        self._apply_open_state()

    async def _hide_after_close(self, gen: int) -> None:
        await asyncio.sleep(MOTION_PANEL_MS / 1000)
        if gen != self._open_gen or self._is_open:
            return
        self.visible = False
        try:
            self.update()
        except Exception:
            pass

    def _on_reorder(self, e: ft.OnReorderEvent) -> None:
        old_idx = getattr(e, "old_index", None)
        new_idx = getattr(e, "new_index", None)
        if old_idx is None or new_idx is None:
            return
        self.player_bar.move_queue_item(int(old_idx), int(new_idx))

    def _build_row(self, queue_pos: int, item: PlayableTrack, is_current: bool) -> ft.Container:
        return build_queue_row(queue_pos, item, is_current, self.player_bar, reorderable=True)

    def refresh(self) -> None:
        q = self.player_bar.queue
        items = q.ordered_tracks()
        cur = q.current
        self._count_txt.value = f"{len(items)} {'pista' if len(items) == 1 else 'pistas'}"
        self._clear_btn.disabled = not items
        self._list_view.controls = [
            self._build_row(idx, pt, pt is cur)
            for idx, pt in enumerate(items)
        ]
        self._empty_box.visible = not items
        self._list_view.visible = bool(items)
        self._letra_view.refresh_track()
        try:
            self.update()
        except Exception:
            pass
