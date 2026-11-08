"""Normalización y validación de imágenes de portada.

Las portadas llegan de fuentes heterogéneas: artwork de plataforma (JPEG,
a veces PNG), miniaturas de YouTube (WebP, casi siempre 16:9) y archivos
locales. ``write_audio_metadata`` declara siempre el mime ``image/jpeg``, así
que todo lo que llegue a los tags debe ser JPEG real.

Regla de encuadre: una imagen cuadrada se usa tal cual. Una no cuadrada se
rellena hasta el cuadrado con un fondo neutro, salvo que el borde sea
*letterbox/pillarbox* (barras negras o de un color uniforme), en cuyo caso se
recorta solo esa barra. Recortar un 16:9 completo mutila el encuadre y
convierte una miniatura de vídeo en una portada peor que no tener ninguna; por
eso el recorte se limita a las barras y nunca se aplica a una imagen ya
cuadrada.
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
            # El trim va antes de decidir: un 1:1 con barras dentro sigue
            # siendo 1:1 y, sin esto, se guardaría con los márgenes puestos.
            img = _trim_uniform_bars(img)
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


# Tolerancia por canal para considerar un píxel «igual que el borde». Una
# barra de letterbox real es plana; el ruido de compresión de un JPEG rara vez
# pasa de unos pocos niveles.
_BAR_TOLERANCE = 12
# Fracción máxima del lado que se acepta como barra. Un recorte mayor ya no es
# letterbox: es la propia imagen, y mutilarla es peor que rellenar.
_BAR_MAX_FRACTION = 0.30


def _row_is_bar(img, y: int, ref: tuple[int, int, int], step: int) -> bool:
    for x in range(0, img.width, step):
        px = img.getpixel((x, y))
        if isinstance(px, int):  # imágenes L/P escalarizadas
            px = (px, px, px)
        if any(abs(px[i] - ref[i]) > _BAR_TOLERANCE for i in range(3)):
            return False
    return True


def _col_is_bar(img, x: int, ref: tuple[int, int, int], step: int) -> bool:
    for y in range(0, img.height, step):
        px = img.getpixel((x, y))
        if isinstance(px, int):
            px = (px, px, px)
        if any(abs(px[i] - ref[i]) > _BAR_TOLERANCE for i in range(3)):
            return False
    return True


def _edge_rgb(img) -> tuple[int, int, int]:
    px = img.getpixel((0, 0))
    if isinstance(px, int):
        px = (px, px, px)
    return (px[0], px[1], px[2])


def _is_uniform(img) -> bool:
    """``True`` si toda la imagen es un solo color.

    Sin esta guarda, una portada oscura uniforme o un color plano se
    recortarían un 30 % por lado sin motivo: no hay barras que quitar.
    """
    ref = _edge_rgb(img)
    step = max(1, min(img.width, img.height) // 32)
    for y in range(0, img.height, step):
        if not _row_is_bar(img, y, ref, step):
            return False
    return True


def _trim_uniform_bars(img):
    """Recorta barras letterbox/pillarbox de un borde uniforme.

    Detecta filas y columnas del borde cuyo contenido coincide con el color de
    la esquina y las elimina, hasta ``_BAR_MAX_FRACTION`` del lado. Si el
    recorte deja una imagen no cuadrada, el llamador la rellena como antes, así
    que esta función solo puede mejorar el encuadre, nunca empeorarlo.
    """
    if _is_uniform(img):
        return img
    ref = _edge_rgb(img)
    step = max(1, min(img.width, img.height) // 64)
    top = bottom = left = right = 0
    # El tope se mide sobre el eje de cada barra: una miniatura 16:9 con un
    # artwork cuadrado centrado tiene barras laterales del 22 %, así que un
    # límite común sobre el lado menor las dejaría a medio quitar.
    max_vertical = int(img.height * _BAR_MAX_FRACTION)
    max_horizontal = int(img.width * _BAR_MAX_FRACTION)

    while top < max_vertical and _row_is_bar(img, top, ref, step):
        top += 1
    while bottom < max_vertical and _row_is_bar(img, img.height - 1 - bottom, ref, step):
        bottom += 1
    while left < max_horizontal and _col_is_bar(img, left, ref, step):
        left += 1
    while right < max_horizontal and _col_is_bar(img, img.width - 1 - right, ref, step):
        right += 1

    if not any((top, bottom, left, right)):
        return img
    box = (left, top, img.width - right, img.height - bottom)
    if box[2] <= 0 or box[3] <= 0:
        return img
    return img.crop(box)


# Sufijo de tamaño que usan los CDN de Google (`=w120-h120-l90-rj`, `=s120`).
_GOOGLE_SIZE_SUFFIX_RE = re.compile(r"=[swh]\d+[^/]*$")
_GOOGLE_CDN_HOSTS = ("googleusercontent.com", "ggpht.com")

# Miniaturas de vídeo de YouTube. Son fotogramas 16:9 del vídeo, no artwork
# del álbum, así que nunca deben ganarle a una carátula real. ``i.ytimg.com``
# no está en ``_GOOGLE_CDN_HOSTS`` porque esas URL no aceptan sufijo de tamaño.
_VIDEO_THUMB_HOSTS = ("i.ytimg.com", "img.youtube.com")
_VIDEO_THUMB_PATH_RE = re.compile(r"/(hqdefault|mqdefault|sddefault|maxresdefault|hq720)\.(jpg|webp)$")


def is_video_thumbnail_url(url: str) -> bool:
    """``True`` si ``url`` es una miniatura de vídeo de YouTube."""
    if not url:
        return False
    lowered = url.lower()
    if any(host in lowered for host in _VIDEO_THUMB_HOSTS):
        return True
    return bool(_VIDEO_THUMB_PATH_RE.search(lowered))


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
