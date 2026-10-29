"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.6.0                               ║
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
Versión: 4.6.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

import flet as ft

from core.models import Track
from ui.fonts import font_family_for, mono_family
from ui.tokens import (
    BG_LIST, BG_HOVER, SKELETON_DARK,
    TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM,
    ACCENT, SUCCESS, BG_SURFACE, BORDER_ROW,
    BG_INPUT, BORDER_LIGHT,
)
from ui.widgets import _status_icon, input_border

# Altura fija de fila para cálculos de virtualización
ITEM_H = 64


class SkeletonRow(ft.Container):
    """
    Fila placeholder con Shimmer + zoom sutil (reemplaza opacity pulse).

    Usa ft.Shimmer(base/highlight) para efecto deslizante y mantiene
    animate_scale 600ms para zoom. Dinámico por viewport se calcula en
    ui/main_ui.py (_calc_skeleton_count) y se muestra entre cambios de
    vista / organizar (ver main_ui _show_reload_shimmer).
    """

    def __init__(self, _index: int):
        self._pulse_task: Optional[asyncio.Task] = None
        self._index = _index

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
        # Shimmer envolviendo la fila — base/highlight desde tokens (OLED)
        try:
            _shimmer = ft.Shimmer(
                base_color=ft.Colors.with_opacity(0.35, SKELETON_DARK),
                highlight_color=ft.Colors.with_opacity(0.9, BG_SURFACE),
                content=_row,
            )
        except Exception:
            # fallback si Shimmer no disponible en esta versión
            _shimmer = _row

        super().__init__(
            height=ITEM_H,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
            content=_shimmer,
            opacity=1.0,
            scale=0.98,
            animate_scale=ft.Animation(600, ft.AnimationCurve.EASE_IN_OUT),
        )

    async def start_pulse(self) -> None:
        """Zoom sutil 0.98↔1.0 (600ms, stagger 60ms). Shimmer corre solo via ft.Shimmer."""
        self._pulse_task = asyncio.current_task()
        try:
            await asyncio.sleep(0.06 * self._index)
        except asyncio.CancelledError:
            return
        try:
            while True:
                self.scale = 1.0
                try:
                    self.update()
                except Exception:
                    pass
                await asyncio.sleep(0.6)
                self.scale = 0.98
                try:
                    self.update()
                except Exception:
                    pass
                await asyncio.sleep(0.6)
        except asyncio.CancelledError:
            pass

    def stop_pulse(self) -> None:
        if self._pulse_task:
            self._pulse_task.cancel()
            self._pulse_task = None
        try:
            self.scale = 1.0
        except Exception:
            pass


def build_preview_row(track: Track, index: int) -> ft.Container:
    """
    Fila Preview readonly — espejo 1:1 de SongRow sin columna Estado/Sel.
    Mantiene ITEM_H, padding 16/0, spacing 16, widths 32/55/expand3/expand2/48,
    tipografías y colores idénticos para alineación visual con SongRow.
    Solo quita Estado (26) y Checkbox (32) — resto idéntico.
    """
    # thumbnail idéntico a SongRow (55) con fallback
    if track.img_url:
        thumb_content = ft.Image(
            src=track.img_url,
            fit=ft.BoxFit.COVER,
            error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=18),
        )
    else:
        thumb_content = ft.Container(
            content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=20),
            alignment=ft.Alignment.CENTER,
        )
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


