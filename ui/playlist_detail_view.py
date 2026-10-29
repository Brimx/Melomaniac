"""
ui/playlist_detail_view.py — Detalle playlist Biblioteca: header + Ver en plataforma + lista tracks bajo demanda

Para playlists de plataforma ofrece «Descargar» (cola yt-dlp de ``AppState``).
Para carpetas locales (``loader``) ofrece abrir la carpeta y quitarla de la
Biblioteca.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from core.config import LOCAL_LIBRARY_LABEL
from core.library_models import PlaylistSummary
from core.models import Track, TransferState
from engine.downloader import open_in_file_manager
from services import settings_store
from ui.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, font_family_for, mono_family
from ui.song_row import ITEM_H, LibrarySkeletonRow
from ui.tokens import (
    ACCENT, BG_HOVER, BG_LIST, BG_SURFACE, BORDER_LIGHT, BORDER_MUTED, BORDER_ROW,
    SKELETON_DARK, TEXT_DIM, TEXT_MUTED, TEXT_PRIMARY,
)
from ui.widgets import _ghost_btn, _primary_btn, app_text, notify, open_external


class PlaylistDetailView(ft.Container):
    def __init__(self, page: ft.Page, summary: PlaylistSummary, service, on_back: Callable[[], None],
                 app_state=None, loader: Optional[Callable] = None,
                 on_remove: Optional[Callable[[], None]] = None,
                 on_open_downloads: Optional[Callable[[], None]] = None,
                 player_bar=None):
        super().__init__(expand=True, bgcolor=BG_LIST)
        self.app_state = app_state
        self.player_bar = player_bar
        self._loader = loader
        self._on_open_downloads = on_open_downloads
        self._is_local = summary.platform == LOCAL_LIBRARY_LABEL
        self._page_ref = page
        self.summary = summary
        self.service = service
        self.on_back = on_back
        self._tracks: list[Track] = []
        self._loading = True
        self._error = ""

        # header
        cover_url = summary.cover_urls[0] if summary.cover_urls else ""
        if cover_url:
            cover = ft.Container(width=120, height=120, border_radius=10, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=ft.Image(src=cover_url, fit=ft.BoxFit.COVER, error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM)))
        else:
            cover = ft.Container(width=120, height=120, border_radius=10, bgcolor=SKELETON_DARK, content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=32), alignment=ft.Alignment.CENTER)

        self._title = app_text(summary.name, size=16, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD)
        self._desc = app_text(summary.description or "", size=11, color=TEXT_MUTED, font_family=FONT_TEXT, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS) if summary.description else ft.Container()
        count_label = f"{summary.track_count} canciones" if summary.track_count is not None else "Conteo al abrir"
        self._count = app_text(f"{count_label} • {summary.platform}", size=11, color=TEXT_DIM, font_family=mono_family("light"))

        self._open_btn = _ghost_btn("Ver playlist en plataforma", ft.Icons.OPEN_IN_NEW, lambda e: self._launch(summary.external_url), width=260, height=40) if summary.external_url else ft.Container()

        if self._is_local:
            self._play_all_btn = _primary_btn("Reproducir", ft.Icons.PLAY_ARROW_ROUNDED, self._on_play_all, height=40)
            self._play_all_btn.disabled = True
            actions = [
                self._play_all_btn,
                _ghost_btn("Abrir carpeta", ft.Icons.FOLDER_OPEN, lambda e: self._open_folder(), height=40),
                _ghost_btn("Quitar de la Biblioteca", ft.Icons.REMOVE_CIRCLE_OUTLINE, lambda e: on_remove() if on_remove else None, height=40),
            ]
            self._download_btn = None
        else:
            self._play_all_btn = _primary_btn("Reproducir", ft.Icons.PLAY_ARROW_ROUNDED, self._on_play_all, height=40)
            self._play_all_btn.visible = False
            self._download_btn = _primary_btn("Descargar playlist", ft.Icons.DOWNLOAD, self._on_download, height=40)
            self._download_btn.disabled = True
            actions = [self._play_all_btn, self._download_btn]
        if summary.external_url:
            actions.append(self._open_btn)
        actions_row = ft.Row(controls=actions, spacing=8, wrap=True)

        header = ft.Container(
            bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_LIGHT), border_radius=12, padding=ft.Padding.all(12),
            content=ft.Row(controls=[cover, ft.Column(controls=[self._title, self._count, self._desc, actions_row], spacing=6, expand=True, alignment=ft.MainAxisAlignment.CENTER)], spacing=12),
        )

        # list area — skeletons dinámicos según altura disponible (paridad Gestión)
        self._skeletons: list[LibrarySkeletonRow] = []
        self._skeleton_tasks: list[asyncio.Task] = []
        self._skeleton_list = ft.ListView(expand=True, spacing=0)
        self._skeleton_wrap = ft.Container(content=self._skeleton_list, expand=True, visible=True)
        self._sync_skeleton_count()
        self._list_view = ft.ListView(expand=True, spacing=0, visible=False)
        self._error_box = ft.Container(visible=False, bgcolor=BG_SURFACE, border=ft.Border.all(0.8, BORDER_MUTED), border_radius=10, padding=ft.Padding.all(12), content=ft.Column(controls=[
            app_text("No se pudo cargar", size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
            app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
            _ghost_btn("Reintentar", ft.Icons.REFRESH, lambda e: self.reload()),
        ], spacing=8))

        body = ft.Column(controls=[
            ft.Row(controls=[ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=TEXT_MUTED, on_click=lambda e: on_back()), app_text("Volver", size=12, color=TEXT_MUTED)], spacing=4),
            header,
            ft.Divider(height=1, color=BORDER_MUTED),
            ft.Stack(controls=[self._skeleton_wrap, self._list_view, self._error_box], expand=True),
        ], spacing=10, expand=True)

        self.content = ft.Container(content=body, padding=ft.Padding.all(12), expand=True)
        # kick load
        try:
            asyncio.create_task(self.reload())
        except Exception:
            pass

    def _open_folder(self) -> None:
        if not open_in_file_manager(self.summary.id):
            notify(self._page_ref, "No se pudo abrir la carpeta", kind="error")

    def _on_download(self, _e=None) -> None:
        """Encola todas las pistas de la playlist en la cola de Descargas."""
        st = self.app_state
        if st is None or not self._tracks:
            return
        if st.download_state == TransferState.RUNNING:
            notify(self._page_ref, "Ya hay una descarga en curso", kind="error")
            return
        try:
            st.download_tracks(self._tracks, destination_dir=settings_store.get_download_dir())
        except Exception as exc:  # noqa: BLE001
            notify(self._page_ref, f"No se pudo iniciar la descarga: {exc}", kind="error")
            return
        cb = self._on_open_downloads
        notify(
            self._page_ref, f"Descargando {len(self._tracks)} pistas",
            action="Ver" if cb else None,
            on_action=(lambda e: cb()) if cb else None,
        )

    def _launch(self, url: str) -> None:
        """Delega en ``ui.widgets.open_external``."""
        open_external(getattr(self, "_page_ref", None), url)

    def _calc_detail_skeleton_count(self) -> int:
        """Cuántos LibrarySkeletonRow caben — espejo de main_ui._calc_skeleton_count.

        Resta breadcrumb + header (~150) + divider + padding para overfill.
        """
        try:
            page = getattr(self, "_page_ref", None)
            h = (page.height if page else None) or (page.window.height if page and getattr(page, "window", None) else None) or 900
            if not h or h < 600:
                h = 900
        except Exception:
            h = 900
        header_h = 200  # breadcrumb 36 + header card ~150 + divider/pad
        avail = max(180, h - header_h - 48)
        return max(8, min(20, int(avail // ITEM_H)))

    def _sync_skeleton_count(self) -> None:
        """Recrea skeletons si cambió el count (on_resize) — paridad Gestión."""
        try:
            new_cnt = self._calc_detail_skeleton_count()
            if new_cnt != len(self._skeletons):
                self._stop_skeleton_pulse()
                self._skeletons = [LibrarySkeletonRow(i) for i in range(new_cnt)]
                self._skeleton_list.controls = self._skeletons
                if self._skeleton_wrap.visible:
                    self._ensure_skeletons_pulsing()
                try:
                    self._skeleton_list.update()
                except Exception:
                    pass
        except Exception:
            pass

    def _ensure_skeletons_pulsing(self) -> None:
        try:
            for sk in self._skeletons:
                try:
                    self._skeleton_tasks.append(asyncio.create_task(sk.start_pulse()))
                except Exception:
                    pass
        except Exception:
            pass

    def _stop_skeleton_pulse(self) -> None:
        for sk in self._skeletons:
            try:
                sk.stop_pulse()
            except Exception:
                pass
        for t in self._skeleton_tasks:
            try:
                if not t.done():
                    t.cancel()
            except Exception:
                pass
        self._skeleton_tasks = []

    async def reload(self) -> None:
        self._loading = True
        self._error = ""
        self._sync_skeleton_count()
        self._skeleton_wrap.visible = True
        self._list_view.visible = False
        self._error_box.visible = False
        self._ensure_skeletons_pulsing()
        try:
            self.update()
        except Exception:
            pass
        try:
            if self._loader is not None:
                _meta, tracks = await self._loader()
            else:
                _meta, tracks = await self.service.fetch_playlist(self.summary.platform, self.summary.id, None)
            self._tracks = tracks

            # Mapear pistas que ya existen en descargas o carpetas locales
            self._match_local_files()

            if self._download_btn is not None:
                self._download_btn.disabled = not tracks
            self._update_header_play_count()
            self._loading = False
            self.summary.track_count = len(tracks)
            # header count real tras cargar (fix Apple count 0 en summary)
            try:
                self._count.value = f"{len(tracks)} canciones • {self.summary.platform}"
            except Exception:
                pass
            self._list_view.controls = []
            for i, tr in enumerate(tracks, 1):
                row = self._build_track_row(tr, i)
                self._list_view.controls.append(row)
            self._stop_skeleton_pulse()
            self._skeleton_wrap.visible = False
            self._list_view.visible = True
        except Exception as exc:
            auth_manager = getattr(self.service, "auth_manager", None)
            if auth_manager is not None and self._loader is None:
                auth_manager.record_operation_failure(self.summary.platform, exc)
            self._loading = False
            self._error = str(exc)[:400]
            self._stop_skeleton_pulse()
            self._skeleton_wrap.visible = False
            self._error_box.visible = True
            try:
                self._error_box.content.controls[1].value = self._error  # type: ignore
            except Exception:
                pass
        try:
            self.update()
        except Exception:
            pass

    def _match_local_files(self) -> int:
        """Asocia source_path a las pistas que ya existen en el directorio de descargas o carpetas locales."""
        try:
            from engine.downloader import _target_stem, resolve_download_dir
            from engine.parsers import _LOCAL_AUDIO_EXTENSIONS
            candidate_dirs = [resolve_download_dir(settings_store.get_download_dir())]
            for extra in settings_store.get_local_folders():
                if extra and os.path.isdir(extra):
                    candidate_dirs.append(Path(extra))

            disk_files: dict[str, str] = {}
            for d in candidate_dirs:
                try:
                    for fname in os.listdir(d):
                        lower = fname.lower()
                        if lower not in disk_files:
                            disk_files[lower] = os.path.join(str(d), fname)
                except OSError:
                    continue

            matched = 0
            for tr in self._tracks:
                if tr.source_path and os.path.isfile(tr.source_path):
                    matched += 1
                    continue
                stem = _target_stem(tr).lower()
                for ext in _LOCAL_AUDIO_EXTENSIONS:
                    candidate_name = f"{stem}{ext}".lower()
                    if candidate_name in disk_files:
                        tr.source_path = disk_files[candidate_name]
                        matched += 1
                        break
            return matched
        except Exception:
            return 0

    def _update_header_play_count(self) -> None:
        """Actualiza el botón Reproducir en la cabecera según las pistas disponibles en disco."""
        if self._play_all_btn is None:
            return
        downloaded = sum(1 for t in self._tracks if t.source_path and os.path.isfile(t.source_path))
        total = len(self._tracks)
        if downloaded > 0:
            self._play_all_btn.visible = True
            self._play_all_btn.disabled = False
            try:
                txt = "Reproducir" if downloaded == total or self._is_local else f"Reproducir ({downloaded})"
                if hasattr(self._play_all_btn.content, "value"):
                    self._play_all_btn.content.value = txt
                elif hasattr(self._play_all_btn, "text"):
                    self._play_all_btn.text = txt
            except Exception:
                pass
        else:
            if not self._is_local:
                self._play_all_btn.visible = False
            else:
                self._play_all_btn.disabled = True

    def _download_single_track(self, track: Track, action_container: ft.Container) -> None:
        """Descarga una pista individual y al completarse habilita su reproducción inmediata."""
        st = self.app_state
        if st is None:
            notify(self._page_ref, "Estado de aplicación no disponible", kind="error")
            return
        if st.download_state == TransferState.RUNNING:
            notify(self._page_ref, "Ya hay una descarga en curso", kind="error")
            return

        async def _do_download() -> None:
            action_container.content = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT)
            action_container.tooltip = "Descargando..."
            try:
                action_container.update()
            except Exception:
                pass

            notify(self._page_ref, f"Descargando «{track.name}»...")
            try:
                task = st.download_tracks([track], destination_dir=settings_store.get_download_dir())
                await task
            except Exception as exc:  # noqa: BLE001
                notify(self._page_ref, f"Error al descargar: {exc}", kind="error")
                action_container.content = ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, size=16, color=TEXT_MUTED)
                action_container.tooltip = "Descargar canción"
                try:
                    action_container.update()
                except Exception:
                    pass
                return

            if track.source_path and os.path.isfile(track.source_path):
                notify(
                    self._page_ref,
                    f"Descarga lista: {track.artist} — {track.name}",
                    action="Reproducir",
                    on_action=lambda e: self._play_single_track(track, 1),
                )
                action_container.content = ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=16, color=ACCENT)
                action_container.tooltip = "Reproducir"
                try:
                    action_container.update()
                except Exception:
                    pass
                self._update_header_play_count()
                try:
                    self.update()
                except Exception:
                    pass
            else:
                notify(self._page_ref, f"No se pudo descargar «{track.name}»", kind="error")
                action_container.content = ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, size=16, color=TEXT_MUTED)
                action_container.tooltip = "Descargar canción"
                try:
                    action_container.update()
                except Exception:
                    pass

        try:
            asyncio.create_task(_do_download())
        except Exception:
            pass

    def _on_play_all(self, _e=None) -> None:
        """Reproduce todas las pistas locales disponibles en el reproductor."""
        paths = [t.source_path for t in self._tracks if t.source_path and os.path.isfile(t.source_path)]
        if not paths:
            notify(self._page_ref, "No hay archivos disponibles en disco", kind="error")
            return
        if self.player_bar is not None and hasattr(self.player_bar, "play_files"):
            self.player_bar.play_files(paths, start_index=0)
            notify(self._page_ref, f"Reproduciendo {len(paths)} canciones")
        elif self.player_bar is not None and hasattr(self.player_bar, "play_file"):
            self.player_bar.play_file(paths[0])

    def _play_single_track(self, track: Track, index: int) -> None:
        """Reproduce una sola pista local en el reproductor encolando la lista."""
        if not track.source_path or not os.path.isfile(track.source_path):
            notify(self._page_ref, "Archivo no encontrado en disco", kind="error")
            return
        paths = [t.source_path for t in self._tracks if t.source_path and os.path.isfile(t.source_path)]
        if self.player_bar is not None and hasattr(self.player_bar, "play_files") and paths:
            try:
                start_idx = paths.index(track.source_path)
            except ValueError:
                start_idx = max(0, min(len(paths) - 1, index - 1))
            self.player_bar.play_files(paths, start_index=start_idx)
        elif self.player_bar is not None and hasattr(self.player_bar, "play_file"):
            self.player_bar.play_file(track.source_path)

    def _open_track(self, track: Track) -> None:
        """Pista local → reproducir si hay archivo; si no, abrir explorador. Plataforma → su URL."""
        if self._is_local and track.source_path and os.path.isfile(track.source_path):
            self._play_single_track(track, 1)
        elif self._is_local and track.source_path:
            open_in_file_manager(track.source_path)
        else:
            self._launch(track.source_url or "")

    def _build_track_row(self, track: Track, index: int) -> ft.Container:
        """Fila Biblioteca: [#][thumb48][título/artista expand3][álbum expand2][dur48][open/play 32].

        Misma geometría que SongRow Gestión (spacing 16, ITEM_H) + columna
        álbum. Hover: BG_HOVER + scale 1.01. Click: press scale 0.99 +
        opacidad 0.85 antes de abrir URL o reproducir.
        """
        has_file = bool(track.source_path and os.path.isfile(track.source_path))

        thumb = ft.Container(
            width=48, height=48, border_radius=6, bgcolor=SKELETON_DARK, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Image(src=track.img_url, fit=ft.BoxFit.COVER, error_content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=16)) if track.img_url else ft.Container(content=ft.Icon(ft.Icons.MUSIC_NOTE, color=TEXT_DIM, size=16), alignment=ft.Alignment.CENTER),
        )
        title = ft.Text(track.name, size=13, color=TEXT_PRIMARY, font_family=font_family_for(track.name, "semibold"), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        artist = ft.Text(track.artist, size=11, color=TEXT_MUTED, font_family=font_family_for(track.artist), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        album = ft.Text(track.album or "—", size=11, color=TEXT_MUTED, font_family=font_family_for(track.album or ""), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        dur = ft.Text(track.duration, size=11, color=TEXT_DIM, font_family=mono_family("light"))

        action_icon = ft.Container(
            content=ft.Icon(
                ft.Icons.PLAY_ARROW_ROUNDED if has_file else ft.Icons.DOWNLOAD_ROUNDED,
                size=16,
                color=ACCENT if has_file else TEXT_MUTED,
            ),
            width=32,
            height=32,
            border_radius=16,
            alignment=ft.Alignment.CENTER,
            tooltip="Reproducir" if has_file else "Descargar canción",
            ink=True,
        )

        def _on_action_click(_e) -> None:
            if track.source_path and os.path.isfile(track.source_path):
                self._play_single_track(track, index)
            else:
                self._download_single_track(track, action_icon)

        action_icon.on_click = _on_action_click

        row = ft.Container(
            height=ITEM_H, bgcolor=BG_LIST, border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)), padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            ink=True,
            animate=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(120, ft.AnimationCurve.EASE_OUT),
            animate_opacity=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
            content=ft.Row(controls=[
                ft.Container(content=ft.Text(str(index), size=11, color=TEXT_MUTED, font_family=mono_family("light")), width=28, alignment=ft.Alignment.CENTER),
                thumb,
                ft.Column(controls=[title, artist], spacing=1, expand=3, tight=True, alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(content=album, expand=2, alignment=ft.Alignment.CENTER_LEFT),
                ft.Container(content=dur, width=48, alignment=ft.Alignment.CENTER),
                action_icon,
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

        def _on_hover(e: ft.HoverEvent) -> None:
            hovering = e.data == "true"
            row.bgcolor = BG_HOVER if hovering else BG_LIST
            row.scale = 1.01 if hovering else 1.0
            try:
                row.update()
            except Exception:
                pass

        def _on_click(_e) -> None:
            async def _press_and_act() -> None:
                try:
                    row.scale = 0.99
                    row.opacity = 0.85
                    row.update()
                except Exception:
                    pass
                try:
                    await asyncio.sleep(0.08)
                except asyncio.CancelledError:
                    return
                try:
                    row.scale = 1.0
                    row.opacity = 1.0
                    row.update()
                except Exception:
                    pass
                if track.source_path and os.path.isfile(track.source_path):
                    self._play_single_track(track, index)
                else:
                    self._download_single_track(track, action_icon)

            try:
                asyncio.create_task(_press_and_act())
            except Exception:
                if track.source_path and os.path.isfile(track.source_path):
                    self._play_single_track(track, index)
                else:
                    self._download_single_track(track, action_icon)

        row.on_hover = _on_hover
        row.on_click = _on_click
        return row
