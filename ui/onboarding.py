"""
ui/onboarding.py — Bienvenida de primera ejecución
════════════════════════════════════════════════════
Recuadro fijo sobre un fondo de red de puntos (``ui/onboarding_bg.py``) con un
slider de progreso que se rellena según el paso. Solo cambia el contenido
interior, con deslizamiento + fundido y entrada escalonada.

MANTENIMIENTO (regla del proyecto): toda función visible nueva para el usuario
debe añadir o actualizar un paso en ``ONBOARDING_STEPS``. Es la única lista:
la navegación, el slider y el conteo salen de ella.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Callable

import flet as ft

from core.config import AUDIO_FORMATS
from services import settings_store
from ui.fonts import FONT_HEADLINE, FONT_TEXT, mono_family
from ui.icons import PIcons, app_icon
from ui.onboarding_bg import OnboardingBackground
from ui.tokens import (
    ACCENT, BG_DEEP, BG_SURFACE, BORDER_MUTED, MOTION_ENTER_MS, MOTION_PANEL_MS,
    ONBOARDING_CARD_ALPHA, ONBOARDING_CARD_H, ONBOARDING_CARD_W, ONBOARDING_SLIDE,
    ONBOARDING_STAGGER_MS, PLAYER_PROGRESS_ANIM_MS, PLAYER_PROGRESS_H,
    RADIUS_DIALOG, RADIUS_INPUT, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARNING,
)
from ui.widgets import _ghost_btn, _primary_btn, app_text, notify, organize_dropdown

_PAD = 28
_INNER_W = ONBOARDING_CARD_W - 2 * _PAD


def _feature(icon: str, title: str, text: str) -> ft.Control:
    """Fila icono + título + descripción, común a todos los pasos."""
    return ft.Row(controls=[
        ft.Container(
            width=36, height=36, border_radius=RADIUS_INPUT,
            border=ft.Border.all(0.8, BORDER_MUTED), alignment=ft.Alignment.CENTER,
            content=app_icon(icon, size=18, color=ACCENT),
        ),
        ft.Column(controls=[
            app_text(title, size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE),
            app_text(text, size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
        ], spacing=2, expand=True),
    ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER)


# ── pasos ──────────────────────────────────────────────────────────────

def _step_welcome(view: "OnboardingView") -> list[ft.Control]:
    return [
        _feature(PIcons.ARROWS_LEFT_RIGHT, "Muévete entre plataformas",
                 "Spotify, YouTube Music y Apple Music en un mismo lugar."),
        _feature(PIcons.FOLDER_OPEN, "Tu música local también cuenta",
                 "Carpetas con audio, descargas y listas importadas."),
        _feature(PIcons.SLIDERS_HORIZONTAL, "Tú decides cómo se ordena",
                 "Organiza, divide y revisa antes de transferir nada."),
    ]


def _step_gestion(view: "OnboardingView") -> list[ft.Control]:
    return [
        _feature(PIcons.LINK, "Carga una playlist",
                 "Por URL o ID de cualquier plataforma, o desde un archivo TXT, CSV, M3U, PLS, XSPF…"),
        _feature(PIcons.SORT_ASCENDING, "Organiza y divide",
                 "Ordena por artista, álbum o duración; reparte en listas por género, año o artista."),
        _feature(PIcons.ARROWS_LEFT_RIGHT, "Transfiere con revisión",
                 "Las coincidencias dudosas se marcan para que las confirmes y quedan en un reporte."),
        _feature(PIcons.DOWNLOAD_SIMPLE, "Exporta",
                 "Guarda la lista en el formato que necesites."),
    ]


def _step_library(view: "OnboardingView") -> list[ft.Control]:
    return [
        _feature(PIcons.BOOKS, "Biblioteca",
                 "Sincroniza tus playlists, añade carpetas locales y reprodúcelas con el reproductor integrado."),
        _feature(PIcons.DOWNLOAD_SIMPLE, "Descargas",
                 "Cola con yt-dlp. No se baja audio de Apple Music ni Spotify: se usa la coincidencia en YouTube."),
        _feature(PIcons.IMAGE, "Portadas y metadatos",
                 "Se resuelven portadas por álbum y se escriben etiquetas en los archivos."),
    ]


def _step_prefs(view: "OnboardingView") -> list[ft.Control]:
    fmt = organize_dropdown(
        [(f, f.upper()) for f in AUDIO_FORMATS],
        settings_store.get_download_format(), "Formato de descarga", width=180,
    )
    fmt.on_change = lambda e: settings_store.set_download_format(str(e.control.value or ""))
    covers = ft.Switch(
        label="Resolver portadas online", value=settings_store.get_covers_online(),
        active_color=ACCENT,
        on_change=lambda e: settings_store.set_covers_online(bool(e.control.value)),
    )
    view.dir_text = app_text(
        view.download_dir(), size=11, color=TEXT_PRIMARY, font_family=mono_family("light"),
        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
    )
    folder = ft.Row(controls=[
        app_icon(PIcons.FOLDER, size=18, color=TEXT_MUTED),
        view.dir_text,
        _ghost_btn("Cambiar", PIcons.PENCIL_SIMPLE, view.pick_dir, width=120, height=36),
    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    return [
        app_text("Carpeta donde se guardan las descargas", size=11, color=TEXT_MUTED,
                 font_family=FONT_TEXT),
        folder,
        ft.Container(height=4),
        fmt,
        covers,
        app_text("Todo esto se puede cambiar después en Config.", size=11, color=TEXT_MUTED,
                 font_family=FONT_TEXT),
    ]


def _step_system(view: "OnboardingView") -> list[ft.Control]:
    from services import desktop_integration

    env = desktop_integration.get_runtime_environment()

    if env.os_name == "linux":
        launcher_title = "Lanzador en el menú de aplicaciones"
        launcher_desc = (
            "Crea la entrada .desktop en ~/.local/share/applications y su icono para que "
            "Melomaniac aparezca en el menú de tu escritorio."
        )
        if env.is_appimage:
            update_title = "AppImage y actualizaciones"
            update_desc = (
                "Conserva el AppImage en una carpeta estable (ej. ~/.local/bin). Si descargas "
                "una versión nueva con otro nombre, Melomaniac actualizará el lanzador automáticamente al abrirla."
            )
        else:
            update_title = "Actualizaciones y lanzador"
            update_desc = (
                "Al actualizar la aplicación, el lanzador se mantendrá sincronizado con tu ejecutable."
            )
    elif env.os_name == "windows":
        if env.is_windows_installer:
            launcher_title = "Integración con Windows"
            launcher_desc = (
                "Instalado vía Setup: tus accesos directos en el Menú Inicio y Escritorio "
                "son administrados por el instalador."
            )
            update_title = "Actualizaciones automáticas"
            update_desc = (
                "Al instalar una nueva versión, el instalador actualiza Melomaniac en su sitio "
                "conservando tus datos y accesos."
            )
        else:
            launcher_title = "Acceso en el Escritorio"
            launcher_desc = (
                "Crea un acceso directo .lnk en tu Escritorio para abrir Melomaniac sin buscar la carpeta."
            )
            update_title = "Actualización de la versión portable"
            update_desc = (
                "Si descomprimes una versión nueva en otra ruta, puedes refrescar el acceso directo desde Configuración."
            )
    else:
        launcher_title = "Integración con el sistema"
        launcher_desc = "Acceso directo y ciclo de ejecución de la aplicación."
        update_title = "Actualizaciones"
        update_desc = "Consulta nuevas versiones y novedades desde la sección Configuración."

    features = [
        _feature(PIcons.DESKTOP, launcher_title, launcher_desc),
        _feature(PIcons.ARROWS_CLOCKWISE, update_title, update_desc),
    ]

    action_slot = ft.Container()

    def _render_action() -> None:
        st = desktop_integration.get_shortcut_status()
        if env.is_windows_installer:
            action_slot.content = ft.Row(
                controls=[
                    app_icon(PIcons.CHECK_CIRCLE, color=SUCCESS, size=18, fill=True),
                    app_text("Gestionado por el instalador de Windows (Menú Inicio y Escritorio)",
                             size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        elif st.installed and st.is_current:
            action_slot.content = ft.Row(
                controls=[
                    app_icon(PIcons.CHECK_CIRCLE, color=SUCCESS, size=18, fill=True),
                    app_text("Acceso directo instalado y sincronizado", size=11, color=SUCCESS, font_family=FONT_TEXT),
                    ft.Container(width=6),
                    _ghost_btn("Reinstalar", PIcons.ARROWS_CLOCKWISE, _on_click_install, width=120, height=36),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        elif st.installed and not st.is_current:
            action_slot.content = ft.Row(
                controls=[
                    _primary_btn("Actualizar acceso directo", PIcons.ARROWS_CLOCKWISE, _on_click_install, width=220, height=38),
                    app_text("Apunta a una ruta anterior", size=11, color=WARNING, font_family=FONT_TEXT),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        else:
            action_slot.content = ft.Row(
                controls=[
                    _primary_btn("Crear acceso directo", PIcons.PLUS, _on_click_install, width=200, height=38),
                    app_text("Opcional: puedes crearlo o cambiarlo en Config", size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        try:
            action_slot.update()
        except Exception:
            pass

    def _on_click_install(e=None) -> None:
        ok, msg = desktop_integration.install_shortcut()
        kind = "info" if ok else "error"
        notify(view._page_ref, msg, kind=kind)
        _render_action()

    _render_action()

    return [
        *features,
        ft.Container(height=4),
        action_slot,
    ]


def _step_connect(view: "OnboardingView") -> list[ft.Control]:
    return [
        _feature(PIcons.LOCK, "Es opcional",
                 "Sin credenciales puedes usar lo local y cargar playlists públicas."),
        _feature(PIcons.GLOBE, "Sesión del navegador",
                 "Melomaniac lee primero la sesión de tu navegador, en memoria y sin guardarla."),
        _feature(PIcons.KEY, "Credenciales manuales",
                 "Si hace falta, pégalas en el asistente. Se guardan solo en tu equipo, en config/."),
        ft.Container(height=4),
        ft.Row(controls=[
            _primary_btn("Configurar plataformas", PIcons.GEAR,
                         view.connect, width=240, height=40),
        ]),
    ]


def _step_done(view: "OnboardingView") -> list[ft.Control]:
    return [
        _feature(PIcons.CHECK_CIRCLE, "Todo listo",
                 "Empieza cargando una playlist en Gestión o añadiendo una carpeta en Biblioteca."),
        _feature(PIcons.ARROWS_CLOCKWISE, "Volver a verla",
                 "Esta guía está en Config > Ver bienvenida."),
    ]


@dataclass(frozen=True)
class Step:
    key: str
    icon: str
    title: str
    subtitle: str
    build: Callable[["OnboardingView"], list[ft.Control]]


# Única lista de pasos. Añadir aquí al integrar funciones nuevas.
ONBOARDING_STEPS: tuple[Step, ...] = (
    Step("welcome", PIcons.HAND_WAVING, "Bienvenido a Melomaniac",
         "Gestiona, transfiere y reproduce tu música sin depender de una sola plataforma.",
         _step_welcome),
    Step("gestion", PIcons.ARROWS_LEFT_RIGHT, "Gestión",
         "El espacio de trabajo para una playlist.", _step_gestion),
    Step("library", PIcons.BOOKS, "Biblioteca y Descargas",
         "Lo tuyo, sincronizado y en disco.", _step_library),
    Step("prefs", PIcons.SLIDERS_HORIZONTAL, "Preferencias",
         "Tres ajustes para empezar cómodo.", _step_prefs),
    Step("system", PIcons.DESKTOP, "Sistema y actualizaciones",
         "Acceso directo y ciclo de vida de la aplicación.", _step_system),
    Step("connect", PIcons.LINK, "Conectar plataformas",
         "Para transferir y sincronizar necesitas una sesión.", _step_connect),
    Step("done", PIcons.ROCKET_LAUNCH, "Listo",
         "Ya puedes empezar.", _step_done),
)


class OnboardingView(ft.Container):
    """Overlay a pantalla completa.

    Args:
        page: Página Flet.
        on_connect: Abre el asistente de credenciales.
        on_close: Se invoca tras el fundido de salida (quitar el overlay).
    """

    def __init__(self, page: ft.Page, on_connect: Callable[[], None] | None = None,
                 on_close: Callable[[], None] | None = None) -> None:
        super().__init__(
            expand=True, bgcolor=BG_DEEP, opacity=1,
            animate_opacity=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        self._page_ref = page
        self._on_connect = on_connect
        self._on_close = on_close
        self._index = 0
        self._busy = False
        self.dir_text: ft.Text | None = None
        self._picker = ft.FilePicker()
        page.services.append(self._picker)
        self._bg = OnboardingBackground()

        self._fill = ft.Container(
            height=PLAYER_PROGRESS_H, bgcolor=ACCENT, border_radius=PLAYER_PROGRESS_H,
            width=self._fill_width(0),
            animate=ft.Animation(PLAYER_PROGRESS_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        self._counter = app_text("", size=11, color=TEXT_MUTED, font_family=mono_family("light"))
        self._title = app_text("", size=22, color=TEXT_PRIMARY, font_family=FONT_HEADLINE)
        self._subtitle = app_text("", size=12, color=TEXT_MUTED, font_family=FONT_TEXT)
        self._icon = app_icon(PIcons.CIRCLE, size=26, color=ACCENT)
        self._items = ft.Column(spacing=14)
        self._body = ft.Container(
            expand=True, opacity=1, offset=ft.Offset(0, 0),
            animate_opacity=ft.Animation(MOTION_ENTER_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_offset=ft.Animation(MOTION_ENTER_MS, ft.AnimationCurve.EASE_IN_OUT),
            content=ft.Column(controls=[
                ft.Row(controls=[self._icon, self._title], spacing=12,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self._subtitle,
                ft.Container(height=6),
                self._items,
            ], spacing=6),
        )
        self._skip_btn = _ghost_btn("Omitir", PIcons.SKIP_FORWARD, self._on_skip, width=120, height=40)
        self._back_btn = _ghost_btn("Anterior", PIcons.ARROW_LEFT, self._on_back, width=130, height=40)
        self._next_slot = ft.Container()

        card = ft.Container(
            width=ONBOARDING_CARD_W, height=ONBOARDING_CARD_H,
            bgcolor=ft.Colors.with_opacity(ONBOARDING_CARD_ALPHA, BG_SURFACE),
            border=ft.Border.all(0.8, BORDER_MUTED), border_radius=RADIUS_DIALOG,
            padding=ft.Padding.all(_PAD),
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Container(
                        width=_INNER_W - 70, height=PLAYER_PROGRESS_H, bgcolor=BORDER_MUTED,
                        border_radius=PLAYER_PROGRESS_H, alignment=ft.Alignment.CENTER_LEFT,
                        content=self._fill,
                    ),
                    self._counter,
                ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=10),
                self._body,
                ft.Row(controls=[
                    self._skip_btn, ft.Container(expand=True), self._back_btn, self._next_slot,
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=6),
        )
        self.content = ft.Stack(
            expand=True, fit=ft.StackFit.EXPAND,
            controls=[self._bg, ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=card)],
        )
        self._render(initial=True)

    # ── ciclo de vida ──────────────────────────────────────────────────

    def did_mount(self) -> None:
        self._bg.start()
        asyncio.create_task(self._enter(0))

    def will_unmount(self) -> None:
        self._bg.stop()

    # ── utilidades de pasos ────────────────────────────────────────────

    @staticmethod
    def _fill_width(index: int) -> float:
        return (_INNER_W - 70) * (index + 1) / len(ONBOARDING_STEPS)

    @staticmethod
    def download_dir() -> str:
        from engine.downloader import resolve_download_dir
        try:
            return str(resolve_download_dir(settings_store.get_download_dir() or None, create=False))
        except Exception:
            return settings_store.get_download_dir() or ""

    async def pick_dir(self, e=None) -> None:
        try:
            current = self.download_dir()
            path = await self._picker.get_directory_path(
                dialog_title="Carpeta de descargas",
                initial_directory=current if os.path.isdir(current) else None,
            )
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo abrir el selector: {exc}", kind="error")
            return
        if path:
            settings_store.set_download_dir(path)
            if self.dir_text is not None:
                self.dir_text.value = self.download_dir()
                self.dir_text.update()

    def connect(self, e=None) -> None:
        if self._on_connect is not None:
            self._on_connect()

    # ── render ─────────────────────────────────────────────────────────

    def _render(self, initial: bool = False) -> None:
        step = ONBOARDING_STEPS[self._index]
        last = self._index == len(ONBOARDING_STEPS) - 1
        self._icon.value = step.icon
        self._title.value = step.title
        self._subtitle.value = step.subtitle
        self._counter.value = f"{self._index + 1}/{len(ONBOARDING_STEPS)}"
        self._fill.width = self._fill_width(self._index)
        self._items.controls = step.build(self)
        for c in self._items.controls:
            c.opacity = 0
            c.animate_opacity = ft.Animation(MOTION_ENTER_MS, ft.AnimationCurve.EASE_IN_OUT)
        self._back_btn.visible = self._index > 0
        self._skip_btn.visible = not last
        self._next_slot.content = (
            _primary_btn("Empezar", PIcons.CHECK, self._on_finish, width=150, height=40) if last
            else _primary_btn("Siguiente", PIcons.ARROW_RIGHT, self._on_next, width=150, height=40)
        )
        if not initial:
            self.update()

    async def _enter(self, direction: int) -> None:
        """Entrada escalonada de los elementos del paso actual."""
        for c in list(self._items.controls):
            c.opacity = 1
            try:
                c.update()
            except Exception:
                return
            await asyncio.sleep(ONBOARDING_STAGGER_MS / 1000)

    async def _go(self, delta: int) -> None:
        target = self._index + delta
        if self._busy or not 0 <= target < len(ONBOARDING_STEPS):
            return
        self._busy = True
        try:
            self._body.opacity = 0
            self._body.offset = ft.Offset(-delta * ONBOARDING_SLIDE, 0)
            self._body.update()
            await asyncio.sleep(MOTION_ENTER_MS / 1000)
            self._index = target
            # Posiciona el lado de entrada sin animar y luego anima a 0.
            anim = self._body.animate_offset
            self._body.animate_offset = None
            self._body.offset = ft.Offset(delta * ONBOARDING_SLIDE, 0)
            self._render()
            await asyncio.sleep(0.02)
            self._body.animate_offset = anim
            self._body.offset = ft.Offset(0, 0)
            self._body.opacity = 1
            self._body.update()
            await self._enter(delta)
        finally:
            self._busy = False

    # ── handlers ───────────────────────────────────────────────────────

    async def _on_next(self, e=None) -> None:
        await self._go(1)

    async def _on_back(self, e=None) -> None:
        await self._go(-1)

    async def _on_skip(self, e=None) -> None:
        await self._close()

    async def _on_finish(self, e=None) -> None:
        await self._close()

    async def _close(self) -> None:
        if self._busy:
            return
        self._busy = True
        settings_store.set_onboarding_done(True)
        self.opacity = 0
        self.update()
        await asyncio.sleep(MOTION_PANEL_MS / 1000)
        self._bg.stop()
        if self._on_close is not None:
            self._on_close()
