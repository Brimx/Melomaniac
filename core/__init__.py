"""Core models and application state."""

__all__ = [
    "Track",
    "PlaylistMeta",
    "SearchResult",
    "LoadState",
    "TransferState",
    "AppState",
]


def __getattr__(name: str):
    """Lazily expose public core symbols without creating import cycles."""
    if name in {"Track", "PlaylistMeta", "SearchResult", "LoadState", "TransferState"}:
        from core.models import LoadState, PlaylistMeta, SearchResult, Track, TransferState

        return {
            "Track": Track,
            "PlaylistMeta": PlaylistMeta,
            "SearchResult": SearchResult,
            "LoadState": LoadState,
            "TransferState": TransferState,
        }[name]
    if name == "AppState":
        from core.state import AppState

        return AppState
    raise AttributeError(f"module 'core' has no attribute {name!r}")
