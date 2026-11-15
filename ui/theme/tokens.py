"""
ui/tokens.py — Melomaniac v4.9.0 — Design Tokens
═══════════════════════════════════════════════════
Fuente única de verdad para colores y tokens OLED.
Antes duplicados en app.py, ui/auth_manager.py, ui/main_ui.py,
ui/song_row.py, ui/widgets.py, ui/telemetry.py.

Importar desde aquí garantiza consistencia y evita drift.
"""

# Fondos
BG_DEEP      = "#FF000000"  # Negro absoluto
BG_PANEL     = "#FF080808"  # Paneles
BG_SURFACE   = "#FF111118"  # Superficies elevadas
BG_INPUT     = "#FF16161F"  # Inputs
BG_LIST      = "#FF161622"  # Listas
BG_HOVER     = "#FF1E1E28"  # Hover filas
SIDEBAR_BG   = "#FF0E0E15"  # Sidebar
CHIP_BG      = "#FF1A1A22"  # Chips
SKELETON_DARK = "#FF0E1016" # Skeletons

# Bordes
BORDER_LIGHT   = "#FF3D4455"
BORDER_MUTED   = "#FF2A3040"
BORDER_CONTROL = "#FF5A6478"  # >= 3:1 sobre BG_SURFACE (controles interactivos)

# Superficies y pigmentos de papel (demo «Sala de escucha»)
BG_INSET     = "#FF050608"
ON_ACCENT    = "#FF06101F"
SHADOW_INK   = "#FF224799"  # 75% ACCENT_DIM + BG_DEEP (sombra de hoja 1)
SHADOW_INK_2 = "#FF11244D"  # 50% SHADOW_INK + BG_DEEP (segunda hoja apilada)
PAPER_BG     = "#FFE9E2D0"  # Banda obi / papel hueso
HANKO        = "#FFD9443B"  # Sello hanko / bermellón
SEAL_COLOR   = HANKO  # alias heredado
DISABLED_OPACITY = 0.38

# Acento y estados
ACCENT       = "#FF4F8BFF"
ACCENT_HOVER = "#FF6BA3FF"
ACCENT_DIM   = "#FF2D5FCC"
ACCENT_HALO  = "#FF2A3F5C"
SUCCESS      = "#FF00D084"
WARNING      = "#FFFFA500"
ERROR_COL    = "#FFFF4444"

# Bordes / divisores
BORDER_ROW   = "#FF252530"

# Overlays blancos translúcidos (tabs, inputs, notas, banners)
OVERLAY_06   = "#06FFFFFF"
OVERLAY_08   = "#08FFFFFF"
OVERLAY_10   = "#0AFFFFFF"
OVERLAY_14   = "#14FFFFFF"
OVERLAY_18   = "#18FFFFFF"
WARN_BG      = "#120C0000"
WARN_BORDER  = "#30FFA500"

# Texto
TEXT_PRIMARY = "#FFF2EDE4"  # blanco cálido (§2.7)
TEXT_MUTED   = "#FF848DA1"
TEXT_DIM     = "#FF3D4455"
DECOR_LINE   = TEXT_DIM  # Alias semántico: solo adornos, nunca texto legible

# Escala de espaciado (§4.3)
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 24
SPACE_6 = 32
SPACE_7 = 48

# Escala tipográfica (§4.4; suelo funcional 11 px)
FS_META    = 11
FS_SM      = 12
FS_BODY    = 14
FS_ROW     = 16
FS_SEC     = 20
FS_PAGE    = 28
FS_DISPLAY = 40

# Radios canónicos y geometría de relieve / foco
RADIUS_SHARP  = 3
RADIUS_SM     = 6
RADIUS_INPUT  = RADIUS_SHARP  # deprecated: migrar en Ola 2
RADIUS_BUTTON = RADIUS_SHARP  # deprecated: migrar en Ola 2
RADIUS_DIALOG = RADIUS_SM  # deprecated: migrar en Ola 2
RADIUS_RAIL        = 0  # deprecated: migrar en Ola 2
RADIUS_RAIL_ITEM   = RADIUS_SHARP  # deprecated: migrar en Ola 2

PAPER_LIFT        = 3  # Desplazamiento (+x, +y) de hoja flotante estándar
PAPER_LIFT_SM     = 2  # Desplazamiento (+x, +y) de botón compacto o chip activo
PAPER_CANTO_PX    = 2  # Filete interior inferior (grosor de la hoja)
FOCUS_RING_WIDTH  = 2  # Grosor del anillo de foco (2 px)
FOCUS_RING_OFFSET = 2  # Aire exterior alrededor del elemento enfocado (2 px)
SELECTION_BAR_W   = 2  # Marca lateral izquierda de selección en filas

