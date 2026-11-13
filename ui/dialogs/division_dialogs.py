"""ui/division_dialogs.py — Diálogos y acciones de Organizar, Dividir y edición de divisiones.

Mixin de `PlaylistManagerUI` (ui/main_ui.py): los métodos se mezclan en la clase
y comparten su `self`.
"""

from __future__ import annotations

import flet as ft

from ui.components.widgets import (
    app_dialog,
    app_text_field,
    debounce,
    dialog_action,
    input_border,
    limit_label,
    organize_dropdown,
    safe_update,
)
from ui.theme.tokens import (
    ACCENT,
    BG_INPUT,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    DIALOG_OPTION_LIMIT,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
)
from ui.theme.icons import PIcons, app_icon
from ui.theme.fonts import FONT_HEADLINE, FONT_TEXT


class DivisionMixin:
    """Diálogos y acciones de Organizar, Dividir y edición de divisiones."""

    def _on_organize(self, _e: ft.ControlEvent) -> None:
        """Organizar separado — Mantener orden (agrupar) o A→Z/Z→A + agrupar; sin alcance."""
        try:
            from ui.dialogs.organize_panel import show_organize_dialog
            show_organize_dialog(self.page, self.state)
        except Exception as exc:
            # fallback legacy mínimo — incluye Mantener orden para agrupar
            try:
                from engine.organizer import group_tracks_stable
                _dd = organize_dropdown([("artist","Artista"),("album","Álbum"),("name","Título"),("duration_ms","Duración"),("release_date","Fecha")], getattr(self.state, "organize_sort_key", "artist"),"Ordenar por")
                _ord_raw = getattr(self.state, "organize_order", "az")
                if _ord_raw == "mantener":
                    _ord_raw = "original"
                _orden = {"value": _ord_raw if _ord_raw in ("az","za","original") else "az"}
                try:
                    _seg = ft.SegmentedButton(selected=[_orden["value"]], allow_empty_selection=False, allow_multiple_selection=False, show_selected_icon=False,
                        style=ft.ButtonStyle(bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE}, color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED}),
                        segments=[ft.Segment(value="original", label=ft.Text("Mantener orden", size=10)), ft.Segment(value="az", label=ft.Text("A → Z", size=11)), ft.Segment(value="za", label=ft.Text("Z → A", size=11))],
                        on_change=lambda e: _orden.__setitem__("value", list(e.control.selected)[0] if hasattr(e.control,"selected") and e.control.selected else e.control.value))
                except Exception:
                    _seg = ft.Dropdown(value=_orden["value"], width=240, bgcolor=BG_INPUT, border=input_border(), options=[ft.dropdown.Option("original","Mantener orden"), ft.dropdown.Option("az","A → Z"), ft.dropdown.Option("za","Z → A")], on_select=lambda e: _orden.__setitem__("value", e.control.value))
                def _ap(_e):
                    # persist fallback también
                    try:
                        self.state.organize_sort_key = _dd.value
                        self.state.organize_order = _orden["value"]
                    except Exception:
                        pass
                    if _orden["value"] == "original":
                        # Mantener orden sirve para agrupar: si hay agrupación previa usa group, si no restaura
                        # fallback sin selector de agrupar → solo restaura
                        self.state.organize_sort(["original_position"], False, scope="all")
                    else:
                        rev = _orden["value"]=="za"
                        self.state.organize_sort([_dd.value], rev, scope="all")
                    self._close_dlg(dlg)
                def _cl(_e):
                    try:
                        self.state.clear_organize()
                    except Exception:
                        self.state.organize_sort(["original_position"], False, scope="all")
                    self._close_dlg(dlg)
                _has = bool(getattr(self.state, "show_dual", False) or getattr(self.state, "segments", {}) or (getattr(self.state, "source_tracks", None) and self.state.tracks != self.state.source_tracks))
                _acts: list[ft.Control] = []
                if _has:
                    _acts.append(dialog_action("Limpiar", _cl, kind="danger"))
                _acts += [dialog_action("Cancelar", lambda _:self._close_dlg(dlg), kind="muted"), dialog_action("Aplicar", _ap, kind="primary")]
                dlg = app_dialog(
                    "Organizar",
                    ft.Container(
                        content=ft.Column([_dd, _seg], tight=True, spacing=12),
                        min_width=280,
                        max_width=420,
                    ),
                    _acts,
                    width=None,
                    bgcolor=BG_SURFACE,
                    radius=10,
                    actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN if len(_acts) == 3 else ft.MainAxisAlignment.END,
                )
                self.page.show_dialog(dlg)
            except Exception:
                self.state.log(f"[ERROR] Organizar fallback falló: {exc}")

    def _on_reorganize(self, _e: ft.ControlEvent) -> None:
        """Reorganizar por niveles §8 — control específico Artistas/Álbumes drag & drop."""
        try:
            from ui.dialogs.organize_panel import show_reorganize_dialog
            show_reorganize_dialog(self.page, self.state)
        except Exception as exc:
            self.state.log(f"[ERROR] Reorganizar falló: {exc}")

    def _on_split(self, _e: ft.ControlEvent) -> None:
        """Dividir — genera Divisiones por Artista/Álbum/Género/Título §2,5."""
        try:
            from ui.dialogs.organize_panel import show_group_dialog
            show_group_dialog(self.page, self.state)
        except Exception as exc:
            try:
                _dd=organize_dropdown([("artist","Artista"),("album","Álbum"),("genre","Género"),("name","Título")],"artist","Dividir por")
                def _ap(_e): self.state.organize_split(_dd.value, scope="all"); self._close_dlg(dlg)
                def _cl(_e): self.state.clear_split(); self._close_dlg(dlg)
                acts=[]
                if bool(self.state.segments): acts.append(dialog_action("Limpiar División",_cl,kind="danger"))
                acts+=[dialog_action("Cancelar",lambda _:self._close_dlg(dlg),kind="muted"),dialog_action("Dividir",_ap,kind="primary")]
                dlg = app_dialog(
                    "Dividir",
                    ft.Container(
                        content=ft.Column([_dd], tight=True, spacing=12),
                        min_width=280,
                        max_width=400,
                    ),
                    acts,
                    width=None,
                    bgcolor=BG_SURFACE,
                    radius=10,
                )
                self.page.show_dialog(dlg)
            except Exception:
                self.state.log(f"[ERROR] Dividir fallback falló: {exc}")

    def _partition_btn_label(self) -> str:
        s = self.state
        if not s.segments:
            return "Todos"
        total = len(s.segments)
        active = s.active_segment_keys
        if active is None or len(active) == total or len(active) == 0:
            return f"Todos ({total})"
        if len(active) == 1:
            return next(iter(active))
        return f"{len(active)} seleccionados"

    def _refresh_division_popup(self) -> None:
        """Popup dinámico 2..5 divisiones — texto del botón = activa."""
        try:
            s = self.state
            if not s.segments:
                self._division_popup.visible = False
                self._division_col.visible = False
                return
            active = s.active_segment_key or next(iter(s.segments.keys()))
            self._division_btn_text.value = active
            items = []
            for k in s.segments.keys():
                cnt = len(s.segments.get(k, []))
                def _on_pick(e, _k=k):
                    try:
                        s.set_active_segment(_k)
                        self._division_btn_text.value = _k
                        self._division_popup.update()
                    except Exception:
                        pass
                items.append(ft.PopupMenuItem(content=ft.Text(f"{k} ({cnt})"), data=k, on_click=_on_pick, checked=(k == active)))
            self._division_popup.items = items
            self._division_popup.visible = True
            self._division_col.visible = True
            try:
                self._division_popup.update()
                self._division_col.update()
            except Exception:
                pass
        except Exception:
            pass

    def _open_partition_picker(self) -> None:
        s = self.state
        if not s.segments:
            self._snack("No hay divisiones — divide primero por Artista/Álbum/Género/Título", error=True)
            return
        all_keys = list(s.segments.keys())  # orden 1ª aparición O(n) §12
        # estado local mutable — §7 División activa vs seleccionadas para transferir
        selected: set[str] = set(s.active_segment_keys) if s.active_segment_keys is not None else set(s.segments.keys())

        search_field = app_text_field(hint_text="Buscar división…", prefix_icon=PIcons.MAGNIFYING_GLASS, expand=True, height=36, content_padding=ft.Padding.symmetric(horizontal=10, vertical=6))
        list_view = ft.ListView(spacing=4, padding=ft.Padding.all(4), expand=True)

        def _rebuild(filter_q: str = ""):
            q = (filter_q or "").strip().lower()
            list_view.controls.clear()
            for k in all_keys:
                if q and q not in k.lower():
                    continue
                cnt = len(s.segments.get(k, []))
                is_checked = k in selected
                # checkbox por partición — buscable
                chk = ft.Checkbox(
                    label=f"{k} ({cnt})",
                    value=is_checked,
                    fill_color={ft.ControlState.SELECTED: ACCENT},
                    check_color=TEXT_PRIMARY,
                    label_style=ft.TextStyle(color=TEXT_PRIMARY if is_checked else TEXT_MUTED, size=11, font_family=FONT_HEADLINE),
                    border_side=ft.BorderSide(1.2, ACCENT if is_checked else TEXT_DIM),
                    data=k,
                    on_change=lambda e, _k=k: _on_toggle(_k, bool(e.control.value)),
                )
                list_view.controls.append(chk)
            try:
                list_view.update()
            except Exception:
                pass

        def _on_toggle(key: str, checked: bool):
            if checked:
                selected.add(key)
            else:
                selected.discard(key)
            # no cierra, solo actualiza estado visual; si queda 0 → interpreta como Todos luego

        def _on_search(e):
            _rebuild(e.control.value or "")

        search_field.on_change = _on_search
        _rebuild("")

        dlg_ref: dict = {}

        def _apply(_e):
            # si todos seleccionados → None (Todos)
            if len(selected) == len(all_keys) or len(selected) == 0:
                s.set_active_segments(None)
            else:
                s.set_active_segments(set(selected))
            self._close_dlg(dlg_ref.get("dlg"))

        def _select_all(_e):
            selected.clear()
            selected.update(all_keys)
            _rebuild(search_field.value or "")

        def _clear(_e):
            selected.clear()
            _rebuild(search_field.value or "")

        dlg = app_dialog(
            "Filtrar divisiones",
            ft.Container(
                content=ft.Column([
                    search_field,
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                    ft.Container(content=list_view, min_height=180, max_height=260),
                ], tight=True, spacing=8),
                min_width=300,
                max_width=420,
            ),
            [
                dialog_action("Seleccionar todos", _select_all, kind="muted"),
                dialog_action("Limpiar", _clear, kind="muted"),
                dialog_action("Cancelar", lambda _: self._close_dlg(dlg_ref.get("dlg")), kind="muted"),
                dialog_action("Aplicar", _apply, kind="primary"),
            ],
            width=None, bgcolor=BG_SURFACE, radius=10,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)

    def _open_edit_division_dialog(self) -> None:
        """Editar división §4 — nombre editable, criterio artista/álbum/canción (canción default), quitar/agregar expand full."""
        s = self.state
        if not s.segments:
            self._snack("No hay divisiones para editar — divide primero", error=True)
            return
        active = s.active_segment_key or next(iter(s.segments.keys()))
        src_tracks = getattr(s, "source_tracks", s.tracks) or s.tracks
        div_tracks = s.segments.get(active, [])
        # nombre editable de la división (playlist derivada)
        name_field = app_text_field(value=active, label="Nombre división", hint_text=active, expand=True, height=36, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4))
        def _on_name(e):
            nonlocal active
            new = (e.control.value or "").strip()
            if new and new != active and new not in s.segments:
                s.segments[new] = s.segments.pop(active)
                if s.active_segment_key == active:
                    s.active_segment_key = new
                    s.active_segment_keys = {new}
                # Sin notify por tecla: la UI de fondo se actualiza al aplicar.
                active = new
        name_field.on_change = _on_name
        name_field.on_submit = _on_name
        # Solo se restauran los miembros de la división; no hace falta copiar
        # los Track completos (deepcopy de 1.000 pistas era costoso).
        _snapshot = list(s.segments.get(active, []))
        # criterio añadir / eliminar: artista/álbum/canción (canción default)
        add_criterion = {"value": "song"}
        remove_criterion = {"value": "song"}
        def _icon_for(v: str):
            return PIcons.USER if v == "artist" else PIcons.DISC if v == "album" else PIcons.MUSIC_NOTE
        # listas buscables
        left_sel: set[str] = set()
        right_sel: set[str] = set()
        left_search = app_text_field(hint_text="Buscar en ORIGINAL…", prefix_icon=PIcons.MAGNIFYING_GLASS, expand=True, height=32, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4))
        right_search = app_text_field(hint_text="Buscar en División…", prefix_icon=PIcons.MAGNIFYING_GLASS, expand=True, height=32, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4))
        left_lv = ft.ListView(spacing=4, padding=ft.Padding.all(6), expand=True)
        right_lv = ft.ListView(spacing=4, padding=ft.Padding.all(6), expand=True)
        left_limit = ft.Text("", size=9, color=TEXT_DIM, font_family=FONT_TEXT)
        right_limit = ft.Text("", size=9, color=TEXT_DIM, font_family=FONT_TEXT)

        def _rebuild_left(q: str = ""):
            ql = (q or "").strip().lower()
            left_lv.controls.clear()
            crit = add_criterion["value"]
            if crit == "artist":
                artists = sorted(
                    (a for a in set(t.artist for t in src_tracks if t.artist)
                     if not ql or ql in a.lower()),
                    key=lambda x: x.lower(),
                )
                shown = artists[:DIALOG_OPTION_LIMIT]
                left_limit.value = limit_label(len(shown), len(artists))
                for a in shown:
                    # cuenta canciones de ese artista en ORIGINAL
                    cnt = sum(1 for t in src_tracks if t.artist == a)
                    label = f"{a} ({cnt})"
                    chk = ft.Checkbox(label=label, value=a in left_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if a in left_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in left_sel else TEXT_DIM), data=a, on_change=lambda e, _a=a: (left_sel.add(_a) if e.control.value else left_sel.discard(_a)))
                    left_lv.controls.append(chk)
            elif crit == "album":
                albums = sorted(
                    (a for a in set(t.album for t in src_tracks if t.album)
                     if not ql or ql in a.lower()),
                    key=lambda x: x.lower(),
                )
                shown = albums[:DIALOG_OPTION_LIMIT]
                left_limit.value = limit_label(len(shown), len(albums))
                for a in shown:
                    cnt = sum(1 for t in src_tracks if t.album == a)
                    label = f"{a} ({cnt})"
                    chk = ft.Checkbox(label=label, value=a in left_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if a in left_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in left_sel else TEXT_DIM), data=a, on_change=lambda e, _a=a: (left_sel.add(_a) if e.control.value else left_sel.discard(_a)))
                    left_lv.controls.append(chk)
            else:
                matches = []
                for t in src_tracks:
                    label = f"{t.name[:24]} — {t.artist[:16]}"
                    if not ql or ql in label.lower():
                        matches.append((t, label))
                shown = matches[:DIALOG_OPTION_LIMIT]
                left_limit.value = limit_label(len(shown), len(matches))
                for t, label in shown:
                    chk = ft.Checkbox(label=label, value=t.id in left_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if t.id in left_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if t.id in left_sel else TEXT_DIM), data=t.id, on_change=lambda e, _tid=t.id: (left_sel.add(_tid) if e.control.value else left_sel.discard(_tid)))
                    left_lv.controls.append(chk)
            try: left_lv.update(); left_limit.update()
            except: pass

        def _rebuild_right(q: str = ""):
            ql = (q or "").strip().lower()
            right_lv.controls.clear()
            crit = remove_criterion["value"]
            if crit == "artist":
                artists = sorted(
                    (a for a in set(t.artist for t in div_tracks if t.artist)
                     if not ql or ql in a.lower()),
                    key=lambda x: x.lower(),
                )
                shown = artists[:DIALOG_OPTION_LIMIT]
                right_limit.value = limit_label(len(shown), len(artists))
                for a in shown:
                    cnt = sum(1 for t in div_tracks if t.artist == a)
                    chk = ft.Checkbox(label=f"{a} ({cnt})", value=a in right_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if a in right_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in right_sel else TEXT_DIM), data=a, on_change=lambda e, _a=a: (right_sel.add(_a) if e.control.value else right_sel.discard(_a)))
                    right_lv.controls.append(chk)
            elif crit == "album":
                albums = sorted(
                    (a for a in set(t.album for t in div_tracks if t.album)
                     if not ql or ql in a.lower()),
                    key=lambda x: x.lower(),
                )
                shown = albums[:DIALOG_OPTION_LIMIT]
                right_limit.value = limit_label(len(shown), len(albums))
                for a in shown:
                    cnt = sum(1 for t in div_tracks if t.album == a)
                    chk = ft.Checkbox(label=f"{a} ({cnt})", value=a in right_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if a in right_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in right_sel else TEXT_DIM), data=a, on_change=lambda e, _a=a: (right_sel.add(_a) if e.control.value else right_sel.discard(_a)))
                    right_lv.controls.append(chk)
            else:
                matches = []
                for t in div_tracks:
                    label = f"{t.name[:24]} — {t.artist[:16]}"
                    if not ql or ql in label.lower():
                        matches.append((t, label))
                shown = matches[:DIALOG_OPTION_LIMIT]
                right_limit.value = limit_label(len(shown), len(matches))
                for t, label in shown:
                    chk = ft.Checkbox(label=label, value=t.id in right_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if t.id in right_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if t.id in right_sel else TEXT_DIM), data=t.id, on_change=lambda e, _tid=t.id: (right_sel.add(_tid) if e.control.value else right_sel.discard(_tid)))
                    right_lv.controls.append(chk)
            try: right_lv.update(); right_limit.update()
            except: pass

        # IconButton + ContextMenu a la IZQUIERDA de cada buscador, icono cambia según criterio.
        add_icon_ctrl = app_icon(PIcons.MUSIC_NOTE, size=16, color=ACCENT)
        add_popup = ft.PopupMenuButton(
            content=add_icon_ctrl, tooltip="Añadir por",
            items=[
                ft.PopupMenuItem(content=ft.Row([app_icon(PIcons.USER, size=15, color=TEXT_PRIMARY), ft.Text("Artista", font_family=FONT_TEXT, size=12)], spacing=8), on_click=lambda _: _set_add_crit("artist")),
                ft.PopupMenuItem(content=ft.Row([app_icon(PIcons.DISC, size=15, color=TEXT_PRIMARY), ft.Text("Álbum", font_family=FONT_TEXT, size=12)], spacing=8), on_click=lambda _: _set_add_crit("album")),
                ft.PopupMenuItem(content=ft.Row([app_icon(PIcons.MUSIC_NOTE, size=15, color=TEXT_PRIMARY), ft.Text("Canción", font_family=FONT_TEXT, size=12)], spacing=8), on_click=lambda _: _set_add_crit("song")),
            ],
        )
        remove_icon_ctrl = app_icon(PIcons.MUSIC_NOTE, size=16, color=WARNING)
        remove_popup = ft.PopupMenuButton(
            content=remove_icon_ctrl, tooltip="Eliminar por",
            items=[
                ft.PopupMenuItem(content=ft.Row([app_icon(PIcons.USER, size=15, color=TEXT_PRIMARY), ft.Text("Artista", font_family=FONT_TEXT, size=12)], spacing=8), on_click=lambda _: _set_remove_crit("artist")),
                ft.PopupMenuItem(content=ft.Row([app_icon(PIcons.DISC, size=15, color=TEXT_PRIMARY), ft.Text("Álbum", font_family=FONT_TEXT, size=12)], spacing=8), on_click=lambda _: _set_remove_crit("album")),
                ft.PopupMenuItem(content=ft.Row([app_icon(PIcons.MUSIC_NOTE, size=15, color=TEXT_PRIMARY), ft.Text("Canción", font_family=FONT_TEXT, size=12)], spacing=8), on_click=lambda _: _set_remove_crit("song")),
            ],
        )
        def _set_add_crit(v: str):
            add_criterion["value"] = v
            add_icon_ctrl.value = _icon_for(v)
            try: add_popup.update()
            except: pass
            _rebuild_left(left_search.value or "")
        def _set_remove_crit(v: str):
            remove_criterion["value"] = v
            remove_icon_ctrl.value = _icon_for(v)
            try: remove_popup.update()
            except: pass
            _rebuild_right(right_search.value or "")

        left_search.on_change = lambda e: debounce(
            "edit_division_left",
            lambda: _rebuild_left(left_search.value or ""),
        )
        right_search.on_change = lambda e: debounce(
            "edit_division_right",
            lambda: _rebuild_right(right_search.value or ""),
        )
        _rebuild_left("")
        _rebuild_right("")

        def _add_to_division(_e):
            crit = add_criterion["value"]
            if crit == "artist":
                target_ids = [t.id for t in src_tracks if t.artist in left_sel]
            elif crit == "album":
                target_ids = [t.id for t in src_tracks if t.album in left_sel]
            else:
                target_ids = list(left_sel)
            s.add_tracks_to_division(target_ids, active, notify=False)
            left_sel.clear()
            nonlocal div_tracks
            div_tracks = s.segments.get(active, [])
            _rebuild_left(left_search.value or "")
            _rebuild_right(right_search.value or "")
            safe_update(dlg_ref.get("dlg"))

        def _remove_from_division(_e):
            nonlocal div_tracks
            crit = remove_criterion["value"]
            if crit == "artist":
                target_ids = [t.id for t in list(div_tracks) if t.artist in right_sel]
            elif crit == "album":
                target_ids = [t.id for t in list(div_tracks) if t.album in right_sel]
            else:
                target_ids = list(right_sel)
            s.remove_tracks_from_division(target_ids, active, notify=False)
            right_sel.clear()
            div_tracks = s.segments.get(active, [])
            _rebuild_right(right_search.value or "")
            safe_update(dlg_ref.get("dlg"))

        def _apply_edit(_e):
            s.apply_search(s.search_query)
            self._close_dlg(dlg_ref.get("dlg"))

        def _close_edit(_e):
            # Cerrar conserva los cambios, igual que el flujo anterior.
            s.apply_search(s.search_query)
            self._close_dlg(dlg_ref.get("dlg"))

        def _revert_edit(_e):
            s.segments[active] = list(_snapshot)
            s.apply_search(s.search_query)
            try:
                nonlocal div_tracks
                div_tracks = s.segments.get(active, [])
                _rebuild_left(left_search.value or "")
                _rebuild_right(right_search.value or "")
                safe_update(dlg_ref.get("dlg"))
            except: pass

        dlg_ref: dict = {}
        dlg = app_dialog(
            f"Editar división — {active}",
            ft.Container(
                content=ft.Column([
                    name_field,
                    ft.Row([
                        ft.Column([ft.Row([add_popup, left_search], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER), left_limit, ft.Container(content=left_lv, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, min_height=200, max_height=320, min_width=240), dialog_action("Agregar →", _add_to_division, kind="primary", icon=PIcons.ARROW_RIGHT)], spacing=6, expand=True),
                        ft.Column([ft.Row([remove_popup, right_search], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER), right_limit, ft.Container(content=right_lv, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, min_height=200, max_height=320, min_width=240), dialog_action("← Quitar", _remove_from_division, kind="danger", icon=PIcons.ARROW_LEFT)], spacing=6, expand=True),
                    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
                ], tight=True, spacing=8),
                min_width=520,
                max_width=820,
            ),
            [
                dialog_action("Cerrar", _close_edit, kind="muted"),
                dialog_action("Revertir", _revert_edit, kind="muted"),
                dialog_action("Aplicar", _apply_edit, kind="primary"),
            ],
            width=None, bgcolor=BG_SURFACE, radius=10,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)
