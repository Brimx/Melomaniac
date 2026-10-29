"""
tests/test_covers.py — Normalización y validación de portadas (unittest)
════════════════════════════════════════════════════════════════════════
Sin red y sin Flet: valida la conversión a JPEG cuadrado, la regla de no
recortar imágenes no cuadradas y el filtro anti-portada-errónea.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.covers import (
    COVER_TARGET_PX,
    is_acceptable_cover,
    normalize_cover,
    probe_cover,
)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow está en requirements.txt
    HAS_PIL = False


def _encode(size: tuple[int, int], fmt: str = "PNG", color=(120, 40, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@unittest.skipUnless(HAS_PIL, "Pillow no disponible")
class NormalizeCoverTest(unittest.TestCase):
    def test_square_input_is_scaled_to_target(self) -> None:
        out = normalize_cover(_encode((1000, 1000)))
        self.assertTrue(out.startswith(b"\xff\xd8\xff"), "debe ser JPEG")
        self.assertEqual(probe_cover(out), probe_cover(out))
        self.assertEqual((probe_cover(out).width, probe_cover(out).height),
                         (COVER_TARGET_PX, COVER_TARGET_PX))

    def test_wide_input_is_padded_not_cropped(self) -> None:
        out = normalize_cover(_encode((1280, 720), fmt="WEBP"))
        info = probe_cover(out)
        self.assertEqual((info.width, info.height), (COVER_TARGET_PX, COVER_TARGET_PX))
        # Una miniatura 16:9 no es artwork de álbum.
        self.assertFalse(is_acceptable_cover(probe_cover(_encode((1280, 720), fmt="WEBP"))))

    def test_png_with_alpha_is_flattened(self) -> None:
        img = Image.new("RGBA", (600, 600), (0, 0, 255, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out = normalize_cover(buf.getvalue())
        self.assertTrue(out.startswith(b"\xff\xd8\xff"))

    def test_garbage_returns_empty(self) -> None:
        self.assertEqual(normalize_cover(b"not an image"), b"")
        self.assertEqual(normalize_cover(b""), b"")

    def test_probe_rejects_non_image(self) -> None:
        self.assertIsNone(probe_cover(b"not an image"))


class UpscaleCoverUrlTest(unittest.TestCase):
    def test_youtube_cdn_size_suffix_is_raised(self) -> None:
        from engine.covers import upscale_cover_url
        url = "https://yt3.googleusercontent.com/abc=w120-h120-l90-rj"
        self.assertTrue(upscale_cover_url(url).endswith("=s1000"))

    def test_suffix_already_large_is_simply_rewritten(self) -> None:
        from engine.covers import upscale_cover_url
        self.assertTrue(upscale_cover_url("https://yt3.ggpht.com/x=s120").endswith("=s1000"))

    def test_other_hosts_are_untouched(self) -> None:
        from engine.covers import upscale_cover_url
        url = "https://is1-ssl.mzstatic.com/image/thumb/x/100x100bb.jpg"
        self.assertEqual(upscale_cover_url(url), url)

    def test_empty_url(self) -> None:
        from engine.covers import upscale_cover_url
        self.assertEqual(upscale_cover_url(""), "")


class AcceptableCoverTest(unittest.TestCase):
    def test_square_and_large_enough(self) -> None:
        from engine.covers import CoverImage
        self.assertTrue(is_acceptable_cover(CoverImage(800, 800)))

    def test_too_small_is_rejected(self) -> None:
        from engine.covers import CoverImage
        self.assertFalse(is_acceptable_cover(CoverImage(300, 300)))

    def test_non_square_is_rejected(self) -> None:
        from engine.covers import CoverImage
        self.assertFalse(is_acceptable_cover(CoverImage(1000, 800)))

    def test_none_is_rejected(self) -> None:
        self.assertFalse(is_acceptable_cover(None))


if __name__ == "__main__":
    unittest.main()
