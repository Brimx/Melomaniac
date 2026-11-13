"""Vistas de primer nivel del shell: Biblioteca, Detalle, Descargas y Configuración."""

from ui.views.config_view import ConfigView
from ui.views.downloads_view import DownloadsView
from ui.views.library_view import LibraryView
from ui.views.playlist_detail_view import PlaylistDetailView

__all__ = [
    "ConfigView",
    "DownloadsView",
    "LibraryView",
    "PlaylistDetailView",
]
