"""Flet UI components and authentication controls."""

__all__ = [
    "AuthManager",
    "ConfigWizard",
    "PlaylistManagerUI",
    "SongRow",
    "SkeletonRow",
    "ITEM_H",
]


def __getattr__(name: str):
    """Lazily expose UI components to avoid eager Flet import chains."""
    if name == "AuthManager":
        from ui.auth_manager import AuthManager

        return AuthManager
    if name == "ConfigWizard":
        from ui.config_wizard import ConfigWizard

        return ConfigWizard
    if name == "PlaylistManagerUI":
        from ui.main_ui import PlaylistManagerUI

        return PlaylistManagerUI
    if name in {"SongRow", "SkeletonRow", "ITEM_H"}:
        from ui.song_row import ITEM_H, SkeletonRow, SongRow

        return {"SongRow": SongRow, "SkeletonRow": SkeletonRow, "ITEM_H": ITEM_H}[name]
    raise AttributeError(f"module 'ui' has no attribute {name!r}")
