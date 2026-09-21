"""
ui/organize_panel.py — Melomaniac v4.2 — Paneles separados Organizar / Agrupar

Dos diálogos iguales en estética (tokens OLED) pero separados para UX clara:
- Organizar: ordenar alfabéticamente (A→Z / Z→A) o Original; si nada alfabético → Original.
  Personalizado surge al hacer drag&drop (CUSTOM §19) — no es opción manual inicial.
- Agrupar: dividir en segmentos y elegir artista/álbum con lista counts.

Sin alcance (siempre toda la playlist) y sin letra pequeña (§25 alcance = filtros).
Genre pendiente biblioteca.
"""

from __future__ import annotations

import flet as ft

from ui.tokens import BG_SURFACE, BG_INPUT, BORDER_LIGHT, ACCENT, TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM, CHIP_BG
from ui.widgets import dialog_action, app_dialog, organize_dropdown
from engine.organizer import split_tracks


def _close_dlg(page: ft.Page, dlg: ft.AlertDialog | None) -> None:
    if dlg is None:
        return
    try:
        dlg.open = False
        page.update()
    except Exception:
        pass


def show_organize_dialog(page: ft.Page, state) -> None:
    """Organizar — ordenar alfabético explícito o Original; sin alcance; sin grupo."""
    sort_key = {"value": "artist"}  # artist/album/name/duration_ms/release_date
    orden = {"value": "original"}  # original | az | za

    dd_sort = organize_dropdown(
        [("artist", "Artista"), ("album", "Álbum"), ("name", "Título"), ("duration_ms", "Duración"), ("release_date", "Fecha lanzamiento")],
        sort_key["value"], "Ordenar por",
    )
    def _on_sort_change(e):
        sort_key["value"] = e.control.value
    dd_sort.on_select = _on_sort_change  # type: ignore

    # Orden explícito: Original vs Alfabético (si no se selecciona alfabético → Original)
    try:
        seg_orden = ft.SegmentedButton(
            selected=["original"], allow_empty_selection=False, allow_multiple_selection=False, show_selected_icon=False,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
            ),
            segments=[
                ft.Segment(value="original", label=ft.Text("Original", size=11)),
                ft.Segment(value="az", label=ft.Text("A → Z", size=11)),
                ft.Segment(value="za", label=ft.Text("Z → A", size=11)),
            ],
            on_change=lambda e: orden.__setitem__("value", list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value),
        )
    except Exception:
        seg_orden = ft.Dropdown(
            value="original", width=220, height=38,
            bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
            options=[ft.dropdown.Option("original","Original"), ft.dropdown.Option("az","A → Z"), ft.dropdown.Option("za","Z → A")],
            on_select=lambda e: orden.__setitem__("value", e.control.value),
        )

    content = ft.Column([
        dd_sort,
        seg_orden,
    ], tight=True, spacing=12)

    dlg_ref: dict = {}

    def _apply(_e):
        # si no es alfabético → Original (no ordenar, respeta orden fuente §13)
        if orden["value"] == "original":
            # volver a original (usa original_position sin reverse)
            try:
                for idx, tr in enumerate(getattr(state, "source_tracks", state.tracks)):
                    setattr(tr, "_orig_pos", idx)
            except Exception:
                pass
            # si tenemos source_tracks, restaurar desde él; si no, sort por original_position
            try:
                src = getattr(state, "source_tracks", None)
                if src is not None and len(src) == len(state.tracks):
                    # restaura original id order
                    id_to_idx = {t.id: i for i, t in enumerate(src)}
                    state.tracks = sorted(state.tracks, key=lambda t: id_to_idx.get(t.id, 9999))
                    state.apply_search(state.search_query)
                    _close_dlg(page, dlg_ref.get("dlg"))
                    return
            except Exception:
                pass
            state.organize_sort(["original_position"], False, scope="all")
        else:
            rev = orden["value"] == "za"
            state.organize_sort([sort_key["value"]], rev, scope="all")
        _close_dlg(page, dlg_ref.get("dlg"))

    dlg = app_dialog(
        "Organizar",
        ft.Container(content=content, width=380, padding=ft.Padding.all(8)),
        [
            dialog_action("Cancelar", lambda _: _close_dlg(page, dlg_ref.get("dlg")), kind="muted"),
            dialog_action("Aplicar", _apply, kind="primary"),
        ],
        width=420, bgcolor=BG_SURFACE, radius=10,
    )
    dlg_ref["dlg"] = dlg
    page.show_dialog(dlg)


