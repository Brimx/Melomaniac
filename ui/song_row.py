"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.2.0                               ║
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
Versión: 4.2.0
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
    ACCENT, BG_SURFACE, BORDER_ROW,
)
from ui.widgets import _status_icon

# Altura fija de fila para cálculos de virtualización
ITEM_H = 64


class SkeletonRow(ft.Container):
    """
    Fila placeholder animada mostrada durante carga de canciones.
    
    Implementa un skeleton screen que reduce la percepción de latencia
    al mostrar la estructura de la fila antes de que los datos estén
    disponibles. El efecto shimmer (pulse) proporciona feedback visual
    de que la carga está en progreso.
    
    Attributes:
        _pulse_task: Tarea asyncio que controla la animación de pulse.
    
    Methods:
        start_pulse: Inicia la animación de pulse (actualmente placeholder).
        stop_pulse: Detiene la animación y cancela la tarea.
    
    Note:
        El skeleton mantiene las mismas proporciones y espaciado que
        SongRow para evitar layout shift cuando los datos reales se cargan.
        Esto es crítico para UX: el usuario no percibe "saltos" visuales.
    """

    def __init__(self, _index: int):
        """
        Inicializa una fila skeleton con placeholders para cada elemento.

        Args:
            _index: Índice de la fila (usado para stagger escalonado).
        """
        self._pulse_task: Optional[asyncio.Task] = None
        self._index = _index

        # Placeholders con dimensiones idénticas a SongRow
        self._num    = ft.Container(width=28, height=10, border_radius=3, bgcolor=SKELETON_DARK)
        self._thumb  = ft.Container(width=55, height=55, border_radius=8, bgcolor=SKELETON_DARK)
        self._title  = ft.Container(expand=3, height=10, border_radius=3, bgcolor=SKELETON_DARK)
        self._album  = ft.Container(expand=2, height=10, border_radius=3, bgcolor=SKELETON_DARK)
        self._dur    = ft.Container(width=48,  height=10, border_radius=3, bgcolor=SKELETON_DARK)
        self._status = ft.Container(width=26,  height=10, border_radius=3, bgcolor=SKELETON_DARK)
        self._chk    = ft.Container(width=32,  height=18, border_radius=4, bgcolor=SKELETON_DARK)

        super().__init__(
            height=ITEM_H,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)),
            content=ft.Row(
                controls=[self._num, self._thumb, self._title,
                           self._album, self._dur, self._status, self._chk],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            opacity=0.45,
            scale=0.98,
            animate_opacity=ft.Animation(600, ft.AnimationCurve.EASE_IN_OUT),
            animate_scale=ft.Animation(600, ft.AnimationCurve.EASE_IN_OUT),
        )

    async def start_pulse(self) -> None:
        """
        Animación sutil opacity 0.45↔1.0 + scale 0.98↔1.0 (600ms, stagger 60ms por índice).
        Da sensación de carga real sin ser intrusiva.
        """
        self._pulse_task = asyncio.current_task()
        # stagger inicial según índice para ola
        try:
            await asyncio.sleep(0.06 * self._index)
        except asyncio.CancelledError:
            return
        try:
            while True:
                self.opacity = 1.0
                self.scale = 1.0
                try:
                    self.update()
                except Exception:
                    pass
                await asyncio.sleep(0.6)
                self.opacity = 0.45
                self.scale = 0.98
                try:
                    self.update()
                except Exception:
                    pass
                await asyncio.sleep(0.6)
        except asyncio.CancelledError:
            pass

    def stop_pulse(self) -> None:
        """
        Detiene la animación de pulse y cancela la tarea asyncio.

        Debe ser llamado antes de reemplazar el skeleton con SongRow
        para evitar tareas huérfanas en el event loop. Resetea opacity/scale.
        """
        if self._pulse_task:
            self._pulse_task.cancel()
            self._pulse_task = None
        try:
            self.opacity = 1.0
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

    def __init__(self, track: Track, index: int, on_toggle: Callable[[str], None]):
        """
        Inicializa una fila de canción con todos sus componentes visuales.
        
        Args:
            track: Instancia de Track con metadatos de la canción.
            index: Número de fila (1-indexed) mostrado al usuario.
            on_toggle: Callback (track_id: str) -> None ejecutado al
                      cambiar el estado del checkbox.
        """
        self.track      = track
        self._on_toggle = on_toggle

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
                ft.Container(content=self._status_icon, width=26, alignment=ft.Alignment.CENTER),
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

    def _on_hover(self, e: ft.HoverEvent) -> None:
        """
        Maneja el evento de hover cambiando el color de fondo.
        
        Proporciona feedback visual sutil al pasar el cursor sobre la fila.
        La animación de 100ms (definida en __init__) suaviza la transición.
        
        Args:
            e: Evento de hover con data "true" (entrando) o "false" (saliendo).
        """
        self.bgcolor = BG_HOVER if e.data == "true" else BG_LIST
        self.update()

    def refresh(self, track: Track) -> None:
        """
        Actualiza la fila con nuevos datos del track.
        
        Usado para reflejar cambios en el estado de la canción sin recrear
        toda la fila. Actualiza checkbox y icono de estado eficientemente.
        
        Args:
            track: Instancia actualizada de Track.
        
        Note:
            Solo actualiza los elementos que típicamente cambian durante
            una transferencia (selected, transfer_status). Título, artista
            y thumbnail permanecen constantes.
        """
        self.track       = track
        self._chk.value  = track.selected
        self._album_text.value = track.album or "—"
        status_cell      = self.content.controls[5]
        status_cell.content = _status_icon(track.transfer_status)
        self.update()
