import inspect
import unittest
from unittest import mock

from core import download_flow
from services import settings_store


class DownloadLyricsOptions(unittest.TestCase):
    def _opts(self, lrc, embed):
        with mock.patch.object(settings_store, "get_lyrics_save_lrc", return_value=lrc), \
             mock.patch.object(settings_store, "get_lyrics_embed_tags", return_value=embed):
            return download_flow.build_lyrics_download_options()

    def test_disabled_gives_no_fetcher(self):
        self.assertEqual(self._opts(False, False), {})

    def test_lrc_or_embed_wires_fetcher(self):
        for lrc, embed in ((True, False), (False, True), (True, True)):
            o = self._opts(lrc, embed)
            self.assertTrue(callable(o["lyrics_fetcher"]))
            self.assertEqual((o["save_lrc"], o["embed_lyrics"]), (lrc, embed))

    def test_download_audio_call_receives_options(self):
        self.assertIn("**lyrics_opts", inspect.getsource(download_flow))


if __name__ == "__main__":
    unittest.main()
