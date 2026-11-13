"""core/download_flow.py — Descargas con yt-dlp: cola, pausa, reintentos y manifiesto.

Coordinador de descargas compuesto por `AppState` (core/state.py).
"""

from __future__ import annotations

import asyncio
import os
import time

from core.models import Track, TransferState


class DownloadRunner:
    """Descargas con yt-dlp: cola, pausa, reintentos y manifiesto."""

    def __init__(self, state) -> None:
        self._state = state

    def download_selected(
        self,
        *,
        audio_format: str = "",
        destination_dir: str = "",
    ) -> asyncio.Task:
        """Lanza la descarga de audio de las pistas seleccionadas.

        Cada pista se resuelve como *coincidencia en YouTube*: si la pista
        viene de YouTube Music se reutiliza su ``source_url``; si viene de
        Apple Music o Spotify se busca la grabación equivalente. Nunca se
        descarga desde la plataforma de origen.

        El trabajo pesado corre con ``asyncio.to_thread`` porque ``yt_dlp``
        es bloqueante. El estado se muta solo en el hilo de la UI.

        Args:
            audio_format: Formato de salida (``mp3``, ``m4a``, ``opus``,
                ``flac``, ``wav``). Vacío usa ``DEFAULT_AUDIO_FORMAT``.
            destination_dir: Directorio destino. Vacío usa la resolución
                por defecto de ``engine.downloader``.

        Returns:
            La tarea creada. Si ya hay una descarga en curso, devuelve esa
            misma tarea sin lanzar otra.
        """
        selected = [t for t in self._state.tracks if t.selected]
        platforms = {t.platform for t in selected if t.platform}
        return self._state.download_tracks(
            selected,
            audio_format=audio_format, destination_dir=destination_dir,
            playlist_name=self._state.playlist_name if self._state.playlist_name else "",
            platform=platforms.pop() if len(platforms) == 1 else "",
        )

    def download_tracks(
        self,
        tracks: list[Track],
        *,
        audio_format: str = "",
        destination_dir: str = "",
        playlist_name: str = "",
        platform: str = "",
    ) -> asyncio.Task:
        """Lanza la descarga de una lista arbitraria de pistas.

        Reutiliza la misma cola que ``download_selected``; Biblioteca la usa
        para bajar una playlist completa sin cargarla en Gestión.

        Args:
            playlist_name: Si se indica, las pistas van a una subcarpeta con
                ese nombre dentro de ``destination_dir`` y se escribe
                ``playlist.json`` con el orden original.
            platform: Plataforma de origen, para nombrar la subcarpeta.

        Returns:
            La tarea creada, o la en curso si ya hay una descarga activa.
        """
        if self._state._download_task is not None and not self._state._download_task.done():
            return self._state._download_task
        self._state._download_task = asyncio.create_task(
            self._run_downloads(
                list(tracks), audio_format=audio_format, destination_dir=destination_dir,
                playlist_name=playlist_name, platform=platform,
            )
        )
        return self._state._download_task

    def cancel_downloads(self) -> None:
        """Marca la descarga en curso para que se detenga en la próxima pista."""
        if self._state._download_cancel is not None:
            self._state._download_cancel.set()

    def pause_downloads(self) -> None:
        """Pausa la cola, incluida la pista que se está descargando.

        No se limita a esperar a que termine la pista: la aborta desde el
        progress hook y deja el ``.part`` en disco para reanudar desde ahí.
        """
        if self._state.download_state != TransferState.RUNNING:
            return
        if self._state._download_pause is not None:
            self._state._download_pause.set()
        self._state.download_paused = True
        # La pista activa se marca como pausada en el acto. yt-dlp tarda un
        # momento en abortar, y hasta ahora la fila pasaba por «Error» antes
        # de llegar al estado correcto, que se lee como un fallo real.
        for item in self._state.download_items:
            if item.get("status") == "running":
                item["status"] = "paused"
                item["error"] = "Pausada"
        self._state._log("[INFO] Descargas en pausa")
        self._state.notify()

    def resume_downloads(self) -> asyncio.Task | None:
        """Reanuda la cola pausada desde donde se quedó.

        Relanza las pistas que no terminaron conservando formato, carpeta y
        nombre de playlist. Los archivos ya completos se omiten por
        ``skip_existing`` y la pista interrumpida se reanuda desde su
        ``.part``.
        """
        if not self._state.download_paused:
            return None
        pending = list(self._state._download_pending_tracks)
        ctx = dict(self._state._download_resume_ctx)
        self._state.download_paused = False
        if not pending:
            self._state._log("[WARN] No hay pistas pendientes para reanudar")
            self._state.notify()
            return None
        self._state._log(f"[INFO] Reanudando descargas · {len(pending)} pistas pendientes")

        async def _resume_after_pause() -> None:
            # El task pausado puede seguir vivo unos instantes mientras las
            # tareas en espera ven el evento. ``download_tracks`` devolvería
            # ese task sin arrancar nada, así que primero se deja terminar.
            previous = self._state._download_task
            if previous is not None and not previous.done():
                try:
                    await previous
                except Exception:
                    pass
            self._state._download_task = None
            self._state.download_tracks(
                pending,
                audio_format=ctx.get("format", ""),
                destination_dir=ctx.get("destination_dir", ""),
                playlist_name=ctx.get("playlist_name", ""),
                platform=ctx.get("platform", ""),
            )

        return asyncio.create_task(_resume_after_pause())

    async def _run_downloads(
        self, selected: list[Track], *, audio_format: str = "", destination_dir: str = "",
        playlist_name: str = "", platform: str = "", pinned_video_id: str = "",
    ) -> None:
        """Ejecuta la cola de descargas con concurrencia acotada.

        Implementa la fase 1 del pendiente de Descargas: módulo de descarga
        con ``yt_dlp`` como librería, caché en disco y cancelación. El
        progreso se publica por pista para que la UI muestre avance real.

        Con ``playlist_name`` las pistas van a una subcarpeta propia y se deja
        un manifiesto con el orden original: una carpeta plana mezclaba listas
        distintas y todos los ``cover.jpg`` acababan en el mismo sitio.
        """
        from core.config import (
            AUDIO_BITRATES,
            DEFAULT_AUDIO_FORMAT,
            DOWNLOAD_CONCURRENCY,
        )
        from engine.downloader import (
            download_audio, ffmpeg_available, read_playlist_manifest,
            resolve_download_dir, resolve_playlist_dir, write_playlist_manifest,
        )

        if not selected:
            return

        fmt = (audio_format or DEFAULT_AUDIO_FORMAT).lower().lstrip(".")
        if fmt not in AUDIO_BITRATES:
            fmt = DEFAULT_AUDIO_FORMAT
        if playlist_name:
            target_dir = resolve_playlist_dir(
                destination_dir or None, playlist_name, platform
            )
        else:
            target_dir = resolve_download_dir(destination_dir or None)

        self._state.audio_format      = fmt
        self._state.download_dir      = str(target_dir)
        self._state.download_state    = TransferState.RUNNING
        self._state.download_progress = 0
        self._state.download_total    = len(selected)
        self._state.download_current  = ""
        self._state.download_done     = []
        self._state.download_skipped  = []
        self._state.download_failed   = []
        self._state.download_missing  = []
        items = {
            id(t): {"id": t.id, "title": f"{t.artist} — {t.name}", "status": "queued",
                    "percent": 0.0, "error": ""}
            for t in selected
        }
        self._state.download_items    = list(items.values())
        self._state._download_cancel  = asyncio.Event()
        self._state._download_pause   = asyncio.Event()
        cancel = self._state._download_cancel
        pause = self._state._download_pause
        # Contexto para reanudar exactamente esta cola, no otra.
        self._state.download_paused = False
        self._state._download_pending_tracks = []
        self._state._download_resume_ctx = {
            "format": fmt,
            "destination_dir": str(destination_dir or ""),
            "playlist_name": playlist_name,
            "platform": platform,
        }
        queued = list(selected)

        # Sin FFmpeg toda la cola fallaría igual pista por pista. Se marca de
        # una vez para que la UI muestre un solo diagnóstico accionable.
        if not ffmpeg_available():
            message = "FFmpeg no está instalado o no está en PATH"
            for item in self._state.download_items:
                item["status"] = "failed"
                item["error"] = message
            # Misma forma que una falla real para que el panel de resultados
            # pueda renderizarlas y reintentarlas sin un caso especial.
            self._state.download_failed = [
                {
                    "id": t.id, "name": t.name, "artist": t.artist,
                    "album": t.album, "platform": t.platform,
                    "duration_ms": t.duration_ms, "isrc": t.isrc,
                    "error": message, "format": fmt, "_track": t,
                }
                for t in selected
            ]
            self._state.download_progress = self._state.download_total
            self._state.download_state    = TransferState.DONE
            self._state._log(f"[ERROR] Descargas canceladas · {message}")
            self._state.notify()
            return

        self._state._log(
            f"[INFO] Descargas · {len(selected)} pistas → {fmt.upper()} en {target_dir}"
        )
        self._state.notify()

        # Un único resolver por cola: su memo por álbum evita repetir la
        # consulta para cada pista del mismo disco. ``engine`` no puede
        # importar ``services``, así que la cadena se inyecta aquí.
        cover_resolver = None
        artwork_fetcher = None
        try:
            from services import settings_store
            if settings_store.get_covers_online():
                from services.covers import CoverResolver
                cover_resolver = CoverResolver()
                artwork_fetcher = lambda t: cover_resolver.resolve(  # noqa: E731
                    t, allow_youtube_thumb=True
                ).data or None
        except Exception as exc:  # noqa: BLE001 - portadas nunca bloquean la cola
            self._state._log(f"[WARN] Portadas online no disponibles: {exc}")

        dl_dir_arg = destination_dir
        local_folders_arg: tuple[str, ...] = ()
        if not pinned_video_id:
            try:
                from services import settings_store
                from services.local_index import match_tracks_against_library

                if not dl_dir_arg:
                    dl_dir_arg = settings_store.get_download_dir()
                local_folders_arg = tuple(settings_store.get_local_folders())
                await asyncio.to_thread(
                    match_tracks_against_library,
                    selected,
                    download_dir=dl_dir_arg,
                    local_folders=local_folders_arg,
                    playlist_name=playlist_name,
                    platform=platform,
                )
            except Exception:
                pass

        semaphore = asyncio.Semaphore(max(1, DOWNLOAD_CONCURRENCY))
        lock = asyncio.Lock()

        loop = asyncio.get_running_loop()
        last_push = [0.0]

        def _push() -> None:
            now = time.monotonic()
            if now - last_push[0] >= 0.25:
                last_push[0] = now
                self._state.notify()

        async def _one(track: Track) -> None:
            item = items[id(track)]

            def _hook(info: dict) -> None:
                # Corre en el hilo de yt-dlp: solo muta valores simples y
                # pide el notify al loop de la UI (limitado a 4/s).
                if info.get("status") != "downloading":
                    return
                total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                if total:
                    item["percent"] = min(95.0, 100.0 * (info.get("downloaded_bytes") or 0) / total)
                    loop.call_soon_threadsafe(_push)

            async with semaphore:
                if cancel.is_set():
                    item["status"] = "failed"
                    item["error"] = "Cancelada"
                    return
                # La pausa puede haberse pedido mientras la pista esperaba
                # turno: nunca debe arrancar una descarga nueva, pero tampoco
                # perderse de la lista de pendientes.
                if pause.is_set():
                    item["status"] = "paused"
                    item["error"] = "Pausada"
                    if track not in self._state._download_pending_tracks:
                        self._state._download_pending_tracks.append(track)
                    return
                if not pinned_video_id and not (track.source_path and os.path.isfile(track.source_path)):
                    try:
                        from services import settings_store
                        from services.local_index import match_tracks_against_library

                        await asyncio.to_thread(
                            match_tracks_against_library,
                            [track],
                            download_dir=dl_dir_arg or settings_store.get_download_dir(),
                            local_folders=local_folders_arg or settings_store.get_local_folders(),
                            playlist_name=playlist_name,
                            platform=platform,
                        )
                    except Exception:
                        pass
                if not pinned_video_id and track.source_path and os.path.isfile(track.source_path):
                    async with lock:
                        item["status"], item["percent"] = "skipped", 100.0
                        self._state.download_skipped.append(
                            {
                                "id": track.id,
                                "name": track.name,
                                "artist": track.artist,
                                "path": track.source_path,
                                "video_id": "",
                                "format": fmt,
                                "duration_ms": track.duration_ms,
                            }
                        )
                        self._state.download_progress += 1
                        self._state.notify()
                    return
                async with lock:
                    item["status"] = "running"
                    self._state.download_current = f"{track.artist} — {track.name}"[:80]
                    self._state.notify()
                try:
                    result = await asyncio.to_thread(
                        download_audio,
                        track,
                        destination_dir=target_dir,
                        audio_format=fmt,
                        progress_hook=_hook,
                        artwork_fetcher=artwork_fetcher,
                        skip_existing=True,
                        pause_event=pause,
                        video_id=pinned_video_id,
                    )
                except Exception as exc:  # noqa: BLE001 - nunca debe tumbar la cola
                    result = None
                    error = str(exc)
                else:
                    error = getattr(result, "error", "") or ""

                from engine.downloader import DOWNLOAD_PAUSED as _PAUSED_CODE

                if result is not None and result.code == _PAUSED_CODE:
                    # La pista quedó a medias: se conserva para reanudarla y
                    # el ``.part`` se queda en disco.
                    async with lock:
                        item["status"] = "paused"
                        item["error"] = "Pausada"
                        if track not in self._state._download_pending_tracks:
                            self._state._download_pending_tracks.append(track)
                        self._state.download_progress += 1
                        self._state.notify()
                    return

                async with lock:
                    if result is not None and result.ok:
                        track.source_path = result.path
                        entry = {
                            "id": track.id,
                            "name": track.name,
                            "artist": track.artist,
                            "path": result.path,
                            "video_id": result.video_id,
                            "format": fmt,
                            "duration_ms": track.duration_ms,
                        }
                        if getattr(result, "skipped", False):
                            # Ya existía: no se descargó ni se reescribió. Se
                            # cuenta aparte para que «Completado» no mienta.
                            item["status"], item["percent"] = "skipped", 100.0
                            self._state.download_skipped.append(entry)
                        else:
                            item["status"], item["percent"] = "done", 100.0
                            self._state.download_done.append(entry)
                    else:
                        item["status"], item["error"] = "failed", error or "Error desconocido"
                        self._state.download_failed.append(
                            {
                                "id": track.id,
                                "name": track.name,
                                "artist": track.artist,
                                "album": track.album,
                                "platform": track.platform,
                                "duration_ms": track.duration_ms,
                                "isrc": track.isrc,
                                "error": error or "Error desconocido",
                                "format": fmt,
                                # Vídeo que falló: el panel lo marca para no
                                # ofrecer otra vez el mismo candidato.
                                "video_id": str(getattr(result, "video_id", "") or ""),
                                "_track": track,
                            }
                        )
                        self._state._log(
                            f"[ERROR] ✗ Descarga · {track.name[:42]}: {error[:90]}"
                        )
                    self._state.download_progress += 1
                    self._state.notify()

        try:
            await asyncio.gather(*(_one(t) for t in selected))
            cancelled = cancel.is_set()
            if pause.is_set():
                # Las pistas que el semáforo nunca dejó arrancar también
                # cuentan como pendientes: no se descargaron. Las que sí
                # terminaron (o ya existían) no se repiten al reanudar.
                finished = {
                    str(e.get("id")) for e in (*self._state.download_done, *self._state.download_skipped)
                    if isinstance(e, dict)
                }
                for t in queued:
                    if t.id in finished:
                        continue
                    if t not in self._state._download_pending_tracks:
                        self._state._download_pending_tracks.append(t)
                self._state._log(
                    f"[INFO] Descargas pausadas · {len(self._state._download_pending_tracks)} pendientes"
                )
            self._state.download_state = TransferState.DONE
            self._collect_download_missing(selected)
            skipped = len(self._state.download_skipped)
            missing = len(self._state.download_missing)
            summary = (
                f"Descargas {'canceladas' if cancelled else 'completas'} · "
                f"OK: {len(self._state.download_done)} · Fallidas: {len(self._state.download_failed)}"
                + (f" · Omitidas: {skipped}" if skipped else "")
                + (f" · Faltantes: {missing}" if missing else "")
            )
            self._state._log(f"[INFO] {summary}")
            if playlist_name:
                self._write_download_manifest(
                    target_dir, playlist_name, platform, selected,
                    write_playlist_manifest, read_playlist_manifest,
                )
        except Exception as exc:  # noqa: BLE001
            self._state.download_state = TransferState.ERROR
            self._state._log(f"[ERROR] Descargas interrumpidas: {exc}")
        finally:
            self._state.download_current = ""
            self._state._download_cancel = None
            # ``_download_pause`` se conserva si la cola quedó pausada: la
            # siguiente ejecución arranca con un evento nuevo.
            self._state._download_pause = None
            self._state.notify()

    def _collect_download_missing(self, selected: list[Track]) -> None:
        """Pistas de la cola que quedaron sin archivo y sin error propio.

        Canciones canceladas o que nunca llegaron a arrancar no son fallos de
        descarga: nadie intentó resolverlas. Se listan aparte para que el
        usuario pueda reintentarlas o rebuscarlas sin confundirlas con un
        error del extractor.
        """
        finished = {
            str(e.get("id")) for e in (*self._state.download_done, *self._state.download_skipped)
            if isinstance(e, dict)
        }
        pending = {id(t) for t in self._state._download_pending_tracks}
        self._state.download_missing = [
            {
                "id": t.id,
                "name": t.name,
                "artist": t.artist,
                "album": t.album,
                "platform": t.platform,
                "duration_ms": t.duration_ms,
                "isrc": t.isrc,
                "src_url": t.source_url,
                "error": "Pausada" if id(t) in pending else "No se intentó",
                "format": self._state.audio_format,
                "_track": t,
            }
            for t in selected
            if t.id not in finished
        ]

    def retry_downloads(self, entries: list[dict], *, audio_format: str = "") -> asyncio.Task | None:
        """Relanza la descarga de pistas fallidas o faltantes.

        Reutiliza la cola normal: conserva carpeta, playlist y plataforma del
        último intento. ``audio_format`` permite probar otro contenedor sin
        tocar la preferencia persistida.

        Args:
            entries: Entradas de ``download_failed``/``download_missing``; se
                acepta cualquier dict con ``_track`` o con los metadatos
                mínimos para reconstruir la pista.
            audio_format: Formato para este reintento; vacío usa el último.

        Returns:
            La tarea creada, o ``None`` si no hay nada que reintentar.
        """
        tracks: list[Track] = []
        for entry in entries or []:
            track = entry.get("_track") if isinstance(entry, dict) else None
            if track is None and isinstance(entry, dict):
                track = Track(
                    id=str(entry.get("id") or ""),
                    name=str(entry.get("name") or ""),
                    artist=str(entry.get("artist") or ""),
                    album=str(entry.get("album") or ""),
                    duration="",
                    img_url="",
                    platform=str(entry.get("platform") or ""),
                    duration_ms=int(entry.get("duration_ms") or 0),
                    isrc=entry.get("isrc") or None,
                    source_url=str(entry.get("src_url") or ""),
                )
            if track is not None:
                tracks.append(track)
        if not tracks:
            return None
        ctx = dict(self._state._download_resume_ctx)
        fmt = (audio_format or ctx.get("format") or self._state.audio_format or "").lower().lstrip(".")
        self._state.download_paused = False
        self._state._log(f"[INFO] Reintentando descargas · {len(tracks)} pistas en {fmt.upper()}")
        return self._state.download_tracks(
            tracks,
            audio_format=fmt,
            destination_dir=ctx.get("destination_dir", ""),
            playlist_name=ctx.get("playlist_name", ""),
            platform=ctx.get("platform", ""),
        )

    async def research_download(self, entry: dict, *, audio_format: str = "") -> dict:
        """Rebusca una pista en YouTube y devuelve los mejores candidatos.

        Sin descargar nada: el usuario ve qué encontró el extractor, con su
        video y duración, antes de decidir. Corrige el caso típico de una
        búsqueda que eligió mal el resultado.

        Args:
            entry: Entrada de ``download_failed``/``download_missing``.
            audio_format: Formato de destino para el candidato elegido.

        Returns:
            ``{"query": str, "candidates": [...]}``; ``candidates`` puede ir
            vacío y entonces ``error`` explica por qué.
        """
        from engine.downloader import search_candidates

        track = entry.get("_track") if isinstance(entry, dict) else None
        if track is None:
            track = Track(
                id=str((entry or {}).get("id") or ""),
                name=str((entry or {}).get("name") or ""),
                artist=str((entry or {}).get("artist") or ""),
                album=str((entry or {}).get("album") or ""),
                duration="", img_url="",
                platform=str((entry or {}).get("platform") or ""),
                duration_ms=int((entry or {}).get("duration_ms") or 0),
                isrc=(entry or {}).get("isrc") or None,
                source_url=str((entry or {}).get("src_url") or ""),
            )
        found = await asyncio.to_thread(search_candidates, track)
        found["format"] = (audio_format or self._state.audio_format or "").lower().lstrip(".")
        found["track"] = track
        return found

    def download_candidate(
        self, track: Track, video_id: str, *, audio_format: str = "",
    ) -> asyncio.Task | None:
        """Descarga un candidato concreto elegido por el usuario.

        Comparte carpeta, playlist y plataforma del último intento; solo
        cambia el video y, opcionalmente, el contenedor.
        """
        if not video_id:
            return None
        ctx = dict(self._state._download_resume_ctx)
        fmt = (audio_format or ctx.get("format") or self._state.audio_format or "").lower().lstrip(".")
        self._state._log(f"[INFO] Descargando candidato {video_id} · {track.name[:42]} en {fmt.upper()}")
        return self._download_video_task(track, video_id, fmt, ctx)

    def _download_video_task(
        self, track: Track, video_id: str, fmt: str, ctx: dict,
    ) -> asyncio.Task | None:
        """Cola de una sola pista con ``video_id`` ya resuelto."""
        if self._state._download_task is not None and not self._state._download_task.done():
            return self._state._download_task
        self._state._download_task = asyncio.create_task(
            self._run_downloads(
                [track],
                audio_format=fmt,
                destination_dir=ctx.get("destination_dir", ""),
                playlist_name=ctx.get("playlist_name", ""),
                platform=ctx.get("platform", ""),
                pinned_video_id=video_id,
            )
        )
        return self._state._download_task

    def _write_download_manifest(
        self, target_dir, playlist_name: str, platform: str,
        selected: list[Track], writer, reader=None,
    ) -> None:
        """Actualiza ``playlist.json`` fusionando pistas nuevas sin borrar el resto.

        Conserva el orden previo cuando se descarga una canción suelta o un lote
        parcial dentro de una playlist ya existente, y enriquece las entradas
        nuevas o descargadas con ``id``, ``isrc``, ``duration_ms`` y ``path``.
        """
        try:
            target_resolved = os.path.normcase(os.path.realpath(str(target_dir)))
        except OSError:
            target_resolved = ""

        def _in_target_dir(path_str: str) -> bool:
            if not path_str or not target_resolved:
                return False
            try:
                file_resolved = os.path.normcase(os.path.realpath(path_str))
                return os.path.commonpath([target_resolved, file_resolved]) == target_resolved
            except (OSError, ValueError):
                return False

        new_entries = [
            {
                "position": i,
                "id": t.id,
                "name": t.name,
                "artist": t.artist,
                "album": t.album,
                "platform": t.platform,
                "isrc": t.isrc,
                "duration_ms": t.duration_ms,
                "path": t.source_path if _in_target_dir(t.source_path or "") else "",
            }
            for i, t in enumerate(selected, 1)
        ]

        def _entry_key(entry: dict) -> tuple[str, str]:
            eid = str(entry.get("id") or "").strip()
            if eid:
                return ("id", eid)
            name = str(entry.get("name") or entry.get("title") or "").strip().lower()
            artist = str(entry.get("artist") or "").strip().lower()
            return ("pair", f"{name}::{artist}")

        existing_raw = reader(target_dir) if callable(reader) else {}
        existing_list = existing_raw.get("tracks") if isinstance(existing_raw, dict) else None
        if isinstance(existing_list, list) and existing_list:
            merged: list[dict] = [dict(e) for e in existing_list if isinstance(e, dict)]
            idx_by_key = {_entry_key(e): idx for idx, e in enumerate(merged)}
            for entry in new_entries:
                key = _entry_key(entry)
                idx = idx_by_key.get(key)
                if idx is None and key[0] == "id":
                    pair_key = (
                        "pair",
                        f"{str(entry.get('name') or '').strip().lower()}::{str(entry.get('artist') or '').strip().lower()}",
                    )
                    idx = idx_by_key.get(pair_key)
                if idx is not None:
                    cur = merged[idx]
                    for field in ("id", "name", "artist", "album", "platform", "isrc", "duration_ms"):
                        if entry.get(field) and not cur.get(field):
                            cur[field] = entry[field]
                    if entry.get("path"):
                        cur["path"] = entry["path"]
                else:
                    idx_by_key[key] = len(merged)
                    merged.append(entry)
            for pos, entry in enumerate(merged, 1):
                entry["position"] = pos
            entries = merged
        else:
            entries = new_entries

        written = writer(
            target_dir, name=playlist_name, platform=platform, entries=entries
        )
        if written:
            self._state._log(f"[INFO] Manifiesto de playlist: {written}")
        else:
            self._state._log("[WARN] No se pudo escribir el manifiesto de la playlist")
