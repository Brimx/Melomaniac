"""Shell principal y controladores compuestos de la interfaz."""

from ui.shell.catalog_panel import CatalogController
from ui.shell.main_actions import ActionsController
from ui.shell.main_layout import LayoutController, compute_contextual_actions_state
from ui.shell.main_ui import PlaylistManagerUI

__all__ = [
    "ActionsController",
    "CatalogController",
    "LayoutController",
    "PlaylistManagerUI",
    "compute_contextual_actions_state",
]
