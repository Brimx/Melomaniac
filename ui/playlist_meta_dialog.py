"""
ui/playlist_meta_dialog.py — MelomaniacPass v3.3.4

Diálogo de personalización de playlist (nombre + descripción).

Patrón tomado de ConfigWizard (ui/config_wizard.py): overlay modal con
backdrop + tarjeta centrada, pero sin AlertDialog para poder animar
(scale + opacity + offset) y colocar el layer de marca detrás. El
backdrop vive como hijo directo del Stack raíz: recibe el clic de
cancelación sin GestureDetector, porque el posicionamiento absoluto
debe resolverse en ese nivel en Flet.

Layout (660x250):
    Stack interno capa 0 (fondo, abajo-derecha): icono de marca con
    opacidad baja. Hoy es el icono del programa; cuando exista el
    asset definitivo basta cambiar APP_LOGO_PATH.
    Stack interno capa 1 (frente, izquierda): labels + textbox de
    Nombre y Descripción, alineados a la izquierda.

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
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from ui.tokens import (
    BG_PANEL, BG_INPUT, BORDER_LIGHT,
    ACCENT, TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM,
)
from ui.widgets import _section_label

DIALOG_W = 660
DIALOG_H = 250

# Asset definitivo pendiente (ico/jpg/png). Si no existe, se muestra
# el icono del programa con opacidad baja como placeholder.
APP_LOGO_PATH = Path(__file__).resolve().parent.parent / "resources" / "icon.png"


@dataclass
class PlaylistMetaResult:
    confirmed: bool
    title: str
    description: str


class PlaylistMetaDialog:
    """Overlay modal animado para editar nombre/descripción antes de crear."""

    def __init__(self, page: ft.Page, *, default_title: str,
                 default_description: str = "") -> None:
        self.page = page
        self._default_title = (default_title or "").strip()
        self._default_description = default_description or ""
        self._future: asyncio.Future[PlaylistMetaResult] | None = None
        self._stack: ft.Stack | None = None
        self._backdrop: ft.Container | None = None
        self._card: ft.Container | None = None
        self._title_field: ft.TextField | None = None
        self._desc_field: ft.TextField | None = None

    # ── API pública ──────────────────────────────────────────────

    async def show(self) -> PlaylistMetaResult:
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        self._build()
        assert self._stack is not None
        self.page.overlay.append(self._stack)
        self.page.update()
        # Entrada: fade backdrop + pop de la tarjeta
        assert self._backdrop is not None and self._card is not None
        self._backdrop.opacity = 0.6
        self._card.scale = 1.0
        self._card.opacity = 1.0
        self._card.offset = ft.Offset(0, 0)
        self._backdrop.update()
        self._card.update()
        try:
            return await self._future
        finally:
            self._remove()

    # ── Construcción ─────────────────────────────────────────────

    def _field_style(self) -> dict:
        return {
            "bgcolor": BG_INPUT,
            "border_color": "#18FFFFFF",
            "focused_border_color": ACCENT,
            "label_style": ft.TextStyle(color=TEXT_MUTED, size=10),
            "text_style": ft.TextStyle(color=TEXT_PRIMARY, size=12),
            "border_radius": 8,
        }

    def _build(self) -> None:
        self._title_field = ft.TextField(
            value=self._default_title,
            text_align=ft.TextAlign.LEFT,
            on_submit=lambda _: self._on_confirm(None),
            **self._field_style(),
        )
        self._desc_field = ft.TextField(
            value=self._default_description,
            multiline=True,
            min_lines=2,
            max_lines=3,
            text_align=ft.TextAlign.LEFT,
            **self._field_style(),
        )

        # Capa 0 — marca de fondo abajo-derecha (detrás del form)
        if APP_LOGO_PATH.exists():
            brand_art: ft.Control = ft.Image(
                src=str(APP_LOGO_PATH), fit=ft.BoxFit.CONTAIN,
                width=150, height=150, opacity=0.25,
            )
        else:
            brand_art = ft.Icon(
                ft.Icons.MUSIC_NOTE, size=120, color=TEXT_DIM,
            )
        brand_layer = ft.Container(
            right=0, bottom=0,
            width=220, height=DIALOG_H,
            alignment=ft.Alignment.BOTTOM_RIGHT,
            padding=ft.Padding.only(right=20, bottom=16),
            opacity=0.35,
            content=brand_art,
        )

        # Capa 1 — formulario a la izquierda
        form = ft.Container(
            padding=ft.Padding.only(left=16, right=210, top=12, bottom=12),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.EDIT, color=ACCENT, size=16),
                            ft.Text("Personalizar playlist", size=13,
                                    color=TEXT_PRIMARY,
                                    font_family="IBM Plex Sans Bold"),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    _section_label("NOMBRE"),
                    self._title_field,
                    _section_label("DESCRIPCIÓN"),
                    self._desc_field,
                    ft.Row(
                        controls=[
                            ft.TextButton(
                                "Cancelar",
                                on_click=self._on_cancel,
                                style=ft.ButtonStyle(
                                    color={ft.ControlState.DEFAULT: TEXT_MUTED}),
                            ),
                            ft.ElevatedButton(
                                "Crear y transferir",
                                icon=ft.Icons.SWAP_HORIZ,
                                on_click=self._on_confirm,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.START,
                        spacing=8,
                    ),
                ],
                spacing=6,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        close_btn = ft.Container(
            top=6, right=6,
            content=ft.IconButton(
                icon=ft.Icons.CLOSE, icon_size=16,
                icon_color=TEXT_MUTED, tooltip="Cerrar",
                on_click=self._on_cancel,
            ),
        )

        self._backdrop = ft.Container(
            left=0, top=0, right=0, bottom=0,
            bgcolor="#FF000000",
            opacity=0.0,
            animate_opacity=150,
            ink=False,
            on_click=self._on_cancel,
        )
        self._card = ft.Container(
            width=DIALOG_W, height=DIALOG_H,
            bgcolor=BG_PANEL,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=14,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            scale=0.85,
            opacity=0.0,
            offset=ft.Offset(0, 0.06),
            animate_scale=ft.Animation(250, ft.AnimationCurve.EASE_OUT_BACK),
            animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            animate_offset=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            content=ft.Stack(controls=[brand_layer, form, close_btn]),
        )
        self._stack = ft.Stack(
            controls=[
                # Hijo directo del Stack: el posicionamiento absoluto
                # (left/top/right/bottom) solo es válido aquí o en
                # page.overlay; dentro de un GestureDetector revienta
                # con "Error displaying Container".
                self._backdrop,
                ft.Container(
                    left=0, top=0, right=0, bottom=0,
                    alignment=ft.Alignment.CENTER,
                    content=self._card,
                ),
            ],
            left=0, top=0, right=0, bottom=0,
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

    def _remove(self) -> None:
        try:
            if self._stack is not None and self._stack in self.page.overlay:
                self.page.overlay.remove(self._stack)
                self.page.update()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        finally:
            self._stack = None
