"""Registro y selección tipográfica de Melomaniac.

La aplicación usa familias con responsabilidades claras:

* Stack Sans Notch: identidad y marca.
* Stack Sans Headline: titulares, labels y encabezados.
* Stack Sans Text: texto general en escritura latina.
* IBM Plex Sans: escritura cirílica.
* IBM Plex Mono: consola, logs y datos técnicos.
* CJK: fallback registrado cuando existe una fuente compatible.
"""

from __future__ import annotations

import os
from pathlib import Path


# Aliases usados por Flet. Cada uno se registra contra un archivo estático.
FONT_NOTCH = "Stack Sans Notch"
FONT_HEADLINE = "Stack Sans Headline"
FONT_TEXT = "Stack Sans Text"
FONT_TEXT_LIGHT = "Stack Sans Text Light"
FONT_TEXT_MEDIUM = "Stack Sans Text Medium"
FONT_TEXT_SEMI = "Stack Sans Text SemiBold"
FONT_TEXT_BOLD = "Stack Sans Text Bold"

FONT_HEADLINE_LIGHT = "Stack Sans Headline Light"
FONT_HEADLINE_MEDIUM = "Stack Sans Headline Medium"
FONT_HEADLINE_SEMI = "Stack Sans Headline SemiBold"
FONT_HEADLINE_BOLD = "Stack Sans Headline Bold"

FONT_IBM = "IBM Plex Sans"
FONT_MONO = "IBM Plex Mono"
FONT_MONO_LIGHT = "IBM Plex Mono Light"

# Compatibilidad con los nombres internos que ya importan algunos widgets.
FONT = FONT_TEXT
FONT_LIGHT = FONT_TEXT_LIGHT
FONT_MEDIUM = FONT_TEXT_MEDIUM
FONT_SEMI = FONT_TEXT_SEMI
FONT_BOLD = FONT_TEXT_BOLD

FONT_CJK = "Melomaniac CJK"

_WEIGHT_LABELS = {
    "light": "Light",
    "regular": "Regular",
    "medium": "Medium",
    "semibold": "SemiBold",
    "bold": "Bold",
}
_SUPPORTED_WEIGHTS = ("light", "regular", "medium", "semibold", "bold")

_CJK_RANGES = ((0x3040, 0x309F), (0x30A0, 0x30FF), (0x4E00, 0x9FFF))
_CYRILLIC_RANGE = (0x0400, 0x04FF)
_CJK_REGISTERED = False


def _contains_range(text: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(
        start <= ord(character) <= end
        for character in text
        for start, end in ranges
    )


def classify_script(text: str | None) -> str:
    """Clasifica texto por el alfabeto más restrictivo detectado."""
    value = text or ""
    if _contains_range(value, _CJK_RANGES):
        return "cjk"
    if _contains_range(value, (_CYRILLIC_RANGE,)):
        return "cyrillic"
    return "latin"


def _family_alias(family: str, weight: str) -> str:
    label = _WEIGHT_LABELS.get(weight.lower(), "Regular")
    return family if label == "Regular" else f"{family} {label}"


def font_family_for(text: str | None, weight: str = "regular") -> str:
    """Selecciona Stack Text, IBM Plex Sans o CJK según la escritura."""
    script = classify_script(text)
    if script == "cjk" and _CJK_REGISTERED:
        return FONT_CJK
    if script == "cyrillic":
        return _family_alias(FONT_IBM, weight)
    return _family_alias(FONT_TEXT, weight)


def headline_family_for(text: str | None, weight: str = "regular") -> str:
    """Selecciona Headline para UI y conserva cobertura no latina."""
    script = classify_script(text)
    if script == "cjk" and _CJK_REGISTERED:
        return FONT_CJK
    if script == "cyrillic":
        return _family_alias(FONT_IBM, weight)
    return _family_alias(FONT_HEADLINE, weight)


def brand_family(weight: str = "regular") -> str:
    """Alias para la tipografía distintiva de Melomaniac."""
    return _family_alias(FONT_NOTCH, weight)


def mono_family(weight: str = "regular") -> str:
    """Alias mono permitido: Light 300 o Regular 400, sin itálicas."""
    return FONT_MONO_LIGHT if weight.lower() == "light" else FONT_MONO


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
    """Construye el registro de fuentes estáticas de ``page.fonts``."""
    global _CJK_REGISTERED

    registry: dict[str, str] = {}

    def add_family(family: str, directory: str, prefix: str) -> None:
        family_dir = fonts_dir / directory
        for weight in _SUPPORTED_WEIGHTS:
            alias = _family_alias(family, weight)
            label = _WEIGHT_LABELS[weight]
            registry[alias] = str(family_dir / f"{prefix}-{label}.ttf")

    add_family(FONT_NOTCH, "Stack_Sans_Notch", "StackSansNotch")
    add_family(FONT_HEADLINE, "Stack_Sans_Headline", "StackSansHeadline")
    add_family(FONT_TEXT, "Stack_Sans_Text", "StackSansText")

    ibm_dir = fonts_dir / "IBM_Plex_Sans"
    registry.update(
        {
            FONT_IBM: str(ibm_dir / "IBMPlexSans-Regular.ttf"),
            f"{FONT_IBM} Light": str(ibm_dir / "IBMPlexSans-Light.ttf"),
            f"{FONT_IBM} Medium": str(ibm_dir / "IBMPlexSans-Medium.ttf"),
            f"{FONT_IBM} SemiBold": str(ibm_dir / "IBMPlexSans-SemiBold.ttf"),
            f"{FONT_IBM} Bold": str(ibm_dir / "IBMPlexSans-Bold.ttf"),
        }
    )

    mono_dir = fonts_dir / "IBM_Plex_Mono"
    registry[FONT_MONO] = str(mono_dir / "IBMPlexMono-Regular.ttf")
    registry[FONT_MONO_LIGHT] = str(mono_dir / "IBMPlexMono-Light.ttf")

    cjk_path = _find_cjk_font(fonts_dir)
    if cjk_path is None:
        _CJK_REGISTERED = False
    else:
        registry[FONT_CJK] = str(cjk_path)
        _CJK_REGISTERED = True
    return registry
