"""Pure playlist parsing, metadata and matching helpers."""

__all__ = [
    "clean_metadata",
    "build_search_query",
    "parse_local_playlist",
    "parse_local_playlist_with_paths",
    "build_local_tracks",
    "read_audio_metadata",
    "export_tracks",
    "default_export_path",
]


def __getattr__(name: str):
    """Lazily expose engine helpers so package imports stay acyclic."""
    if name in {"clean_metadata", "build_search_query"}:
        from engine.normalizer import build_search_query, clean_metadata

        return {"clean_metadata": clean_metadata, "build_search_query": build_search_query}[name]
    if name in {"parse_local_playlist", "parse_local_playlist_with_paths", "build_local_tracks"}:
        from engine.parsers import build_local_tracks, parse_local_playlist, parse_local_playlist_with_paths

        return {
            "parse_local_playlist": parse_local_playlist,
            "parse_local_playlist_with_paths": parse_local_playlist_with_paths,
            "build_local_tracks": build_local_tracks,
        }[name]
    if name == "read_audio_metadata":
        from engine.audio_metadata import read_audio_metadata

        return read_audio_metadata
    if name in {"export_tracks", "default_export_path"}:
        from engine.exporters import default_export_path, export_tracks

        return {"export_tracks": export_tracks, "default_export_path": default_export_path}[name]
    raise AttributeError(f"module 'engine' has no attribute {name!r}")
