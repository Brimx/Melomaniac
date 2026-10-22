---
name: melomaniac-memory
description: Convenção y flujo de la memoria de Melomaniac en el vault de Obsidian (notas, frontmatter, wikilinks, notas de sesión). Úsala al registrar o consultar la memoria del proyecto, al abrir o cerrar una sesión de trabajo o al enlazar notas temáticas desde una sesión.
---

# Memoria de Melomaniac (Obsidian)

La memoria del proyecto vive **fuera del repositorio**, en `~/Documentos/Obsidian Vault/Proyectos/Melomaniac/`. El código y su configuración son la fuente de verdad del comportamiento; estas notas conservan contexto, decisiones y conocimiento que no cabe en el código.

Regla dura: **solo esta carpeta**. No consultes ni escribas en otros proyectos del vault.

## Al empezar una tarea

1. Lee `Inicio.md` (índice y límites de uso) y `Pendientes/README.md`.
2. Abre `Wiki/`, `Reutilizables/UI/`, `Reutilizables/` o `Herramientas/` según el tema de la tarea.
3. No copies el contenido de una nota al chat: enlázalo.

## Mapa de secciones

| Sección | Qué va allí |
|---|---|
| `Wiki/` | Conocimiento estable: `Arquitectura.md`, `Flujos.md`, `Versiones.md` |
| `Ideas/` | Propuestas que aún **no** son compromisos |
| `Decisiones/` | Elecciones tomadas y su motivo (una nota por decisión, con fecha) |
| `Pendientes/` | Asuntos confirmados o por priorizar |
| `Reutilizables/` | Módulos, funciones, clases y snippets reutilizables (`Reutilizables/UI/` para tokens, tipografía, controles y animaciones) |
| `Herramientas/` | Skills y MCPs por agente: propósito, agente compatible, uso y ruta de configuración. **Nunca credenciales** |
| `Sesiones/` | Una nota por sesión significativa, en orden cronológico |
| `Fuentes heredadas/` | Material original preservado sin reescribir |

## Formato de nota

```markdown
---
tipo: wiki | idea | decision | pendiente | reutilizable | herramienta | sesion | indice
proyecto: Melomaniac
creado: 2026-10-22
tags: [melomaniac, <tema>]
---
# Título

Una idea por nota, en una frase, arriba.
```

- Frontmatter siempre con `tipo`, `proyecto`, `creado` y `tags`.
- Enlaces internos con wikilinks: `[[Wiki/Arquitectura|Arquitectura]]`. Desde la raíz del vault: `[[Proyectos/Melomaniac/Inicio|Melomaniac]]`.
- Fecha y hora en **hora local de Bogotá** (UTC-5). El sistema ya está en ese huso.
- Título en el idioma de la nota; el código y los nombres de símbolo van en su idioma original.

## Nota de sesión

Una por sesión significativa. Plantilla:

```markdown
---
tipo: sesion
proyecto: Melomaniac
creado: 2026-10-22
tags: [melomaniac, sesion]
---
# Sesión — <objetivo en una frase>

- **Fecha y hora:** 2026-10-22 01:10 (Bogotá)
- **Objetivo:** <qué se iba a hacer>
- **Versión documentada:** <versión tomada de README.md y CHANGELOG.md>
- **Entorno:** <agente usado, herramientas relevantes>
- **Resultado:** <qué quedó hecho>
- **Pendientes:** <qué quedó abierto>
- **Notas relacionadas:** <wikilinks a las notas temáticas actualizadas>
```

Reglas de la sesión:

- La sesión **enlaza** el conocimiento temático; no lo duplica.
- La versión documentada se toma de `README.md` y `CHANGELOG.md`. Si discrepan de otra fuente, **registra la discrepancia** y no la resuelvas por inferencia.
- Si la sesión no cambió nada significativo, no crees nota.

## Qué no se guarda

- Secretos, tokens, cookies, credenciales ni valores de configuración sensibles. En `Herramientas/` se registra propósito, agente compatible, uso y ruta de configuración.
- Código copiado desde el repo: se referencia el archivo y la línea (`ruta:línea`).
- Detalles triviales de una sesión que no dejan una decisión, un bug, una idea o un reusable.