class PreviewSkeletonRow(ft.Container):
    """
    Skeleton espejo de Preview (sin Estado/Sel) — mismo ritmo Shimmer+zoom.
    """

    def __init__(self, _index: int):
        self._pulse_task: Optional[asyncio.Task] = None
        self._index = _index
        self._num = ft.Container(width=28, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._thumb = ft.Container(width=55, height=55, border_radius=8, bgcolor=BG_SURFACE)
        self._title = ft.Container(expand=3, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._album = ft.Container(expand=2, height=10, border_radius=3, bgcolor=BG_SURFACE)
        self._dur = ft.Container(width=48, height=10, border_radius=3, bgcolor=BG_SURFACE)
        _row = ft.Row(
            controls=[self._num, self._thumb, self._title, self._album, self._dur],
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        try:
            _shimmer = ft.Shimmer(
                base_color=ft.Colors.with_opacity(0.35, SKELETON_DARK),
                highlight_color=ft.Colors.with_opacity(0.9, BG_SURFACE),
                content=_row,
            )
        except Exception:
            _shimmer = _row
        super().__init__(
            height=ITEM_H,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
            content=_shimmer,
            opacity=1.0,
            scale=0.98,
            animate_scale=ft.Animation(600, ft.AnimationCurve.EASE_IN_OUT),
        )

    async def start_pulse(self) -> None:
        self._pulse_task = asyncio.current_task()
        try:
            await asyncio.sleep(0.06 * self._index)
        except asyncio.CancelledError:
            return
        try:
            while True:
                self.scale = 1.0
                try:
                    self.update()
                except Exception:
                    pass
                await asyncio.sleep(0.6)
                self.scale = 0.98
                try:
                    self.update()
                except Exception:
                    pass
                await asyncio.sleep(0.6)
        except asyncio.CancelledError:
            pass

    def stop_pulse(self) -> None:
        if self._pulse_task:
            self._pulse_task.cancel()
            self._pulse_task = None
        try:
            self.scale = 1.0
        except Exception:
            pass


class LibrarySkeletonRow(ft.Container):
    """
    Skeleton Biblioteca — clon de SkeletonRow con zonas adaptadas al detail.

    Gestión tiene [#][thumb][title][album][dur][status][chk]; Biblioteca
    detail tiene [#][thumb48][title][album][dur][open-btn]: se atenúan esas
    6 zonas (sin status/chk). Mismo Shimmer + scale 0.98↔1.0 600ms stagger
    e ITEM_H para paridad exacta con Gestión.
    """

    def __init__(self, _index: int):
        self._pulse_task: Optional[asyncio.Task] = None
        self._index = _index
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
        try:
            _shimmer = ft.Shimmer(
                base_color=ft.Colors.with_opacity(0.35, SKELETON_DARK),
                highlight_color=ft.Colors.with_opacity(0.9, BG_SURFACE),
                content=_row,
            )
        except Exception:
            _shimmer = _row
        super().__init__(
            height=ITEM_H,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
            content=_shimmer,
            opacity=1.0,
            scale=0.98,
            animate_scale=ft.Animation(600, ft.AnimationCurve.EASE_IN_OUT),
        )

    async def start_pulse(self) -> None:
        """Zoom sutil 0.98↔1.0 (600ms, stagger 60ms) — idéntico a SkeletonRow."""
        self._pulse_task = asyncio.current_task()
        try:
            await asyncio.sleep(0.06 * self._index)
        except asyncio.CancelledError:
            return
        try:
            while True:
                self.scale = 1.0
                try:
                    self.update()
                except Exception:
                    pass
                await asyncio.sleep(0.6)
                self.scale = 0.98
                try:
                    self.update()
                except Exception:
                    pass
                await asyncio.sleep(0.6)
        except asyncio.CancelledError:
            pass

    def stop_pulse(self) -> None:
        if self._pulse_task:
            self._pulse_task.cancel()
            self._pulse_task = None
        try:
            self.scale = 1.0
        except Exception:
            pass


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
        """
        self.track      = track
        self._on_toggle = on_toggle
        self._on_move = on_move
        self._on_download = on_download
        self._index = index
        self._hovered: bool = False
        self._download_busy: bool = bool(download_busy)
        self._download_done: bool = bool(download_done)
        # Último estado pintado: permite refrescar solo lo que cambió.
        self._last_selected = track.selected
        self._last_album = track.album or "—"
        self._last_status = track.transfer_status

        # ──────────────────────────────────────────────────────────────
        # THUMBNAIL DE ÁLBUM CON FALLBACK
        # ──────────────────────────────────────────────────────────────
        # Intenta cargar imagen de álbum, fallback a icono si falla
        
        if track.img_url:
            _thumb_content = ft.Image(
                src=track.img_url,
                fit=ft.BoxFit.COVER,
                error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=18),
            )
        else:
            _thumb_content = ft.Container(
                content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=20),
                alignment=ft.Alignment.CENTER,
            )

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

        self._status_cell = ft.Container(
            content=self._status_icon,
            width=26,
            alignment=ft.Alignment.CENTER,
            animate=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
        )
        self._sync_status_cell()
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
                ft.Container(content=album_text,         expand=2, alignment=ft.Alignment.CENTER_LEFT),
                ft.Container(content=dur_text,          width=48, alignment=ft.Alignment.CENTER),
                self._status_cell,
                ft.Container(content=self._chk,         width=32, alignment=ft.Alignment.CENTER),
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
            animate=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
            on_hover=self._on_hover,
            content=row_content,
        )

    def _handle_download_click(self, _e=None) -> None:
        if self._download_busy or self._on_download is None:
            return
        self._on_download(self.track.id)

    def _sync_status_cell(self) -> None:
        status = self.track.transfer_status
        transfer_active = status in ("searching", "transferred", "not_found", "error", "revision_necesaria")
        if self._download_done:
            self._status_cell.content = ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=SUCCESS, size=15)
            self._status_cell.tooltip = "Descargada"
            self._status_cell.on_click = None
            return
        if self._hovered and self._on_download is not None and not transfer_active:
            self._status_cell.content = ft.Icon(
                ft.Icons.DOWNLOAD_ROUNDED,
                color=TEXT_DIM if self._download_busy else ACCENT,
                size=16,
            )
            self._status_cell.tooltip = (
                "Descarga en curso" if self._download_busy else "Descargar canción"
            )
            self._status_cell.on_click = (
                None if self._download_busy else self._handle_download_click
            )
            return
        self._status_cell.content = _status_icon(status)
        self._status_cell.tooltip = None
        self._status_cell.on_click = None

    def _on_hover(self, e: ft.HoverEvent) -> None:
        """
        Maneja el evento de hover cambiando el color de fondo y la celda de acción.
        
        Proporciona feedback visual sutil al pasar el cursor sobre la fila.
        La animación de 100ms (definida en __init__) suaviza la transición.
        
        Args:
            e: Evento de hover con data "true" (entrando) o "false" (saliendo).
        """
        hovered = e.data == "true" or e.data is True
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
