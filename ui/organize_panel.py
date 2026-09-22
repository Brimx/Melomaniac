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
    """Dividir — exclusivamente cantidad 2-5, luego contenido por división vía ExpansionPanelList."""
    max_divs = {"value": getattr(state, "split_key_count", "2") if getattr(state, "split_key_count", None) in ("2","3","4","5") else "2"}

    dd_count = organize_dropdown([("2","2 divisiones"),("3","3 divisiones"),("4","4 divisiones"),("5","5 divisiones")], max_divs["value"], "Cantidad de divisiones (max 5)")
    count_hint = ft.Text(f"Se crearán {max_divs['value']} divisiones vacías — luego configura contenido por división.", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans")
    def _on_count_change(e):
        max_divs["value"] = e.control.value
        try:
            count_hint.value = f"Se crearán {max_divs['value']} divisiones vacías — luego configura contenido por división."
            count_hint.update()
        except: pass
        _rebuild_divisions()
    dd_count.on_select = _on_count_change  # type: ignore

    # fuente para opciones
    src = getattr(state, "source_tracks", state.tracks) if getattr(state, "tracks", None) else state.tracks or []
    artists_all = sorted(set(t.artist for t in src if t.artist), key=lambda x: x.lower())
    albums_all = sorted(set(t.album for t in src if t.album), key=lambda x: x.lower())
    songs_all = src[:]  # para buscador canciones

    # estado por división: {División 1: {name, artists:set, albums:set, songs:set(ids)}}
    divisions_state: dict[str, dict] = {}
    def _init_divisions():
        n = int(max_divs["value"])
        divisions_state.clear()
        for i in range(n):
            key = f"División {i+1}"
            divisions_state[key] = {"name": key, "artists": set(), "albums": set(), "songs": set()}
    _init_divisions()

    # nombre fields por división
    name_fields: dict[str, ft.TextField] = {}

    # contenedor dinámico para divisiones
    divisions_col = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, height=380)

    def _build_division_panel(div_key: str):
        st = divisions_state[div_key]
        # nombre editable
        name_tf = ft.TextField(value=st["name"], label=f"Nombre {div_key}", width=360, height=36, bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT, text_style=ft.TextStyle(size=11, color=TEXT_PRIMARY), label_style=ft.TextStyle(size=9, color=TEXT_MUTED))
        def _on_name(e, _k=div_key):
            divisions_state[_k]["name"] = (e.control.value or _k).strip() or _k
        name_tf.on_change = _on_name
        name_fields[div_key] = name_tf

        # buscadores + listas por tipo
        art_search = ft.TextField(hint_text="Buscar artista…", prefix_icon=ft.Icons.SEARCH, width=360, height=32, bgcolor=BG_INPUT, border_color=BORDER_LIGHT, dense=True, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4), text_style=ft.TextStyle(size=11))
        alb_search = ft.TextField(hint_text="Buscar álbum…", prefix_icon=ft.Icons.SEARCH, width=360, height=32, bgcolor=BG_INPUT, border_color=BORDER_LIGHT, dense=True, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4), text_style=ft.TextStyle(size=11))
        song_search = ft.TextField(hint_text="Buscar canción…", prefix_icon=ft.Icons.SEARCH, width=360, height=36, bgcolor=BG_INPUT, border_color=BORDER_LIGHT, dense=True, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4), text_style=ft.TextStyle(size=11))

        art_lv = ft.ListView(height=140, spacing=4, padding=ft.Padding.all(4), expand=False)
        alb_lv = ft.ListView(height=140, spacing=4, padding=ft.Padding.all(4), expand=False)
        song_lv = ft.ListView(height=170, spacing=4, padding=ft.Padding.all(4), expand=False)  # más grande para canciones

        # headers dinámicos para contador
        art_header = ft.Text(f"Artistas ({len(st['artists'])})", size=11, color=TEXT_PRIMARY)
        alb_header = ft.Text(f"Álbumes ({len(st['albums'])})", size=11, color=TEXT_PRIMARY)
        song_header = ft.Text(f"Canciones ({len(st['songs'])})", size=11, color=TEXT_PRIMARY)

        def _update_headers():
            try:
                art_header.value = f"Artistas ({len(st['artists'])})"
                alb_header.value = f"Álbumes ({len(st['albums'])})"
                song_header.value = f"Canciones ({len(st['songs'])})"
                art_header.update(); alb_header.update(); song_header.update()
            except: pass

        def _rebuild_art(q=""):
            ql = (q or "").lower()
            art_lv.controls.clear()
            for a in artists_all:
                if ql and ql not in a.lower(): continue
                def _on_art(e, _a=a):
                    if e.control.value: st["artists"].add(_a)
                    else: st["artists"].discard(_a)
                    _update_headers()
                chk = ft.Checkbox(label=a, value=a in st["artists"], fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=11, color=TEXT_PRIMARY if a in st["artists"] else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in st["artists"] else TEXT_DIM), on_change=_on_art)
                art_lv.controls.append(chk)
            try: art_lv.update()
            except: pass
        def _rebuild_alb(q=""):
            ql = (q or "").lower()
            alb_lv.controls.clear()
            for a in albums_all:
                if ql and ql not in a.lower(): continue
                def _on_alb(e, _a=a):
                    if e.control.value: st["albums"].add(_a)
                    else: st["albums"].discard(_a)
                    _update_headers()
                chk = ft.Checkbox(label=a, value=a in st["albums"], fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=11, color=TEXT_PRIMARY if a in st["albums"] else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in st["albums"] else TEXT_DIM), on_change=_on_alb)
                alb_lv.controls.append(chk)
            try: alb_lv.update()
            except: pass
        def _rebuild_song(q=""):
            ql = (q or "").lower()
            song_lv.controls.clear()
            for t in songs_all:
                label = f"{t.name} — {t.artist}"
                if ql and ql not in label.lower(): continue
                def _on_song(e, _tid=t.id):
                    if e.control.value: st["songs"].add(_tid)
                    else: st["songs"].discard(_tid)
                    _update_headers()
                chk = ft.Checkbox(label=label, value=t.id in st["songs"], fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=11, color=TEXT_PRIMARY if t.id in st["songs"] else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if t.id in st["songs"] else TEXT_DIM), on_change=_on_song)
                song_lv.controls.append(chk)
            try: song_lv.update()
            except: pass

        art_search.on_change = lambda e: _rebuild_art(e.control.value or "")
        alb_search.on_change = lambda e: _rebuild_alb(e.control.value or "")
        song_search.on_change = lambda e: _rebuild_song(e.control.value or "")
        _rebuild_art(""); _rebuild_alb(""); _rebuild_song("")

        # ExpansionPanelList con 3 paneles — más grandes para legibilidad
        exp = ft.ExpansionPanelList(
            expand_icon_color=TEXT_MUTED,
            elevation=0,
            divider_color=BORDER_LIGHT,
            controls=[
                ft.ExpansionPanel(
                    header=ft.ListTile(title=art_header),
                    content=ft.Column([art_search, ft.Container(content=art_lv, height=170, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8)], tight=True, spacing=6),
                    bgcolor=BG_SURFACE, can_tap_header=True,
                ),
                ft.ExpansionPanel(
                    header=ft.ListTile(title=alb_header),
                    content=ft.Column([alb_search, ft.Container(content=alb_lv, height=170, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8)], tight=True, spacing=6),
                    bgcolor=BG_SURFACE, can_tap_header=True,
                ),
                ft.ExpansionPanel(
                    header=ft.ListTile(title=song_header),
                    content=ft.Column([song_search, ft.Container(content=song_lv, height=200, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8)], tight=True, spacing=6),
                    bgcolor=BG_SURFACE, can_tap_header=True,
                ),
            ],
        )
        return ft.Container(
            content=ft.Column([name_tf, exp], spacing=8),
            bgcolor=BG_SURFACE, border=ft.Border.all(0.6, BORDER_LIGHT), border_radius=10, padding=ft.Padding.all(8),
        )

    def _rebuild_divisions():
        _init_divisions()
        divisions_col.controls.clear()
        for k in list(divisions_state.keys()):
            divisions_col.controls.append(_build_division_panel(k))
            divisions_col.controls.append(ft.Divider(height=1, color=BORDER_LIGHT))
        try: divisions_col.update()
        except: pass
        try: name_fields.update({k: name_fields.get(k) for k in divisions_state.keys()})
        except: pass

    _rebuild_divisions()

    content = ft.Column([
        dd_count,
        count_hint,
        ft.Divider(height=1, color=BORDER_LIGHT),
        divisions_col,
    ], tight=True, spacing=10, scroll=ft.ScrollMode.AUTO)

    dlg_ref: dict = {}

    def _apply(_e):
        # construye divisiones desde estado por división (nombre + artistas/álbumes/canciones)
        try:
            n = int(max_divs["value"])
            try: state.split_key_count = str(n)
            except: pass
            new_segs: dict[str, list] = {}
            src_all = src  # original tracks para filtrar
            for div_key, st in divisions_state.items():
                name = (name_fields.get(div_key).value or div_key).strip() if div_key in name_fields and name_fields[div_key].value else st.get("name", div_key)
                # evita colisión nombres
                base = name
                c = 1
                while base in new_segs:
                    c+=1; base = f"{name} ({c})"
                # reúne tracks según selección
                sel_art = st.get("artists", set())
                sel_alb = st.get("albums", set())
                sel_songs = st.get("songs", set())
                lst = []
                for t in src_all:
                    if t.artist in sel_art or (t.album and t.album in sel_alb) or t.id in sel_songs:
                        lst.append(t)
                new_segs[base] = lst
            # si todo vacío y sin selección, deja vacías (usuario puede luego editar)
            state.segments = new_segs
            # activa primera división para preview
            if new_segs:
                first = next(iter(new_segs.keys()))
                state.active_segment_key = first
                state.active_segment_keys = {first}
            else:
                state.active_segment_keys = None
                state.active_segment_key = None
            state.show_dual = True
            state.dual_mode = "doble"
            state.apply_search(state.search_query)
            state.log(f"[INFO] Divisiones creadas: {len(new_segs)}")
        except Exception as exc:
            try: state.log(f"[ERROR] Crear divisiones fallo: {exc}")
            except: pass
        _close_dlg(page, dlg_ref.get("dlg"))

    def _clear(_e):
        state.clear_split()
        _close_dlg(page, dlg_ref.get("dlg"))

    actions: list[ft.Control] = []
    if bool(getattr(state, "segments", {})):
        actions.append(dialog_action("Limpiar División", _clear, kind="danger"))
    actions += [
        dialog_action("Cancelar", lambda _: _close_dlg(page, dlg_ref.get("dlg")), kind="muted"),
        dialog_action("Dividir", _apply, kind="primary"),
    ]

    dlg = app_dialog(
        "Dividir",
        ft.Container(content=content, width=520, height=520, padding=ft.Padding.all(8)),
        actions,
        width=560, actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN if len(actions)==3 else ft.MainAxisAlignment.END,
        bgcolor=BG_SURFACE, radius=10,
    )
    dlg_ref["dlg"] = dlg
    page.show_dialog(dlg)


