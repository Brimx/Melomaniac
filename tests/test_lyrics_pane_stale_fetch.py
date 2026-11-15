import types
import unittest
from unittest import mock

from ui.player import player_queue as pq


class StaleFetchRegression(unittest.TestCase):
    def test_cached_track_change_bumps_fetch_gen(self):
        pane = pq.LyricsPane.__new__(pq.LyricsPane)
        track = types.SimpleNamespace(id="2", name="n", artist="a")
        cur = types.SimpleNamespace(path="/x", track=track)
        pane.player_bar = types.SimpleNamespace(queue=types.SimpleNamespace(current=cur))
        key = pq.LyricsPane._track_key(cur)
        pane._fetch_gen = 5
        pane._loaded_key = ("otra",)
        pane._status = "synced"
        pane._cache = {key: "cached"}
        pane._actively_visible = True
        applied = []
        pane._apply_result = applied.append
        with mock.patch.object(pq, "_lyrics_enabled", return_value=True):
            pane.refresh_track()
        self.assertEqual(applied, ["cached"])
        self.assertGreater(pane._fetch_gen, 5)  # consulta previa queda obsoleta


if __name__ == "__main__":
    unittest.main()
