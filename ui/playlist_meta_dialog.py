"""
ui/playlist_meta_dialog.py — MelomaniacPass v3.3.4

Diálogo de personalización de playlist (nombre + descripción).

Unificado con el resto del código (ui/main_ui.py, ui/config_wizard.py):
usa ft.AlertDialog modal + page.show_dialog, sin Stack/overlay manual.
El Stack posicionado era el que re-medía los TextFields y les daba
anchos distintos; con AlertDialog el content fija el ancho una vez y
ambos fields rellenan igual.

Uso:
    from ui.playlist_meta_dialog import PlaylistMetaDialog
    meta = await PlaylistMetaDialog(page,
        default_title=state.playlist_name,
        default_description=state.playlist_description).show()
    if not meta.confirmed:
        return
    await state.transfer_playlist(title_override=meta.title,
                                  description_override=meta.description)
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass

import flet as ft

from ui.tokens import (
    BG_PANEL, BG_INPUT, BORDER_LIGHT,
    ACCENT, TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM,
)
from ui.widgets import _section_label

CONTENT_W = 420


@dataclass
class PlaylistMetaResult:
    confirmed: bool
    title: str
    description: str


class PlaylistMetaDialog:
    """Modal AlertDialog para editar nombre/descripción antes de crear."""

    def __init__(self, page: ft.Page, *, default_title: str,
                 default_description: str = "") -> None:
        self.page = page
        # Las APIs traen la descripción con \n, \t, espacios múltiples y
        # a veces entidades HTML (Spotify). _as_text solo recorta los
        # extremos, así que aquí se colapsa todo a una línea: si no, el
        # TextField multilínea crece hasta max_lines por saltos
        # incrustados y parece de distinto tamaño aunque el width sea igual.
        self._default_title = self._clean(default_title)
        self._default_description = self._clean(default_description)
        self._future: asyncio.Future[PlaylistMetaResult] | None = None
        self._dlg: ft.AlertDialog | None = None
        self._title_field: ft.TextField | None = None
        self._desc_field: ft.TextField | None = None

    # ── API pública ──────────────────────────────────────────────

    @staticmethod
    def _clean(value: str | None) -> str:
        """Colapsa la metadata cruda a una línea editable."""
        if not value:
            return ""
        text = html.unescape(value)
        text = re.sub(r"<[^>]+>", "", text)  # por si Spotify trae <a> tags
        return re.sub(r"\s+", " ", text).strip()

    async def show(self) -> PlaylistMetaResult:
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        self._build()
        assert self._dlg is not None
        self.page.show_dialog(self._dlg)
        try:
            return await self._future
        finally:
            self._close()

    # ── Construcción (patrón main_ui._ask_playlist_name_then_ingest) ──

    @staticmethod
    def _field_style() -> dict:
        return {
            "bgcolor": BG_INPUT,
            "border_color": BORDER_LIGHT,
            "focused_border_color": ACCENT,
            "hint_style": ft.TextStyle(color=TEXT_DIM, size=11),
            "label_style": ft.TextStyle(color=TEXT_MUTED, size=10),
            "text_style": ft.TextStyle(color=TEXT_PRIMARY, size=12),
            "text_size": 12,
            "dense": True,
            "border_radius": 10,
            "content_padding": ft.Padding.symmetric(horizontal=12, vertical=10),
        }

    def _build(self) -> None:
        self._title_field = ft.TextField(
            value=self._default_title,
            hint_text="Nombre de la playlist",
            autofocus=True,
            multiline=False,
            width=CONTENT_W,
            **self._field_style(),
            on_submit=lambda _: self._on_confirm(None),
        )
        self._desc_field = ft.TextField(
            value=self._default_description,
            hint_text="Descripción (opcional)",
            multiline=True,
            min_lines=2,
            max_lines=3,
            width=CONTENT_W,
            **self._field_style(),
        )
        self._dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.EDIT, color=ACCENT, size=18),
                    ft.Text(
                        "Personalizar playlist",
                        size=14, color=TEXT_PRIMARY,
                        font_family="IBM Plex Sans Bold",
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        _section_label("NOMBRE"),
                        self._title_field,
                        _section_label("DESCRIPCIÓN"),
                        self._desc_field,
                    ],
                    spacing=8,
                    tight=True,
                    width=CONTENT_W,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                width=CONTENT_W,
                padding=ft.Padding.symmetric(horizontal=4, vertical=8),
            ),
            actions=[
                ft.TextButton(
                    "Crear y transferir",
                    icon=ft.Icons.SWAP_HORIZ,
                    on_click=self._on_confirm,
                    style=ft.ButtonStyle(
                        color={ft.ControlState.DEFAULT: ACCENT}),
                ),
                ft.TextButton(
                    "Cancelar",
                    on_click=self._on_cancel,
                    style=ft.ButtonStyle(
                        color={ft.ControlState.DEFAULT: TEXT_MUTED}),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=BG_PANEL,
            shape=ft.RoundedRectangleBorder(radius=14),
        )

    # ── Eventos ──────────────────────────────────────────────────

    def _finish(self, result: PlaylistMetaResult) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_result(result)

    def _on_confirm(self, _e) -> None:
        title = ((self._title_field.value or "") if self._title_field else "")
        desc = ((self._desc_field.value or "") if self._desc_field else "")
        self._finish(PlaylistMetaResult(
            confirmed=True, title=title.strip(), description=desc.strip()))

    def _on_cancel(self, _e) -> None:
        self._finish(PlaylistMetaResult(
            confirmed=False,
            title=self._default_title,
            description=self._default_description,
        ))

    def _close(self) -> None:
        try:
            if self._dlg is not None:
                self._dlg.open = False
                self.page.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        finally:
            self._dlg = None
