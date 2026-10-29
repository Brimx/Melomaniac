"""Normalización y validación de imágenes de portada.

Las portadas llegan de fuentes heterogéneas: artwork de plataforma (JPEG,
a veces PNG), miniaturas de YouTube (WebP, casi siempre 16:9) y archivos
locales. ``write_audio_metadata`` declara siempre el mime ``image/jpeg``, así
que todo lo que llegue a los tags debe ser JPEG real.

Regla de encuadre: una imagen cuadrada se usa tal cual; una no cuadrada se
rellena hasta el cuadrado con un fondo neutro, nunca se recorta. Recortar un
16:9 mutila el encuadre y convierte una miniatura de vídeo en una portada
peor que no tener ninguna.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

# Medidas de salida. 800 px cubre con holgura una fila de 55 px y una tarjeta
# de 160 px sin inflar el archivo.
COVER_TARGET_PX = 800
COVER_MIN_PX = 500
COVER_JPEG_QUALITY = 85

# Fondo neutro para el relleno; coincide con BG_SURFACE de ui/tokens, pero
# engine no puede importar ui (dirección de dependencias), así que se declara
# aquí como valor de imagen, no como token de UI.
_FILL_RGB = (17, 17, 24)


@dataclass(frozen=True)
class CoverImage:
    """Resultado de analizar una imagen de portada."""

    width: int
    height: int

    @property
    def is_square(self) -> bool:
        return self.width > 0 and self.width == self.height

    @property
    def longest_side(self) -> int:
        return max(self.width, self.height)


def probe_cover(data: bytes) -> CoverImage | None:
    """Devuelve las dimensiones de ``data`` o ``None`` si no es una imagen."""
    if not data:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return CoverImage(img.width, img.height)
    except Exception:
        return None


def normalize_cover(
    data: bytes,
    *,
    target_px: int = COVER_TARGET_PX,
    quality: int = COVER_JPEG_QUALITY,
) -> bytes:
    """Convierte cualquier imagen a JPEG cuadrado de ``target_px``.

    Una imagen 1:1 se escala; cualquier otra se rellena con ``_FILL_RGB``
    centrada. Devuelve ``b""`` si la entrada no es una imagen legible o si
    Pillow no está disponible.
    """
    if not data:
        return b""
    try:
        from PIL import Image
    except ImportError:
        return b""

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            # El alpha de PNG/WebP se compone sobre el fondo neutro; JPEG no
            # admite transparencia.
            if img.mode in ("RGBA", "LA", "P"):
                converted = img.convert("RGBA")
                flat = Image.new("RGB", converted.size, _FILL_RGB)
                flat.paste(converted, mask=converted.split()[-1])
                img = flat
            elif img.mode != "RGB":
                img = img.convert("RGB")

            side = max(1, int(target_px))
            if img.width == img.height:
                square = img.resize((side, side), Image.LANCZOS)
            else:
                scale = side / max(img.width, img.height)
                new_size = (
                    max(1, round(img.width * scale)),
                    max(1, round(img.height * scale)),
                )
                resized = img.resize(new_size, Image.LANCZOS)
                square = Image.new("RGB", (side, side), _FILL_RGB)
                square.paste(
                    resized,
                    ((side - new_size[0]) // 2, (side - new_size[1]) // 2),
                )

            out = io.BytesIO()
            square.save(out, format="JPEG", quality=int(quality), optimize=True)
            return out.getvalue()
    except Exception:
        return b""


def is_acceptable_cover(candidate: CoverImage | None, *, min_px: int = COVER_MIN_PX) -> bool:
    """Filtro anti-portada-errónea: cuadrada y con suficiente resolución."""
    if candidate is None:
        return False
    return candidate.is_square and candidate.longest_side >= min_px


# Sufijo de tamaño que usan los CDN de Google (`=w120-h120-l90-rj`, `=s120`).
_GOOGLE_SIZE_SUFFIX_RE = re.compile(r"=[swh]\d+[^/]*$")
_GOOGLE_CDN_HOSTS = ("googleusercontent.com", "ggpht.com")


def upscale_cover_url(url: str, px: int = 1000) -> str:
    """Sube la resolución de una URL de portada que declara su tamaño.

    YouTube Music entrega ``...=w120-h120-l90-rj``: es artwork 1:1 real, pero a
    120 px, por debajo del mínimo aceptable. El CDN respeta ``s{px}`` y
    devuelve la misma imagen a 1000 px sin coste de API. Aplicarlo primero
    evita casi todas las consultas a iTunes/Deezer.
    """
    if not url or not any(host in url for host in _GOOGLE_CDN_HOSTS):
        return url
    return _GOOGLE_SIZE_SUFFIX_RE.sub(f"=s{px}", url)
