---
name: Melomaniac
description: Sistema visual de Melomaniac en Flet: lienzo OLED, texto cálido, acento azul y detalles de papel, obi y sello hanko.
colors:
  background: "#000000"
  panel: "#080808"
  inset: "#050608"
  surface: "#111118"
  input: "#16161F"
  list: "#161622"
  hover: "#1E1E28"
  sidebar: "#0E0E15"
  chip: "#1A1A22"
  paper: "#E9E2D0"
  hanko: "#D9443B"
  accent: "#4F8BFF"
  accent-hover: "#6BA3FF"
  accent-pressed: "#2D5FCC"
  accent-halo: "#2A3F5C"
  success: "#00D084"
  warning: "#FFA500"
  error: "#FF4444"
  border: "#3D4455"
  border-muted: "#2A3040"
  border-control: "#5A6478"
  border-row: "#252530"
  text-primary: "#F2EDE4"
  text-muted: "#848DA1"
  text-dim: "#3D4455"
typography:
  brand: {fontFamily: Stack Sans Notch}
  headline: {fontFamily: Stack Sans Headline}
  body: {fontFamily: Stack Sans Text}
  mono: {fontFamily: IBM Plex Mono}
  cyrillic: {fontFamily: IBM Plex Sans}
  sizes: {meta: 11px, small: 12px, body: 14px, row: 16px, section: 20px, page: 28px, display: 40px}
rounded:
  none: 0px
  sharp: 3px
  sm: 6px
  pill: 999px
spacing:
  0: 0px
  1: 4px
  2: 8px
  3: 12px
  4: 16px
  5: 24px
  6: 32px
  7: 48px
---

## Dirección visual

La interfaz combina un lienzo OLED oscuro con acentos cálidos inspirados en papel, lomos *obi* y sellos *hanko*. La jerarquía se construye con espacio, contraste, alineación y relieve medido. Las superficies oscuras mantienen continuidad; las bandas de papel resaltan contenido seleccionado o editorial, y el azul identifica acciones y foco.

Las decisiones de producto deben conservar legibilidad en listas largas y ventanas compactas. La portada acompaña la información de la pista; no debe desplazar las acciones ni los metadatos que permiten organizar una biblioteca.

## Colores

`ui/theme/tokens.py` es la fuente única de colores. Los valores de abajo son la forma sin alfa de los tokens RGBA del código.

| Token de código | Hex | Uso |
|---|---|---|
| `BG_DEEP` | `#000000` | Fondo principal |
| `BG_PANEL` | `#080808` | Paneles profundos |
| `BG_INSET` | `#050608` | Superficies interiores |
| `BG_SURFACE` | `#111118` | Superficie elevada y skeletons |
| `BG_INPUT` | `#16161F` | Campos de entrada |
| `BG_LIST` | `#161622` | Filas de listas |
| `BG_HOVER` | `#1E1E28` | Hover de filas |
| `SIDEBAR_BG` | `#0E0E15` | Rail lateral |
| `CHIP_BG` | `#1A1A22` | Chips y etiquetas |
| `PAPER_BG` | `#E9E2D0` | Papel, bandas *obi* y superficies claras |
| `HANKO` | `#D9443B` | Sello bermellón |
| `ACCENT` | `#4F8BFF` | Acción principal y foco |
| `ACCENT_HOVER` | `#6BA3FF` | Acción principal en hover |
| `ACCENT_DIM` | `#2D5FCC` | Acción presionada |
| `ACCENT_HALO` | `#2A3F5C` | Relieve azul de acciones |
| `TEXT_PRIMARY` | `#F2EDE4` | Texto principal cálido |
| `TEXT_MUTED` | `#848DA1` | Texto secundario |
| `TEXT_DIM` | `#3D4455` | Marcas decorativas, no texto informativo |
| `BORDER_LIGHT` | `#3D4455` | Borde visible |
| `BORDER_MUTED` | `#2A3040` | Borde discreto |
| `BORDER_CONTROL` | `#5A6478` | Contorno de controles interactivos |
| `BORDER_ROW` | `#252530` | Separador de filas |
| `SUCCESS` / `WARNING` / `ERROR_COL` | `#00D084` / `#FFA500` / `#FF4444` | Estados semánticos |

`ON_ACCENT` (`#06101F`) define texto e iconos sobre el azul. `SHADOW_INK` y `SHADOW_INK_2` crean el relieve de papel. Los overlays blancos y de advertencia también están centralizados en `tokens.py`; no se inventan valores locales.

## Tipografía

`ui/theme/fonts.py` selecciona la familia por escritura, no por idioma del sistema:

