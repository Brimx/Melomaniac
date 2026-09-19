"""Tipografía adaptativa para los metadatos de canciones.

La selección se basa en bloques Unicode, no en traducción ni en detección
heurística de idioma. Esto permite mantener la personalidad de IBM Plex Sans
para el catálogo occidental y cambiar únicamente las etiquetas que necesitan
glifos cirílicos o CJK.
"""

from __future__ import annotations

import os
from pathlib import Path


FONT = "IBM Plex Sans"
FONT_LIGHT = "IBM Plex Sans Light"
FONT_MEDIUM = "IBM Plex Sans Medium"
FONT_SEMI = "IBM Plex Sans SemiBold"
FONT_BOLD = "IBM Plex Sans Bold"

# Alias estable para el set CJK. Su ruta se resuelve al iniciar la aplicación
# porque la fuente puede venir empaquetada o estar instalada en el sistema.
FONT_CJK = "Melomaniac CJK"

_CJK_RANGES = ((0x3040, 0x309F), (0x30A0, 0x30FF), (0x4E00, 0x9FFF))
_CYRILLIC_RANGE = (0x0400, 0x04FF)
_CJK_REGISTERED = False

_WEIGHT_FONTS = {
    "light": FONT_LIGHT,
    "regular": FONT,
    "medium": FONT_MEDIUM,
    "semibold": FONT_SEMI,
    "bold": FONT_BOLD,
}


def _contains_range(text: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(
        start <= ord(character) <= end
        for character in text
        for start, end in ranges
    )


def classify_script(text: str | None) -> str:
    """Clasifica texto por el alfabeto más restrictivo detectado.

    CJK tiene prioridad sobre cirílico para que un título mixto reciba el
    set capaz de representar todos sus glifos. El resto usa la fuente latina.
    """
    value = text or ""
    if _contains_range(value, _CJK_RANGES):
        return "cjk"
    if _contains_range(value, (_CYRILLIC_RANGE,)):
        return "cyrillic"
    return "latin"


def font_family_for(text: str | None, weight: str = "regular") -> str:
    """Devuelve el alias Flet adecuado para un control de texto.

    El set CJK solo tiene que registrarse una vez; si el sistema no ofrece
    una fuente CJK local se conserva el alias latino para que la aplicación
    siga iniciando y el motor de texto pueda aplicar su fallback nativo.
    """
    if classify_script(text) == "cjk" and _CJK_REGISTERED:
        return FONT_CJK
    return _WEIGHT_FONTS.get(weight.lower(), FONT)


def _find_cjk_font(fonts_dir: Path) -> Path | None:
    """Encuentra una fuente CJK empaquetada o instalada localmente."""
    bundled = sorted(fonts_dir.glob("NotoSansCJK*.ttc"))
    candidates = [
        *bundled,
        Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates.extend(
        [
            windows_dir / "NotoSansCJK-Regular.ttc",
            windows_dir / "msgothic.ttc",
            windows_dir / "simsun.ttc",
        ]
    )
    return next((path for path in candidates if path.is_file()), None)


def build_font_registry(fonts_dir: Path) -> dict[str, str]:
    """Construye el registro de fuentes de ``page.fonts`` y prepara CJK."""
    global _CJK_REGISTERED

    registry = {
        FONT: str(fonts_dir / "IBMPlexSans_w400.ttf"),
        FONT_LIGHT: str(fonts_dir / "IBMPlexSans_w300.ttf"),
        FONT_MEDIUM: str(fonts_dir / "IBMPlexSans_w500.ttf"),
        FONT_SEMI: str(fonts_dir / "IBMPlexSans_w600.ttf"),
        FONT_BOLD: str(fonts_dir / "IBMPlexSans_w700.ttf"),
    }
    cjk_path = _find_cjk_font(fonts_dir)
    if cjk_path is None:
        _CJK_REGISTERED = False
    else:
        registry[FONT_CJK] = str(cjk_path)
        _CJK_REGISTERED = True
    return registry
