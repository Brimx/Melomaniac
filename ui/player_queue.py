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
import os

import flet as ft

from core.playback import PlayableTrack
from services import settings_store
from ui.theme.fonts import (
    FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_PHOSPHOR, FONT_PHOSPHOR_FILL,
    FONT_TEXT, font_family_for, mono_family,
)
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.song_row import ITEM_H
from ui.theme.tokens import (
    ACCENT, ACCENT_HALO, BG_PANEL, BG_SURFACE, BORDER_MUTED,
    CHIP_BG, MOTION_HOVER_MS, MOTION_PANEL_MS, PLAYER_QUEUE_W,
    RADIUS_BUTTON, RADIUS_INPUT, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, app_text, section_label

_KEY_QUEUE_OPEN = "queue_panel_open"
_KEY_QUEUE_TAB = "queue_panel_tab"
_TAB_COLA = "cola"
_TAB_LETRA = "letra"
_VALID_TABS = (_TAB_COLA, _TAB_LETRA)
_TAB_SPECS = (
    (_TAB_COLA, "Cola", PIcons.QUEUE),
    (_TAB_LETRA, "Letra", PIcons.MUSIC_NOTES),
)


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

        self._letra_view = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            visible=(self._active_tab == _TAB_LETRA),
            content=ft.Column(
                controls=[
                    app_icon(PIcons.MUSIC_NOTES, size=40, color=TEXT_DIM, fill=True),
                    app_text(
                        "Letra",
                        size=13,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_BOLD,
                    ),
                    app_text(
                        "Las letras de la pista actual aparecerán aquí próximamente.",
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
        tr = item.track
        title = tr.name or os.path.basename(item.path)
        artist = tr.artist or "Archivo local"
        ext_badge = (item.container or "AUDIO").upper()
        stable_key = f"qrow_{id(item)}_{item.path}"

        drag_handle = ft.ReorderableDragHandle(
            mouse_cursor=ft.MouseCursor.GRAB,
            content=ft.Container(
                width=24,
                height=ITEM_H - 12,
                alignment=ft.Alignment.CENTER,
                content=app_icon(PIcons.DOTS_SIX_VERTICAL, size=16, color=TEXT_DIM),
            ),
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
            on_click=lambda _e, pos=queue_pos: self.player_bar.select_queue_index(pos),
            content=lead_icon,
        )

        meta = ft.Container(
            expand=True,
            ink=True,
            on_click=lambda _e, pos=queue_pos: self.player_bar.select_queue_index(pos),
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
            on_click=lambda _e, pos=queue_pos: self.player_bar.remove_queue_index(pos),
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
                controls=[drag_handle, lead_btn, meta, fmt_chip, remove_btn],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

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
        try:
            self.update()
        except Exception:
            pass
