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
        self._active_session: dict | None = None
        # Contexto de descarga por pista (formato, carpeta, playlist y
        # plataforma). Una sesión puede mezclar pistas de varias playlists, así
        # que reanudar y reintentar no pueden depender del contexto inicial.
        self._track_ctx: dict[int, dict] = {}

    def _resolve_fmt_and_dir(
        self,
        audio_format: str,
        destination_dir: str,
        playlist_name: str,
        platform: str,
    ):
        from core.config import AUDIO_BITRATES, DEFAULT_AUDIO_FORMAT
        from engine.downloader import resolve_download_dir, resolve_playlist_dir

        fmt = (audio_format or DEFAULT_AUDIO_FORMAT).lower().lstrip(".")
        if fmt not in AUDIO_BITRATES:
            fmt = DEFAULT_AUDIO_FORMAT
        if playlist_name:
            target_dir = resolve_playlist_dir(
                destination_dir or None, playlist_name, platform
            )
        else:
            target_dir = resolve_download_dir(destination_dir or None)
        return fmt, target_dir

    def _enqueue_into_session(
        self,
        session: dict,
        tracks: list[Track],
        *,
        audio_format: str = "",
        destination_dir: str = "",
        playlist_name: str = "",
        platform: str = "",
        pinned_video_id: str = "",
        is_initial: bool = False,
    ) -> list[asyncio.Future]:
        loop = asyncio.get_running_loop()
        fmt, target_dir = self._resolve_fmt_and_dir(
            audio_format, destination_dir, playlist_name, platform
        )
        batch_futures: list[asyncio.Future] = []
        added_count = 0
        for t in tracks:
            tid = id(t)
            existing_fut = session["track_futures"].get(tid)
            existing_item = session["items"].get(tid)
            if (
                existing_fut is not None
                and not existing_fut.done()
                and existing_item is not None
                and existing_item.get("status") in ("queued", "running")
            ):
                # Mismo objeto Track: el future ya es suyo, no hay nada que
                # propagar al terminar.
                batch_futures.append(existing_fut)
                continue
            if not pinned_video_id:
                t_key = (
                    str(t.id or "").strip(),
                    str(t.name or "").strip().lower(),
                    str(t.artist or "").strip().lower(),
                )
                dup_tid = session["active_keys"].get(t_key) if any(t_key) else None
                if dup_tid is not None:
                    dup_fut = session["track_futures"].get(dup_tid)
                    dup_item = session["items"].get(dup_tid)
                    if (
                        dup_fut is not None
                        and not dup_fut.done()
                        and dup_item is not None
                        and dup_item.get("status") in ("queued", "running")
                    ):
                        # Otro objeto Track con la misma canción (por ejemplo al
                        # recargar el detalle): comparte la descarga, pero
                        # necesita recibir el source_path cuando acabe.
                        session["alias_tracks"].setdefault(dup_tid, []).append(t)
                        batch_futures.append(dup_fut)
                        continue
                if any(t_key):
                    session["active_keys"][t_key] = tid

            self._track_ctx[tid] = {
                "format": fmt,
                "destination_dir": str(destination_dir or ""),
                "playlist_name": playlist_name,
                "platform": platform,
            }
            fut = loop.create_future()
            session["track_futures"][tid] = fut
            item = {
                "id": t.id,
                "title": f"{t.artist} — {t.name}",
                "status": "queued",
                "percent": 0.0,
                "error": "",
            }
            session["items"][tid] = item
            self._state.download_items.append(item)
            session["queued"].append(t)
            if playlist_name:
                grp_key = (str(target_dir), playlist_name, platform)
                session["manifest_groups"].setdefault(grp_key, (target_dir, []))[1].append(t)
            self._state.download_total += 1
            session["pending_jobs"].append(
                (t, target_dir, fmt, playlist_name, platform, pinned_video_id, destination_dir)
            )
            batch_futures.append(fut)
            added_count += 1

        if not is_initial and added_count > 0:
            self._state._log(
                f"[INFO] Cola de descargas · +{added_count} pista(s) encolada(s) "
                f"(total: {self._state.download_total})"
            )
            session["wake_event"].set()
            self._state.notify()
        return batch_futures

    def _start_or_enqueue(
        self,
        tracks: list[Track],
        *,
        audio_format: str = "",
        destination_dir: str = "",
        playlist_name: str = "",
        platform: str = "",
        pinned_video_id: str = "",
    ) -> asyncio.Task:
        selected = list(tracks or [])
        if not selected:
            if self._state._download_task is not None and not self._state._download_task.done():
                return self._state._download_task
            self._state._download_task = asyncio.create_task(
                self._run_downloads(
                    [],
                    audio_format=audio_format,
                    destination_dir=destination_dir,
                    playlist_name=playlist_name,
                    platform=platform,
                    pinned_video_id=pinned_video_id,
                )
            )
            return self._state._download_task

        if (
            self._state._download_task is not None
            and not self._state._download_task.done()
            and self._active_session is not None
        ):
            session = self._active_session
            if self._session_is_closing(session):
                # Pausada o cancelada: sus pistas nuevas saldrían como
                # «Pausada» o «Cancelada» sin intentarse, y al cerrar pisaría
                # el estado de la sesión siguiente. Se espera y se abre otra.
                return self._start_after_previous(
                    selected,
                    audio_format=audio_format,
                    destination_dir=destination_dir,
                    playlist_name=playlist_name,
                    platform=platform,
                    pinned_video_id=pinned_video_id,
                )
            batch_futures = self._enqueue_into_session(
                session,
                selected,
                audio_format=audio_format or self._state.audio_format,
                destination_dir=destination_dir,
                playlist_name=playlist_name,
                platform=platform,
                pinned_video_id=pinned_video_id,
                is_initial=False,
            )
            return self._build_batch_wait_task(session, batch_futures)

        fmt, target_dir = self._resolve_fmt_and_dir(
            audio_format, destination_dir, playlist_name, platform
        )
        self._state.audio_format      = fmt
        self._state.download_dir      = str(target_dir)
        self._state.download_state    = TransferState.RUNNING
        self._state.download_progress = 0
        self._state.download_total    = 0
        self._state.download_current  = ""
        self._state.download_done     = []
        self._state.download_skipped  = []
        self._state.download_failed   = []
        self._state.download_missing  = []
        self._state.download_items    = []
        self._state._download_cancel  = asyncio.Event()
        self._state._download_pause   = asyncio.Event()
        self._state.download_paused   = False
        self._state._download_pending_tracks = []
        self._state._download_resume_ctx = {
            "format": fmt,
            "destination_dir": str(destination_dir or ""),
            "playlist_name": playlist_name,
            "platform": platform,
        }
        session = {
            "items": {},
            "track_futures": {},
            "alias_tracks": {},
            "active_keys": {},
            "queued": [],
            "pending_jobs": [],
            "manifest_groups": {},
            "wake_event": asyncio.Event(),
            "cancel": self._state._download_cancel,
            "pause": self._state._download_pause,
            "initial_fmt": fmt,
            "initial_target_dir": target_dir,
        }
        self._active_session = session
        batch_futures = self._enqueue_into_session(
            session,
            selected,
            audio_format=fmt,
            destination_dir=destination_dir,
            playlist_name=playlist_name,
            platform=platform,
            pinned_video_id=pinned_video_id,
            is_initial=True,
        )
        self._state._download_task = asyncio.create_task(
            self._run_downloads(
                selected,
                audio_format=fmt,
                destination_dir=destination_dir,
                playlist_name=playlist_name,
                platform=platform,
                pinned_video_id=pinned_video_id,
                _session=session,
            )
        )
        return self._build_batch_wait_task(session, batch_futures)

    def _session_is_closing(self, session: dict) -> bool:
        """Una sesión pausada o cancelada no admite encolado en caliente.

        Sus pistas nuevas saldrían marcadas como «Pausada» o «Cancelada» sin
        intentarse: la descarga la hará la sesión siguiente.
        """
        return bool(self._state.download_paused or session["cancel"].is_set())

    def _start_after_previous(self, tracks: list[Track], **kwargs) -> asyncio.Task:
        """Espera a que la sesión anterior cierre y arranca una nueva.

        La sesión pausada o cancelada sigue viva unos instantes mientras
        yt-dlp aborta. Abrir otra encima compartiría ``download_items``,
        ``download_state`` y los eventos de cancelación, y la vieja los
        pisaría al terminar. Si la anterior estaba pausada, la nueva cola
        nace pausada para no descargar sin que el usuario lo pida.
        """
        previous = self._state._download_task
        was_paused = bool(self._state.download_paused)

        async def _after_previous() -> None:
            if previous is not None and not previous.done():
                try:
                    await previous
                except Exception:
                    pass
            self._state._download_task = None
            task = self._start_or_enqueue(tracks, **kwargs)
            if was_paused and self._state._download_pause is not None:
                self._state.download_paused = True
                self._state._download_pause.set()
                self._state.notify()
            try:
                await task
            except Exception:
                pass

        return asyncio.create_task(_after_previous())

    def _ctx_for(self, track: Track) -> dict:
        """Contexto de descarga de una pista, con la sesión inicial de respaldo."""
        ctx = self._track_ctx.get(id(track))
        if ctx:
            return dict(ctx)
        return dict(self._state._download_resume_ctx)

    def _group_by_ctx(self, tracks: list[Track]) -> list[tuple[dict, list[Track]]]:
        """Agrupa pistas por contexto, conservando el orden de llegada.

        Una sesión puede mezclar playlists; reanudar y reintentar tienen que
        devolver cada pista a su carpeta y a su manifiesto.
        """
        groups: list[tuple[dict, list[Track]]] = []
        index: dict[tuple, int] = {}
        for track in tracks:
            ctx = self._ctx_for(track)
            key = (
                ctx.get("format", ""),
                ctx.get("destination_dir", ""),
                ctx.get("playlist_name", ""),
                ctx.get("platform", ""),
            )
            pos = index.get(key)
            if pos is None:
                index[key] = len(groups)
                groups.append((ctx, [track]))
            else:
                groups[pos][1].append(track)
        return groups

    def _build_batch_wait_task(
        self, session: dict, batch_futures: list[asyncio.Future]
    ) -> asyncio.Task:
        async def _wait_batch() -> None:
            if batch_futures:
                await asyncio.gather(*batch_futures, return_exceptions=True)
            if not session["pending_jobs"] and all(
                f.done() for f in session["track_futures"].values()
            ):
                dl_task = self._state._download_task
                if dl_task is not None and not dl_task.done():
                    try:
                        await dl_task
                    except Exception:
                        pass

        return asyncio.create_task(_wait_batch())

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
        """Lanza o encola la descarga de una lista arbitraria de pistas.

        Si ya hay una sesión de descarga activa, encola las pistas nuevas en
        caliente sobre el mismo semáforo de concurrencia (``DOWNLOAD_CONCURRENCY``)
        y devuelve una tarea que se completa en cuanto este lote termina.
        """
        return self._start_or_enqueue(
            list(tracks),
            audio_format=audio_format,
            destination_dir=destination_dir,
            playlist_name=playlist_name,
            platform=platform,
        )

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
        self._state.download_paused = False
        if not pending:
            self._state._log("[WARN] No hay pistas pendientes para reanudar")
            self._state.notify()
            return None
        self._state._log(f"[INFO] Reanudando descargas · {len(pending)} pistas pendientes")
        groups = self._group_by_ctx(pending)

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
            # Un grupo por carpeta y playlist: el primero abre la sesión y el
            # resto entra en caliente con su propio contexto.
            tasks = [
                self._state.download_tracks(
                    tracks,
                    audio_format=ctx.get("format", ""),
                    destination_dir=ctx.get("destination_dir", ""),
                    playlist_name=ctx.get("playlist_name", ""),
                    platform=ctx.get("platform", ""),
                )
                for ctx, tracks in groups
            ]
            for task in tasks:
                try:
                    await task
                except Exception:
                    pass

        return asyncio.create_task(_resume_after_pause())

    async def _run_downloads(
        self, selected: list[Track], *, audio_format: str = "", destination_dir: str = "",
        playlist_name: str = "", platform: str = "", pinned_video_id: str = "",
        _session: dict | None = None,
    ) -> None:
        """Ejecuta la cola de descargas con concurrencia acotada y encolado dinámico."""
        from core.config import (
            AUDIO_BITRATES,
            DEFAULT_AUDIO_FORMAT,
            DOWNLOAD_CONCURRENCY,
        )
        from engine.downloader import (
            download_audio, ffmpeg_available, read_playlist_manifest,
            resolve_download_dir, resolve_playlist_dir, write_playlist_manifest,
        )

        if not selected and (_session is None or not _session.get("queued")):
            return

        if _session is None:
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
            self._state.download_total    = 0
            self._state.download_current  = ""
            self._state.download_done     = []
            self._state.download_skipped  = []
            self._state.download_failed   = []
            self._state.download_missing  = []
            self._state.download_items    = []
            self._state._download_cancel  = asyncio.Event()
            self._state._download_pause   = asyncio.Event()
            self._state.download_paused   = False
            self._state._download_pending_tracks = []
            self._state._download_resume_ctx = {
                "format": fmt,
                "destination_dir": str(destination_dir or ""),
                "playlist_name": playlist_name,
                "platform": platform,
            }
            session = {
                "items": {},
                "track_futures": {},
                "active_keys": {},
                "queued": [],
                "pending_jobs": [],
                "manifest_groups": {},
                "wake_event": asyncio.Event(),
                "cancel": self._state._download_cancel,
                "pause": self._state._download_pause,
                "initial_fmt": fmt,
                "initial_target_dir": target_dir,
            }
            self._active_session = session
            self._enqueue_into_session(
                session,
                selected,
                audio_format=fmt,
                destination_dir=destination_dir,
                playlist_name=playlist_name,
                platform=platform,
                pinned_video_id=pinned_video_id,
                is_initial=True,
            )
        else:
            session = _session
            fmt = session["initial_fmt"]
            target_dir = session["initial_target_dir"]

        items = session["items"]
        queued = session["queued"]
        cancel = session["cancel"]
        pause = session["pause"]

        # Sin FFmpeg toda la cola fallaría igual pista por pista. Se marca de
        # una vez para que la UI muestre un solo diagnóstico accionable.
        if not ffmpeg_available():
            message = "FFmpeg no está instalado o no está en PATH"
            for item in self._state.download_items:
                item["status"] = "failed"
                item["error"] = message
            self._state.download_failed = [
                {
                    "id": t.id, "name": t.name, "artist": t.artist,
                    "album": t.album, "platform": t.platform,
                    "duration_ms": t.duration_ms, "isrc": t.isrc,
                    "error": message, "format": fmt, "_track": t,
                }
                for t in queued
            ]
            self._state.download_progress = self._state.download_total
            self._state.download_state    = TransferState.DONE
            self._state._log(f"[ERROR] Descargas canceladas · {message}")
            for fut in session["track_futures"].values():
                if not fut.done():
                    fut.set_result(False)
            if self._active_session is session:
                self._active_session = None
            self._state.notify()
            return

        self._state._log(
            f"[INFO] Descargas · {len(queued)} pistas → {fmt.upper()} en {target_dir}"
        )
        self._state.notify()

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

        local_folders_arg: tuple[str, ...] = ()
        try:
            from services import settings_store
            local_folders_arg = tuple(settings_store.get_local_folders())
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

        async def _one(
            track: Track,
            job_target_dir,
            job_fmt: str,
            job_playlist_name: str,
            job_platform: str,
            job_pinned_video_id: str,
            job_dl_dir_arg: str,
        ) -> None:
            item = items[id(track)]
            fut = session["track_futures"].get(id(track))

            def _hook(info: dict) -> None:
                if info.get("status") != "downloading":
                    return
                total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                if total:
                    item["percent"] = min(95.0, 100.0 * (info.get("downloaded_bytes") or 0) / total)
                    loop.call_soon_threadsafe(_push)

            try:
                async with semaphore:
                    if cancel.is_set():
                        item["status"] = "failed"
                        item["error"] = "Cancelada"
                        return
                    if pause.is_set():
                        item["status"] = "paused"
                        item["error"] = "Pausada"
                        if track not in self._state._download_pending_tracks:
                            self._state._download_pending_tracks.append(track)
                        return
                    if not job_pinned_video_id and not (track.source_path and os.path.isfile(track.source_path)):
                        try:
                            from services import settings_store
                            from services.local_index import match_tracks_against_library

                            await asyncio.to_thread(
                                match_tracks_against_library,
                                [track],
                                download_dir=job_dl_dir_arg or settings_store.get_download_dir(),
                                local_folders=local_folders_arg or settings_store.get_local_folders(),
                                playlist_name=job_playlist_name,
                                platform=job_platform,
                            )
                        except Exception:
                            pass
                    if not job_pinned_video_id and track.source_path and os.path.isfile(track.source_path):
                        async with lock:
                            item["status"], item["percent"] = "skipped", 100.0
                            self._state.download_skipped.append(
                                {
                                    "id": track.id,
                                    "name": track.name,
                                    "artist": track.artist,
                                    "path": track.source_path,
                                    "video_id": "",
                                    "format": job_fmt,
                                    "duration_ms": track.duration_ms,
                                }
                            )
                            self._state.download_progress += 1
                            if job_playlist_name:
                                self._write_download_manifest(
                                    job_target_dir, job_playlist_name, job_platform,
                                    [track], write_playlist_manifest, read_playlist_manifest,
                                )
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
                            destination_dir=job_target_dir,
                            audio_format=job_fmt,
                            progress_hook=_hook,
                            artwork_fetcher=artwork_fetcher,
                            skip_existing=True,
                            pause_event=pause,
                            video_id=job_pinned_video_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - nunca debe tumbar la cola
                        result = None
                        error = str(exc)
                    else:
                        error = getattr(result, "error", "") or ""

                    from engine.downloader import DOWNLOAD_PAUSED as _PAUSED_CODE

                    if result is not None and result.code == _PAUSED_CODE:
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
                                "format": job_fmt,
                                "duration_ms": track.duration_ms,
                            }
                            if getattr(result, "skipped", False):
                                item["status"], item["percent"] = "skipped", 100.0
                                self._state.download_skipped.append(entry)
                            else:
                                item["status"], item["percent"] = "done", 100.0
                                self._state.download_done.append(entry)
                            if job_playlist_name:
                                self._write_download_manifest(
                                    job_target_dir, job_playlist_name, job_platform,
                                    [track], write_playlist_manifest, read_playlist_manifest,
                                )
                            # Otros objetos ``Track`` de la misma canción (el
                            # detalle recargado) reciben también el archivo.
                            for alias in session["alias_tracks"].get(id(track), ()):
                                alias.source_path = result.path
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
                                    "format": job_fmt,
                                    "video_id": str(getattr(result, "video_id", "") or ""),
                                    "_track": track,
                                }
                            )
                            self._state._log(
                                f"[ERROR] ✗ Descarga · {track.name[:42]}: {error[:90]}"
                            )
                        self._state.download_progress += 1
                        self._state.notify()
            finally:
                if fut is not None and not fut.done():
                    fut.set_result(True)

        try:
            in_flight: set[asyncio.Task] = set()
            while session["pending_jobs"] or in_flight:
                while session["pending_jobs"]:
                    job = session["pending_jobs"].pop(0)
                    t_task = asyncio.create_task(_one(*job))
                    in_flight.add(t_task)
                    t_task.add_done_callback(in_flight.discard)
                if in_flight:
                    wake_task = asyncio.create_task(session["wake_event"].wait())
                    await asyncio.wait(
                        [*in_flight, wake_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not wake_task.done():
                        wake_task.cancel()
                    session["wake_event"].clear()

            cancelled = cancel.is_set()
            if pause.is_set():
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
            if self._active_session is session:
                self._state.download_state = TransferState.DONE
            self._collect_download_missing(queued)
            skipped = len(self._state.download_skipped)
            missing = len(self._state.download_missing)
            summary = (
                f"Descargas {'canceladas' if cancelled else 'completas'} · "
                f"OK: {len(self._state.download_done)} · Fallidas: {len(self._state.download_failed)}"
                + (f" · Omitidas: {skipped}" if skipped else "")
                + (f" · Faltantes: {missing}" if missing else "")
            )
            self._state._log(f"[INFO] {summary}")
            for (_, grp_pl_name, grp_plat), (grp_dir, grp_tracks) in session["manifest_groups"].items():
                if grp_pl_name and grp_tracks:
                    self._write_download_manifest(
                        grp_dir, grp_pl_name, grp_plat, grp_tracks,
                        write_playlist_manifest, read_playlist_manifest,
                    )
        except Exception as exc:  # noqa: BLE001
            if self._active_session is session:
                self._state.download_state = TransferState.ERROR
            self._state._log(f"[ERROR] Descargas interrumpidas: {exc}")
        finally:
            if self._active_session is session:
                self._active_session = None
                self._state.download_current = ""
            for fut in session["track_futures"].values():
                if not fut.done():
                    fut.set_result(False)
            # Solo se limpian los eventos de esta sesión: si otra ya arrancó,
            # los suyos son otros objetos y borrarlos dejaba la cola nueva sin
            # pausa ni cancelación.
            if self._state._download_cancel is session["cancel"]:
                self._state._download_cancel = None
            if self._state._download_pause is session["pause"]:
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
        self._state.download_paused = False
        groups = self._group_by_ctx(tracks)
        base_fmt = (audio_format or self._state.audio_format or "").lower().lstrip(".")
        formats = {
            str(ctx.get("format") or base_fmt).upper() for ctx, _ in groups
        }
        detail = f" en {next(iter(formats))}" if len(formats) == 1 and base_fmt else ""
        self._state._log(
            f"[INFO] Reintentando descargas · {len(tracks)} pistas{detail}"
        )

        # Se encola un grupo por carpeta y playlist. La primera llamada abre la
        # sesión y las siguientes entran en caliente con su propio contexto, así
        # que el reintento no devuelve todas las pistas a la carpeta de la
        # primera.
        tasks = [
            self._state.download_tracks(
                group,
                audio_format=(
                    audio_format or ctx.get("format") or base_fmt
                ).lower().lstrip("."),
                destination_dir=ctx.get("destination_dir", ""),
                playlist_name=ctx.get("playlist_name", ""),
                platform=ctx.get("platform", ""),
            )
            for ctx, group in groups
        ]
        return tasks[0] if tasks else None

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
        ctx = self._ctx_for(track)
        fmt = (audio_format or ctx.get("format") or self._state.audio_format or "").lower().lstrip(".")
        self._state._log(f"[INFO] Descargando candidato {video_id} · {track.name[:42]} en {fmt.upper()}")
        return self._download_video_task(track, video_id, fmt, ctx)

    def _download_video_task(
        self, track: Track, video_id: str, fmt: str, ctx: dict,
    ) -> asyncio.Task | None:
        """Cola o encolado en caliente de una pista con ``video_id`` ya resuelto."""
        return self._start_or_enqueue(
            [track],
            audio_format=fmt,
            destination_dir=ctx.get("destination_dir", ""),
            playlist_name=ctx.get("playlist_name", ""),
            platform=ctx.get("platform", ""),
            pinned_video_id=video_id,
        )

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
