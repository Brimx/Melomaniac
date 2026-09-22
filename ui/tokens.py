"""
ui/tokens.py — Melomaniac v4.5.1 — Design Tokens
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
