"""
ui/organize_panel.py — Melomaniac v4.2 — Paneles separados Organizar / Agrupar

Dos diálogos iguales en estética (tokens OLED) pero separados para UX clara:
- Organizar: Mantener orden (agrupar sin reordenar) o A→Z/Z→A + agrupar estable;
  Mantener orden sirve para agrupar dispersos 5+1 sin sort.
  Persistencia: re-abrir recuerda última config. Limpiar revierte a SOURCE.
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
    """Organizar — Mantener orden (agrupar) o A→Z/Z→A + agrupar; persiste config; limpiar todo."""
    # ── cargar persistida (fallback az) ────────────────────────────────
    _init_sort = getattr(state, "organize_sort_key", "artist")
    if _init_sort not in ("artist", "album", "name", "duration_ms", "release_date"):
        _init_sort = "artist"
    _init_orden = getattr(state, "organize_order", "az")
    # "original" = Mantener orden (agrupar sin reordenar 5+1); "mantener" alias
    if _init_orden == "mantener":
        _init_orden = "original"
    if _init_orden not in ("az", "za", "original"):
        _init_orden = "az"
    _init_group = getattr(state, "organize_group_by", "none")
    if _init_group not in ("none", "artist", "album", "release_date"):
        _init_group = "none"
    _init_second = getattr(state, "organize_second_key", "none")
    _init_third = getattr(state, "organize_third_key", "none")
    _init_adv = bool(getattr(state, "organize_advanced", False))

    sort_key = {"value": _init_sort}  # primaria
    orden = {"value": _init_orden}  # original (=Mantener orden) | az | za
    group_by = {"value": _init_group}  # none | artist | album | release_date
    # avanzado: 2ª/3ª clave jerárquica (auto si no se usa)
    second_key = {"value": _init_second}
    third_key = {"value": _init_third}
    avanzado = {"value": _init_adv}

    dd_sort = organize_dropdown(
        [("artist", "Artista"), ("album", "Álbum"), ("name", "Título"), ("duration_ms", "Duración"), ("release_date", "Fecha lanzamiento")],
        sort_key["value"], "Ordenar por",
    )
    def _on_sort_change(e):
        sort_key["value"] = e.control.value
    dd_sort.on_select = _on_sort_change  # type: ignore

    # Mantener orden = agrupar dispersos sin reordenar 5+1; A→Z/Z→A = alfabético
    try:
        seg_orden = ft.SegmentedButton(
            selected=[orden["value"]], allow_empty_selection=False, allow_multiple_selection=False, show_selected_icon=False,
            style=ft.ButtonStyle(bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE}, color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED}),
            segments=[ft.Segment(value="original", label=ft.Text("Mantener orden", size=10)), ft.Segment(value="az", label=ft.Text("A → Z", size=11)), ft.Segment(value="za", label=ft.Text("Z → A", size=11))],
            on_change=lambda e: orden.__setitem__("value", list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value),
        )
    except Exception:
        seg_orden = ft.Dropdown(value=orden["value"], width=240, height=38, bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT, text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"), options=[ft.dropdown.Option("original","Mantener orden"), ft.dropdown.Option("az","A → Z"), ft.dropdown.Option("za","Z → A")], on_select=lambda e: orden.__setitem__("value", e.control.value))

    # Agrupar estable: trae dispersos juntos (5+1) §12-15; ninguno = sin agrupar
    try:
        seg_group = ft.SegmentedButton(
            selected=[group_by["value"]], allow_empty_selection=False, allow_multiple_selection=False, show_selected_icon=False,
            style=ft.ButtonStyle(bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE}, color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED}),
            segments=[ft.Segment(value="none", label=ft.Text("Sin agrupar", size=10)), ft.Segment(value="artist", label=ft.Text("Artista", size=10)), ft.Segment(value="album", label=ft.Text("Álbum", size=10)), ft.Segment(value="release_date", label=ft.Text("Fecha", size=10))],
            on_change=lambda e: group_by.__setitem__("value", list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value),
        )
    except Exception:
        seg_group = ft.Dropdown(value=group_by["value"], width=300, height=38, bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT, text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"), options=[ft.dropdown.Option("none","Sin agrupar"), ft.dropdown.Option("artist","Artista"), ft.dropdown.Option("album","Álbum"), ft.dropdown.Option("release_date","Fecha")], on_select=lambda e: group_by.__setitem__("value", e.control.value))

    # Avanzado — muestra 2ª/3ª clave para jerarquía manual
    dd_second = organize_dropdown([("none","—"), ("artist","Artista"), ("album","Álbum"), ("name","Título"), ("release_date","Fecha")], second_key["value"], "2ª clave")
    dd_third = organize_dropdown([("none","—"), ("artist","Artista"), ("album","Álbum"), ("name","Título"), ("release_date","Fecha")], third_key["value"], "3ª clave")
    def _on_second(e): second_key["value"] = e.control.value
    def _on_third(e): third_key["value"] = e.control.value
    dd_second.on_select = _on_second  # type: ignore
    dd_third.on_select = _on_third  # type: ignore
    adv_row = ft.Row([dd_second, dd_third], spacing=8, visible=_init_adv)
    def _on_adv(e): 
        avanzado["value"] = bool(e.control.value)
        adv_row.visible = avanzado["value"]
        try: adv_row.update()
        except: pass
        try: page.update()
        except: pass
    sw_adv = ft.Switch(label="Avanzado", value=_init_adv, active_color=ACCENT, on_change=_on_adv)

    content = ft.Column([
        dd_sort,
        seg_orden,
        ft.Text("Agrupar dispersos juntos:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
        seg_group,
        sw_adv,
        adv_row,
    ], tight=True, spacing=12)

    dlg_ref: dict = {}

    def _persist():
        """Guarda selección para próxima apertura."""
        try:
            state.organize_sort_key = sort_key["value"]
            state.organize_order = orden["value"]
            state.organize_group_by = group_by["value"]
            state.organize_second_key = second_key["value"]
            state.organize_third_key = third_key["value"]
            state.organize_advanced = bool(avanzado["value"])
        except Exception:
            pass

    def _apply(_e):
        _persist()
        # Mantener orden (=original) sirve para agrupar sin reordenar 5+1
        g = None if group_by["value"] == "none" else group_by["value"]
        ord_val = orden["value"]
        is_alpha = ord_val in ("az", "za")
        rev = ord_val == "za"
        # — Mantener orden branch —
        if not is_alpha:
            if g is None:
                # Mantener orden sin agrupar → volver a fuente (SOURCE intacta)
                try:
                    src = getattr(state, "source_tracks", None)
                    if src is not None and len(src) == len(state.tracks):
                        id_to_idx = {t.id: i for i, t in enumerate(src)}
                        state.tracks = sorted(state.tracks, key=lambda t: id_to_idx.get(t.id, 9999))
                        state.apply_search(state.search_query)
                        _close_dlg(page, dlg_ref.get("dlg"))
                        return
                except Exception:
                    pass
                state.organize_sort(["original_position"], False, scope="all")
                _close_dlg(page, dlg_ref.get("dlg"))
                return
            # Mantener orden + agrupar → solo agrupar estable sin sort (§12)
            try:
                for idx, tr in enumerate(getattr(state, "source_tracks", state.tracks)):
                    setattr(tr, "_orig_pos", idx)
            except Exception:
                pass
            from engine.organizer import group_tracks_stable
            state.tracks = group_tracks_stable(state.tracks, g)
            if state.segments:
                for k in list(state.segments.keys()):
                    state.segments[k] = group_tracks_stable(state.segments[k], g)
            state.show_dual = True
            state.dual_mode = "doble"
            state.apply_search(state.search_query)
            _close_dlg(page, dlg_ref.get("dlg"))
            return
        # — Alfabético A→Z / Z→A —
        if not avanzado["value"]:
            auto_map = {
                "artist": ["artist", "album", "name"],
                "album": ["album", "artist", "name"],
                "name": ["name", "artist"],
                "release_date": ["release_date", "artist"],
                "duration_ms": ["duration_ms", "artist"],
            }
            keys = auto_map.get(sort_key["value"], [sort_key["value"]])
        else:
            keys = [sort_key["value"]]
            if second_key["value"] != "none" and second_key["value"] not in keys:
                keys.append(second_key["value"])
            if third_key["value"] != "none" and third_key["value"] not in keys:
                keys.append(third_key["value"])
        # si hay agrupar, usa GROUP→SORT per-grupo §14; si no, sort plano
        if g:
            from engine.organizer import organize_with_grouping
            try:
                for idx, tr in enumerate(getattr(state, "source_tracks", state.tracks)):
                    setattr(tr, "_orig_pos", idx)
            except Exception:
                pass
            state.tracks = organize_with_grouping(state.tracks, g, keys, rev)
            if state.segments:
                for k in list(state.segments.keys()):
                    state.segments[k] = organize_with_grouping(state.segments[k], None, keys, rev)
            state.show_dual = True
            state.dual_mode = "doble"
            state.apply_search(state.search_query)
        else:
            state.organize_sort(keys, rev, scope="all")
        _close_dlg(page, dlg_ref.get("dlg"))

    def _clear(_e):
        # Limpiar todo Organizar (como Dividir): vuelve a SOURCE y quita dual/segmentos
        try:
            state.clear_organize()
        except Exception:
            # fallback minimal
            try:
                state.tracks = list(getattr(state, "source_tracks", state.tracks))
                state.segments = {}
                state.active_segment_keys = None
                state.active_segment_key = None
                state.show_dual = False
                state.dual_mode = "lista"
                state.apply_search(state.search_query)
            except Exception:
                pass
        _close_dlg(page, dlg_ref.get("dlg"))

    # determinar si hay transformación para mostrar Limpiar
    _has_transform = False
    try:
        src = getattr(state, "source_tracks", None)
        if src and len(src) == len(state.tracks) and state.tracks != src:
            _has_transform = True
        if getattr(state, "show_dual", False) or getattr(state, "segments", {}):
            _has_transform = True
    except Exception:
        pass

    actions: list[ft.Control] = []
    if _has_transform:
        actions.append(dialog_action("Limpiar", _clear, kind="danger"))
    actions += [
        dialog_action("Cancelar", lambda _: _close_dlg(page, dlg_ref.get("dlg")), kind="muted"),
        dialog_action("Aplicar", _apply, kind="primary"),
    ]

    dlg = app_dialog(
        "Organizar",
        ft.Container(content=content, width=380, padding=ft.Padding.all(8)),
        actions,
        width=420,
        actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN if len(actions)==3 else ft.MainAxisAlignment.END,
        bgcolor=BG_SURFACE, radius=10,
    )
    dlg_ref["dlg"] = dlg
    page.show_dialog(dlg)


def show_group_dialog(page: ft.Page, state) -> None:
    """Agrupar/Dividir — elegir Artista/Álbum con lista counts clara; sin alcance. Orden estable O(n)."""
    _init_split = getattr(state, "split_key", "artist")
    if _init_split not in ("artist", "album"):
        _init_split = "artist"
    split_key = {"value": _init_split}

    dd_split = organize_dropdown([("artist", "Artista"), ("album", "Álbum")], split_key["value"], "Agrupar por")
    def _on_split_change(e):
        split_key["value"] = e.control.value
        _refresh_preview()
    dd_split.on_select = _on_split_change  # type: ignore

    preview_list = ft.ListView(height=180, spacing=4, padding=ft.Padding.all(4), expand=False, visible=True)
    preview_empty = ft.Text("Sin segmentos (carga una playlist)", size=11, color=TEXT_DIM, font_family="IBM Plex Sans")
    preview_wrap = ft.Container(content=ft.Column([preview_list, preview_empty], spacing=6), bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, padding=ft.Padding.all(8), visible=True)
    # multi-selección local (checkboxes) — Todos por defecto, persiste tras Organizar
    selected: dict[str, set] = {"value": set()}

    def _refresh_preview():
        try:
            # siempre toda la playlist (sin alcance)
            src = getattr(state, "source_tracks", state.tracks) if getattr(state, "tracks", None) else []
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
            # orden 1ª aparición O(n) §12 — sin sort por tamaño ni alfabético
            sorted_keys = list(segs.keys())
            # init / sync selected con estado actual si primera vez o tras cambio de criterio
            cur_sel = selected["value"]
            if not cur_sel or not cur_sel.issubset(set(segs.keys())):
                # si hay active multi/single previo y coincide con nuevo segs, respeta
                if getattr(state, "active_segment_keys", None) is not None:
                    inter = {k for k in state.active_segment_keys if k in segs}
                    # si cambio de criterio (artist→album) inter vacía → Todos
                    cur_sel = inter if inter else set(sorted_keys)
                elif getattr(state, "active_segment_key", None) and state.active_segment_key in segs:
                    cur_sel = {state.active_segment_key}
                else:
                    # si preview viene de state.segments distinto, usar esos keys si coinciden
                    if getattr(state, "segments", {}) and set(state.segments.keys()) == set(segs.keys()):
                        cur_sel = set(state.active_segment_keys) if state.active_segment_keys else set(sorted_keys)
                    else:
                        cur_sel = set(sorted_keys)
                selected["value"] = cur_sel
            controls = []
            for k in sorted_keys:
                cnt = len(segs[k])
                is_checked = k in selected["value"]
                chk = ft.Checkbox(
                    label=f"{k} ({cnt})",
                    value=is_checked,
                    fill_color={ft.ControlState.SELECTED: ACCENT},
                    check_color=TEXT_PRIMARY,
                    label_style=ft.TextStyle(color=TEXT_PRIMARY if is_checked else TEXT_MUTED, size=11, font_family="IBM Plex Sans"),
                    border_side=ft.BorderSide(1.2, ACCENT if is_checked else TEXT_DIM),
                    data=k,
                    on_change=lambda e, _k=k: _on_toggle(_k, bool(e.control.value)),
                )
                controls.append(chk)
            preview_list.controls = controls
            try:
                preview_list.update(); preview_wrap.update()
            except:
                pass
        except Exception:
            pass

    def _on_toggle(key: str, checked: bool):
        if checked:
            selected["value"].add(key)
        else:
            selected["value"].discard(key)
        try:
            preview_list.update(); preview_wrap.update()
        except Exception:
            pass

    _refresh_preview()

    content = ft.Column([
        dd_split,
        ft.Text("Selecciona particiones (varias) — se aplicará tras Agrupar:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
        preview_wrap,
    ], tight=True, spacing=12)

    dlg_ref: dict = {}

    def _apply(_e):
        # siempre toda la playlist; persiste split_key y aplica multi-selección
        state.organize_split(split_key["value"], scope="all")
        # aplica selección multi del preview (Todos = None)
        try:
            segs = state.segments
            if segs:
                sel = selected["value"]
                if len(sel) == len(segs) or len(sel) == 0:
                    state.set_active_segments(None)
                else:
                    state.set_active_segments(set(sel))
        except Exception:
            pass
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
