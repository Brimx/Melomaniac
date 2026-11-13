"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.9.0                               ║
║              Componentes de Fila de Canción                          ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: ui/song_row.py
Descripción: Componentes visuales para representar canciones en listas.
            Incluye SongRow (fila interactiva con hover) y SkeletonRow
            (placeholder animado durante carga).

Componentes:
    - SkeletonRow: Fila placeholder con efecto shimmer durante carga
    - SongRow: Fila interactiva con thumbnail, metadatos, checkbox y estado

Estrategia de Diseño:
    Las filas de canción implementan un sistema de feedback visual multi-nivel:
    
    1. Estados de hover: Cambio sutil de fondo para indicar interactividad
    2. Iconos de estado: Feedback visual del progreso de transferencia
    3. Checkboxes: Selección individual de canciones
    4. Thumbnails: Identificación visual rápida con fallback a icono
    5. Skeleton loading: Placeholder animado que reduce percepción de latencia
    
    La altura fija (64px) garantiza scroll suave y cálculos de virtualización
    precisos en listas largas (1000+ canciones).

Autor: Melomaniac Team
Versión: 4.9.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional, Sequence

import flet as ft

from core.models import Track
from ui.theme.fonts import font_family_for, mono_family
from ui.theme.icons import PIcons, app_icon
from ui.theme.tokens import (
    BG_LIST, BG_HOVER, SKELETON_DARK,
    TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM,
    ACCENT, SUCCESS, BG_SURFACE, BORDER_ROW,
    BG_INPUT, BORDER_LIGHT,
    MOTION_HOVER_MS, MOTION_REVEAL_MS, MOTION_SKELETON_MS,
)
from ui.widgets import (
    ContextMenuAction,
    _status_icon,
    build_context_menu_button,
    input_border,
    open_external,
    wrap_with_context_menu,
)

# Altura fija de fila para cálculos de virtualización
ITEM_H = 64

# Secuencia acotada en ASCII puro para el barrido de entrada en skeletons (una sola pieza mono)
_ASCII_REVEAL_FRAMES = (
    "[=----]",
    "[==---]",
    "[===--]",
    "[====-]",
    "[=====]",
)
_REDUCED_MOTION_TRUTHY = frozenset({"1", "true", "yes", "on", "reduce"})
_REDUCED_MOTION_FALSY = frozenset({"0", "false", "no", "off"})


def is_reduced_motion(page: ft.Page | None = None, *, reduced_motion: bool | None = None) -> bool:
    """Devuelve True si debe omitirse el movimiento decorativo (entorno o página)."""
    if reduced_motion is not None:
        return bool(reduced_motion)
    for var in ("MELOMANIAC_REDUCED_MOTION", "REDUCED_MOTION", "PREFERS_REDUCED_MOTION"):
        raw = os.environ.get(var, "").strip().lower()
        if raw in _REDUCED_MOTION_TRUTHY:
            return True
    if os.environ.get("GTK_ENABLE_ANIMATIONS", "").strip().lower() in _REDUCED_MOTION_FALSY:
        return True
    if page is not None:
        if getattr(page, "reduced_motion", False):
            return True
        media = getattr(page, "media", None)
        if media is not None and getattr(media, "disable_animations", False):
            return True
    return False


