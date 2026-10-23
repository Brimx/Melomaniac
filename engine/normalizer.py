"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.6.0                               ║
║              Motor de Normalización de Metadatos                     ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: engine/normalizer.py
Descripción: Motor de normalización y limpieza de metadatos de canciones.
            Implementa estrategias de preprocesamiento para maximizar la
            precisión del matching fuzzy entre plataformas.

Estrategia de Diseño:
    El normalizer actúa como primera línea de defensa contra la variabilidad
    de metadatos entre plataformas. Implementa múltiples capas de limpieza:
    
    1. Normalización Unicode (NFC): Unifica representaciones de caracteres
       para garantizar comparaciones consistentes entre plataformas.
    
    2. Eliminación de ruido catalogado: Remueve sufijos comunes que no
       aportan valor semántico (Remastered, Official Video, Live, etc).
    
    3. Purga de paréntesis/corchetes: Elimina información parentética que
       frecuentemente difiere entre plataformas (versiones, ediciones).
    
    4. Detección de contenido letal: Identifica palabras que invalidan
       matches (cover, karaoke, tribute) para prevenir falsos positivos.
    
    5. Normalización de espacios: Colapsa espacios múltiples y elimina
       whitespace redundante.

Umbrales de Confianza:
    - FUZZY_IDEAL (85%): Match de alta confianza, aceptado automáticamente
    - FUZZY_LOG_BAND_LOW (70%): Match válido pero registrado para análisis
    - FUZZY_REVISION_THRESHOLD (40%): Requiere revisión manual
    - ARTIST_EXACT_MIN (99%): Umbral para considerar artista exacto

