---
name: Melomaniac
description: Sistema visual de Melomaniac, aplicación de escritorio en Flet. Paleta OLED oscura, tipografía Stack Sans con fallback IBM Plex y CJK, escalas fijas de espaciado y radio.
colors:
  background: "#000000"
  surface: "#111118"
  panel: "#080808"
  input: "#16161F"
  list: "#161622"
  hover: "#1E1E28"
  sidebar: "#0E0E15"
  chip: "#1A1A22"
  primary: "#4F8BFF"
  primary-hover: "#6BA3FF"
  primary-pressed: "#2D5FCC"
  primary-halo: "#2A3F5C"
  success: "#00D084"
  warning: "#FFA500"
  error: "#FF4444"
  border: "#3D4455"
  border-muted: "#2A3040"
  border-row: "#252530"
  text-primary: "#F2F6FF"
  text-muted: "#7A8499"
  text-dim: "#3D4455"
typography:
  brand: {fontFamily: Stack Sans Notch, fontSize: 52px, fontWeight: 700}
  title: {fontFamily: Stack Sans Headline, fontSize: 22px, fontWeight: 600}
  heading: {fontFamily: Stack Sans Headline, fontSize: 16px, fontWeight: 600}
  body: {fontFamily: Stack Sans Text, fontSize: 14px, fontWeight: 400, lineHeight: 1.4}
  body-small: {fontFamily: Stack Sans Text, fontSize: 13px, fontWeight: 400, lineHeight: 1.4}
  label: {fontFamily: Stack Sans Headline, fontSize: 12px, fontWeight: 600}
  caption: {fontFamily: Stack Sans Text, fontSize: 11px, fontWeight: 400}
  mono: {fontFamily: IBM Plex Mono, fontSize: 12px, fontWeight: 400}
  cyrillic: {fontFamily: IBM Plex Sans, fontSize: 14px, fontWeight: 400}
rounded:
  xs: 3px
  sm: 4px
  md: 6px
  lg: 8px
  xl: 10px
  pill: 20px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
components:
  app-canvas:
    backgroundColor: "{colors.background}"
    textColor: "{colors.text-primary}"
  surface-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: 12px
  panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.md}"
  sidebar:
    backgroundColor: "{colors.sidebar}"
    textColor: "{colors.text-muted}"
  list-row:
    backgroundColor: "{colors.list}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
  list-row-hover:
    backgroundColor: "{colors.hover}"
    textColor: "{colors.text-primary}"
  input:
    backgroundColor: "{colors.input}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: 8px
  input-focus:
    backgroundColor: "{colors.input}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.md}"
  chip:
    backgroundColor: "{colors.chip}"
    textColor: "{colors.text-muted}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 4px
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.text-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.xl}"
    padding: 12px
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.xl}"
  button-primary-pressed:
    backgroundColor: "{colors.primary-pressed}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.xl}"
  focus-ring:
    backgroundColor: "{colors.primary-halo}"
  button-ghost:
    backgroundColor: "{colors.background}"
    textColor: "{colors.text-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.xl}"
    padding: 12px
  button-ghost-hover:
    backgroundColor: "{colors.background}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.xl}"
  divider-row:
    backgroundColor: "{colors.border-row}"
  border-strong:
    backgroundColor: "{colors.border}"
  border-muted:
    backgroundColor: "{colors.border-muted}"
  icon-placeholder:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-dim}"
  status-success:
    backgroundColor: "{colors.success}"
    textColor: "{colors.background}"
  status-warning:
    backgroundColor: "{colors.warning}"
    textColor: "{colors.background}"
  status-error:
    backgroundColor: "{colors.error}"
    textColor: "{colors.background}"
---

## Overview

Melomaniac es una aplicación de escritorio (Flet, Python) para organizar, buscar y exportar playlists entre YouTube Music, Apple Music y Spotify. Su interfaz es un instrumento de trabajo denso y oscuro: se usa de noche, con listas largas y con la mirada fija en la pantalla.

La identidad visual es OLED: negro absoluto de fondo y superficies que solo se separan por un uno o dos niveles de gris azulado. No hay sombras como mecanismo principal de jerarquía —la jerarquía viene del contraste de superficie y del espacio—. El acento azul es el único color saturado y se reserva para lo accionable.

Criterio: la UI debe leerse como una sola superficie oscura continua donde el contenido flota, no como un conjunto de tarjetas. Un elemento destaca por su posición o por su acento, no por estar encerrado en un marco.

## Colors

Fondo y superficies, de más profundo a más elevado:

| Token | Hex | Uso |
|---|---|---|
| `background` | `#000000` | Lienzo de la app, base de los botones ghost |
| `panel` | `#080808` | Paneles y contenedores de información |
| `sidebar` | `#0E0E15` | Rail de navegación lateral |
| `surface` | `#111118` | Superficies elevadas: tarjetas, placeholders de skeleton |
| `input` | `#16161F` | Campos de texto |
| `list` | `#161622` | Fondo de las filas de lista |
| `hover` | `#1E1E28` | Fila de lista bajo el cursor |
| `chip` | `#1A1A22` | Chips, tags y badges |

El acento y sus estados:

| Token | Hex | Uso |
|---|---|---|
| `primary` | `#4F8BFF` | Botón primario, icono seleccionado |
| `primary-hover` | `#6BA3FF` | Botón primario en hover |
| `primary-pressed` | `#2D5FCC` | Botón primario presionado |
| `primary-halo` | `#2A3F5C` | Halo de elevación del botón primario en hover |

Estados semánticos, usados solo en iconografía y bordes, nunca como relleno grande:

| Token | Hex |
|---|---|
| `success` | `#00D084` |
| `warning` | `#FFA500` |
| `error` | `#FF4444` |

