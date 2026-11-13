"""ui/dialogs/transfer_helpers.py — Helpers compartidos para preflight y destino."""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import EXPORT_DEST_LABEL
from core.models import Track
from core.state import AppState
from ui.components.widgets import _ghost_btn, safe_update
from ui.theme.fonts import FONT_TEXT
from ui.theme.icons import PIcons, app_icon
from ui.theme.tokens import (
    BORDER_LIGHT,
    CHIP_BG,
    ERROR_COL,
    RADIUS_INPUT,
    SUCCESS,
    TEXT_MUTED,
)


def get_synced_playlists_for(host, dest: str) -> list:
    """Devuelve playlists sincronizadas en Biblioteca para ``dest``."""
    lib = getattr(host, "library_state", None)
    if lib is None or dest not in AppState.PLATFORMS:
        return []
    try:
        return list(lib.get_playlists(dest) or [])
    except Exception:
        return []


async def run_dialog_preflight(host, dest: str) -> tuple[bool, str, bool]:
    """Ejecuta comprobación en vivo de sesión para ``dest``.

    Retorna ``(ok, error_msg, expired)``.
    """
    if dest == EXPORT_DEST_LABEL:
        return True, "Exportación local sin sesión requerida", False
    if dest not in AppState.PLATFORMS:
        return True, "", False
    am = getattr(host, "auth_manager", None)
    if am is None:
        cached = host.state.auth_session_ok.get(dest)
        if cached is False:
            hint = host.state.auth_session_hint.get(dest) or "revisa Configuración"
            return False, hint, True
        return True, "", False
    try:
        results = await am.check_sessions([dest])
    except Exception as exc:  # pylint: disable=broad-exception-caught
        host.state.log(f"[ERROR] Preflight de {dest}: {exc}")
        return False, str(exc), False
    if hasattr(am, "ingest_preflight_results"):
        am.ingest_preflight_results(results)
    result = next((r for r in results if getattr(r, "platform", "") == dest), None)
    if result is None:
        return True, "", False
    return (
        bool(result.ok),
        str(getattr(result, "error", "") or ""),
        bool(getattr(result, "expired", False)),
    )


