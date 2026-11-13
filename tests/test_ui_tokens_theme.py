"""
tests/test_ui_tokens_theme.py — Pruebas A1: tokens, Theme y reveal ASCII en skeletons
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from ui.theme import tokens


def _run_in_subprocess(code: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Subprocess check failed (rc={proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


class TestTokensAndTheme(unittest.TestCase):
    def test_tokens_a1_values(self) -> None:
        self.assertEqual(tokens.MOTION_REVEAL_MS, 400)
        self.assertEqual(tokens.TEXT_MUTED.upper(), "#FF848DA1")

    def test_app_oled_theme_and_widgets(self) -> None:
        _run_in_subprocess(
            """
import os
import flet as ft
import app
from ui.theme import tokens
from ui.components.widgets import (
    app_text_field,
    build_input_decoration_theme,
    input_border,
    organize_dropdown,
)

assert os.environ.get("FLET_DESKTOP_FLAVOR") == "full"
theme = app.build_oled_theme()
assert theme.material_tap_target_size == ft.MaterialTapTargetSize.SHRINK_WRAP
idt = theme.input_decoration_theme
assert isinstance(idt, ft.InputDecorationTheme)
assert idt.dense is True
assert idt.filled is True
assert idt.fill_color == tokens.BG_INPUT
assert isinstance(idt.border, dict)
assert idt.border[ft.ControlState.DEFAULT].side.color == tokens.BORDER_LIGHT
assert idt.border[ft.ControlState.FOCUSED].side.color == tokens.ACCENT
assert idt.border[ft.ControlState.DEFAULT].border_radius == tokens.RADIUS_INPUT

idt2 = build_input_decoration_theme()
borders = input_border()
assert ft.ControlState.DEFAULT in borders and ft.ControlState.FOCUSED in borders
assert borders[ft.ControlState.DEFAULT].side.color == idt2.border[ft.ControlState.DEFAULT].side.color

tf = app_text_field(label="Nombre", hint_text="Escribe aquí")
assert tf.dense is True
assert tf.filled is True
assert tf.fill_color == tokens.BG_INPUT
assert tf.bgcolor == tokens.BG_INPUT
assert tf.content_padding == idt2.content_padding

dd = organize_dropdown([("a", "A"), ("b", "B")], "a", "Clave", width=180)
assert dd.width == 180
assert dd.dense is True
assert dd.filled is True
assert dd.fill_color == tokens.BG_INPUT
assert dd.content_padding == idt2.content_padding
assert dd.border[ft.ControlState.FOCUSED].side.color == tokens.ACCENT
"""
        )

    def test_skeleton_ascii_reveal_and_reduced_motion(self) -> None:
        _run_in_subprocess(
            """
import asyncio
import os
from unittest.mock import patch
from core.models import Track
from ui.components.song_row import (
    SongRow,
    build_preview_row,
)
from ui.components.skeletons import (
    LibrarySkeletonRow,
    PreviewSkeletonRow,
    SkeletonRow,
    is_reduced_motion,
)

async def _run() -> None:
    for cls in (SkeletonRow, PreviewSkeletonRow, LibrarySkeletonRow):
        sk = cls(0, reduced_motion=False)
        seen_frames = []
        orig_safe_update = sk._safe_update

        def _capture_update(ctrl=None, _seen=seen_frames, _orig=orig_safe_update, _sk=sk):
            if ctrl is _sk._reveal_wrap and _sk._reveal_text.value:
                _seen.append(_sk._reveal_text.value)
            _orig(ctrl)

        sk._safe_update = _capture_update
        with patch("ui.components.skeletons.asyncio.sleep", return_value=None):
            await sk._run_ascii_reveal()

        assert len(seen_frames) >= 3, (cls.__name__, seen_frames)
        assert all(f.isascii() for f in seen_frames)
        assert not sk._reveal_wrap.visible
        assert sk._reveal_text.value == ""

    sk_rm = SkeletonRow(0, reduced_motion=True)
    await sk_rm.start_pulse()
    assert not sk_rm._reveal_wrap.visible
    assert sk_rm._reveal_text.value == ""
    assert sk_rm.scale == 1.0

    with patch.dict(os.environ, {"MELOMANIAC_REDUCED_MOTION": "1"}):
        assert is_reduced_motion()
        sk_env = LibrarySkeletonRow(0)
        await sk_env.start_pulse()
        assert not sk_env._reveal_wrap.visible
        assert sk_env._reveal_text.value == ""

    tr = Track(
        id="t1",
        name="Canción",
        artist="Artista",
        album="Álbum",
        duration="3:15",
        img_url="",
        platform="spotify",
    )
    row = SongRow(tr, 1, on_toggle=lambda _tid: None)
    preview = build_preview_row(tr, 1)
    assert not hasattr(row, "_reveal_wrap")
    assert not hasattr(row, "_run_ascii_reveal")
    assert not hasattr(preview, "_reveal_wrap")

    sk_stop = SkeletonRow(0, reduced_motion=False)
    task = asyncio.create_task(sk_stop.start_pulse())
    await asyncio.sleep(0.05)
    sk_stop.stop_pulse()
    await asyncio.gather(task, return_exceptions=True)
    assert not sk_stop._reveal_wrap.visible
    assert sk_stop._reveal_text.value == ""
    assert sk_stop.scale == 1.0

    from unittest.mock import MagicMock
    from ui.components.widgets import (
        close_dialog,
        notify,
    )
    from ui.components.telemetry import TelemetryDrawer

    dummy_page = MagicMock()
    dummy_page.overlay = []
    dummy_page.height = 900
    dlg = MagicMock()
    dlg.page = dummy_page
    dlg.open = True
    close_dialog(dummy_page, dlg)
    assert dlg.open is False
    dlg.update.assert_called_once()
    dummy_page.update.assert_not_called()

    notify(dummy_page, "Guardado")
    dummy_page.show_dialog.assert_called_once()
    dummy_page.update.assert_not_called()

    td_page = MagicMock()
    td_page.height = 900
    td = TelemetryDrawer(td_page)
    td.container = MagicMock()
    td.container.page = td_page
    td._d_tab_btns[2].on_click(None)
    td.container.update.assert_called_once()
    td_page.update.assert_not_called()

asyncio.run(_run())
"""
        )