def show_reorganize_dialog(page: ft.Page, state) -> None:
    """Control REORGANIZAR por niveles §8 — drag Artistas/Álbumes, lista refleja resultado."""
    src = state.display_tracks if getattr(state, "display_tracks", None) else state.tracks
    if not src:
        try:
            from ui.widgets import notify
            notify(page, "No hay playlist cargada para reorganizar", kind="error")
        except Exception:
            pass
        return
    artists: list[str] = list(dict.fromkeys(t.artist for t in src))
    albums: list[str] = list(dict.fromkeys(t.album for t in src if t.album))
    dlg_ref: dict = {}

    def _apply_artists_reorder(new_order: list[str]):
        rank = {a: i for i, a in enumerate(new_order)}
        base = list(state.tracks)
        base.sort(key=lambda t: rank.get(t.artist, 9999))
        state.tracks = base
        state.show_dual = True
        state.dual_mode = "doble"
        state.apply_search(state.search_query)

    def _apply_albums_reorder(new_order: list[str]):
        rank = {a: i for i, a in enumerate(new_order)}
        base = list(state.tracks)
        base.sort(key=lambda t: rank.get(t.album or "—", 9999))
        state.tracks = base
        state.show_dual = True
        state.dual_mode = "doble"
        state.apply_search(state.search_query)

    try:
        art_items = artists[:]
        alb_items = albums[:]

        def _art_on_reorder(e):
            try:
                oi = e.old_index; ni = e.new_index
                if 0 <= oi < len(art_items) and 0 <= ni < len(art_items):
                    val = art_items.pop(oi); art_items.insert(ni, val)
                    try:
                        art_lv.controls = [ft.ListTile(title=ft.Text(a, size=11, color=TEXT_PRIMARY, font_family="IBM Plex Sans"), leading=ft.Icon(ft.Icons.DRAG_HANDLE, size=14, color=TEXT_DIM)) for a in art_items]
                        art_lv.update()
                    except Exception:
                        pass
            except Exception:
                pass

        art_lv = ft.ReorderableListView(controls=[ft.ListTile(title=ft.Text(a, size=11, color=TEXT_PRIMARY, font_family="IBM Plex Sans"), leading=ft.Icon(ft.Icons.DRAG_HANDLE, size=14, color=TEXT_DIM)) for a in art_items], on_reorder=_art_on_reorder, height=180)

        def _alb_on_reorder(e):
            try:
                oi = e.old_index; ni = e.new_index
                if 0 <= oi < len(alb_items) and 0 <= ni < len(alb_items):
                    val = alb_items.pop(oi); alb_items.insert(ni, val)
                    try:
                        alb_lv.controls = [ft.ListTile(title=ft.Text(a, size=11, color=TEXT_PRIMARY, font_family="IBM Plex Sans"), leading=ft.Icon(ft.Icons.DRAG_HANDLE, size=14, color=TEXT_DIM)) for a in alb_items]
                        alb_lv.update()
                    except Exception:
                        pass
            except Exception:
                pass

        alb_lv = ft.ReorderableListView(controls=[ft.ListTile(title=ft.Text(a, size=11, color=TEXT_PRIMARY, font_family="IBM Plex Sans"), leading=ft.Icon(ft.Icons.DRAG_HANDLE, size=14, color=TEXT_DIM)) for a in alb_items], on_reorder=_alb_on_reorder, height=180)

        def _apply(_e):
            if art_items != artists:
                _apply_artists_reorder(art_items)
            if alb_items != albums:
                _apply_albums_reorder(alb_items)
            _close_dlg(page, dlg_ref.get("dlg"))

        content = ft.Column([
            ft.Text("REORGANIZAR — arrastra para reordenar", size=11, color=TEXT_PRIMARY, font_family="IBM Plex Sans Medium"),
            ft.Text("Artistas", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
            ft.Container(content=art_lv, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, padding=ft.Padding.all(4)),
            ft.Text("Álbumes", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
            ft.Container(content=alb_lv, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, padding=ft.Padding.all(4)),
            ft.Text("La lista principal refleja el resultado — reordenamiento §9, no reasignación.", size=9, color=TEXT_DIM, font_family="IBM Plex Sans"),
        ], tight=True, spacing=10, scroll=ft.ScrollMode.AUTO)

        dlg = app_dialog("Reorganizar", ft.Container(content=content, width=420, height=460, padding=ft.Padding.all(8)), [dialog_action("Cancelar", lambda _: _close_dlg(page, dlg_ref.get("dlg")), kind="muted"), dialog_action("Aplicar", _apply, kind="primary")], width=460, bgcolor=BG_SURFACE, radius=10)
        dlg_ref["dlg"] = dlg
        page.show_dialog(dlg)
        return
    except Exception as exc:
        try:
            from ui.widgets import notify
            notify(page, f"Reorganizar fallback: {exc}", kind="error")
        except Exception:
            pass


# Compat: antiguo unificado → delega a organizar por defecto (mantener llamadas legacy)
def show_organize_divide_dialog(page: ft.Page, state) -> None:
    show_organize_dialog(page, state)
