# Changelog

## 3.3.7 — 2026-09-16 · Actualización documental

- Sincroniza `README.md` y `README.en.md` con las funcionalidades actuales: importación de audio y metadatos Mutagen, escaneo de disponibilidad, caché, límites de concurrencia y lotes por plataforma.
- Actualiza `ARCHITECTURE.md` y `ARCHITECTURE.en.md` con la composición real de `AppState`, `MusicApiService`, `AuthManager` y la UI.
- Documenta el comportamiento actual de descripciones en Spotify, la resolución de ISRC en Apple Music y los formatos locales soportados.

## 3.3.7 — 2026-09-09

- Permite editar el nombre y la descripción de la playlist antes de iniciar la transferencia.
- Añade el álbum como metadato visible en cada fila de la playlist.
- Corrige el diálogo modal de metadatos: el backdrop ahora es hijo directo del `Stack` raíz, gestiona `on_click` directamente y usa `ink=False`.
- Reorganiza la configuración runtime en `config/` y mueve autenticación, metadatos de audio y resiliencia a sus paquetes correspondientes.
- Mantiene la versión del proyecto alineada en `3.3.7`.
- Incluye pruebas unitarias para ISRC, Mutagen y Apple Music; la UI continúa validándose manualmente.