def make_preflight_status_row(host, on_status_change=None):
    """Widget reutilizable de preflight en vivo dentro de diálogos."""
    icon_ctrl = app_icon(PIcons.INFO, size=15, color=TEXT_MUTED)
    status_txt = ft.Text(
        "Comprobando sesión…",
        size=11,
        color=TEXT_MUTED,
        font_family=FONT_TEXT,
        expand=True,
    )
    state = {"dest": "", "ok": None, "seq": 0}
    config_btn = _ghost_btn("Configurar", PIcons.GEAR, lambda _: None, height=30)
    config_btn.visible = False

    async def _run(dest: str) -> bool:
        state["dest"] = dest
        state["seq"] += 1
        seq = state["seq"]
        if dest == EXPORT_DEST_LABEL:
            state["ok"] = True
            icon_ctrl.value = PIcons.CHECK_CIRCLE
            icon_ctrl.color = SUCCESS
            status_txt.value = "Exportación local no requiere sesión."
            status_txt.color = SUCCESS
            config_btn.visible = False
            if on_status_change:
                on_status_change(True)
            safe_update(banner)
            return True
        state["ok"] = None
        icon_ctrl.value = PIcons.CLOCK
        icon_ctrl.color = TEXT_MUTED
        status_txt.value = f"Comprobando sesión de {dest}…"
        status_txt.color = TEXT_MUTED
        config_btn.visible = False
        if on_status_change:
            on_status_change(None)
        safe_update(banner)
        ok, err, _expired = await run_dialog_preflight(host, dest)
        if seq != state["seq"]:
            return ok
        state["ok"] = ok
        if ok:
            icon_ctrl.value = PIcons.CHECK_CIRCLE
            icon_ctrl.color = SUCCESS
            status_txt.value = f"Sesión activa en {dest}"
            status_txt.color = SUCCESS
            config_btn.visible = False
        else:
            icon_ctrl.value = PIcons.WARNING_CIRCLE
            icon_ctrl.color = ERROR_COL
            detail = f": {err[:90]}" if err else " (revisa credenciales)"
            status_txt.value = f"Sesión no válida en {dest}{detail}"
            status_txt.color = ERROR_COL
            am = getattr(host, "auth_manager", None)
            config_btn.visible = am is not None
            if am is not None:
                config_btn.on_click = lambda _, p=dest: am.open_wizard(p)
        if on_status_change:
            on_status_change(ok)
        safe_update(banner)
        return ok

    recheck_btn = _ghost_btn(
        "Comprobar",
        PIcons.ARROWS_CLOCKWISE,
        lambda _: asyncio.create_task(_run(state["dest"])),
        height=30,
    )
    banner = ft.Container(
        bgcolor=CHIP_BG,
        border=ft.Border.all(0.8, BORDER_LIGHT),
        border_radius=RADIUS_INPUT,
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        content=ft.Row(
            [icon_ctrl, status_txt, config_btn, recheck_btn],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
    return banner, state, _run


async def resolve_tracks_for_destination(
    host, dest: str, tracks: list[Track]
) -> list[Track]:
    """Resuelve pistas que no tengan ID en ``dest`` usando el servicio."""
    service = getattr(host.state, "service", None)
    resolved: list[Track] = []
    pending: list[Track] = []
    for tr in tracks:
        if getattr(tr, "platform", "") == dest and getattr(tr, "id", ""):
            resolved.append(tr)
        else:
            pending.append(tr)
    if not pending or service is None or not hasattr(service, "search_with_fallback"):
        return resolved

    async def _resolve_one(tr: Track) -> Track | None:
        try:
            res = await service.search_with_fallback(dest, tr)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            host.state.log(f"[WARN] Sin coincidencia para '{tr.name}' en {dest}: {exc}")
            return None
        if res and getattr(res, "track_id", ""):
            return Track(
                id=str(res.track_id),
                name=tr.name,
                artist=tr.artist,
                album=tr.album,
                duration=tr.duration,
                platform=dest,
                duration_ms=tr.duration_ms,
                is_explicit=tr.is_explicit,
                isrc=getattr(res, "isrc", "") or tr.isrc,
                img_url=tr.img_url,
            )
        return None

    if dest == "Apple Music":
        # Apple Music siempre secuencial por AppleRequestLimiter.
        for tr in pending:
            item = await _resolve_one(tr)
            if item is not None:
                resolved.append(item)
    else:
        sem = asyncio.Semaphore(3)

        async def _bounded(tr: Track) -> Track | None:
            async with sem:
                return await _resolve_one(tr)

        results = await asyncio.gather(*(_bounded(t) for t in pending), return_exceptions=True)
        for item in results:
            if isinstance(item, Track):
                resolved.append(item)
    return resolved


async def add_tracks_to_existing_destination(
    host, dest: str, playlist_id: str, playlist_name: str, tracks: list[Track]
) -> int:
    """Inserta ``tracks`` en una playlist existente usando el servicio disponible."""
    service = getattr(host.state, "service", None)
    if service is None:
        host._snack("Servicio de música no disponible", error=True)
        return 0
    if not playlist_id:
        host._snack("Selecciona una playlist destino válida", error=True)
        return 0
    init_map = {
        "YouTube Music": "init_youtube",
        "Apple Music": "init_apple",
        "Spotify": "init_spotify",
    }
    init_name = init_map.get(dest)
    if init_name and callable(getattr(service, init_name, None)):
        ok_auth = await getattr(service, init_name)()
        if not ok_auth:
            host._snack(f"No se pudo autenticar en {dest}", error=True)
            return 0

    host._snack(f"Preparando {len(tracks)} canciones para «{playlist_name}»…")
    resolved = await resolve_tracks_for_destination(host, dest, tracks)
    if not resolved:
        host._snack(f"No se encontraron coincidencias válidas en {dest}", error=True)
        return 0

    added = 0
    batch_fn = getattr(service, "add_tracks_to_playlist", None)
    if callable(batch_fn):
        try:
            added = int(await batch_fn(dest, playlist_id, resolved))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            host.state.log(f"[ERROR] add_tracks_to_playlist({dest}): {exc}")
            host._snack(f"Error al añadir pistas: {exc}", error=True)
            return 0
    elif callable(getattr(service, "add_track", None)):
        for tr in resolved:
            try:
                ok, _msg = await service.add_track(dest, playlist_id, tr.id)
                if ok:
                    added += 1
            except Exception as exc:  # pylint: disable=broad-exception-caught
                host.state.log(f"[WARN] add_track({tr.name}): {exc}")
    else:
        host._snack("El servicio actual no soporta añadir pistas a playlists", error=True)
        return 0

    host.state.log(
        f"[SUCCESS] {added}/{len(resolved)} pistas añadidas a «{playlist_name}» ({dest})"
    )
    host._snack(f"{added} canciones añadidas a «{playlist_name}» ({dest})")
    return added


__all__ = [
    "add_tracks_to_existing_destination",
    "get_synced_playlists_for",
    "make_preflight_status_row",
    "resolve_tracks_for_destination",
    "run_dialog_preflight",
]