Autor: Melomaniac Team
Versión: 4.6.0
Fecha: 2026
"""

from __future__ import annotations

import re
import unicodedata

# ══════════════════════════════════════════════════════════════════════
# EXPRESIONES REGULARES COMPILADAS
# ══════════════════════════════════════════════════════════════════════
# Precompiladas para máximo rendimiento en operaciones repetitivas.

# Detecta y remueve sufijos de ruido común en títulos de canciones
_NOISE_RE = re.compile(
    r'\s*[\(\[]\s*(?:'
    r'remaster(?:ed)?(?:\s+\d{4})?'
    r'|official\s+(?:video|audio|lyric\s+video|music\s+video|visualizer)'
    r'|explicit'
    r'|single'
    r'|hd|hq|4k'
    r'|stereo|mono'
    r'|radio\s+edit'
    r'|bonus\s+track'
    r'|live(?:\s+(?:at|from|version)\b[^)\]]*)?'
    r')\s*[\)\]]',
    re.IGNORECASE,
)

# Limpia paréntesis/corchetes y colaboraciones (feat./ft.)
_CLEAN_RE = re.compile(
    r'\s*[\(\[].*?[\)\]]|\s+feat\.?\s.*|\s+ft\.?\s.*',
    re.IGNORECASE,
)

# Purga agresiva de todo contenido entre paréntesis/corchetes
_PURGE_BRACKETS_RE = re.compile(r'\([^)]*\)|\[[^\]]*\]')

# Remueve palabras de ruido catalogadas
_PURGE_NOISE_WORDS = re.compile(
    r'\b(?:official\s+video|remaster(?:ed)?|live|deluxe|video\s+edit|feat\.?|ft\.?)\b',
    re.IGNORECASE,
)

# Detector de caracteres CJK (Chino, Japonés, Coreano) y Hangul
# Usado para aplicar estrategias de normalización específicas para idiomas asiáticos
_ASIAN_RE = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\u3130-\u318f]')

# Detector de alfabeto cirílico para generar variantes latinas de búsqueda
_CYRILLIC_RE = re.compile(r'[\u0400-\u04FF]')

# Transliteración cirílica → latina (ruso, ucraniano, bielorruso y serbio).
# Se usa solo como variante de búsqueda: el original se conserva primero.
_CYRILLIC_TRANSLIT = str.maketrans({
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e',
    'ё': 'yo', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k',
    'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
    'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '',
    'э': 'e', 'ю': 'yu', 'я': 'ya',
    'і': 'i', 'ї': 'yi', 'є': 'ye', 'ґ': 'g', 'ў': 'u',
    'ђ': 'dj', 'ј': 'j', 'љ': 'lj', 'њ': 'nj', 'ћ': 'c', 'џ': 'dz',
    'ѕ': 'dz', 'ѐ': 'e', 'ѝ': 'i',
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E',
    'Ё': 'Yo', 'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K',
    'Л': 'L', 'М': 'M', 'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R',
    'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F', 'Х': 'Kh', 'Ц': 'Ts',
    'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch', 'Ъ': '', 'Ы': 'Y', 'Ь': '',
    'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya',
    'І': 'I', 'Ї': 'Yi', 'Є': 'Ye', 'Ґ': 'G', 'Ў': 'U',
    'Ђ': 'Dj', 'Ј': 'J', 'Љ': 'Lj', 'Њ': 'Nj', 'Ћ': 'C', 'Џ': 'Dz',
    'Ѕ': 'Dz', 'Ѐ': 'E', 'Ѝ': 'I',
})

# Palabras que invalidan incondicionalmente un resultado de búsqueda
# Previenen falsos positivos con covers, karaokes y parodias
_LETHAL_WORDS: frozenset[str] = frozenset({'cover', 'karaoke', 'tribute', 'parody'})

# ══════════════════════════════════════════════════════════════════════
# UMBRALES DE CONFIANZA FUZZY
# ══════════════════════════════════════════════════════════════════════
# Definen los niveles de confianza para el matching fuzzy entre metadatos.

FUZZY_IDEAL = 85                            # Match de alta confianza (≥85%)
FUZZY_LOG_BAND_LOW = 70                     # Match válido pero registrado (70-84%)
FUZZY_REVISION_THRESHOLD = 40               # Requiere revisión manual (<40%)
FUZZY_TITLE_IDEAL_WHEN_ARTIST_EXACT = 60   # Umbral reducido si artista es exacto
ARTIST_EXACT_MIN = 99                       # Umbral para artista exacto (≥99%)
ARTIST_PERFECT = 100                        # Artista perfectamente idéntico


def normalize_isrc(value: object) -> str | None:
    """Normaliza un ISRC para compararlo entre proveedores.

    Apple y Spotify pueden devolver el código con o sin guiones. Se conserva
    una representación compacta en mayúsculas para caché y búsquedas exactas.
    Valores que no tienen la longitud de un ISRC se descartan.
    """
    if value is None:
        return None
    compact = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    return compact if len(compact) == 12 else None


def _normalize_title(text: str) -> str:
    """
    Normaliza un título de canción para comparación fuzzy.
    
    Aplica una secuencia de transformaciones para extraer el núcleo semántico:
    1. Normalización Unicode NFC (Canonical Decomposition + Composition)
    2. Eliminación de paréntesis/corchetes y colaboraciones (feat./ft.)
    3. Eliminación de guiones finales
    4. Conversión a minúsculas
    5. Colapso de espacios múltiples
    
    Args:
        text: Título original de la canción.
    
    Returns:
        Título normalizado en minúsculas sin ruido.
    
    Example:
        >>> _normalize_title("Bohemian Rhapsody (Remastered 2011)")
        "bohemian rhapsody"
        >>> _normalize_title("Shape of You [Official Video]")
        "shape of you"
    
    Note:
        La normalización NFC es crítica para idiomas con diacríticos
        (español, francés, portugués) donde un mismo carácter puede
        tener múltiples representaciones Unicode.
    """
    text = unicodedata.normalize('NFC', str(text))
    text = _CLEAN_RE.sub('', text)
    text = re.sub(r'\s*[-–]\s*$', '', text)
    return ' '.join(text.split()).strip().lower()


def _strip_noise(text: str) -> str:
    """
    Remueve sufijos de ruido de títulos remotos preservando el original si queda vacío.
    
    Aplica regex de ruido catalogado (_NOISE_RE) para eliminar sufijos comunes
    que no aportan valor semántico. Si la limpieza elimina todo el contenido,
    retorna el texto original para evitar pérdida total de información.
    
    Args:
        text: Texto a limpiar.
    
    Returns:
        Texto sin sufijos de ruido, o texto original si quedaría vacío.
    
    Example:
        >>> _strip_noise("Imagine (Remastered 2010)")
        "Imagine"
        >>> _strip_noise("(Remastered)")  # Quedaría vacío
        "(Remastered)"  # Retorna original
    
    Note:
        Esta función es defensiva: prefiere retener ruido antes que
        perder completamente el contenido del campo.
    """
    cleaned = _NOISE_RE.sub('', text).strip()
    return cleaned if cleaned else text.strip()


def clean_metadata(title: str, artist: str) -> tuple[str, str]:
    """
    Extrae el núcleo semántico de título y artista eliminando ruido catalogado.
    
    Implementa limpieza agresiva de metadatos aplicando múltiples estrategias:
    1. Normalización Unicode NFC
    2. Purga de paréntesis y corchetes completos
    3. Eliminación de palabras de ruido catalogadas
    4. Eliminación de sufijos de ruido específicos
    5. Colapso de espacios múltiples
    6. Fallback al original si la limpieza elimina todo
    
    Args:
        title: Título original de la canción.
        artist: Nombre original del artista.
    
    Returns:
        Tupla (título_limpio, artista_limpio) con núcleo semántico.
    
    Example:
        >>> clean_metadata("Stairway to Heaven (Remastered)", "Led Zeppelin")
        ("Stairway to Heaven", "Led Zeppelin")
        >>> clean_metadata("Blinding Lights [Official Video]", "The Weeknd (feat. Chromatics)")
        ("Blinding Lights", "The Weeknd")
    
    Note:
        Esta función es el corazón del sistema de normalización. Su objetivo
        es maximizar la probabilidad de match fuzzy exitoso eliminando
        variaciones superficiales que difieren entre plataformas pero no
        afectan la identidad fundamental de la canción.
    """
    # Normalización Unicode inicial
    t = unicodedata.normalize('NFC', str(title).strip())
    a = unicodedata.normalize('NFC', str(artist).strip())
    
    # Purga agresiva de paréntesis y corchetes
    t = _PURGE_BRACKETS_RE.sub('', t)
    a = _PURGE_BRACKETS_RE.sub('', a)
    
    # Eliminación de palabras de ruido catalogadas
    t = _PURGE_NOISE_WORDS.sub('', t)
    a = _PURGE_NOISE_WORDS.sub('', a)
    
    # Eliminación de sufijos de ruido específicos
    t = _strip_noise(t) if t else t
    a = _strip_noise(a) if a else a
    
    # Colapso de espacios múltiples
    t = ' '.join(t.split()).strip()
    a = ' '.join(a.split()).strip()
    
    # Fallback al original si la limpieza eliminó todo
    if not t:
        t = str(title).strip()
    if not a:
        a = str(artist).strip()
    
    return t, a


def has_cyrillic(text: str) -> bool:
    """Indica si ``text`` contiene al menos un carácter cirílico."""
    return bool(_CYRILLIC_RE.search(str(text or "")))


def transliterate_cyrillic(text: str) -> str:
    """
    Translitera caracteres cirílicos a latino preservando el resto.

    No reemplaza el original: se usa como variante adicional cuando el
    catálogo de destino indexa la obra en alfabeto latino.
    """
    return str(text or "").translate(_CYRILLIC_TRANSLIT)


def build_search_variants(title: str, artist: str) -> list[tuple[str, str]]:
    """
    Genera variantes de búsqueda en orden de preferencia, sin duplicados.

    Orden:
    1. Título/artista limpios (núcleo semántico).
    2. Original tal cual llega de la fuente.
    3. Título normalizado + artista limpio (comportamiento previo).
    4. Transliteración latina si hay cirílico.
    5. Solo título limpio como último recurso.

    El llamador decide cuándo detenerse según la calidad del resultado.
    """
    base_t, base_a = clean_metadata(title, artist)
    original_t = str(title or "").strip()
    original_a = str(artist or "").strip()
    candidates: list[tuple[str, str]] = [
        (base_t, base_a),
        (original_t, original_a),
        (_normalize_title(original_t), base_a),
    ]
    if has_cyrillic(f"{original_t}{original_a}"):
        candidates.append((
            transliterate_cyrillic(base_t),
            transliterate_cyrillic(base_a),
        ))
        candidates.append((
            transliterate_cyrillic(original_t),
            transliterate_cyrillic(base_a),
        ))
    candidates.append((base_t, ""))

    variants: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for t_pass, a_pass in candidates:
        t_pass = ' '.join(str(t_pass or "").split())
        a_pass = ' '.join(str(a_pass or "").split())
        if not t_pass:
            continue
        key = (t_pass.casefold(), a_pass.casefold())
        if key in seen:
            continue
        seen.add(key)
        variants.append((t_pass, a_pass))
    return variants


def build_search_query(title: str, artist: str) -> str:
    """
    Construye query de búsqueda optimizado siguiendo REGLA 2 (Prioridad de Obra).
    
    Implementa la estrategia de búsqueda universal que prioriza el nombre de
    la canción seguido del artista. Este orden maximiza la precisión en
    plataformas con algoritmos de búsqueda que pesan más los primeros términos.
    
    Formato: "[Nombre de la Canción] [Artista]"
    
    Args:
        title: Título de la canción (puede estar vacío).
        artist: Nombre del artista (puede estar vacío).
    
    Returns:
        Query de búsqueda optimizado. Si solo uno está presente, retorna ese.
        Si ambos están vacíos, retorna string vacío.
    
    Example:
        >>> build_search_query("Bohemian Rhapsody", "Queen")
        "Bohemian Rhapsody Queen"
        >>> build_search_query("", "Queen")
        "Queen"
        >>> build_search_query("Bohemian Rhapsody", "")
        "Bohemian Rhapsody"
    
    Note:
        La REGLA 2 (Prioridad de Obra) es resultado de análisis empírico
        que demostró que este orden produce mejores resultados en las tres
        plataformas soportadas (YouTube Music, Apple Music, etc.) comparado
        con el orden inverso o queries más complejas.
    """
    t = title.strip() if title else ""
    a = artist.strip() if artist else ""
    if t and a:
        return f"{t} {a}"
    return t or a