class _BaseSkeletonRow(ft.Container):
    """Base compartida para filas skeleton con Shimmer, barrido ASCII acotado y pulso."""

    def __init__(
        self,
        _index: int,
        *,
        row: ft.Row,
        padding: ft.Padding,
        reveal_left: float,
        reduced_motion: bool | None = None,
    ):
        self._pulse_task: Optional[asyncio.Task] = None
        self._index = _index
        self._reduced_motion = reduced_motion

        try:
            _shimmer: ft.Control = ft.Shimmer(
                base_color=ft.Colors.with_opacity(0.35, SKELETON_DARK),
                highlight_color=ft.Colors.with_opacity(0.9, BG_SURFACE),
                content=row,
            )
        except Exception:
            _shimmer = row

        self._reveal_text = ft.Text(
            "",
            size=10,
            color=TEXT_MUTED,
            font_family=mono_family("light"),
            opacity=1.0,
        )
        self._reveal_wrap = ft.Container(
            content=self._reveal_text,
            left=reveal_left,
            top=0,
            bottom=0,
            alignment=ft.Alignment.CENTER_LEFT,
            visible=False,
            opacity=0.0,
            animate_opacity=ft.Animation(MOTION_REVEAL_MS, ft.AnimationCurve.EASE_OUT),
        )

        reduce = is_reduced_motion(reduced_motion=reduced_motion)
        super().__init__(
            height=ITEM_H,
            padding=padding,
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
            content=ft.Stack(controls=[_shimmer, self._reveal_wrap]),
            opacity=1.0,
            scale=1.0 if reduce else 0.98,
            animate_scale=None if reduce else ft.Animation(MOTION_SKELETON_MS, ft.AnimationCurve.EASE_IN_OUT),
        )

    def _safe_update(self, ctrl: ft.Control | None = None) -> None:
        target = ctrl if ctrl is not None else self
        try:
            target.update()
        except Exception:
            pass

    def _reset_reveal(self) -> None:
        self._reveal_text.value = ""
        self._reveal_wrap.opacity = 0.0
        self._reveal_wrap.visible = False

    async def _run_ascii_reveal(self) -> None:
        """Barrido ASCII de una sola pasada acotada a MOTION_REVEAL_MS."""
        try:
            page = self.page
        except Exception:
            page = None
        if is_reduced_motion(page, reduced_motion=self._reduced_motion):
            return
        step_s = (MOTION_REVEAL_MS / 1000.0) / len(_ASCII_REVEAL_FRAMES)
        self._reveal_wrap.visible = True
        self._reveal_wrap.opacity = 1.0
        try:
            for frame in _ASCII_REVEAL_FRAMES:
                self._reveal_text.value = frame
                self._safe_update(self._reveal_wrap)
                await asyncio.sleep(step_s)
        finally:
            self._reset_reveal()
            self._safe_update(self._reveal_wrap)

    async def start_pulse(self) -> None:
        """Barrido ASCII acotado al activar + zoom sutil 0.98↔1.0 (600ms, stagger 60ms)."""
        self._pulse_task = asyncio.current_task()
        try:
            page = self.page
        except Exception:
            page = None
        if is_reduced_motion(page, reduced_motion=self._reduced_motion):
            self._reset_reveal()
            self.scale = 1.0
            self._safe_update()
            return
        try:
            await asyncio.sleep(0.06 * self._index)
        except asyncio.CancelledError:
            return
        try:
            await self._run_ascii_reveal()
            pulse_s = MOTION_SKELETON_MS / 1000.0
            while True:
                self.scale = 1.0
                self._safe_update()
                await asyncio.sleep(pulse_s)
                self.scale = 0.98
                self._safe_update()
                await asyncio.sleep(pulse_s)
        except asyncio.CancelledError:
            pass

    def stop_pulse(self) -> None:
        if self._pulse_task:
            self._pulse_task.cancel()
            self._pulse_task = None
        try:
            self._reset_reveal()
            self.scale = 1.0
        except Exception:
            pass


def start_skeleton_pulse_tasks(
    skeletons: list[_BaseSkeletonRow],
    tasks: list[asyncio.Task],
) -> None:
    """Lanza las tareas de pulso skeleton solo si hay un event loop activo."""
    if tasks or not skeletons:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for sk in skeletons:
        tasks.append(loop.create_task(sk.start_pulse()))


def stop_skeleton_pulse_tasks(
    skeletons: list[_BaseSkeletonRow],
    tasks: list[asyncio.Task],
) -> None:
    """Detiene y cancela todas las tareas de pulso skeleton de forma segura."""
    for sk in skeletons:
        try:
            sk.stop_pulse()
        except Exception:
            pass
    for task in tasks:
        try:
            if not task.done():
                task.cancel()
        except Exception:
            pass
    tasks.clear()


