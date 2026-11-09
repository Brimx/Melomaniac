"""core/download_flow.py — Descargas con yt-dlp: cola, pausa, reintentos y manifiesto.

Mixin de `AppState` (core/state.py): los métodos se mezclan en la clase
y comparten su `self`.
"""

from __future__ import annotations

import asyncio
import time

from core.models import Track, TransferState


class DownloadMixin:
    """Descargas con yt-dlp: cola, pausa, reintentos y manifiesto."""

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
        selected = [t for t in self.tracks if t.selected]
        platforms = {t.platform for t in selected if t.platform}
        return self.download_tracks(
            selected,
            audio_format=audio_format, destination_dir=destination_dir,
            playlist_name=self.playlist_name if self.playlist_name else "",
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
        if self._download_task is not None and not self._download_task.done():
            return self._download_task
        self._download_task = asyncio.create_task(
            self._run_downloads(
                list(tracks), audio_format=audio_format, destination_dir=destination_dir,
                playlist_name=playlist_name, platform=platform,
            )
        )
        return self._download_task

    def cancel_downloads(self) -> None:
        """Marca la descarga en curso para que se detenga en la próxima pista."""
        if self._download_cancel is not None:
            self._download_cancel.set()

    def pause_downloads(self) -> None:
        """Pausa la cola, incluida la pista que se está descargando.

        No se limita a esperar a que termine la pista: la aborta desde el
        progress hook y deja el ``.part`` en disco para reanudar desde ahí.
        """
        if self.download_state != TransferState.RUNNING:
            return
        if self._download_pause is not None:
            self._download_pause.set()
        self.download_paused = True
        # La pista activa se marca como pausada en el acto. yt-dlp tarda un
        # momento en abortar, y hasta ahora la fila pasaba por «Error» antes
        # de llegar al estado correcto, que se lee como un fallo real.
        for item in self.download_items:
            if item.get("status") == "running":
                item["status"] = "paused"
                item["error"] = "Pausada"
        self._log("[INFO] Descargas en pausa")
        self.notify()

    def resume_downloads(self) -> asyncio.Task | None:
        """Reanuda la cola pausada desde donde se quedó.

        Relanza las pistas que no terminaron conservando formato, carpeta y
        nombre de playlist. Los archivos ya completos se omiten por
        ``skip_existing`` y la pista interrumpida se reanuda desde su
        ``.part``.
        """
        if not self.download_paused:
            return None
        pending = list(self._download_pending_tracks)
        ctx = dict(self._download_resume_ctx)
        self.download_paused = False
        if not pending:
            self._log("[WARN] No hay pistas pendientes para reanudar")
            self.notify()
            return None
        self._log(f"[INFO] Reanudando descargas · {len(pending)} pistas pendientes")

        async def _resume_after_pause() -> None:
            # El task pausado puede seguir vivo unos instantes mientras las
            # tareas en espera ven el evento. ``download_tracks`` devolvería
            # ese task sin arrancar nada, así que primero se deja terminar.
            previous = self._download_task
            if previous is not None and not previous.done():
                try:
                    await previous
                except Exception:
                    pass
            self._download_task = None
            self.download_tracks(
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
            download_audio, ffmpeg_available, resolve_download_dir, resolve_playlist_dir,
            write_playlist_manifest,
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

        self.audio_format      = fmt
        self.download_dir      = str(target_dir)
        self.download_state    = TransferState.RUNNING
        self.download_progress = 0
        self.download_total    = len(selected)
        self.download_current  = ""
        self.download_done     = []
        self.download_skipped  = []
        self.download_failed   = []
        self.download_missing  = []
        items = {
            id(t): {"id": t.id, "title": f"{t.artist} — {t.name}", "status": "queued",
                    "percent": 0.0, "error": ""}
            for t in selected
        }
        self.download_items    = list(items.values())
        self._download_cancel  = asyncio.Event()
        self._download_pause   = asyncio.Event()
        cancel = self._download_cancel
        pause = self._download_pause
        # Contexto para reanudar exactamente esta cola, no otra.
        self.download_paused = False
        self._download_pending_tracks = []
        self._download_resume_ctx = {
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
            for item in self.download_items:
                item["status"] = "failed"
                item["error"] = message
            # Misma forma que una falla real para que el panel de resultados
            # pueda renderizarlas y reintentarlas sin un caso especial.
            self.download_failed = [
                {
                    "id": t.id, "name": t.name, "artist": t.artist,
                    "album": t.album, "platform": t.platform,
                    "duration_ms": t.duration_ms, "isrc": t.isrc,
                    "error": message, "format": fmt, "_track": t,
                }
                for t in selected
            ]
            self.download_progress = self.download_total
            self.download_state    = TransferState.DONE
            self._log(f"[ERROR] Descargas canceladas · {message}")
            self.notify()
            return

        self._log(
            f"[INFO] Descargas · {len(selected)} pistas → {fmt.upper()} en {target_dir}"
        )
        self.notify()

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
            self._log(f"[WARN] Portadas online no disponibles: {exc}")

        semaphore = asyncio.Semaphore(max(1, DOWNLOAD_CONCURRENCY))
        lock = asyncio.Lock()

        loop = asyncio.get_running_loop()
        last_push = [0.0]

        def _push() -> None:
            now = time.monotonic()
            if now - last_push[0] >= 0.25:
                last_push[0] = now
                self.notify()

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
                    if track not in self._download_pending_tracks:
                        self._download_pending_tracks.append(track)
                    return
                async with lock:
                    item["status"] = "running"
                    self.download_current = f"{track.artist} — {track.name}"[:80]
                    self.notify()
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
                        if track not in self._download_pending_tracks:
                            self._download_pending_tracks.append(track)
                        self.download_progress += 1
                        self.notify()
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
                            self.download_skipped.append(entry)
                        else:
                            item["status"], item["percent"] = "done", 100.0
                            self.download_done.append(entry)
                    else:
                        item["status"], item["error"] = "failed", error or "Error desconocido"
                        self.download_failed.append(
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
                        self._log(
                            f"[ERROR] ✗ Descarga · {track.name[:42]}: {error[:90]}"
                        )
                    self.download_progress += 1
                    self.notify()

        try:
            await asyncio.gather(*(_one(t) for t in selected))
            cancelled = cancel.is_set()
            if pause.is_set():
                # Las pistas que el semáforo nunca dejó arrancar también
                # cuentan como pendientes: no se descargaron. Las que sí
                # terminaron (o ya existían) no se repiten al reanudar.
                finished = {
                    str(e.get("id")) for e in (*self.download_done, *self.download_skipped)
                    if isinstance(e, dict)
                }
                for t in queued:
                    if t.id in finished:
                        continue
                    if t not in self._download_pending_tracks:
                        self._download_pending_tracks.append(t)
                self._log(
                    f"[INFO] Descargas pausadas · {len(self._download_pending_tracks)} pendientes"
                )
            self.download_state = TransferState.DONE
            self._collect_download_missing(selected)
            skipped = len(self.download_skipped)
            missing = len(self.download_missing)
            summary = (
                f"Descargas {'canceladas' if cancelled else 'completas'} · "
                f"OK: {len(self.download_done)} · Fallidas: {len(self.download_failed)}"
                + (f" · Omitidas: {skipped}" if skipped else "")
                + (f" · Faltantes: {missing}" if missing else "")
            )
            self._log(f"[INFO] {summary}")
            if playlist_name:
                self._write_download_manifest(
                    target_dir, playlist_name, platform, selected, write_playlist_manifest
                )
        except Exception as exc:  # noqa: BLE001
            self.download_state = TransferState.ERROR
            self._log(f"[ERROR] Descargas interrumpidas: {exc}")
        finally:
            self.download_current = ""
            self._download_cancel = None
            # ``_download_pause`` se conserva si la cola quedó pausada: la
            # siguiente ejecución arranca con un evento nuevo.
            self._download_pause = None
            self.notify()

    def _collect_download_missing(self, selected: list[Track]) -> None:
        """Pistas de la cola que quedaron sin archivo y sin error propio.

        Canciones canceladas o que nunca llegaron a arrancar no son fallos de
        descarga: nadie intentó resolverlas. Se listan aparte para que el
        usuario pueda reintentarlas o rebuscarlas sin confundirlas con un
        error del extractor.
        """
        finished = {
            str(e.get("id")) for e in (*self.download_done, *self.download_skipped)
            if isinstance(e, dict)
        }
        pending = {id(t) for t in self._download_pending_tracks}
        self.download_missing = [
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
                "format": self.audio_format,
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
        ctx = dict(self._download_resume_ctx)
        fmt = (audio_format or ctx.get("format") or self.audio_format or "").lower().lstrip(".")
        self.download_paused = False
        self._log(f"[INFO] Reintentando descargas · {len(tracks)} pistas en {fmt.upper()}")
        return self.download_tracks(
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
        found["format"] = (audio_format or self.audio_format or "").lower().lstrip(".")
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
        ctx = dict(self._download_resume_ctx)
        fmt = (audio_format or ctx.get("format") or self.audio_format or "").lower().lstrip(".")
        self._log(f"[INFO] Descargando candidato {video_id} · {track.name[:42]} en {fmt.upper()}")
        return self._download_video_task(track, video_id, fmt, ctx)

    def _download_video_task(
        self, track: Track, video_id: str, fmt: str, ctx: dict,
    ) -> asyncio.Task | None:
        """Cola de una sola pista con ``video_id`` ya resuelto."""
        if self._download_task is not None and not self._download_task.done():
            return self._download_task
        self._download_task = asyncio.create_task(
            self._run_downloads(
                [track],
                audio_format=fmt,
                destination_dir=ctx.get("destination_dir", ""),
                playlist_name=ctx.get("playlist_name", ""),
                platform=ctx.get("platform", ""),
                pinned_video_id=video_id,
            )
        )
        return self._download_task

    def _write_download_manifest(
        self, target_dir, playlist_name: str, platform: str,
        selected: list[Track], writer,
    ) -> None:
        """Deja ``playlist.json`` con el orden original de la playlist.

        El orden se toma de ``selected``, no de ``download_done``: esa lista
        se llena a medida que terminan las descargas concurrentes y no
        representa el orden de la lista. Las pistas sin archivo quedan con
        ``path`` vacío para saber qué falta.
        """
        entries = [
            {
                "position": i,
                "name": t.name,
                "artist": t.artist,
                "album": t.album,
                "platform": t.platform,
                "path": t.source_path or "",
            }
            for i, t in enumerate(selected, 1)
        ]
        written = writer(
            target_dir, name=playlist_name, platform=platform, entries=entries
        )
        if written:
            self._log(f"[INFO] Manifiesto de playlist: {written}")
        else:
            self._log("[WARN] No se pudo escribir el manifiesto de la playlist")