- **Stack Sans Notch:** marca.
- **Stack Sans Headline:** encabezados y etiquetas de interfaz.
- **Stack Sans Text:** texto general latino.
- **IBM Plex Sans:** texto cirílico.
- **IBM Plex Mono:** logs y datos técnicos.
- **CJK:** se usa cuando hay una fuente compatible disponible; si no, se conserva el fallback del sistema.

La escala tipográfica canónica (`FS_*` en `tokens.py`) es: meta 11 px, pequeña 12 px, cuerpo 14 px, fila 16 px, sección 20 px, página 28 px y display 40 px. Los componentes pueden definir tamaños puntuales cuando su función lo exige, pero los valores compartidos deben salir de esta escala.

## Espaciado y medidas

La escala de espacio vive en `SPACE_0`–`SPACE_7`: `0`, `4`, `8`, `12`, `16`, `24`, `32` y `48` px.

| Elemento | Medida de referencia |
|---|---:|
| Fila de pista (`ITEM_H`) | 64 px |
| Rail colapsado / expandido | 68 / 220 px |
| Barra de reproducción | 72 px |
| Panel lateral de cola | 340 px |
| Tarjeta de playlist | 160 px |

Las listas de altura uniforme mantienen `item_extent=ITEM_H` y una caché de scroll acotada. El rail desplaza el contenido; el panel de cola se integra al layout. En ventanas estrechas, se priorizan controles utilizables y texto sin recortes.

## Formas y profundidad

Los únicos radios canónicos son `RADIUS_NONE` (0 px), `RADIUS_SHARP` (3 px), `RADIUS_SM` (6 px) y `RADIUS_PILL` (999 px, solo discos e indicadores circulares). No se deben documentar radios de 10 o 14 px como norma global.

El relieve de papel usa desplazamientos y sombras centralizados (`PAPER_LIFT`, `PAPER_LIFT_SM`, `PAPER_CANTO_PX`, `SHADOW_INK*`). Las filas y paneles oscuros se separan sobre todo por tono, espacio y divisores; la sombra se reserva para elementos que representan papel elevado.

## Componentes y estados

- **Acción primaria:** azul `ACCENT`, texto `ON_ACCENT`; hover y presión usan sus tokens propios.
- **Acción secundaria:** superficie discreta y texto secundario, con borde de control donde haga falta.
- **Entrada:** `BG_INPUT`, texto principal y contorno visible al recibir foco.
- **Fila:** `BG_LIST`, altura `ITEM_H`, divisor `BORDER_ROW`; selección y hover conservan una señal visible además del color.
- **Papel y obi:** `PAPER_BG` con texto oscuro, filete/canto y sombra de papel según el componente.
- **Sello:** `HANKO` se reserva para acentos de identidad.
- **Foco de teclado:** anillo azul de `FOCUS_RING_WIDTH=2` y separación `FOCUS_RING_OFFSET=2`.
- **Estado:** éxito, advertencia y error se acompañan con texto o iconografía; el color no es la única señal.

Los helpers visuales compartidos viven en `ui/components/widgets.py`; las vistas reutilizan esos helpers y los tokens en `ui/theme/tokens.py`.

## Movimiento

Las duraciones están centralizadas en `tokens.py`; se reutilizan por propósito y con las curvas de transición definidas por el componente.

| Token | Duración | Uso |
|---|---:|---|
| `MOTION_PRESS_MS` | 90 ms | Compresión al pulsar |
| `MOTION_HOVER_MS` | 120 ms | Hover y respuesta breve |
| `MOTION_EXIT_MS` | 167 ms | Salida o cierre |
| `MOTION_ENTER_MS` | 200 ms | Entrada y cambio de estado |
| `MOTION_PANEL_MS` | 300 ms | Rail, paneles y vistas |
| `MOTION_REVEAL_MS` | 400 ms | Reveal ASCII de skeletons |
| `MOTION_SKELETON_MS` | 600 ms | Pulso de carga |
| `MOTION_SHIMMER_MS` | 1500 ms | Shimmer |
| `MOTION_AMBIENT_MS` | 24000 ms | Deriva ambiental de Reproduciendo |

Las animaciones respetan movimiento reducido. No se asignan duraciones numéricas ad hoc en `ui/`; si aparece un propósito nuevo y reutilizable, se añade un token con su justificación.

## Criterios de implementación

- Colores, escalas, radios, medidas repetidas y movimiento tienen una sola fuente de verdad en `ui/theme/tokens.py`.
- Tipografías y selección por script pasan por `ui/theme/fonts.py`.
- La jerarquía se resuelve primero con espacio, contraste y alineación; tarjetas, bordes y sombras se usan cuando comunican agrupación o elevación.
- El texto dim es decorativo: no se usa para información que el usuario debe leer.
- El foco debe ser visible, los estados no dependen solo del color y los ajustes de accesibilidad respetan movimiento reducido.
- No se añaden superficies alrededor de cada fila ni se usa el azul como decoración sin función.
