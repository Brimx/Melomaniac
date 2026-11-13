"""ui/views/playlist_detail_rows.py — Filas y menú contextual para PlaylistDetailView."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import flet as ft

from core.models import Track
from engine.downloader import open_in_file_manager
from ui.components.context_menu import ContextMenuAction, build_context_menu_button, wrap_with_context_menu
from ui.components.song_row import ITEM_H, _thumb_content_for
from ui.components.widgets import safe_update
from ui.theme.fonts import font_family_for, mono_family
from ui.theme.icons import PIcons, app_icon
from ui.theme.tokens import (
    ACCENT,
    BG_HOVER,
    BG_LIST,
    BG_SURFACE,
    BORDER_ROW,
    MOTION_HOVER_MS,
    OVERLAY_14,
    SKELETON_DARK,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def apply_row_visual_state(
    row: ft.Container, *, selected: bool, hovering: bool = False
) -> None:
    """Actualiza el fondo y borde izquierdo de una fila de detalle según selección."""
    del hovering
    row.bgcolor = BG_HOVER if selected else BG_LIST
    row.border = (
        ft.Border.only(
            left=ft.BorderSide(2.5, ACCENT),
            bottom=ft.BorderSide(0.5, BORDER_ROW),
        )
        if selected
        else ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW))
    )


def build_track_context_actions(
    view,
    track: Track,
    index: int,
    action_icon: ft.Control | None = None,
) -> list[ContextMenuAction]:
    """Construye las acciones del menú contextual para una fila de Biblioteca."""
    has_disk = view._has_file_by_id.get(id(track))
    if has_disk is None:
        has_disk = bool(track.source_path and os.path.isfile(track.source_path))
    actions: list[ContextMenuAction] = []
    if has_disk:
        actions.extend([
            ContextMenuAction(
                "Reproducir",
                icon=PIcons.PLAY,
                on_click=lambda: view._play_single_track(track, index),
            ),
            ContextMenuAction(
                "Reproducir siguiente",
                icon=PIcons.SKIP_FORWARD,
                on_click=lambda: view._play_next_track(track),
            ),
            ContextMenuAction(
                "Añadir a la cola",
                icon=PIcons.QUEUE,
                on_click=lambda: view._enqueue_single_track(track),
            ),
            ContextMenuAction(
                "Mostrar en carpeta",
                icon=PIcons.FOLDER_OPEN,
                on_click=lambda: open_in_file_manager(track.source_path),
            ),
        ])
    else:
        actions.extend([
            ContextMenuAction(
                "Descargar canción",
                icon=PIcons.DOWNLOAD_SIMPLE,
                on_click=(
                    (lambda: view._download_single_track(track, action_icon))
                    if action_icon is not None
                    else None
                ),
                disabled=view.app_state is None or action_icon is None,
            ),
            ContextMenuAction(
                "Buscar alternativa...",
                icon=PIcons.MAGNIFYING_GLASS,
                on_click=lambda: view._page_ref.run_task(
                    view._open_track_recovery, track, action_icon
                ),
                disabled=view.app_state is None,
            ),
        ])
    if track.source_url:
        actions.append(
            ContextMenuAction(
                "Abrir en plataforma",
                icon=PIcons.ARROW_SQUARE_OUT,
                on_click=lambda: view._launch(track.source_url or ""),
            )
        )
    if view._selection_mode:
        is_sel = id(track) in view._selected_ids
        actions.append(
            ContextMenuAction(
                "Deseleccionar" if is_sel else "Seleccionar",
                icon=PIcons.CIRCLE if is_sel else PIcons.CHECK_CIRCLE,
                on_click=lambda: view.toggle_track_selection(track),
                divider_before=bool(actions),
            )
        )
    else:
        actions.append(
            ContextMenuAction(
                "Seleccionar varias...",
                icon=PIcons.CHECK_CIRCLE,
                on_click=lambda: view._start_selection_with(track),
                divider_before=bool(actions),
            )
        )
    return actions


def build_detail_track_row(
    view,
    track: Track,
    index: int,
    *,
    has_file: Optional[bool] = None,
) -> ft.Container:
    """Fila Biblioteca: [chk?][#][thumb48][título/artista expand3][álbum expand2][dur48][open/play 32][⋮ 28]."""
    if has_file is None:
        has_file = view._has_file_by_id.get(id(track), False)

    thumb = ft.Container(
        width=48,
        height=48,
        border_radius=6,
        bgcolor=SKELETON_DARK,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=_thumb_content_for(track.img_url, box=48, icon_size=16),
    )
    title = ft.Text(
        track.name,
        size=13,
        color=TEXT_PRIMARY,
        font_family=font_family_for(track.name, "semibold"),
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    artist = ft.Text(
        track.artist,
        size=11,
        color=TEXT_MUTED,
        font_family=font_family_for(track.artist),
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    album = ft.Text(
        track.album or "—",
        size=11,
        color=TEXT_MUTED,
        font_family=font_family_for(track.album or ""),
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    dur = ft.Text(track.duration, size=11, color=TEXT_DIM, font_family=mono_family("light"))

    chk = ft.Checkbox(
        value=id(track) in view._selected_ids,
        visible=view._selection_mode,
        fill_color={
            ft.ControlState.SELECTED: ACCENT,
            ft.ControlState.DEFAULT: BG_SURFACE,
        },
        check_color=TEXT_PRIMARY,
        border_side=ft.BorderSide(1.5, TEXT_DIM),
        on_change=lambda _e, tr=track: view.toggle_track_selection(tr),
    )
    view._row_checkboxes[id(track)] = chk

    action_icon = ft.Container(
        content=app_icon(
            PIcons.PLAY if has_file else PIcons.DOWNLOAD_SIMPLE,
            size=16,
            color=ACCENT if has_file else TEXT_MUTED,
            fill=has_file,
        ),
        width=32,
        height=32,
        border_radius=16,
        bgcolor=ft.Colors.TRANSPARENT,
        alignment=ft.Alignment.CENTER,
        scale=1.0,
        animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
        animate_scale=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
    )

    def _on_action_hover(e: ft.HoverEvent) -> None:
        hovering = e.data == "true" or e.data is True
        action_icon.bgcolor = OVERLAY_14 if hovering else ft.Colors.TRANSPARENT
        action_icon.scale = 1.12 if hovering else 1.0
        safe_update(action_icon)

    def _on_action_click(_e) -> None:
        if track.source_path and os.path.isfile(track.source_path):
            view._play_single_track(track, index)
        else:
            view._download_single_track(track, action_icon)

    action_icon.on_hover = _on_action_hover
    action_icon.on_click = _on_action_click
    view._action_containers[id(track)] = action_icon

    menu_btn = build_context_menu_button(
        lambda tr=track, idx=index, ic=action_icon: view._track_context_actions(tr, idx, ic),
        tooltip=None,
        width=28,
        height=28,
        icon_size=16,
        title=track.name,
        get_page=lambda: view._page_ref,
    )

    row_inner = ft.Row(
        controls=[
            chk,
            ft.Container(
                content=ft.Text(
                    str(index), size=11, color=TEXT_MUTED, font_family=mono_family("light")
                ),
                width=28,
                alignment=ft.Alignment.CENTER,
            ),
            thumb,
            ft.Column(
                controls=[title, artist],
                spacing=1,
                expand=3,
                tight=True,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Container(content=album, expand=2, alignment=ft.Alignment.CENTER_LEFT),
            ft.Container(content=dur, width=48, alignment=ft.Alignment.CENTER),
            action_icon,
            menu_btn,
        ],
        spacing=16,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    is_selected = id(track) in view._selected_ids
    row = ft.Container(
        height=ITEM_H,
        bgcolor=BG_HOVER if is_selected else BG_LIST,
        border=(
            ft.Border.only(
                left=ft.BorderSide(2.5, ACCENT),
                bottom=ft.BorderSide(0.5, BORDER_ROW),
            )
            if is_selected
            else ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW))
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        animate_opacity=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
        content=wrap_with_context_menu(
            row_inner,
            lambda tr=track, idx=index, ic=action_icon: view._track_context_actions(tr, idx, ic),
            title=track.name,
            get_page=lambda: view._page_ref,
        ),
    )
    view._row_containers[id(track)] = row

    def _on_click(_e) -> None:
        if view._selection_mode:
            view.toggle_track_selection(track)
            return

        async def _press_and_act() -> None:
            try:
                row.opacity = 0.85
                row.update()
                try:
                    await asyncio.sleep(0.08)
                except asyncio.CancelledError:
                    raise
            finally:
                try:
                    row.opacity = 1.0
                    row.update()
                except Exception:
                    pass
            if track.source_path and os.path.isfile(track.source_path):
                view._play_single_track(track, index)
            else:
                view._download_single_track(track, action_icon)

        try:
            task = asyncio.create_task(_press_and_act())
            view._press_tasks.add(task)
            task.add_done_callback(view._press_tasks.discard)
        except Exception:
            try:
                row.opacity = 1.0
            except Exception:
                pass
            if track.source_path and os.path.isfile(track.source_path):
                view._play_single_track(track, index)
            else:
                view._download_single_track(track, action_icon)

    row.on_click = _on_click
    return row


__all__ = [
    "apply_row_visual_state",
    "build_detail_track_row",
    "build_track_context_actions",
]
