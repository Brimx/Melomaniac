"""Authentication session coordinator for the Flet UI.

Credential storage and pre-flight checks live in services.authentication.
The configuration dialog implementation lives in ui.config_wizard.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import flet as ft

from core.config import PLATFORM_ORDER
from services import browser_session
from services.authentication import (
    PreFlightResult,
    auth_failure_tooltip,
    load_runtime_env,
    preflight_result,
)
from ui.config_wizard import ConfigWizard


class AuthManager:
    """
    High-level auth coordinator used by app.py.

    1. run_startup_check()        — parallel pre-flight on all platforms.
    2. open_wizard(platform)      — opens ConfigWizard (routes to correct tab).
    3. check_sessions()           — validates only when requested by an event.
    4. reload_credentials()       — clears browser cache, reloads env/files, and re-runs checks.
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
        """Validate all platforms on demand through MusicApiService.init_*."""
        return await self.check_sessions(PLATFORM_ORDER)

    async def check_sessions(self, platforms: list[str]) -> list[PreFlightResult]:
        """Validate requested platforms using the three init_* methods."""
        wanted = set(platforms)
        selected = [p for p in PLATFORM_ORDER if p in wanted]
        check_map = {
            "YouTube Music": self._check_youtube_session,
            "Apple Music":   self._check_apple_session,
            "Spotify":       self._check_spotify_session,
        }
        tasks = [check_map[p]() for p in selected if p in check_map]
        if not tasks:
            return []
        outputs = await asyncio.gather(*tasks)
        return sorted(outputs, key=lambda result: PLATFORM_ORDER.index(result.platform))

    async def _check_youtube_session(self) -> PreFlightResult:
        try:
            ok = await self.service.init_youtube()
            return preflight_result(
                "YouTube Music",
                ok,
                self.service.youtube_auth_error,
                self.service.youtube_session_source,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return preflight_result("YouTube Music", False, str(exc))

    async def _check_apple_session(self) -> PreFlightResult:
        try:
            ok = await self.service.init_apple()
            return preflight_result(
                "Apple Music",
                ok,
                self.service.apple_auth_error,
                self.service.apple_session_source,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return preflight_result("Apple Music", False, str(exc))

    async def _check_spotify_session(self) -> PreFlightResult:
        try:
            ok = await self.service.init_spotify()
            return preflight_result(
                "Spotify",
                ok,
                self.service.spotify_auth_error,
                self.service.spotify_session_source,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return preflight_result("Spotify", False, str(exc))

    def record_operation_failure(self, platform: str, error: BaseException | str) -> bool:
        """Reflect confirmed YouTube authorization failures, not transient errors."""
        if platform != "YouTube Music":
            return False
        result = preflight_result("YouTube Music", False, str(error))
        if result.code in ("YT_EXPIRED", "YT_FORBIDDEN", "NO_CREDENTIALS"):
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
                f"{r.platform} · {r.source}" if (r.ok and r.source) else ("" if r.ok else auth_failure_tooltip(r))
            )

    async def run_startup_check(self) -> list[PreFlightResult]:
        """Run preflight on startup, log status and update icons without opening the wizard."""
        self.state_log_fn("[INFO] Pre-flight: verificando credenciales…")
        results = await self.check_all_sessions()
        self._last_results = results
        self._sync_auth_ui_state(results)

        for r in results:
            if r.ok:
                src = f" ({r.source})" if r.source else ""
                self.state_log_fn(f"[INFO]  ✓ {r.platform}: OK{src}")
                print(f"[Auth] {r.platform}: OK{src}", flush=True)
            elif r.expired:
                self.state_log_fn(
                    f"[ERROR] ⚠ {r.platform}: {r.error or 'credenciales expiradas'}"
                )
                print(f"[Auth] {r.platform}: FAIL — {r.error}", flush=True)
            else:
                self.state_log_fn(f"[WARN]  – {r.platform}: {r.error}")
                print(f"[Auth] {r.platform}: WARN — {r.error}", flush=True)

        return results

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
        Hot-reload credentials from browser/disk and reinitialise all services.
        No process restart required.
        """
        self.state_log_fn("[INFO] Recargando credenciales…")
        browser_session.clear_cache()
        load_runtime_env(override=True)

        chk = await self.check_all_sessions()
        for r in chk:
            if r.ok:
                src = f" ({r.source})" if r.source else ""
                self.state_log_fn(f"[SUCCESS] ✓ {r.platform}: reconectado{src}")
            else:
                self.state_log_fn(f"[ERROR]   – {r.platform}: {r.error}")

        self._last_results = chk
        self._sync_auth_ui_state(chk)
        self.state.notify()


__all__ = ["AuthManager"]