# Movimiento — duración por propósito (una sola fuente)
# Curva por propósito: hover/press usa EASE_OUT; el resto EASE_IN_OUT.
# Ninguna animación nueva inventa duración: reutiliza el propósito existente
# o añade uno aquí con su justificación.
MOTION_PRESS_MS    = 90     # compresión táctil al pulsar
MOTION_HOVER_MS    = 120    # hover, press, cambios de color/borde
MOTION_EXIT_MS     = 167    # salida / fundido de cierre
MOTION_ENTER_MS    = 200    # aparición de elemento, progreso, cambio de estado corto
MOTION_PANEL_MS    = 300    # paneles, rail, transiciones de vista
MOTION_REVEAL_MS   = 400    # barrido ASCII acotado al activar skeletons
MOTION_SKELETON_MS = 600    # pulso de carga
MOTION_SHIMMER_MS  = 1500   # barrido shimmer de skeleton
MOTION_AMBIENT_MS  = 24000  # deriva ambiental en escena Reproduciendo

# Medidas del rail de navegación e Inicio
RAIL_W_COLLAPSED = 68
RAIL_W_EXPANDED  = 220
RAIL_BRAND_SLOT_SZ = 32
HOME_HERO_H        = 176
HOME_CONTINUAR_ART = 132
HOME_CONN_ROW_H    = 36
RAIL_MARGIN      = 12
RAIL_PAD_H       = 12
RAIL_PAD_V       = 12
RAIL_ITEM_SPACING = 6
RAIL_ITEM_PAD_H   = 10
RAIL_ITEM_PAD_V   = 10
RAIL_ITEM_H       = 40
RAIL_HEADER_H     = 40
RAIL_LABEL_W      = 140
RAIL_BRAND_W      = 140
RAIL_VERSION_H    = 14
RAIL_VERSION_H_COLLAPSED = 0.1
RAIL_ANIM_MS      = MOTION_PANEL_MS
RAIL_ITEM_ANIM_MS = MOTION_ENTER_MS

# Medidas del reproductor integrado y panel de cola
PLAYER_BAR_H            = 72
PLAYER_THUMB_SIZE       = 44
PLAYER_PROGRESS_H       = 4
PLAYER_PROGRESS_ANIM_MS = RAIL_ITEM_ANIM_MS
PLAYER_QUEUE_W          = 340
PLAYER_QUEUE_ANIM_MS    = RAIL_ANIM_MS

# Listas de selección en diálogos: se muestran por tramos para no construir
# cientos de checkboxes de golpe. El buscador sigue filtrando el total.
DIALOG_OPTION_LIMIT = 80

# Onboarding (primera ejecución): recuadro fijo + fondo de red de puntos.
# Los colores salen de ACCENT/BORDER_MUTED/TEXT_DIM; aquí solo medidas y alfas.
ONBOARDING_CARD_W      = 720
ONBOARDING_CARD_H      = 500
ONBOARDING_CARD_ALPHA  = 0.92   # opacidad de BG_SURFACE: deja ver la red detrás
ONBOARDING_SLIDE       = 0.04   # desplazamiento horizontal relativo entre pasos
ONBOARDING_STAGGER_MS  = 70     # retraso entre elementos al entrar un paso
ONBOARDING_BG_POINTS   = 48
ONBOARDING_BG_LINK     = 150    # distancia máxima (px) para unir dos puntos
ONBOARDING_BG_FPS      = 24     # acotado a 20-24 fps para reducir CPU en Flet
ONBOARDING_BG_SPEED    = 12     # px/s de la capa más cercana
# Profundidad falsa por capas: (factor de velocidad, radio, alfa del punto).
ONBOARDING_BG_LAYERS   = ((0.4, 1.4, 0.35), (0.7, 2.0, 0.55), (1.0, 2.8, 0.85))
# Trazos por arista: continua, discontinua, punteada.
ONBOARDING_BG_DASHES   = (None, (7, 5), (1.5, 5))

# Fondo DISCO del onboarding (modo H: capa estática + reflejo/sello giratorio).
ONBOARDING_DISC_BODY        = "#FF060609"
ONBOARDING_DISC_SEAL        = HANKO
ONBOARDING_DISC_HIGHLIGHT   = "#FFFAFCFF"
ONBOARDING_DISC_BLUR        = 22
ONBOARDING_DISC_MASK_MARGIN = 26
# (cx_frac, cy_frac, r_frac, groove_alpha, rpm, use_accent) — misma dirección (+).
ONBOARDING_DISC_SPECS = (
    (1.00, -0.04, 0.46, 0.17, 1.1, True),
    (-0.02, 1.03, 0.40, 0.13, 0.7, False),
)
