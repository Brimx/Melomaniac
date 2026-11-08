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
- recálculo de portadas de archivos ya descargados
- carpeta de descargas
- limpieza del caché de portadas
"""

from __future__ import annotations

import os

import flet as ft

from core.config import APP_VERSION, AUDIO_FORMATS
from services import desktop_integration, settings_store, update_checker
from ui.fonts import FONT_HEADLINE, FONT_TEXT, mono_family
from ui.icons import PIcons, app_icon
from ui.tokens import (
    ACCENT, BG_LIST, BG_SURFACE, BORDER_MUTED, RADIUS_INPUT,
    SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARNING,
)
from ui.widgets import (
    _ghost_btn, _primary_btn, app_text, notify, open_external,
    organize_dropdown, section_label,
)


class ConfigView(ft.Container):
    """Preferencias persistentes de la aplicación.

    Args:
        page: Página Flet.
        on_change: Callback opcional que se invoca cuando una preferencia
            cambia, para que las vistas afectadas se refresquen.
    """

    def __init__(self, page: ft.Page, on_change=None, on_welcome=None) -> None:
        super().__init__(expand=True, bgcolor=BG_LIST)
        self._page_ref = page
        self._on_change = on_change
        self._on_welcome = on_welcome
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
            "Restablecer", PIcons.ARROWS_CLOCKWISE, self._on_reset_dir,
            width=130, height=36,
        )
        self._dir_reset_btn.visible = bool(settings_store.get_download_dir())

        cache_size = self._cache_size_text()
        self._cache_text = app_text(cache_size, size=11, color=TEXT_MUTED,
                                    font_family=mono_family("light"))
        self._cache_btn = _ghost_btn(
            "Limpiar caché", PIcons.TRASH, self._on_clear_cache,
            width=150, height=36,
        )

        # Recarga de portadas: revisa qué se resolvería y solo escribe si el
        # usuario confirma, para no sobrescribir artwork bueno a ciegas.
        self._cover_refresh_text = app_text(
            "", size=11, color=TEXT_MUTED, font_family=FONT_TEXT, visible=False,
        )
        self._cover_refresh_btn = _ghost_btn(
            "Recalcular portadas", PIcons.IMAGE, self._on_cover_preview,
            width=190, height=36,
        )

        # ── Sistema y accesos directos ──
        self._shortcut_status_text = app_text(
            "", size=11, color=TEXT_PRIMARY, font_family=FONT_TEXT,
        )
        self._shortcut_location_text = app_text(
            "", size=10, color=TEXT_MUTED, font_family=mono_family("light"),
            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._shortcut_warning_text = app_text(
            "", size=11, color=WARNING, font_family=FONT_TEXT, visible=False,
        )
        self._shortcut_actions_row = ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self._refresh_shortcut_ui()

        # ── Actualizaciones ──
        self._version_text = app_text(
            f"Versión instalada: v{APP_VERSION}", size=11, color=TEXT_PRIMARY,
            font_family=mono_family("light"),
        )
        self._update_check_btn = _ghost_btn(
            "Comprobar", PIcons.ARROWS_CLOCKWISE, self._on_check_updates,
            width=130, height=36,
        )
        self._update_result_col = ft.Column(spacing=6)

        header = ft.Row(controls=[
            app_icon(PIcons.GEAR, size=18, color=TEXT_MUTED),
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
                [
                    self._covers_sw,
                    ft.Container(height=4),
                    ft.Row(controls=[self._cover_refresh_btn],
                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    self._cover_refresh_text,
                ],
            ),
            self._card(
                "DESTINO",
                "Carpeta donde se guardan los archivos descargados.",
                [ft.Row(controls=[
                    app_icon(PIcons.FOLDER, size=18, color=TEXT_MUTED),
                    self._dir_text,
                    _ghost_btn("Cambiar", PIcons.PENCIL_SIMPLE, self._on_pick_dir,
                               width=120, height=36),
                    self._dir_reset_btn,
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
            ),
            self._card(
                "BIENVENIDA",
                "Repite la guía de primera ejecución con las funciones principales.",
                [ft.Row(controls=[_ghost_btn(
                    "Ver bienvenida", PIcons.SPARKLE, self._on_show_welcome,
                    width=190, height=36,
                )])],
            ),
            self._card(
                "MANTENIMIENTO",
                "El caché de portadas deja de usarse si cambias de biblioteca; limpiarlo libera espacio.",
                [ft.Row(controls=[self._cache_text, self._cache_btn],
                        spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
            ),
            self._card(
                "SISTEMA Y ACCESO DIRECTO",
                "Lanzador en el menú de aplicaciones del sistema o acceso en el escritorio.",
                [
                    self._shortcut_status_text,
                    self._shortcut_location_text,
                    self._shortcut_warning_text,
                    ft.Container(height=4),
                    self._shortcut_actions_row,
                ],
            ),
            self._card(
                "ACTUALIZACIONES Y RELEASES",
                "Comprueba si existen nuevas versiones de Melomaniac en GitHub.",
                [
                    ft.Row(controls=[self._version_text, self._update_check_btn],
                           spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    self._update_result_col,
                ],
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
        self._refresh_shortcut_ui()
        try:
            self.update()
        except Exception:
            pass

    def _refresh_shortcut_ui(self) -> None:
        env = desktop_integration.get_runtime_environment()
        st = desktop_integration.get_shortcut_status()

        self._shortcut_status_text.value = st.summary
        if st.installed and st.is_current:
            self._shortcut_status_text.color = SUCCESS
        elif st.is_broken or (st.installed and not st.is_current):
            self._shortcut_status_text.color = WARNING
        else:
            self._shortcut_status_text.color = TEXT_PRIMARY

        loc = st.location_path or ""
        if st.target_path:
            loc = f"{loc} → {st.target_path}"
        self._shortcut_location_text.value = loc
        self._shortcut_location_text.visible = bool(loc)

        warn = st.warning or ""
        self._shortcut_warning_text.value = warn
        self._shortcut_warning_text.visible = bool(warn)

        actions: list[ft.Control] = []
        if env.is_windows_installer:
            actions.append(
                app_text("Gestionado por el instalador de Windows (Menú Inicio y Escritorio)",
                         size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
            )
        elif st.installed and st.is_current:
            actions.extend([
                _ghost_btn("Reinstalar", PIcons.ARROWS_CLOCKWISE, self._on_install_shortcut, width=130, height=36),
                _ghost_btn("Quitar", PIcons.TRASH, self._on_remove_shortcut, width=110, height=36),
            ])
        elif st.installed and not st.is_current:
            actions.extend([
                _primary_btn("Actualizar acceso directo", PIcons.ARROWS_CLOCKWISE, self._on_install_shortcut, width=220, height=36),
                _ghost_btn("Quitar", PIcons.TRASH, self._on_remove_shortcut, width=110, height=36),
            ])
        else:
            actions.append(
                _primary_btn("Crear acceso directo", PIcons.PLUS, self._on_install_shortcut, width=200, height=36)
            )

        self._shortcut_actions_row.controls = actions

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

    def _on_show_welcome(self, e=None) -> None:
        if self._on_welcome is not None:
            self._on_welcome()

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

    async def _on_cover_preview(self, e=None) -> None:
        """Calcula qué portadas cambiarían antes de escribir nada."""
        import asyncio

        from services.covers import refresh_downloaded_covers

        folder = self._download_dir
        if not folder or not os.path.isdir(folder):
            notify(self._page_ref, "No hay carpeta de descargas configurada", kind="error")
            return
        self._cover_refresh_btn.disabled = True
        self._cover_refresh_text.visible = True
        self._cover_refresh_text.value = "Buscando portadas… (puede tardar)"
        try:
            self.update()
        except Exception:
            pass
        try:
            report = await asyncio.to_thread(
                refresh_downloaded_covers, folder, apply=False
            )
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo calcular: {exc}", kind="error")
            self._cover_refresh_btn.disabled = False
            return

        pending = len(report.previews)
        if not report.total:
            self._cover_refresh_text.value = "No hay archivos de audio en la carpeta"
        elif not pending:
            self._cover_refresh_text.value = (
                f"{report.total} archivos · {report.unchanged} ya tienen portada · "
                f"{report.missing} sin artwork"
            )
        else:
            self._cover_refresh_text.value = (
                f"{pending} de {report.total} archivos cambiarían de portada"
            )
            await self._confirm_cover_apply(folder, report.previews)
        self._cover_refresh_btn.disabled = False
        try:
            self.update()
        except Exception:
            pass

    async def _confirm_cover_apply(self, folder: str, previews: list) -> None:
        """Pide confirmación y escribe solo las portadas revisadas."""
        import asyncio

        from engine.audio_metadata import write_cover

        sources: dict[str, int] = {}
        for _, _, source in previews:
            sources[source or "desconocido"] = sources.get(source or "desconocido", 0) + 1
        detail = ", ".join(f"{n} de {s}" for s, n in sorted(sources.items(), key=lambda kv: -kv[1]))

        confirmed = await self._ask_confirm(
            "Sobrescribir portadas",
            f"Se reemplazará la portada de {len(previews)} archivos en {folder}.\n\n"
            f"Orígenes: {detail}.\n\n¿Continuar?",
        )
        if not confirmed:
            self._cover_refresh_text.value = "Cancelado: no se modificó ningún archivo"
            return

        def _write() -> tuple[int, int]:
            ok = failed = 0
            for path, data, _ in previews:
                if write_cover(path, data):
                    ok += 1
                else:
                    failed += 1
            return ok, failed

        ok, failed = await asyncio.to_thread(_write)
        self._cover_refresh_text.value = (
            f"{ok} portadas actualizadas" + (f" · {failed} con error" if failed else "")
        )
        notify(self._page_ref, f"{ok} portadas actualizadas", kind="info" if not failed else "error")

    async def _ask_confirm(self, title: str, message: str) -> bool:
        """Diálogo de confirmación simple; ``False`` si se cierra sin aceptar."""
        import asyncio

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def _close(value: bool) -> None:
            if not future.done():
                future.set_result(value)
            dlg.open = False
            try:
                self._page_ref.update()
            except Exception:
                pass

        dlg = ft.AlertDialog(
            modal=True,
            title=app_text(title, size=15, color=TEXT_PRIMARY, font_family=FONT_HEADLINE),
            content=ft.Container(
                width=420,
                content=app_text(message, size=12, color=TEXT_MUTED, font_family=FONT_TEXT),
            ),
            actions=[
                _ghost_btn("Cancelar", PIcons.X, lambda _: _close(False), width=110, height=36),
                _primary_btn("Sobrescribir", PIcons.CHECK, lambda _: _close(True), width=150, height=36),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=lambda _: _close(False),
        )
        self._page_ref.overlay.append(dlg)
        dlg.open = True
        self._page_ref.update()
        return await future

    def _on_install_shortcut(self, e=None) -> None:
        ok, msg = desktop_integration.install_shortcut()
        notify(self._page_ref, msg, kind="info" if ok else "error")
        self._refresh_shortcut_ui()
        try:
            self.update()
        except Exception:
            pass

    def _on_remove_shortcut(self, e=None) -> None:
        ok, msg = desktop_integration.remove_shortcut()
        notify(self._page_ref, msg, kind="info" if ok else "error")
        self._refresh_shortcut_ui()
        try:
            self.update()
        except Exception:
            pass

    async def _on_check_updates(self, e=None) -> None:
        self._update_check_btn.text = "Buscando…"
        self._update_check_btn.disabled = True
        try:
            self._update_check_btn.update()
        except Exception:
            pass

        try:
            info = await update_checker.check_for_updates()
            self._render_update_result(info)
        finally:
            self._update_check_btn.text = "Comprobar"
            self._update_check_btn.disabled = False
            try:
                self._update_check_btn.update()
            except Exception:
                pass

    def _render_update_result(self, info: update_checker.UpdateInfo) -> None:
        ctrls: list[ft.Control] = []
        if not info.checked:
            ctrls.append(
                app_text(f"No se pudo comprobar: {info.error_message or info.instructions}",
                         size=11, color=WARNING, font_family=FONT_TEXT)
            )
        elif info.has_update:
            ctrls.append(
                app_text(f"¡Nueva versión disponible: v{info.latest_version}!",
                         size=12, color=SUCCESS, font_family=FONT_HEADLINE)
            )
            ctrls.append(
                app_text(info.instructions, size=11, color=TEXT_PRIMARY, font_family=FONT_TEXT)
            )
            btn_row = ft.Row(controls=[
                _primary_btn(
                    "Descargar actualización", PIcons.DOWNLOAD_SIMPLE,
                    lambda _: open_external(self._page_ref, info.download_url or info.release_url),
                    width=210, height=36,
                ),
                _ghost_btn(
                    "Ver notas de release", PIcons.ARROW_SQUARE_OUT,
                    lambda _: open_external(self._page_ref, info.release_url),
                    width=180, height=36,
                ),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ctrls.append(btn_row)
        else:
            ctrls.append(
                app_text(f"✓ Estás usando la versión más reciente (v{APP_VERSION}).",
                         size=11, color=SUCCESS, font_family=FONT_TEXT)
            )
            ctrls.append(
                app_text(info.instructions, size=11, color=TEXT_MUTED, font_family=FONT_TEXT)
            )

        self._update_result_col.controls = ctrls
        try:
            self._update_result_col.update()
        except Exception:
            pass
