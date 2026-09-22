"""
ui/playlist_meta_dialog.py — Melomaniac v4.5.1

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
from ui.widgets import _section_label, app_text_field, dialog_action, app_dialog, DialogMixin

CONTENT_W = 420


@dataclass
class PlaylistMetaResult:
    confirmed: bool
    title: str
    description: str
    delete_original: bool = False
    per_division_destinations: dict[str, str] | None = None


class PlaylistMetaDialog(DialogMixin):
    """Modal AlertDialog para editar nombre/descripción antes de crear."""

    def __init__(self, page: ft.Page, *, default_title: str,
                 default_description: str = "", source_requires_auth: bool = True,
                 divisions: list[str] | None = None, global_destination: str | None = None) -> None:
        self.page = page
        # Las APIs traen la descripción con \n, \t, espacios múltiples y
        # a veces entidades HTML (Spotify). _as_text solo recorta los
        # extremos, así que aquí se colapsa todo a una línea: si no, el
        # TextField multilínea crece hasta max_lines por saltos
        # incrustados y parece de distinto tamaño aunque el width sea igual.
        self._default_title = self._clean(default_title)
        self._default_description = self._clean(default_description)
        self._source_requires_auth = source_requires_auth
        self._divisions = divisions
        self._global_destination = global_destination
        self._per_div_dests: dict[str, str] = {}
        self._visited: set[str] = set()
        self._future: asyncio.Future[PlaylistMetaResult] | None = None
        self._dlg: ft.AlertDialog | None = None
        self._title_field: ft.TextField | None = None
        self._desc_field: ft.TextField | None = None
        self._delete_chk: ft.Checkbox | None = None
        self._div_switch: ft.Dropdown | None = None
        self._dest_dd: ft.Dropdown | None = None
        self._primary_btn: ft.TextButton | None = None

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
        # Compat: estilo canónico vive en widgets.app_text_field.
        from ui.widgets import app_text_field
        probe = app_text_field(label="x")
        return {
            "bgcolor": probe.bgcolor,
            "border_color": probe.border_color,
            "focused_border_color": probe.focused_border_color,
            "hint_style": probe.hint_style,
            "label_style": probe.label_style,
            "text_style": probe.text_style,
            "text_size": probe.text_size,
            "dense": True,
            "border_radius": probe.border_radius,
            "content_padding": probe.content_padding,
        }

    def _build(self) -> None:
        self._title_field = app_text_field(
            value=self._default_title,
            hint_text="Nombre de la playlist",
            autofocus=True,
            multiline=False,
            width=CONTENT_W,
            on_submit=lambda _: self._on_next_or_confirm(None),
        )
        self._desc_field = app_text_field(
            value=self._default_description,
            hint_text="Descripción (opcional)",
            multiline=True,
            min_lines=2,
            max_lines=3,
            width=CONTENT_W,
        )
        self._delete_chk = ft.Checkbox(
            label="Eliminar original (avanzado, requiere sesión activa)",
            value=False,
            fill_color={ft.ControlState.SELECTED: ACCENT},
            check_color=TEXT_PRIMARY,
            label_style=ft.TextStyle(color=TEXT_MUTED, size=11, font_family="IBM Plex Sans"),
            border_side=ft.BorderSide(1.2, TEXT_DIM),
            visible=True,
        )
        if not self._source_requires_auth:
            self._delete_chk.disabled = True
            self._delete_chk.label = "Eliminar original (no disponible para local)"
        controls: list[ft.Control] = []
        # División hasta arriba, antes de nombre/descripción (solo Dividir)
        if self._divisions:
            for div in self._divisions:
                self._per_div_dests[div] = "Mantener"
            self._visited.add(self._divisions[0])
            self._div_switch = ft.Dropdown(
                label="División", value=self._divisions[0], width=CONTENT_W, height=38,
                bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
                label_style=ft.TextStyle(color=TEXT_MUTED, size=10, font_family="IBM Plex Sans"),
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
                border_radius=10,
                options=[ft.dropdown.Option(d, d) for d in self._divisions],
            )
            from core.config import PLATFORMS as _PLATS
            dest_opts = ["Mantener"] + [p for p in _PLATS if p != self._global_destination]
            self._dest_dd = ft.Dropdown(
                label="Destino para esta división", value="Mantener", width=CONTENT_W, height=38,
                bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
                label_style=ft.TextStyle(color=TEXT_MUTED, size=10, font_family="IBM Plex Sans"),
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
                border_radius=10,
                options=[ft.dropdown.Option(o, o) for o in dest_opts],
            )
            def _on_div_change(e):
                cur = e.control.value
                try:
                    self._save_current_div()
                    self._dest_dd.value = self._per_div_dests.get(cur, "Mantener")
                    self._dest_dd.update()
                    self._visited.add(cur)
                    self._refresh_primary_label()
                except: pass
            def _on_dest_change(e):
                cur_div = self._div_switch.value if self._div_switch else self._divisions[0]
                self._per_div_dests[cur_div] = e.control.value
            self._div_switch.on_select = _on_div_change  # type: ignore
            self._dest_dd.on_select = _on_dest_change  # type: ignore
            controls += [
                _section_label("DIVISIÓN"),
                self._div_switch,
                self._dest_dd,
                ft.Text("Mantener = usa destino global elegido fuera; otras opciones son las dos plataformas restantes.", size=9, color=TEXT_DIM, font_family="IBM Plex Sans"),
            ]
        controls += [
            _section_label("NOMBRE"),
            self._title_field,
            _section_label("DESCRIPCIÓN"),
            self._desc_field,
            self._delete_chk,
            ft.Text("Desmarcado por defecto — crea nueva sin borrar original.", size=9, color=TEXT_DIM, font_family="IBM Plex Sans"),
        ]
        # Botón Guardar + primario Siguiente/Crear
        self._primary_btn = dialog_action("Siguiente" if self._divisions and len(self._divisions) > 1 else "Crear y transferir", self._on_next_or_confirm, kind="primary", icon=ft.Icons.SWAP_HORIZ)
        self._dlg = app_dialog(
            "Personalizar playlist",
            ft.Column(
                controls=controls,
                spacing=8,
                tight=True,
                width=CONTENT_W,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            [
                self._primary_btn,
                dialog_action("Guardar", self._on_save, kind="muted"),
                dialog_action("Cancelar", self._on_cancel, kind="muted"),
            ],
            width=CONTENT_W,
            icon=ft.Icons.EDIT,
        )

    # ── Eventos ──────────────────────────────────────────────────

    def _finish(self, result: PlaylistMetaResult) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_result(result)

    def _save_current_div(self) -> None:
        try:
            if self._divisions and self._div_switch and self._dest_dd:
                cur = self._div_switch.value
                if cur:
                    self._per_div_dests[cur] = self._dest_dd.value or "Mantener"
                    self._visited.add(cur)
        except: pass

    def _refresh_primary_label(self) -> None:
        try:
            if not self._divisions or not self._primary_btn:
                return
            all_seen = len(self._visited) >= len(self._divisions)
            label = "Crear y transferir" if all_seen else "Siguiente"
            # dialog_action returns TextButton with text attr
            try:
                self._primary_btn.text = label  # type: ignore
            except Exception:
                pass
            try:
                self._primary_btn.update()
            except Exception:
                pass
        except: pass

    def _on_save(self, _e) -> None:
        self._save_current_div()
        self._refresh_primary_label()
        try:
            self.page.update()
        except: pass

    def _on_next_or_confirm(self, _e) -> None:
        # Si hay divisiones sin visitar, avanza a la siguiente en vez de confirmar
        if self._divisions and self._div_switch:
            self._save_current_div()
            remaining = [d for d in self._divisions if d not in self._visited]
            if remaining:
                nxt = remaining[0]
                try:
                    self._div_switch.value = nxt
                    self._div_switch.update()
                    self._dest_dd.value = self._per_div_dests.get(nxt, "Mantener")
                    self._dest_dd.update()
                    self._visited.add(nxt)
                    self._refresh_primary_label()
                    self.page.update()
                except: pass
                return
        self._on_confirm(None)

    def _on_confirm(self, _e) -> None:
        title = ((self._title_field.value or "") if self._title_field else "")
        desc = ((self._desc_field.value or "") if self._desc_field else "")
        delete = bool(self._delete_chk.value) if self._delete_chk else False
        self._save_current_div()
        self._finish(PlaylistMetaResult(
            confirmed=True, title=title.strip(), description=desc.strip(), delete_original=delete, per_division_destinations=dict(self._per_div_dests) if self._per_div_dests else None))

    def _on_cancel(self, _e) -> None:
        self._finish(PlaylistMetaResult(
            confirmed=False,
            title=self._default_title,
            description=self._default_description,
            delete_original=False,
        ))

    def _close(self) -> None:
        try:
            self.close_dialog(self.page, self._dlg)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        finally:
            self._dlg = None