class SkeletonRow(_BaseSkeletonRow):
    """
    Fila placeholder con Shimmer + zoom sutil (reemplaza opacity pulse).

    Usa ft.Shimmer(base/highlight) para efecto deslizante y mantiene
    animate_scale 600ms para zoom. Dinámico por viewport se calcula en
    ui/main_ui.py (_calc_skeleton_count) y se muestra entre cambios de
    vista / organizar (ver main_ui _show_reload_shimmer).
    """

    def __init__(self, _index: int, *, reduced_motion: bool | None = None):
        # placeholders — dentro de Shimmer serán animados por gradiente; bgcolor es base
        self._num = ft.Container(width=28, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=55, height=55, border_radius=8, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._status = ft.Container(width=26, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._chk = ft.Container(width=32, height=18, border_radius=4, bgcolor=BG_SURFACE)

        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur, self._status, self._chk],
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        super().__init__(
            _index,
            row=_row,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            reveal_left=115,
            reduced_motion=reduced_motion,
        )


def _thumb_content_for(
    img_url: str, *, box: int | None = None, icon_size: int = 18,
) -> ft.Control:
    """Contenido de miniatura que rellena y centra su recuadro. Nunca vacío.

    ``ft.Image`` no acepta ``alignment`` en Flet 1.0.5 y, sin tamaño propio,
    toma el intrínseco de la imagen: una portada con dimensiones inesperadas
    queda pegada a una esquina del recuadro recortado. Pasando ``box`` (el lado
    del contenedor que la recorta) la imagen ocupa exactamente ese cuadrado y
    COVER la centra siempre. ``box=None`` deja el tamaño intrínseco para los
    usos que no recortan.
    """
    if img_url:
        size = (
            {
                "width": box,
                "height": box,
                "cache_width": box * 2,
                "cache_height": box * 2,
            }
            if box
            else {}
        )
        return ft.Image(
            src=img_url,
            fit=ft.BoxFit.COVER,
            gapless_playback=True,
            filter_quality=ft.FilterQuality.MEDIUM,
            error_content=app_icon(PIcons.MUSIC_NOTE, color=TEXT_DIM, size=icon_size),
            **size,
        )
    return ft.Container(
        content=app_icon(PIcons.MUSIC_NOTE, color=TEXT_DIM, size=icon_size),
        alignment=ft.Alignment.CENTER,
    )


def build_preview_row(track: Track, index: int) -> ft.Container:
    """
    Fila Preview readonly — espejo 1:1 de SongRow sin columna Estado/Sel.
    Mantiene ITEM_H, padding 16/0, spacing 16, widths 32/55/expand3/expand2/48,
    tipografías y colores idénticos para alineación visual con SongRow.
    Solo quita Estado (26) y Checkbox (32) — resto idéntico.
    """
    # thumbnail idéntico a SongRow (55) con fallback
    thumb_content = _thumb_content_for(track.img_url, box=55)
    thumb = ft.Container(
        width=55, height=55,
        border_radius=8,
        bgcolor=SKELETON_DARK,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=thumb_content,
    )
    num_label = ft.Text(
        str(index), size=11, color=TEXT_MUTED,
        font_family=mono_family("light"),
        text_align=ft.TextAlign.CENTER,
        opacity=1.0,
    )
    title_text = ft.Text(
        track.name, size=13, color=TEXT_PRIMARY,
        font_family=font_family_for(track.name, "semibold"),
        overflow=ft.TextOverflow.ELLIPSIS,
        max_lines=1,
        opacity=1.0,
    )
    artist_text = ft.Text(
        track.artist, size=11, color=TEXT_MUTED,
        font_family=font_family_for(track.artist),
        overflow=ft.TextOverflow.ELLIPSIS,
        max_lines=1,
        opacity=1.0,
    )
    dur_text = ft.Text(
        track.duration, size=11, color=TEXT_DIM,
        font_family=mono_family("light"),
        opacity=1.0,
    )
    album_text = ft.Text(
        track.album or "—", size=11, color=TEXT_MUTED,
        font_family=font_family_for(track.album),
        overflow=ft.TextOverflow.ELLIPSIS,
        max_lines=1,
        opacity=1.0,
    )
    row_content = ft.Row(
        controls=[
            ft.Container(content=num_label, width=32, alignment=ft.Alignment.CENTER),
            thumb,
            ft.Column(
                controls=[title_text, artist_text],
                spacing=1,
                tight=True,
                expand=3,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Container(content=album_text, expand=2, alignment=ft.Alignment.CENTER_LEFT),
            ft.Container(content=dur_text, width=48, alignment=ft.Alignment.CENTER),
        ],
        spacing=16,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return ft.Container(
        height=ITEM_H,
        padding=ft.Padding.symmetric(horizontal=16, vertical=0),
        border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
        border_radius=0,
        bgcolor=BG_LIST,
        content=row_content,
    )


class PreviewSkeletonRow(_BaseSkeletonRow):
    """
    Skeleton espejo de Preview (sin Estado/Sel) — mismo ritmo Shimmer+zoom.
    """

    def __init__(self, _index: int, *, reduced_motion: bool | None = None):
        self._num = ft.Container(width=28, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=55, height=55, border_radius=8, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=3, bgcolor=BG_SURFACE)
        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur],
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        super().__init__(
            _index,
            row=_row,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            reveal_left=115,
            reduced_motion=reduced_motion,
        )


class LibrarySkeletonRow(_BaseSkeletonRow):
    """
    Skeleton Biblioteca — clon de SkeletonRow con zonas adaptadas al detail.

    Gestión tiene [#][thumb][title][album][dur][status][chk]; Biblioteca
    detail tiene [#][thumb48][title][album][dur][open-btn]: se atenúan esas
    6 zonas (sin status/chk). Mismo Shimmer + scale 0.98↔1.0 600ms stagger
    e ITEM_H para paridad exacta con Gestión.
    """

    def __init__(self, _index: int, *, reduced_motion: bool | None = None):
        self._num = ft.Container(width=28, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=48, height=48, border_radius=6, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._action = ft.Container(width=32, height=10, border_radius=3, bgcolor=BG_SURFACE)
        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur, self._action],
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        super().__init__(
            _index,
            row=_row,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            reveal_left=108,
            reduced_motion=reduced_motion,
        )


class SongRow(ft.Container):
    """
    Fila interactiva de canción con hover, thumbnail, metadatos y controles.
    
    Representa una canción individual en la lista con todos sus metadatos
    visuales y controles de interacción. Implementa estados de hover para
    feedback táctil y actualización dinámica de estado de transferencia.
    
    Attributes:
        track: Instancia de Track con metadatos de la canción.
        _on_toggle: Callback ejecutado al cambiar el checkbox.
        _thumb: Container con thumbnail de álbum o icono fallback.
        _chk: Checkbox para selección de la canción.
        _status_icon: Icono indicando estado de transferencia.
    
    Methods:
        refresh: Actualiza la fila con nuevos datos del track.
    
    Example:
        >>> def on_toggle(track_id):
        ...     print(f"Toggled {track_id}")
        >>> row = SongRow(track, index=1, on_toggle=on_toggle)
    
    Note:
        La fila usa altura fija (ITEM_H=64px) para garantizar scroll
        suave y permitir virtualización eficiente en listas largas.
        El hover con animación de 100ms proporciona feedback táctil
        sin ser intrusivo.
    """

    def __init__(
        self,
        track: Track,
        index: int,
        on_toggle: Callable[[str], None],
        on_move: Callable[[str, int], None] | None = None,
        on_download: Callable[[str], None] | None = None,
        *,
        download_busy: bool = False,
        download_done: bool = False,
        on_play: Callable[[Track], Any] | None = None,
        on_play_next: Callable[[Track], Any] | None = None,
        on_add_to_queue: Callable[[Track], Any] | None = None,
        on_open_external: Callable[[Track], Any] | None = None,
        extra_actions: Sequence[ContextMenuAction] | None = None,
        show_album: bool = True,
    ):
        """
        Inicializa una fila de canción con todos sus componentes visuales.
        
        Args:
            track: Instancia de Track con metadatos de la canción.
            index: Número de fila (1-indexed) mostrado al usuario.
            on_toggle: Callback (track_id: str) -> None ejecutado al
                      cambiar el estado del checkbox.
            on_move: Callback opcional (track_id, new_index) para Nº editable §8 — subir/bajar vía teclado.
            on_download: Callback opcional (track_id: str) -> None para descargar la pista.
            download_busy: True si ya hay una cola de descarga activa.
            download_done: True si la pista ya terminó de descargarse.
            on_play: Callback opcional para reproducir la pista desde menú contextual.
            on_play_next: Callback opcional para reproducir siguiente en cola.
            on_add_to_queue: Callback opcional para añadir a la cola.
            on_open_external: Callback opcional para abrir la pista en su plataforma.
            extra_actions: Acciones adicionales para el menú contextual.
        """
        self.track      = track
        self._on_toggle = on_toggle
        self._on_move = on_move
        self._on_download = on_download
        self._on_play = on_play
        self._on_play_next = on_play_next
        self._on_add_to_queue = on_add_to_queue
        self._on_open_external = on_open_external
        self._extra_actions = list(extra_actions or ())
        self._index = index
        self._hovered: bool = False
        self._download_busy: bool = bool(download_busy)
        self._download_done: bool = bool(download_done)
        # Último estado pintado: permite refrescar solo lo que cambió.
        self._last_selected = track.selected
        self._last_album = track.album or "—"
        self._last_status = track.transfer_status
        self._last_img_url = track.img_url

        # ──────────────────────────────────────────────────────────────
        # THUMBNAIL DE ÁLBUM CON FALLBACK
        # ──────────────────────────────────────────────────────────────
        # Intenta cargar imagen de álbum, fallback a icono si falla
        
        _thumb_content = _thumb_content_for(track.img_url, box=55)

        self._thumb = ft.Container(
            width=55, height=55,
            border_radius=8,
            bgcolor=SKELETON_DARK,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=_thumb_content,
        )

        # ──────────────────────────────────────────────────────────────
        # CHECKBOX DE SELECCIÓN
        # ──────────────────────────────────────────────────────────────
        
        self._chk = ft.Checkbox(
            value=track.selected,
            fill_color={
                ft.ControlState.SELECTED: ACCENT,
                ft.ControlState.DEFAULT:  BG_SURFACE,
            },
            check_color=TEXT_PRIMARY,
            border_side=ft.BorderSide(1.5, TEXT_DIM),
            on_change=lambda e: on_toggle(track.id),
        )

        # ──────────────────────────────────────────────────────────────
        # ICONO DE ESTADO DE TRANSFERENCIA
        # ──────────────────────────────────────────────────────────────
        
        self._status_icon = _status_icon(track.transfer_status)
        self._hover_dl_icon = app_icon(PIcons.DOWNLOAD_SIMPLE, color=ACCENT, size=16)
        self._hover_dl_busy_icon = app_icon(PIcons.DOWNLOAD_SIMPLE, color=TEXT_DIM, size=16)
        self._done_icon = app_icon(PIcons.CHECK_CIRCLE, color=SUCCESS, size=15, fill=True)
        self._status_cell_state: tuple | None = None

        # ──────────────────────────────────────────────────────────────
        # ELEMENTOS DE TEXTO
        # ──────────────────────────────────────────────────────────────
        
        # Nº posición editable — TextField 32 ancho para subir/bajar sin drag §8
        if self._on_move is not None:
            def _on_num_submit(e):
                try:
                    new_pos = int((e.control.value or "").strip())
                    if 1 <= new_pos <= 9999 and new_pos != self._index:
                        self._on_move(track.id, new_pos)
                except Exception:
                    pass
                # restaura valor visual (será recreado en sync)
                try:
                    e.control.value = str(self._index)
                    e.control.update()
                except Exception:
                    pass
            num_label = ft.TextField(
                value=str(index), width=32, height=28,
                text_align=ft.TextAlign.CENTER,
                text_style=ft.TextStyle(size=11, color=TEXT_MUTED, font_family=mono_family("light")),
                bgcolor=BG_INPUT, border=input_border(ft.Colors.TRANSPARENT, ACCENT, 6),
                content_padding=ft.Padding.symmetric(horizontal=2, vertical=4),
                dense=True,
                on_submit=_on_num_submit,
                on_blur=_on_num_submit,
            )
        else:
            num_label = ft.Text(
                str(index), size=11, color=TEXT_MUTED,
                font_family=mono_family("light"),
                text_align=ft.TextAlign.CENTER,
                opacity=1.0,
            )

        title_text = ft.Text(
            track.name, size=13, color=TEXT_PRIMARY,
            font_family=font_family_for(track.name, "semibold"),
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
            opacity=1.0,
        )
        artist_text = ft.Text(
            track.artist, size=11, color=TEXT_MUTED,
            font_family=font_family_for(track.artist),
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
            opacity=1.0,
        )
        dur_text = ft.Text(
            track.duration, size=11, color=TEXT_DIM,
            font_family=mono_family("light"),
            opacity=1.0,
        )
        album_text = ft.Text(
            track.album or "—", size=11, color=TEXT_MUTED,
            font_family=font_family_for(track.album),
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
            opacity=1.0,
        )
        self._album_text = album_text

        # ──────────────────────────────────────────────────────────────
        # LAYOUT DE FILA
        # ──────────────────────────────────────────────────────────────
        # Estructura: [#] [Thumb] [Título/Artista] [Álbum] [Duración] [Estado] [✓]

        def _on_status_hover(e: ft.HoverEvent) -> None:
            if not (self._hovered and self._on_download is not None and not self._download_done and not self._download_busy):
                if self._status_cell.scale != 1.0:
                    self._status_cell.scale = 1.0
                    try:
                        self._status_cell.update()
                    except Exception:
                        pass
                return
            hovering = e.data == "true" or e.data is True
            self._status_cell.scale = 1.12 if hovering else 1.0
            try:
                self._status_cell.update()
            except Exception:
                pass

        self._status_cell = ft.Container(
            content=self._status_icon,
            width=26,
            height=26,
            border_radius=13,
            alignment=ft.Alignment.CENTER,
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            on_hover=_on_status_hover,
        )
        self._sync_status_cell()
        self._menu_btn = build_context_menu_button(
            self.context_actions,
            tooltip="Opciones de pista",
            width=28,
            height=28,
            icon_size=16,
        )
        self._album_cell = ft.Container(
            content=album_text,
            expand=2,
            alignment=ft.Alignment.CENTER_LEFT,
            visible=bool(show_album),
        )
        row_content = ft.Row(
            controls=[
                ft.Container(content=num_label, width=32, alignment=ft.Alignment.CENTER),
                self._thumb,
                ft.Column(
                    controls=[title_text, artist_text],
                    spacing=1,
                    tight=True,
                    expand=3,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                self._album_cell,
                ft.Container(content=dur_text,          width=48, alignment=ft.Alignment.CENTER),
                self._status_cell,
                ft.Container(content=self._chk,         width=32, alignment=ft.Alignment.CENTER),
                self._menu_btn,
            ],
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        super().__init__(
            height=ITEM_H,
            padding=ft.Padding.symmetric(horizontal=16, vertical=0),
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
            border_radius=0,
            bgcolor=BG_LIST,
            animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
            on_hover=self._on_hover,
            content=wrap_with_context_menu(
                row_content,
                self.context_actions,
                title=track.name,
                get_page=lambda: getattr(self, "page", None),
            ),
        )

    def set_album_visible(self, visible: bool, *, update: bool = True) -> None:
        """Muestra u oculta la columna de álbum (D26 cuando el panel de cola empuja Gestión)."""
        target = bool(visible)
        if self._album_cell.visible == target:
            return
        self._album_cell.visible = target
        if update:
            try:
                self._album_cell.update()
            except Exception:
                pass

    def context_actions(self) -> list[ContextMenuAction]:
        """Construye las acciones contextuales disponibles para la canción."""
        tr = self.track
        actions: list[ContextMenuAction] = []
        if self._on_play is not None:
            actions.append(
                ContextMenuAction(
                    "Reproducir",
                    icon=PIcons.PLAY,
                    on_click=lambda: self._on_play(tr),
                )
            )
        if self._on_play_next is not None:
            actions.append(
                ContextMenuAction(
                    "Reproducir siguiente",
                    icon=PIcons.SKIP_FORWARD,
                    on_click=lambda: self._on_play_next(tr),
                )
            )
        if self._on_add_to_queue is not None:
            actions.append(
                ContextMenuAction(
                    "Añadir a la cola",
                    icon=PIcons.QUEUE,
                    on_click=lambda: self._on_add_to_queue(tr),
                )
            )
        if self._on_download is not None:
            actions.append(
                ContextMenuAction(
                    "Ya descargada" if self._download_done else "Descargar canción",
                    icon=PIcons.DOWNLOAD_SIMPLE,
                    on_click=lambda: self._on_download(tr.id),
                    disabled=self._download_busy or self._download_done,
                )
            )
        if self._on_open_external is not None or tr.source_url:
            def _open_ext() -> None:
                if self._on_open_external is not None:
                    self._on_open_external(tr)
                else:
                    open_external(getattr(self, "page", None), tr.source_url or "")

            actions.append(
                ContextMenuAction(
                    "Abrir en plataforma",
                    icon=PIcons.ARROW_SQUARE_OUT,
                    on_click=_open_ext,
                )
            )
        actions.append(
            ContextMenuAction(
                "Deseleccionar" if tr.selected else "Seleccionar",
                icon=PIcons.CIRCLE if tr.selected else PIcons.CHECK_CIRCLE,
                on_click=lambda: self._on_toggle(tr.id),
                divider_before=bool(actions),
            )
        )
        actions.extend(self._extra_actions)
        return actions

    def _handle_download_click(self, _e=None) -> None:
        if self._download_busy or self._on_download is None:
            return
        self._on_download(self.track.id)

    def _sync_status_cell(self) -> None:
        status = self.track.transfer_status
        transfer_active = status in ("searching", "transferred", "not_found", "error", "revision_necesaria")
        if self._download_done:
            key: tuple = ("done",)
            if self._status_cell_state == key:
                return
            self._status_cell_state = key
            self._status_cell.content = self._done_icon
            self._status_cell.tooltip = "Descargada"
            self._status_cell.on_click = None
            self._status_cell.scale = 1.0
            return
        if self._hovered and self._on_download is not None and not transfer_active:
            key = ("hover_dl", self._download_busy)
            if self._status_cell_state == key:
                return
            self._status_cell_state = key
            self._status_cell.content = (
                self._hover_dl_busy_icon if self._download_busy else self._hover_dl_icon
            )
            self._status_cell.tooltip = (
                "Descarga en curso" if self._download_busy else "Descargar canción"
            )
            self._status_cell.on_click = (
                None if self._download_busy else self._handle_download_click
            )
            return
        key = ("status", status)
        if self._status_cell_state == key:
            return
        self._status_cell_state = key
        self._status_cell.content = _status_icon(status)
        self._status_cell.tooltip = None
        self._status_cell.on_click = None
        self._status_cell.scale = 1.0

    def _on_hover(self, e: ft.HoverEvent) -> None:
        """Maneja hover cambiando fondo y celda de descarga sin reconstruir controles."""
        hovered = e.data == "true" or e.data is True
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self.bgcolor = BG_HOVER if hovered else BG_LIST
        self._sync_status_cell()
        self.update()

    def refresh(
        self,
        track: Track,
        notify: bool = True,
        *,
        download_busy: bool | None = None,
        download_done: bool | None = None,
    ) -> bool:
        """
        Actualiza la fila con nuevos datos del track.

        Solo pinta los campos que cambiaron respecto al último render. En
        listas largas esto evita reemplazar el icono de estado y publicar una
        actualización por cada fila cuando solo cambió una selección.

        Args:
            track: Instancia actualizada de Track.
            notify: Si es True publica el cambio de esta fila. Los llamadores
                que actualizan muchas filas pueden pasar False y agrupar.
            download_busy: Si no es None, actualiza el bloqueo de descarga en curso.
            download_done: Si no es None, actualiza si la pista ya fue descargada.

        Returns:
            True si algún campo visual cambió.
        """
        dirty = False
        self.track = track

        if self._last_selected != track.selected:
            self._last_selected = track.selected
            self._chk.value = track.selected
            dirty = True

        album = track.album or "—"
        if self._last_album != album:
            self._last_album = album
            self._album_text.value = album
            dirty = True

        # La portada puede resolverse después del primer render (descarga o
        # lectura local). Sin esto la fila se queda con el icono de respaldo.
        if self._last_img_url != track.img_url:
            self._last_img_url = track.img_url
            self._thumb.content = _thumb_content_for(track.img_url, box=55)
            dirty = True

        status_changed = self._last_status != track.transfer_status
        if status_changed:
            self._last_status = track.transfer_status

        busy_changed = download_busy is not None and self._download_busy != bool(download_busy)
        if busy_changed:
            self._download_busy = bool(download_busy)

        done_changed = download_done is not None and self._download_done != bool(download_done)
        if done_changed:
            self._download_done = bool(download_done)

        if status_changed or busy_changed or done_changed:
            self._sync_status_cell()
            dirty = True

        if dirty and notify:
            self.update()
        return dirty
