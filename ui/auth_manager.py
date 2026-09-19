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
    run_preflight,
)
from ui.config_wizard import ConfigWizard

class AuthManager:
    """
    High-level auth coordinator used by app.py.

    1. run_startup_check()        — parallel pre-flight on all platforms.
    2. open_wizard(platform)      — opens ConfigWizard (routes to correct tab).
    3. refresh_session_icons()    — revalidates and updates UI icons.
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
        """Parallel pre-flight on all platforms."""
        return await run_preflight()

    def ingest_preflight_results(self, results: list[PreFlightResult]) -> None:
        """Cache results, update AppState auth flags and notify the UI."""
        self._last_results = results
        self._sync_auth_ui_state(results)
        self.state.notify()

    async def refresh_session_icons(self) -> list[PreFlightResult]:
        """Re-run check_all_sessions and push results to the UI icons."""
        results = await self.check_all_sessions()
        self.ingest_preflight_results(results)
        return results

    def _sync_auth_ui_state(self, results: list[PreFlightResult]) -> None:
        for r in results:
            self.state.auth_session_ok[r.platform]   = r.ok
            self.state.auth_session_hint[r.platform] = (
                "" if r.ok else auth_failure_tooltip(r)
            )

    async def run_startup_check(self) -> list[PreFlightResult]:
        """
        Parallel pre-flight + conditional service init.
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

        await self._init_ing_services(results)

        if need_wizard_for:
            first_fail = need_wizard_for[0]

            async def _open_wizard_deferred() -> None:
                await asyncio.sleep(random.uniform(2.0, 4.0))
                self.open_wizard(first_fail)

            asyncio.create_task(_open_wizard_deferred())

        return results

    async def _init_ing_services(self, results: list[PreFlightResult]) -> None:
        init_methods = {
            "YouTube Music": self.service.init_youtube,
            "Apple Music":   self.service.init_apple,
            "Spotify":       self.service.init_spotify,
        }
        result_by_platform = {result.platform: result for result in results}
        tasks = [
            init_methods[platform]()
            for platform in PLATFORM_ORDER
            if (result_by_platform.get(platform) is not None
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
            "YouTube Music": self.service.init_youtube,
            "Apple Music":   self.service.init_apple,
            "Spotify":       self.service.init_spotify,
        }
        init_results = await asyncio.gather(
            *(init_methods[platform]() for platform in PLATFORM_ORDER),
            return_exceptions=True,
        )
        for plat, res in zip(PLATFORM_ORDER, init_results):
            if res is True:
                self.state_log_fn(f"[SUCCESS] ✓ {plat}: reconectado")
            else:
                self.state_log_fn(f"[ERROR]   – {plat}: {res}")

        chk = await self.check_all_sessions()
        self._last_results = chk
        self._sync_auth_ui_state(chk)
        self.state.notify()

__all__ = ["AuthManager"]
