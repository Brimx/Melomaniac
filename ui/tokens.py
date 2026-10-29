"""
ui/tokens.py — Melomaniac v4.7.0 — Design Tokens
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
BORDER_LIGHT = "#FF3D4455"
BORDER_MUTED = "#FF2A3040"

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
TEXT_PRIMARY = "#FFF2F6FF"
TEXT_MUTED   = "#FF7A8499"
TEXT_DIM     = "#FF3D4455"

# Radios canónicos (reutilizados por botones, inputs, diálogos y rail)
RADIUS_INPUT  = 10
RADIUS_BUTTON = 10
RADIUS_DIALOG = 14
RADIUS_RAIL        = RADIUS_DIALOG
RADIUS_RAIL_ITEM   = RADIUS_BUTTON

# Medidas del rail de navegación
RAIL_W_COLLAPSED = 68
RAIL_W_EXPANDED  = 220
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
RAIL_ANIM_MS      = 300
RAIL_ITEM_ANIM_MS = 200

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
