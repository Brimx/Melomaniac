"""
services/update_checker.py — Verificación de actualizaciones y releases
══════════════════════════════════════════════════════════════════════
Consulta la API pública de GitHub Releases para comprobar si existen
versiones nuevas de Melomaniac y ofrece recomendaciones de actualización
según cómo esté instalada la aplicación (AppImage, instalador Windows,
ZIP portable o repositorio Git).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Tuple

import httpx

from core.config import APP_VERSION, REPO_URL, RELEASES_API_URL
from services.desktop_integration import get_runtime_environment

logger = logging.getLogger(__name__)


def parse_semver(version_str: str) -> Tuple[int, ...]:
    """Convierte una cadena tipo 'v4.9.0' o '4.10.0-rc1' en tupla numérica (4, 9, 0)."""
    cleaned = version_str.strip().lstrip("vV")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", cleaned)
    if not match:
        return (0, 0, 0)
    parts = match.groups()
    return tuple(int(p or 0) for p in parts)


@dataclass(frozen=True)
class UpdateInfo:
    """Resultado de la comprobación de versiones en GitHub."""
    checked: bool
    has_update: bool
    current_version: str
    latest_version: str
    release_url: str
    release_title: str
    release_notes: str
    download_url: str | None
    asset_name: str | None
    instructions: str
    error_message: str | None = None


def _build_instructions(has_update: bool, latest_ver: str) -> str:
    """Genera instrucciones contextuales para el usuario según su empaquetado."""
    env = get_runtime_environment()

    if not has_update:
        if env.is_appimage:
            return (
                "Tienes la versión más reciente. Al actualizar en el futuro con un nuevo "
                "AppImage, Melomaniac sincronizará tu acceso directo automáticamente."
            )
        if env.is_windows_installer:
            return (
                "Tienes la versión más reciente. Las futuras actualizaciones mediante el "
                "instalador Setup.exe conservarán tus datos y accesos directos."
            )
        return "Tienes la versión más reciente de Melomaniac."

    # Si hay actualización disponible:
    if env.is_appimage:
        return (
            f"Nueva versión {latest_ver} disponible. Descarga el nuevo archivo .AppImage y "
            "colócalo en tu carpeta habitual. Al abrir la nueva versión, Melomaniac "
            "actualizará tu acceso directo del sistema automáticamente sin romper nada."
        )

    if env.is_windows_installer:
        return (
            f"Nueva versión {latest_ver} disponible. Descarga y ejecuta el instalador Setup.exe. "
            "Se instalará sobre la versión actual conservando todas tus listas, ajustes y "
            "los accesos directos del Menú Inicio y Escritorio."
        )

    if env.is_windows_portable:
        return (
            f"Nueva versión {latest_ver} disponible. Descarga el ZIP portable y descomprímelo. "
            "Si cambias la ruta de la carpeta, recuerda abrir Configuración para actualizar "
            "el acceso directo en tu Escritorio."
        )

    if env.is_frozen and env.os_name == "linux":
        return (
            f"Nueva versión {latest_ver} disponible en tar.gz. Descomprime la nueva versión "
            "y ejecuta Melomaniac para refrescar el lanzador del sistema."
        )

    return (
        f"Nueva versión {latest_ver} disponible. Para actualizar el código ejecuta:\n"
        "git pull && pip install -r requirements.txt"
    )


async def check_for_updates(timeout_sec: float = 6.0) -> UpdateInfo:
    """Consulta asíncronamente GitHub Releases para comparar versiones."""
    current_semver = parse_semver(APP_VERSION)
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"Melomaniac/{APP_VERSION} ({REPO_URL})",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            resp = await client.get(RELEASES_API_URL, headers=headers)

            if resp.status_code == 404:
                # El repositorio todavía no tiene releases públicas formalizadas
                return UpdateInfo(
                    checked=True,
                    has_update=False,
                    current_version=APP_VERSION,
                    latest_version=APP_VERSION,
                    release_url=REPO_URL,
                    release_title="Versión actual de desarrollo",
                    release_notes="",
                    download_url=None,
                    asset_name=None,
                    instructions=_build_instructions(False, APP_VERSION),
                    error_message=None,
                )

            if not resp.is_success:
                return UpdateInfo(
                    checked=False,
                    has_update=False,
                    current_version=APP_VERSION,
                    latest_version=APP_VERSION,
                    release_url=f"{REPO_URL}/releases",
                    release_title="",
                    release_notes="",
                    download_url=None,
                    asset_name=None,
                    instructions="No se pudo consultar el servidor de actualizaciones.",
                    error_message=f"HTTP {resp.status_code}",
                )

            data: dict[str, Any] = resp.json()
            tag_name = str(data.get("tag_name", "")).strip()
            latest_semver = parse_semver(tag_name)
            has_update = (latest_semver > current_semver)

            # Buscar asset adecuado para la plataforma
            assets: list[dict[str, Any]] = data.get("assets", [])
            download_url: str | None = None
            asset_name: str | None = None
            env = get_runtime_environment()

            target_ext = ""
            if env.is_appimage or (env.os_name == "linux" and not env.is_frozen):
                target_ext = ".AppImage"
            elif env.os_name == "linux":
                target_ext = ".tar.gz"
            elif env.is_windows_installer:
                target_ext = "-Setup.exe"
            elif env.os_name == "windows":
                target_ext = "-windows-x64.zip"

            if target_ext:
                for asset in assets:
                    name = asset.get("name", "")
                    if target_ext in name:
                        download_url = asset.get("browser_download_url")
                        asset_name = name
                        break

            if not download_url:
                download_url = data.get("html_url") or f"{REPO_URL}/releases"

            release_title = data.get("name") or tag_name or f"v{APP_VERSION}"
            release_body = data.get("body", "")

            # Recortar notas si son excesivamente largas
            if len(release_body) > 600:
                release_body = release_body[:597] + "…"

            clean_tag = tag_name.lstrip("vV") or APP_VERSION

            return UpdateInfo(
                checked=True,
                has_update=has_update,
                current_version=APP_VERSION,
                latest_version=clean_tag,
                release_url=data.get("html_url") or f"{REPO_URL}/releases",
                release_title=release_title,
                release_notes=release_body,
                download_url=download_url,
                asset_name=asset_name,
                instructions=_build_instructions(has_update, clean_tag),
                error_message=None,
            )

    except Exception as exc:
        logger.debug("Fallo al verificar actualizaciones: %s", exc)
        return UpdateInfo(
            checked=False,
            has_update=False,
            current_version=APP_VERSION,
            latest_version=APP_VERSION,
            release_url=f"{REPO_URL}/releases",
            release_title="",
            release_notes="",
            download_url=None,
            asset_name=None,
            instructions="No se pudo conectar con GitHub para comprobar actualizaciones.",
            error_message=str(exc),
        )
