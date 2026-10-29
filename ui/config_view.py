"""
ui/config_view.py — Módulo Config: preferencias persistentes
════════════════════════════════════════════════════════════════════
Vista reducida del cuarto destino del rail. Antes era un placeholder de
"en desarrollo" aunque sus ajustes ya existían en ``settings_store``.

No duplica el wizard de credenciales: ese sigue abriéndose desde el engranaje
del panel izquierdo (``ui/config_wizard.py``). Aquí solo van preferencias que
el usuario cambia sin autenticarse:

- formato de audio por defecto de Descargas
- permitir portadas online (iTunes/Deezer/ISRC)
- carpeta de descargas
- limpieza del caché de portadas
"""

from __future__ import annotations

import os

import flet as ft

from core.config import AUDIO_FORMATS
from services import settings_store
from ui.fonts import FONT_HEADLINE, FONT_TEXT, mono_family
from ui.tokens import (
    ACCENT, BG_LIST, BG_SURFACE, BORDER_MUTED, RADIUS_INPUT,
    TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import (
    _ghost_btn, app_text, notify, organize_dropdown, section_label,
)


class ConfigView(ft.Container):
    """Preferencias persistentes de la aplicación.

    Args:
        page: Página Flet.
        on_change: Callback opcional que se invoca cuando una preferencia
            cambia, para que las vistas afectadas se refresquen.
    """

    def __init__(self, page: ft.Page, on_change=None) -> None:
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self._on_change = on_change
        self._picker = ft.FilePicker()
        page.services.append(self._picker)
        self.content = self._build()

    # ── construcción ───────────────────────────────────────────────────

    def _card(self, title: str, subtitle: str, controls: list[ft.Control]) -> ft.Container:
        """Bloque de ajuste: mismo contenedor para todas las secciones."""
        return ft.Container(
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            content=ft.Column(controls=[
                section_label(title),
                app_text(subtitle, size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
                # Los desplegables llevan label flotante: sin holgura la
                # etiqueta se monta sobre la descripción de la tarjeta.
                ft.Container(height=10),
                *controls,
            ], spacing=4),
        )

    def _build(self) -> ft.Control:
        self._format_dd = organize_dropdown(
            [(fmt, fmt.upper()) for fmt in AUDIO_FORMATS],
            settings_store.get_download_format(),
            "Formato de descarga",
            width=180,
        )
        self._format_dd.on_change = self._on_format_change

        self._covers_sw = ft.Switch(
            label="Resolver portadas online",
            value=settings_store.get_covers_online(),
            active_color=ACCENT,
            on_change=self._on_covers_change,
        )

        # Valor inicial real: sin esto la tarjeta DESTINO nace vacía hasta el
        # primer ``refresh()`` y parece que no hay carpeta configurada.
        self._dir_text = app_text(
            self._download_dir, size=11, color=TEXT_PRIMARY, font_family=mono_family("light"),
            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
        )
        self._dir_reset_btn = _ghost_btn(
            "Restablecer", ft.Icons.RESTART_ALT, self._on_reset_dir,
            width=130, height=36,
        )
        self._dir_reset_btn.visible = bool(settings_store.get_download_dir())

        cache_size = self._cache_size_text()
        self._cache_text = app_text(cache_size, size=11, color=TEXT_MUTED,
                                    font_family=mono_family("light"))
        self._cache_btn = _ghost_btn(
            "Limpiar caché", ft.Icons.DELETE_OUTLINE, self._on_clear_cache,
            width=150, height=36,
        )

        header = ft.Row(controls=[
            ft.Icon(ft.Icons.SETTINGS_OUTLINED, size=18, color=TEXT_MUTED),
            app_text("Configuración", size=16, color=TEXT_PRIMARY,
                     font_family=FONT_HEADLINE),
        ], spacing=8)

        cards = ft.Column(controls=[
            self._card(
                "DESCARGAS",
                "Formato por defecto de la cola. Se puede cambiar por descarga en el módulo Descargas.",
                [self._format_dd],
            ),
            self._card(
                "PORTADAS",
                "Consulta iTunes, Deezer y MusicBrainz por ISRC cuando la portada de la "
                "plataforma no sirve. Desactivarlo mantiene el uso solo en red de descarga.",
                [self._covers_sw],
            ),
            self._card(
                "DESTINO",
                "Carpeta donde se guardan los archivos descargados.",
                [ft.Row(controls=[
                    ft.Icon(ft.Icons.FOLDER_OUTLINED, size=18, color=TEXT_MUTED),
                    self._dir_text,
                    _ghost_btn("Cambiar", ft.Icons.EDIT_OUTLINED, self._on_pick_dir,
                               width=120, height=36),
                    self._dir_reset_btn,
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
            ),
            self._card(
                "MANTENIMIENTO",
                "El caché de portadas deja de usarse si cambias de biblioteca; limpiarlo libera espacio.",
                [ft.Row(controls=[self._cache_text, self._cache_btn],
                        spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
            ),
        ], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

        # Mismo chasis que Descargas: cabecera fija + divisoria + cuerpo con
        # scroll y padding de 16. Sin el contenedor exterior las tarjetas se
        # pegaban al borde de la página y el título flotaba sin margen.
        return ft.Container(
            expand=True,
            padding=ft.Padding.all(16),
            content=ft.Column(controls=[
                header,
                ft.Divider(height=1, color=BORDER_MUTED),
                ft.Container(content=cards, expand=True),
            ], spacing=12, expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
        )

    # ── estado ─────────────────────────────────────────────────────────

    @property
    def _download_dir(self) -> str:
        from engine.downloader import resolve_download_dir
        try:
            return str(resolve_download_dir(
                settings_store.get_download_dir() or None, create=False
            ))
        except Exception:
            return settings_store.get_download_dir() or ""

    @staticmethod
    def _cache_size_text() -> str:
        """Tamaño legible del caché de portadas en disco."""
        from services import cover_cache
        try:
            total = sum(
                f.stat().st_size for f in cover_cache.CACHE_DIR.iterdir() if f.is_file()
            )
        except OSError:
            return "Sin caché de portadas"
        if not total:
            return "Sin caché de portadas"
        return f"{total / (1024 * 1024):.1f} MB en caché de portadas"

    def refresh(self) -> None:
        """Recarga los valores desde ``settings_store``."""
        self._format_dd.value = settings_store.get_download_format()
        self._covers_sw.value = settings_store.get_covers_online()
        self._dir_text.value = self._download_dir
        self._dir_reset_btn.visible = bool(settings_store.get_download_dir())
        self._cache_text.value = self._cache_size_text()
        try:
            self.update()
        except Exception:
            pass

    # ── handlers ───────────────────────────────────────────────────────

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass

    def _on_format_change(self, e=None) -> None:
        settings_store.set_download_format(str(getattr(e.control, "value", "") or ""))
        self._changed()

    def _on_covers_change(self, e=None) -> None:
        settings_store.set_covers_online(bool(getattr(e.control, "value", False)))
        self._changed()

    async def _on_pick_dir(self, e=None) -> None:
        try:
            current = self._download_dir
            path = await self._picker.get_directory_path(
                dialog_title="Carpeta de descargas",
                initial_directory=current if os.path.isdir(current) else None,
            )
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo abrir el selector: {exc}", kind="error")
            return
        if path:
            settings_store.set_download_dir(path)
            self.refresh()
            self._changed()

    def _on_reset_dir(self, e=None) -> None:
        settings_store.set_download_dir("")
        self.refresh()
        self._changed()

    def _on_clear_cache(self, e=None) -> None:
        from services import cover_cache

        def _clear() -> int:
            removed = 0
            try:
                for entry in cover_cache.CACHE_DIR.iterdir():
                    if entry.is_file():
                        entry.unlink(missing_ok=True)
                        removed += 1
            except OSError:
                pass
            return removed

        removed = _clear()
        self.refresh()
        notify(
            self._page_ref,
            f"Caché de portadas limpiado ({removed} archivos)" if removed
            else "El caché de portadas ya estaba vacío",
        )
