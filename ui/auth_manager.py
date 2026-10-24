"""Authentication session coordinator for the Flet UI.

Credential storage and pre-flight checks live in services.authentication.
The configuration dialog implementation lives in ui.config_wizard.
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

import flet as ft

from core.config import PLATFORM_ORDER
from services.authentication import (
    PreFlightResult,
    auth_failure_tooltip,
    load_runtime_env,
    read_browser_json,
    run_preflight,
    youtube_preflight_result,
)
from ui.config_wizard import ConfigWizard

class AuthManager:
    """
    High-level auth coordinator used by app.py.

    1. run_startup_check()        — parallel pre-flight on all platforms.
    2. open_wizard(platform)      — opens ConfigWizard (routes to correct tab).
    3. check_sessions()           — validates only when requested by an event.
    4. reload_credentials()       — hot-reloads config/.env and credential JSON files.
    """

    def __init__(self, page: ft.Page, service, state) -> None:
        self.page    = page
        self.service = service

        # Accept AppState or a bound _log method (compatibility)
        if hasattr(state, "notify"):
            self.state        = state
            self.state_log_fn = state._log
        elif callable(state) and getattr(state, "__self__", None) is not None \
                and hasattr(state.__self__, "notify"):
            self.state        = state.__self__
            self.state_log_fn = state
        else:
            raise TypeError(
                "AuthManager(page, service, state): el tercer argumento debe ser "
                "el objeto AppState (p. ej. state), no state._log ni otro valor."
            )

        self._wizard = ConfigWizard(
            page, auth_manager=self, on_saved=self._on_wizard_saved
        )
        self._last_results: list[PreFlightResult] = []
        self._reload_task:  Optional[asyncio.Task] = None  # tracked for hard_cleanup

    # ── Pre-flight / session management ───────────────────────────────

    async def check_all_sessions(self) -> list[PreFlightResult]:
        """Validate all platforms on demand, using the service client for YouTube."""
        return await self.check_sessions(PLATFORM_ORDER)

    async def check_sessions(self, platforms: list[str]) -> list[PreFlightResult]:
        """Validate requested platforms, sharing the production YouTube path."""
        wanted = set(platforms)
        selected = [p for p in PLATFORM_ORDER if p in wanted]
        others = [p for p in selected if p != "YouTube Music"]
        tasks = [run_preflight(others)]
        if "YouTube Music" in selected:
            tasks.append(self._check_youtube_session())
        outputs = await asyncio.gather(*tasks)
        results: list[PreFlightResult] = []
        for output in outputs:
            if isinstance(output, list):
                results.extend(output)
            else:
                results.append(output)
        return sorted(results, key=lambda result: PLATFORM_ORDER.index(result.platform))

    async def _check_youtube_session(self) -> PreFlightResult:
        browser = read_browser_json()
        if not browser.get("Authorization") or not browser.get("Cookie"):
            return youtube_preflight_result(
                False, "config/browser.json: falta Authorization o Cookie"
            )
        if not browser.get("Authorization", "").startswith("SAPISIDHASH"):
            return youtube_preflight_result(
                False, "Authorization no comienza con 'SAPISIDHASH'"
            )
        try:
            ok = await self.service.init_youtube()
            return youtube_preflight_result(ok, self.service.youtube_auth_error)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return youtube_preflight_result(False, str(exc))

    def record_operation_failure(self, platform: str, error: BaseException | str) -> bool:
        """Reflect confirmed YouTube authorization failures, not transient errors."""
        if platform != "YouTube Music":
            return False
        result = youtube_preflight_result(False, str(error))
        if result.code in ("YT_EXPIRED", "YT_FORBIDDEN"):
            self.ingest_preflight_results([result])
            return True
        return False

    def ingest_preflight_results(self, results: list[PreFlightResult]) -> None:
        """Merge results, update AppState auth flags and notify the UI."""
        merged = {r.platform: r for r in self._last_results}
        merged.update({r.platform: r for r in results})
        self._last_results = [
            merged[platform] for platform in PLATFORM_ORDER if platform in merged
        ]
        self._sync_auth_ui_state(results)
        self.state.notify()

    def _sync_auth_ui_state(self, results: list[PreFlightResult]) -> None:
        for r in results:
            if r.platform == "YouTube Music" and not r.ok and r.code in (
                "YT_RATE_LIMITED", "YT_CHECK_FAILED",
            ):
                self.state.auth_session_hint[r.platform] = auth_failure_tooltip(r)
                continue
            self.state.auth_session_ok[r.platform]   = r.ok
            self.state.auth_session_hint[r.platform] = (
                "" if r.ok else auth_failure_tooltip(r)
            )

    async def run_startup_check(self) -> list[PreFlightResult]:
        """
        Expired platforms open the wizard on their respective tab.
        """
        self.state_log_fn("[INFO] Pre-flight: verificando credenciales…")
        results = await self.check_all_sessions()
        self._last_results = results
        self._sync_auth_ui_state(results)

        need_wizard_for: list[str] = []

        for r in results:
            if r.ok:
                self.state_log_fn(f"[INFO]  ✓ {r.platform}: OK")
            elif r.expired:
                need_wizard_for.append(r.platform)
                self.state_log_fn(
                    f"[ERROR] ⚠ {r.platform}: credenciales expiradas — "
                    "actualiza las credenciales en la configuración"
                )
            else:
                self.state_log_fn(f"[WARN]  – {r.platform}: {r.error}")

        await self._init_passing_services(results)

        if need_wizard_for:
            first_fail = need_wizard_for[0]

            async def _open_wizard_deferred() -> None:
                await asyncio.sleep(random.uniform(2.0, 4.0))
                self.open_wizard(first_fail)

            asyncio.create_task(_open_wizard_deferred())

        return results

    async def _init_passing_services(self, results: list[PreFlightResult]) -> None:
        init_methods = {
            "YouTube Music": self.service.init_youtube,
            "Apple Music":   self.service.init_apple,
            "Spotify":       self.service.init_spotify,
        }
        result_by_platform = {result.platform: result for result in results}
        tasks = [
            init_methods[platform]()
            for platform in PLATFORM_ORDER
            if platform != "YouTube Music"
            and (result_by_platform.get(platform) is not None
                and result_by_platform[platform].ok)
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── Wizard ─────────────────────────────────────────────────────────

    def open_wizard(self, platform: Optional[str] = None) -> None:
        """
        Open the ConfigWizard.
        If *platform* is given, the corresponding tab is shown first.
        """
        def _go() -> None:
            try:
                self._wizard.open(self._last_results or None, initial_platform=platform)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                self.state_log_fn(f"[ERROR] Wizard: {ex}")

        try:
            asyncio.get_running_loop().call_soon(_go)
        except RuntimeError:
            _go()

    def _on_wizard_saved(self) -> None:
        """Called by ConfigWizard after the user clicks 'Guardar y Aplicar'."""
        self._reload_task = asyncio.create_task(self.reload_credentials())

    # ── Hot-reload ─────────────────────────────────────────────────────

    async def reload_credentials(self) -> None:
        """
        Hot-reload credentials from disk and reinitialise all services.
        No process restart required.
        """
        self.state_log_fn("[INFO] Recargando credenciales…")
        load_runtime_env(override=True)

        init_methods = {
            "Apple Music":   self.service.init_apple,
            "Spotify":       self.service.init_spotify,
        }
        init_platforms = [platform for platform in PLATFORM_ORDER if platform in init_methods]
        init_results = await asyncio.gather(
            *(init_methods[platform]() for platform in init_platforms),
            return_exceptions=True,
        )
        for plat, res in zip(init_platforms, init_results):
            if res is True:
                self.state_log_fn(f"[SUCCESS] ✓ {plat}: reconectado")
            else:
                self.state_log_fn(f"[ERROR]   – {plat}: {res}")

        chk = await self.check_all_sessions()
        self._last_results = chk
        self._sync_auth_ui_state(chk)
        self.state.notify()

__all__ = ["AuthManager"]
