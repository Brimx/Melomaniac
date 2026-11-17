"""Tests unitarios para settings_store (claves de Config/Escena/Letras) y ConfigView."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft

import services.settings_store as settings_store
import ui.views.config_view as config_view_mod
from ui.components.widgets import AppSwitch
from ui.views.config_sections import CONFIG_SECTIONS
from ui.views.config_view import ConfigView


def _mock_page(width: float = 1280.0, height: float = 800.0) -> MagicMock:
    page = MagicMock()
    page.width = width
    page.height = height
    page.overlay = []
    page.services = []
    page.window = MagicMock()
    page.window.width = width

    def _run_task(coro_fn, *args, **kwargs):
        # Evita RuntimeWarning en tests síncronos cerrando corrutinas no ejecutadas.
        coro = coro_fn(*args, **kwargs)
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    page.run_task = MagicMock(side_effect=_run_task)
    return page


def _walk_controls(root: ft.Control) -> list[ft.Control]:
    found: list[ft.Control] = []
    stack: list[Any] = [root]
    while stack:
        ctrl = stack.pop()
        if ctrl is None:
            continue
        if isinstance(ctrl, ft.Control):
            found.append(ctrl)
        for attr in ("content",):
            child = getattr(ctrl, attr, None)
            if child is not None:
                stack.append(child)
        for attr in ("controls", "segments"):
            children = getattr(ctrl, attr, None)
            if isinstance(children, (list, tuple)):
                stack.extend(children)
    return found


class SettingsStoreConfigTests(unittest.TestCase):
    """Verifica persistencia, validaciones y reset de claves en settings_store."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "settings.json"
        self._p1 = patch.object(settings_store, "SETTINGS_JSON", self._path)
        self._p2 = patch.object(settings_store, "ensure_config_dir", lambda: self._path.parent.mkdir(parents=True, exist_ok=True))
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p2.stop()
        self._p1.stop()
        self._tmp.cleanup()

    def test_defaults_and_roundtrip_for_lyrics_scene_and_appearance(self) -> None:
        self.assertTrue(settings_store.get_lyrics_enabled())
        self.assertEqual(settings_store.get_lyrics_provider(), "lrclib")
        self.assertFalse(settings_store.get_lyrics_save_lrc())
        self.assertFalse(settings_store.get_lyrics_embed_tags())
        self.assertTrue(settings_store.get_scene_show_disc())
        self.assertTrue(settings_store.get_scene_ambient_bg())
        self.assertFalse(settings_store.get_reduce_motion())
        self.assertEqual(settings_store.get_queue_mode(), "push")
        self.assertEqual(settings_store.get_theme(), "sala")

        settings_store.set_lyrics_enabled(False)
        settings_store.set_lyrics_provider("local")
        settings_store.set_lyrics_save_lrc(True)
        settings_store.set_lyrics_embed_tags(True)
        settings_store.set_scene_show_disc(False)
        settings_store.set_scene_ambient_bg(False)
        settings_store.set_reduce_motion(True)
        settings_store.set_mpris_enabled(False)
        settings_store.set_theme("matcha")
        settings_store.set_preferred_browser("firefox")

        self.assertFalse(settings_store.get_lyrics_enabled())
        self.assertEqual(settings_store.get_lyrics_provider(), "local")
        self.assertTrue(settings_store.get_lyrics_save_lrc())
        self.assertTrue(settings_store.get_lyrics_embed_tags())
        self.assertFalse(settings_store.get_scene_show_disc())
        self.assertFalse(settings_store.get_scene_ambient_bg())
        self.assertTrue(settings_store.get_reduce_motion())
        self.assertFalse(settings_store.get_mpris_enabled())
        self.assertEqual(settings_store.get_theme(), "matcha")
        self.assertEqual(settings_store.get_preferred_browser(), "firefox")

    def test_queue_mode_and_theme_validation_and_reset(self) -> None:
        settings_store.set_queue_mode("over")
        self.assertEqual(settings_store.get_queue_mode(), "overlay")
        settings_store.set_queue_mode("invalid")
        self.assertEqual(settings_store.get_queue_mode(), "push")

        settings_store.set_theme("desconocido")
        self.assertEqual(settings_store.get_theme(), "sala")

        settings_store.set_onboarding_done(True)
        settings_store.set_theme("washi")
        settings_store.reset_settings(preserve_onboarding=True)
        self.assertTrue(settings_store.get_onboarding_done())
        self.assertEqual(settings_store.get_theme(), "sala")