Bordes: `border` `#3D4455` (visible), `border-muted` `#2A3040` (fantasma de botón), `border-row` `#252530` (separador de fila, 0.5 px).

Texto: `text-primary` `#F2F6FF`, `text-muted` `#7A8499` (secundario, ~4.6:1 sobre chip), `text-dim` `#3D4455` (iconos de placeholder, nunca informativo).

Los overlays blancos translúcidos (6 %, 8 %, 10 %, 14 %, 18 %) se usan para tabs, inputs, notas y banners: son el único mecanismo de "capa" sobre superficie negra.

## Typography

Stack Sans es la familia de producto; IBM Plex entra por criterio de Coverage, no por gusto.

- `brand` — Stack Sans **Notch**, 52 px. Solo el logotipo.
- `title` — Stack Sans Headline 22 px, semibold. Títulos de sección y títulos de diálogo.
- `heading` — Stack Sans Headline 16 px, semibold. Encabezados de columna y de tarjeta.
- `body` — Stack Sans Text 14 px. Texto de fila y contenido.
- `body-small` — Stack Sans Text 13 px. Texto secundario dentro de una celda.
- `label` — Stack Sans Headline 12 px, semibold. Etiquetas de botones y de segmented buttons.
- `caption` — Stack Sans Text 11 px. Metadatos: duración, número de pistas, badges.
- `mono` — IBM Plex Mono 12 px. Consola, logs y datos técnicos.
- `cyrillic` — IBM Plex Sans 14 px. Sustituye a Stack Sans Text cuando el texto contiene cirílico.

La selección de familia se hace **por rangos de caracteres** del texto renderizado (CJK > cirílico > latino), no por el locale del sistema. Si no hay fuente CJK instalada, se registra el fallback nativo; nunca se fuerza una fuente latina sobre glifos CJK.

Pesos disponibles: Light, Regular, Medium, SemiBold, Bold. El SemiBold es el peso de interfaz por defecto en etiquetas y titulares.

## Layout

Escala de espaciado: `xs` 4, `sm` 8, `md` 12, `lg` 16, `xl` 24. Todo el padding y el `spacing` salen de aquí.

- Altura de fila de canción: **64 px** constantes, padding vertical 12 px, separador inferior de 0.5 px.
- La fila es una rejilla de 7 celdas: índice (28), miniatura (55×55), título (expande 3), álbum (expande 2), duración (48), estado (26), check (32×18). `spacing` horizontal 16.
- Los skeletons replican exactamente esa geometría; si no, la lista salta al cargar.
- El rail lateral es colapsable y recalcula el esqueleto al cambiar de viewport.
- Los diálogos se centran y nunca superan el ancho de la ventana; en ventanas estrechas pasan a columna.

## Elevation & Depth

La profundidad es casi plana. No hay sombras como mecanismo de jerarquía.

- La única sombra del sistema es el halo del botón primario en hover: `primary-halo` con elevación 6.
- Jerarquía por tono de superficie: `background` → `panel`/`sidebar` → `surface` → `input`/`list`.
- Las separaciones dentro de una superficie se hacen con `border-row` a 0.5 px, no con sombra.
- El overlay (blanco translúcido 6–18 %) marca capas modales por encima de la superficie.

## Shapes

Escala de radios: `xs` 3 (placeholders internos), `sm` 4 (inputs pequeños, check), `md` 6 (chips medianos, iconos), `lg` 8 (skeleton de miniatura, superficie), `xl` 10 (botones), `pill` 20 (chips redondeados, contornos circulares).

Regla: los elementos con radio grande son interactivos y redondeados; las filas y superficies son casi rectas. Nada de radios entre 12 y 20 salvo círculos intencionales.

## Components

- **Botón primario** — fondo `primary`, texto `text-primary`, radio `xl`, padding 12 px. Hover: `primary-hover` + halo. Pressed: `primary-pressed`.
- **Botón ghost** — fondo transparente, texto `text-muted`, borde `border-muted` 0.8 px. Hover: texto `text-primary` y borde `primary`. Es la acción secundaria o destructiva.
- **Campo de texto** — fondo `input`, radio `md`, texto `body`.
- **Fila de lista** — fondo `list`, separador `border-row` 0.5 px, alto 64 px. Hover: `hover`.
- **Chip** — fondo `chip`, texto `text-muted` en `caption`, radio `pill`.
- **Superficie elevada** — fondo `surface`, radio `lg`, padding `md`.
- **Icono de placeholder** — `surface` + `text-dim`, para carátulas y estados vacíos.
- **Indicador de estado** — `success` / `warning` / `error` sobre `background` en iconos pequeños.

## Do's and Don'ts

- **Do:** token único para cada valor; si el color no está en `ui/tokens.py`, se añade ahí.
- **Do:** una sola familia tipográfica por script, sin mezclar Stack Sans e IBM Plex en la misma línea.
- **Do:** jerarquía por espacio y por tono de superficie, no por cajas y sombras.
- **Do:** un solo elemento memorable por pantalla; el resto se queda quieto.
- **Don't:** añadir tarjetas alrededor de cada elemento de una lista.
- **Don't:** usar el acento como color decorativo: `#4F8BFF` significa "accionable".
- **Don't:** usar `text-dim` para texto que se debe leer.
- **Don't:** animar con curvas elásticas o rebote.
- **Don't:** variar la duración de la misma animación entre pantallas.
- **Desviación conocida:** el botón primario usa texto claro sobre el acento (3.0:1, y 2.3:1 en hover), por debajo de AA. El linter (`npx @google/design.md lint DESIGN.md`) lo reporta en cada pasada. No lo cambies por tu cuenta: es una decisión de diseño pendiente. El icono de placeholder también queda por debajo de AA a propósito (es decorativo, no informativo).