def show_group_dialog(page: ft.Page, state) -> None:
    """Agrupar/Dividir — elegir Artista/Álbum con lista counts clara; sin alcance."""
    split_key = {"value": "artist"}

    dd_split = organize_dropdown([("artist", "Artista"), ("album", "Álbum")], split_key["value"], "Agrupar por")
    def _on_split_change(e):
        split_key["value"] = e.control.value
        _refresh_preview()
    dd_split.on_select = _on_split_change  # type: ignore

    preview_list = ft.ListView(height=180, spacing=4, padding=ft.Padding.all(4), expand=False, visible=True)
    preview_empty = ft.Text("Sin segmentos (carga una playlist)", size=11, color=TEXT_DIM, font_family="IBM Plex Sans")
    preview_wrap = ft.Container(content=ft.Column([preview_list, preview_empty], spacing=6), bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, padding=ft.Padding.all(8), visible=True)

    def _refresh_preview():
        try:
            # siempre toda la playlist (sin alcance)
            src = getattr(state, "source_tracks", state.tracks) if getattr(state, "tracks", None) else []
            # si hay transformación result, usa tracks (result) para preview actualizado
            src = state.tracks if state.tracks else src
            segs = split_tracks(src, split_key["value"])
            if not segs:
                preview_list.controls = []
                preview_empty.visible = True
                preview_empty.value = "Sin segmentos"
                try: preview_wrap.update()
                except: pass
                return
            preview_empty.visible = False
            # orden 1ª aparición (dict preserva inserción O(n) §12), no alfabético; muestra count desc para UX pero respeta selección
            sorted_keys = sorted(segs.keys(), key=lambda k: len(segs[k]), reverse=True)
            active = getattr(state, "active_segment_key", None)
            controls = []
            for k in sorted_keys:
                cnt = len(segs[k])
                is_active = (k == active)
                row = ft.Row([
                    ft.Icon(ft.Icons.CHECK_CIRCLE if is_active else (ft.Icons.PERSON_OUTLINE if split_key["value"]=="artist" else ft.Icons.ALBUM_OUTLINED), size=14, color=ACCENT if is_active else TEXT_MUTED),
                    ft.Text(k, size=11, color=TEXT_PRIMARY if is_active else TEXT_PRIMARY, font_family="IBM Plex Sans Medium" if is_active else "IBM Plex Sans", expand=True, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                    ft.Container(content=ft.Text(str(cnt), size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"), bgcolor=ACCENT if is_active else BG_SURFACE, border_radius=8, padding=ft.Padding.symmetric(horizontal=6, vertical=2)),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                def _on_pick(e, key=k):
                    try:
                        state.set_active_segment(key)
                        _refresh_preview()
                        page.update()
                    except Exception:
                        pass
                controls.append(ft.Container(content=row, ink=True, on_click=_on_pick, padding=ft.Padding.symmetric(horizontal=6, vertical=4), bgcolor=CHIP_BG if is_active else ft.Colors.TRANSPARENT, border=ft.Border.all(0.6, ACCENT if is_active else ft.Colors.TRANSPARENT), border_radius=6))
            preview_list.controls = controls
            try:
                preview_list.update(); preview_wrap.update()
            except:
                pass
        except Exception:
            pass

    _refresh_preview()

    content = ft.Column([
        dd_split,
        preview_wrap,
    ], tight=True, spacing=12)

    dlg_ref: dict = {}

    def _apply(_e):
        # siempre toda la playlist
        state.organize_split(split_key["value"], scope="all")
        _close_dlg(page, dlg_ref.get("dlg"))

    def _clear(_e):
        state.clear_split()
        _close_dlg(page, dlg_ref.get("dlg"))

    actions: list[ft.Control] = []
    if bool(getattr(state, "segments", {})):
        actions.append(dialog_action("Limpiar División", _clear, kind="danger"))
    actions += [
        dialog_action("Cancelar", lambda _: _close_dlg(page, dlg_ref.get("dlg")), kind="muted"),
        dialog_action("Agrupar", _apply, kind="primary"),
    ]

    dlg = app_dialog(
        "Agrupar",
        ft.Container(content=content, width=420, padding=ft.Padding.all(8)),
        actions,
        width=460, actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN if len(actions)==3 else ft.MainAxisAlignment.END,
        bgcolor=BG_SURFACE, radius=10,
    )
    dlg_ref["dlg"] = dlg
    page.show_dialog(dlg)


# Compat: antiguo unificado → delega a organizar por defecto (mantener llamadas legacy)
def show_organize_divide_dialog(page: ft.Page, state) -> None:
    show_organize_dialog(page, state)