class ConfigViewTests(unittest.TestCase):
    """Verifica construcción perezosa, responsividad, controles nativos y guardas."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "settings.json"
        self._p1 = patch.object(settings_store, "SETTINGS_JSON", self._path)
        self._p2 = patch.object(settings_store, "ensure_config_dir", lambda: self._path.parent.mkdir(parents=True, exist_ok=True))
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p2.stop()
        self._p1.stop()
        self._tmp.cleanup()

    def test_lazy_section_construction_only_instantiates_active_section(self) -> None:
        page = _mock_page(1280)
        with (
            patch.object(config_view_mod, "build_conexiones_section", wraps=config_view_mod.build_conexiones_section) as b_con,
            patch.object(config_view_mod, "build_reproduccion_section", wraps=config_view_mod.build_reproduccion_section) as b_rep,
            patch.object(config_view_mod, "build_apariencia_section", wraps=config_view_mod.build_apariencia_section) as b_apa,
            patch.object(config_view_mod, "build_acerca_section", wraps=config_view_mod.build_acerca_section) as b_ace,
        ):
            view = ConfigView(page)
            self.assertEqual(b_con.call_count, 1)
            self.assertEqual(b_rep.call_count, 0)
            self.assertEqual(b_apa.call_count, 0)
            self.assertEqual(b_ace.call_count, 0)

            view.select_section("apariencia", update=False)
            self.assertEqual(b_apa.call_count, 1)
            self.assertEqual(b_rep.call_count, 0)
            self.assertEqual(b_ace.call_count, 0)

            for sec_id, _num, _label, _icon in CONFIG_SECTIONS:
                view.select_section(sec_id, update=False)
                self.assertEqual(view._active_section, sec_id)
                self.assertGreater(len(view._body_col.controls), 0)

    def test_responsive_breakpoints_compact_normal_wide_and_acerca_split(self) -> None:
        view_compact = ConfigView(_mock_page(960))
        self.assertTrue(view_compact._is_compact)
        self.assertFalse(view_compact._is_wide)
        self.assertIsInstance(view_compact._layout_host.content, ft.Column)

        view = ConfigView(_mock_page(1280))
        self.assertFalse(view._is_compact)
        self.assertFalse(view._is_wide)
        self.assertIsInstance(view._layout_host.content, ft.Row)

        view.select_section("acerca", update=False)
        controls_normal = _walk_controls(view._body_col)
        self.assertTrue(any(isinstance(c, ft.ExpansionTile) for c in controls_normal))

        # Simula evento de tamaño > 1440 px en el propio contenedor.
        view._on_size_change(SimpleNamespace(width=1520.0))  # type: ignore[arg-type]
        self.assertFalse(view._is_compact)
        self.assertTrue(view._is_wide)
        controls_wide = _walk_controls(view._body_col)
        self.assertFalse(any(isinstance(c, ft.ExpansionTile) for c in controls_wide))
        self.assertIsInstance(view._body_col.controls[0], ft.Row)

        # Al encoger <= 1024 px la navegación pasa arriba como Column sin ancho fijo de cuerpo.
        view._on_size_change(SimpleNamespace(width=980.0))  # type: ignore[arg-type]
        self.assertTrue(view._is_compact)
        self.assertIsNone(view._body_frame.width)
        self.assertIsInstance(view._layout_host.content, ft.Column)

    def test_no_duplicate_reduce_motion_in_apariencia_and_disabled_reasons(self) -> None:
        view = ConfigView(_mock_page(1280))
        view.select_section("apariencia", update=False)
        ap_controls = _walk_controls(view._body_col)
        switches = [c for c in ap_controls if isinstance(c, (ft.Switch, AppSwitch))]
        self.assertEqual(len(switches), 0)
        texts = [getattr(c, "value", "") for c in ap_controls if isinstance(c, ft.Text)]
        self.assertTrue(any("Ir a Accesibilidad" in str(t) for t in texts))
        self.assertTrue(any("No disponible:" in str(t) for t in texts))

        view.select_section("accesibilidad", update=False)
        acc_switches = [c for c in _walk_controls(view._body_col) if isinstance(c, (ft.Switch, AppSwitch))]
        self.assertEqual(sum(1 for sw in acc_switches if not sw.disabled), 1)

    def test_native_controls_persist_volume_lyrics_provider_queue_mode_and_theme(self) -> None:
        on_change = MagicMock()
        view = ConfigView(_mock_page(1280), on_change=on_change)

        view.select_section("reproduccion", update=False)
        view._on_volume_sliding(SimpleNamespace(control=SimpleNamespace(value=42)))
        self.assertEqual(view._vol_label.value, "42 %")
        self.assertNotAlmostEqual(settings_store.get_volume(), 0.42, places=2)

        view._on_volume_commit(SimpleNamespace(control=SimpleNamespace(value=42)))
        self.assertAlmostEqual(settings_store.get_volume(), 0.42, places=2)

        view._on_lyrics_provider_change(SimpleNamespace(control=SimpleNamespace(selected=["off"])))
        self.assertEqual(settings_store.get_lyrics_provider(), "off")

        view.select_section("accesibilidad", update=False)
        self.assertIsNone(view._queue_preview_blocks)

        view.select_section("apariencia", update=False)
        view._on_theme_change(SimpleNamespace(control=SimpleNamespace(value="washi")))
        self.assertEqual(settings_store.get_theme(), "washi")
        self.assertGreaterEqual(on_change.call_count, 3)


class ConfigViewAsyncTests(unittest.IsolatedAsyncioTestCase):
    """Verifica confirmaciones asíncronas (quitar carpeta local)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "settings.json"
        self._p1 = patch.object(settings_store, "SETTINGS_JSON", self._path)
        self._p2 = patch.object(settings_store, "ensure_config_dir", lambda: self._path.parent.mkdir(parents=True, exist_ok=True))
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p2.stop()
        self._p1.stop()
        self._tmp.cleanup()

    async def test_remove_local_folder_requires_confirmation_and_isolates_rows(self) -> None:
        settings_store.add_local_folder("/tmp/musica-uno")
        view = ConfigView(_mock_page(1280))
        view.select_section("biblioteca", update=False)

        self.assertTrue(all(ctrl.is_isolated() for ctrl in view._local_folders_col.controls))

        with patch.object(view, "_ask_confirm", new=AsyncMock(return_value=False)) as ask_mock:
            await view._remove_local_folder_async("/tmp/musica-uno")
            ask_mock.assert_awaited_once()
            self.assertEqual(settings_store.get_local_folders(), ["/tmp/musica-uno"])

        with patch.object(view, "_ask_confirm", new=AsyncMock(return_value=True)) as ask_mock:
            await view._remove_local_folder_async("/tmp/musica-uno")
            ask_mock.assert_awaited_once()
            self.assertEqual(settings_store.get_local_folders(), [])

    async def test_verify_without_service_does_not_instantiate_api_service_and_ask_confirm_uses_app_dialog(self) -> None:
        view = ConfigView(_mock_page(1280))
        self.assertIsNone(view.auth_manager)
        self.assertIsNone(view.service)

        await view._verify_platform_async("YouTube Music")
        self.assertEqual(view._provider_status["YouTube Music"]["label"], "Sin servicio")

        with patch.object(config_view_mod, "app_dialog", wraps=config_view_mod.app_dialog) as dlg_spy, \
             patch.object(config_view_mod, "show_dialog") as show_mock:
            def _dismiss_first(page, dlg):
                dlg.on_dismiss(None)

            show_mock.side_effect = _dismiss_first
            res = await view._ask_confirm("Título", "Mensaje")
            self.assertFalse(res)
            dlg_spy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
