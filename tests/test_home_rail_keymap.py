"""Tests para tokens/relieve de papel, rail colapsable, Inicio 3.1, Config DI y foco regional."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flet as ft

import services.settings_store as settings_store
from core.library_models import PlaylistSummary
from core.models import TransferState
from core.state import AppState
from ui.components.widgets import (
    apply_paper_relief,
    apply_region_focus,
    focus_ring_container,
    obi_strip,
    set_focus_ring_container,
    status_chip,
)
from ui.shell.keymap import (
    FocusRegion,
    RegionFocusController,
    SHORTCUTS_BY_ACTION,
    match_shortcut,
)
from ui.shell.main_ui import PlaylistManagerUI
from ui.shell.nav_rail import HOME_MODULE_IDX, SEARCH_MODULE_IDX, _RAIL_COLLAPSED_KEY, build_nav_rail
from ui.theme.tokens import (
    ACCENT,
    BG_PANEL,
    BORDER_CONTROL,
    DECOR_LINE,
    FOCUS_RING_OFFSET,
    FOCUS_RING_WIDTH,
    HOME_CONN_ROW_H,
    HOME_CONTINUAR_ART,
    HOME_HERO_H,
    PAPER_LIFT,
    PAPER_LIFT_SM,
    RAIL_BRAND_SLOT_SZ,
    RAIL_W_COLLAPSED,
    RAIL_W_EXPANDED,
    SHADOW_INK,
    SHADOW_INK_2,
    SPACE_1,
    SPACE_7,
)
from ui.views.home_view import HomeView


def _mock_page(width: float = 1280.0, height: float = 800.0) -> MagicMock:
    page = MagicMock()
    page.width = width
    page.height = height
    page.overlay = []
    page.services = []
    page.window = MagicMock()
    page.window.width = width

    def _run_task(coro_fn, *args, **kwargs):
        coro = coro_fn(*args, **kwargs)
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    page.run_task = MagicMock(side_effect=_run_task)
    return page


def _key_event(key: str, *, ctrl: bool = False, shift: bool = False, alt: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        ctrl=ctrl,
        shift=shift,
        alt=alt,
        meta=False,
        control=None,
    )


class TestTokensAndWidgetsHelpers(unittest.TestCase):
    def test_demo_tokens_and_paper_relief_grammar(self) -> None:
        self.assertEqual((SPACE_1, SPACE_7), (4, 48))
        self.assertEqual((PAPER_LIFT_SM, PAPER_LIFT), (2, 3))
        self.assertEqual((RAIL_W_COLLAPSED, RAIL_W_EXPANDED), (68, 220))
        self.assertEqual(RAIL_BRAND_SLOT_SZ, 32)
        self.assertEqual(HOME_HERO_H, 176)
        self.assertEqual(HOME_CONTINUAR_ART, 132)
        self.assertEqual(HOME_CONN_ROW_H, 36)
        self.assertTrue(BORDER_CONTROL and DECOR_LINE and SHADOW_INK and SHADOW_INK_2)

        card = ft.Container(bgcolor=BG_PANEL)
        apply_paper_relief(card, layers=2)
        self.assertEqual(len(card.shadow), 2)
        self.assertGreater(card.shadow[0].offset.x, 0)
        self.assertGreater(card.shadow[0].offset.y, 0)
        self.assertEqual(card.shadow[0].blur_radius, 0)
        self.assertEqual(card.shadow[0].offset.x, PAPER_LIFT)
        self.assertEqual(card.shadow[1].offset.x, PAPER_LIFT * 2)
        self.assertIsNotNone(card.border)
        self.assertGreater(card.border.bottom.width, card.border.top.width)

        obi = obi_strip("YouTube Music")
        self.assertEqual(obi.width, 15)
        chip = status_chip("OK", kind="ok")
        self.assertIsInstance(chip, ft.Container)

    def test_focus_ring_container_is_2_plus_2_zero_ms(self) -> None:
        inner = ft.Container(width=40, height=20)
        wrap = focus_ring_container(inner, focused=False)
        self.assertEqual(wrap.padding, ft.Padding.all(FOCUS_RING_OFFSET))
        self.assertIsNone(wrap.animate)
        set_focus_ring_container(wrap, True)
        self.assertEqual(wrap.border.top.width, FOCUS_RING_WIDTH)
        self.assertEqual(wrap.border.top.color, ACCENT)
        apply_region_focus(inner, True, inset=True)
        self.assertEqual(inner.border.top.width, FOCUS_RING_WIDTH)
        self.assertEqual(inner.shadow, [])
        self.assertIsNone(inner.animate)


class TestCollapsibleNavRail(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg_file = Path(self._tmp.name) / "settings.json"
        self._p1 = patch.object(settings_store, "SETTINGS_JSON", self._cfg_file)
        self._p2 = patch.object(
            settings_store,
            "ensure_config_dir",
            lambda: self._cfg_file.parent.mkdir(parents=True, exist_ok=True),
        )
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p2.stop()
        self._p1.stop()
        self._tmp.cleanup()

    def _build_rail_harness(self, width: float = 1280.0) -> SimpleNamespace:
        page = _mock_page(width=width)
        state = AppState(service=None)  # type: ignore[arg-type]
        panels = [ft.Container(visible=(i == 0)) for i in range(5)]
        ui = SimpleNamespace(
            page=page,
            state=state,
            _current_module=0,
            _module_panels=panels,
            _module_stack=ft.Stack(controls=panels),
            _sync_skeleton_count=lambda: None,
            _on_state_changed=lambda: None,
            _gestion_dirty=False,
        )
        build_nav_rail(ui)
        return ui

    def test_five_destinations_brand_slot_and_persisted_collapse(self) -> None:
        ui = self._build_rail_harness(width=1280.0)
        self.assertEqual(len(ui._rail_dests), 6)
        self.assertEqual(len(ui._rail_item_refs), 6)
        self.assertEqual(ui._rail_dests[0][0], HOME_MODULE_IDX)
        self.assertEqual(ui._rail_dests[1][0], SEARCH_MODULE_IDX)
        self.assertEqual(ui._rail_dests[-1][0], 3)
        self.assertFalse(ui._rail_collapsed)
        self.assertEqual(ui._nav_rail.width, RAIL_W_EXPANDED)
        self.assertFalse(ui._rail_brand_slot.visible)

        ui._toggle_rail()
        self.assertTrue(ui._rail_collapsed)
        self.assertEqual(ui._nav_rail.width, RAIL_W_COLLAPSED)
        self.assertTrue(ui._rail_brand_slot.visible)
        self.assertTrue(settings_store.load_settings().get(_RAIL_COLLAPSED_KEY))

        rebuilt = self._build_rail_harness(width=1280.0)
        self.assertTrue(rebuilt._rail_collapsed)
        self.assertEqual(rebuilt._nav_rail.width, RAIL_W_COLLAPSED)

    def test_auto_collapse_below_1100_and_active_downloads_badge(self) -> None:
        ui = self._build_rail_harness(width=1280.0)
        self.assertFalse(ui._rail_collapsed)
        ui._sync_rail_breakpoint(1024.0)
        self.assertTrue(ui._rail_collapsed)
        self.assertEqual(ui._nav_rail.width, RAIL_W_COLLAPSED)
        ui._sync_rail_breakpoint(1280.0)
        self.assertFalse(ui._rail_collapsed)

        ui.state.download_state = TransferState.RUNNING
        ui.state.download_items = [
            {"id": "1", "status": "downloading"},
            {"id": "2", "status": "pending"},
        ]
        ui._refresh_rail_downloads_badge(update=False)
        self.assertTrue(ui._rail_dl_badge_expanded.visible)
        self.assertEqual(ui._rail_dl_badge_text.value, "2")


class TestHomeViewAndMainUIIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg_file = Path(self._tmp.name) / "settings.json"
        self._p1 = patch.object(settings_store, "SETTINGS_JSON", self._cfg_file)
        self._p2 = patch.object(
            settings_store,
            "ensure_config_dir",
            lambda: self._cfg_file.parent.mkdir(parents=True, exist_ok=True),
        )
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p2.stop()
        self._p1.stop()
        self._tmp.cleanup()

    def test_home_view_sections_player_callback_and_recents(self) -> None:
        page = _mock_page()
        state = AppState(service=None)  # type: ignore[arg-type]
        state.auth_session_ok["YouTube Music"] = True
        state.auth_session_hint["YouTube Music"] = "User"
        state.auth_session_ok["Spotify"] = False
        state.auth_session_hint["Spotify"] = "Expirada"

        recent = PlaylistSummary(
            id="pl-1",
            name="City Pop 1983",
            platform="Spotify",
            track_count=24,
            cover_urls=(),
            synced_at=1_700_000_000.0,
        )
        lib_state = SimpleNamespace(
            get_playlists=lambda _plat=None: [recent] if _plat == "Spotify" else [],
            local_playlists=[],
            local_folders=[],
            subscribe=lambda _cb: None,
        )
        play_clicks: list[int] = []
        player_bar = SimpleNamespace(
            queue=SimpleNamespace(current=SimpleNamespace(
                title="Plastic Love",
                artist="Mariya Takeuchi",
                album="Variety",
                source="LOCAL",
                img_url="",
            )),
            is_playing=False,
            position_ms=30_000,
            duration_ms=180_000,
            _on_play_pause_click=lambda _e=None: play_clicks.append(1),
            open_now_playing_scene=lambda: None,
            subscribe=lambda _cb: None,
            unsubscribe=lambda _cb: None,
            subscribe_position=lambda _cb: None,
            unsubscribe_position=lambda _cb: None,
        )
        opened: list[PlaylistSummary] = []
        wizards: list[str | None] = []
        home = HomeView(
            page,
            state,
            library_state=lib_state,
            player_bar=player_bar,
            on_navigate=lambda _i: None,
            on_open_playlist=opened.append,
            on_open_wizard=wizards.append,
        )
        self.assertEqual(home._hero.height, HOME_HERO_H)
        self.assertEqual(len(home._platter_card.shadow), 2)
        self.assertEqual(len(home._conn_rows_col.controls), 3)
        self.assertEqual(home._conn_rows_col.controls[0].height, HOME_CONN_ROW_H)
        self.assertEqual(len(home._recents_row.controls), 1)

        home._on_continuar_click()
        self.assertEqual(play_clicks, [1])
        home._on_connection_click("Spotify", True)
        self.assertEqual(wizards, ["Spotify"])
        home._open_recent(recent)
        self.assertEqual(opened, [recent])

    def test_main_ui_config_injection_home_mount_and_keyboard(self) -> None:
        page = _mock_page(width=1280.0)
        dummy_service = MagicMock()
        state = AppState(service=dummy_service)
        ui = PlaylistManagerUI(page, state)

        self.assertEqual(ui._current_module, 0)
        self.assertIsNotNone(getattr(ui, "_panel_inicio", None))
        self.assertIsNotNone(getattr(ui, "_panel_buscar", None))
        self.assertEqual(len(ui._module_panels), 6)
        self.assertEqual(len(ui._module_stack.controls), 6)
        self.assertIs(ui._module_panels[HOME_MODULE_IDX], ui._panel_inicio)
        self.assertIs(ui._module_panels[SEARCH_MODULE_IDX], ui._panel_buscar)
        self.assertIs(ui._panel_config.service, dummy_service)
        self.assertIsNotNone(ui._panel_config._on_open_wizard)

        ui._select_module(HOME_MODULE_IDX)
        self.assertTrue(ui._panel_inicio.visible)
        ui._select_module(SEARCH_MODULE_IDX)
        self.assertTrue(ui._panel_buscar.visible)
        self.assertFalse(ui._panel_inicio.visible)
        ui._select_module(0)
        self.assertFalse(ui._panel_inicio.visible)
        self.assertFalse(ui._panel_buscar.visible)

        auth_mgr = MagicMock()
        ui.auth_manager = auth_mgr
        self.assertIs(ui._panel_config.auth_manager, auth_mgr)

        # Ctrl+6 -> Config, '[' -> colapsa rail, Ctrl+1 -> Inicio
        ui._on_keyboard_event(_key_event("6", ctrl=True))
        self.assertEqual(ui._current_module, 3)
        ui._on_keyboard_event(_key_event("["))
        self.assertTrue(ui._rail_collapsed)
        ui._on_keyboard_event(_key_event("1", ctrl=True))
        self.assertEqual(ui._current_module, HOME_MODULE_IDX)

        # Tab -> Arrow Down -> Enter en rail navega a Buscar (visual idx 1)
        ui._on_keyboard_event(_key_event("Tab"))
        self.assertEqual(ui._focus_regions.active_region, "rail")
        ui._on_keyboard_event(_key_event("Arrow Down"))
        ui._on_keyboard_event(_key_event("Enter"))
        self.assertEqual(ui._current_module, SEARCH_MODULE_IDX)

        # Abrir playlist desde Inicio salta a Biblioteca y delega al panel
        sample = PlaylistSummary(
            id="pl-home",
            name="Test Recent",
            platform="YouTube Music",
            track_count=5,
        )
        with patch.object(ui._panel_biblioteca, "_on_open_playlist") as open_mock:
            ui._open_playlist_from_home(sample)
        self.assertEqual(ui._current_module, 1)
        open_mock.assert_called_once_with(sample)


class TestKeymapAndRegionFocus(unittest.TestCase):
    def test_shortcuts_and_region_controller(self) -> None:
        self.assertIsNotNone(
            match_shortcut(_key_event("Space", ctrl=True), in_text_input=True)
        )
        self.assertEqual(
            match_shortcut(_key_event("[")).action,
            "toggle_rail",
        )
        self.assertEqual(
            match_shortcut(_key_event("a", ctrl=True)).action,
            "select_all",
        )
        for req in ("close_overlay", "play_pause", "focus_search", "toggle_queue", "mute"):
            self.assertIn(req, SHORTCUTS_BY_ACTION)

        ctrl = RegionFocusController()
        focused_a: list[int | None] = []
        focused_b: list[int | None] = []
        activated: list[int] = []
        deleted: list[int] = []
        selected: list[int] = []
        select_all_calls: list[int] = []
        ctrl.register(
            FocusRegion(
                name="rail",
                item_count=lambda: 3,
                on_focus=focused_a.append,
                on_activate=activated.append,
            )
        )
        ctrl.register(
            FocusRegion(
                name="queue",
                item_count=lambda: 2,
                on_focus=focused_b.append,
                on_select=selected.append,
                on_delete=deleted.append,
                on_select_all=lambda: select_all_calls.append(1),
            )
        )
        self.assertTrue(ctrl.handle_event(_key_event("Tab")))
        self.assertEqual(focused_a[-1], 0)
        self.assertTrue(ctrl.handle_event(_key_event("End")))
        self.assertEqual(focused_a[-1], 2)
        self.assertTrue(ctrl.handle_event(_key_event("Enter")))
        self.assertEqual(activated, [2])

        self.assertTrue(ctrl.handle_event(_key_event("Tab")))
        self.assertEqual(ctrl.active_region, "queue")
        self.assertIsNone(focused_a[-1])
        self.assertEqual(focused_b[-1], 0)
        self.assertTrue(ctrl.handle_event(_key_event("Space")))
        self.assertEqual(selected, [0])
        self.assertTrue(ctrl.handle_event(_key_event("Delete")))
        self.assertEqual(deleted, [0])
        self.assertTrue(ctrl.handle_event(_key_event("a", ctrl=True)))
        self.assertEqual(select_all_calls, [1])
        ctrl.handle_event(_key_event("Escape"))
        self.assertIsNone(ctrl.active_region)
        self.assertIsNone(focused_b[-1])


if __name__ == "__main__":
    unittest.main()
