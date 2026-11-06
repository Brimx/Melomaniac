"""
services/desktop_integration.py — Integración con el sistema y accesos directos
═════════════════════════════════════════════════════════════════════════════════
Gestiona la creación, verificación, sincronización y eliminación de lanzadores
y accesos directos en Linux y Windows, adaptándose a cómo se ejecuta la aplicación:

- Linux AppImage: detecta $APPIMAGE y crea el archivo .desktop en
  $XDG_DATA_HOME/applications (o ~/.local/share/applications). Advierte que
  el archivo AppImage debe permanecer en una ruta estable y auto-sincroniza
  el acceso si la ruta cambia tras una actualización.
- Linux Portable (tar.gz o standalone): crea el archivo .desktop apuntando al
  ejecutable extraído.
- Windows Instalado (Inno Setup): detecta si unins000.exe está presente.
  Inno Setup ya administra los accesos en el Menú Inicio y Escritorio.
- Windows Portable (ZIP): ofrece crear el acceso directo (.lnk) en el Escritorio
  mediante PowerShell WScript.Shell, advirtiendo sobre la carpeta de origen.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RuntimeEnvironment:
    """Información sobre la plataforma y el tipo de distribución en ejecución."""
    os_name: str                  # "linux", "windows", "darwin"
    is_frozen: bool               # Compilado standalone (PyInstaller / Flet pack)
    is_appimage: bool             # Corriendo desde paquete AppImage en Linux
    appimage_path: Path | None    # Ruta al archivo .AppImage anfitrión
    is_windows_installer: bool    # Instalado mediante Setup.exe de Inno Setup
    is_windows_portable: bool     # ZIP portable en Windows
    executable_path: Path         # Ejecutable real o script entrypoint
    label: str                    # Etiqueta humana para mostrar en la interfaz


@dataclass(frozen=True)
class ShortcutStatus:
    """Estado actual de la integración de accesos directos en el sistema."""
    supported: bool               # ¿La plataforma admite integración automática?
    installed: bool               # ¿Existe el archivo de acceso directo / lanzador?
    target_path: str | None       # Ruta del ejecutable hacia donde apunta el acceso
    is_current: bool              # ¿Apunta exactamente al ejecutable actual?
    is_broken: bool               # ¿El acceso apunta a un archivo que ya no existe?
    location_path: str | None     # Ruta física del archivo lanzador (.desktop o .lnk)
    summary: str                  # Mensaje resumido para la interfaz
    warning: str | None           # Advertencia sobre estabilidad de ruta si aplica


def get_runtime_environment() -> RuntimeEnvironment:
    """Inspecciona el entorno de ejecución actual."""
    is_frozen = getattr(sys, "frozen", False)
    appimage_raw = os.environ.get("APPIMAGE")
    is_appimage = bool(appimage_raw) and sys.platform.startswith("linux")
    appimage_path = Path(appimage_raw).resolve() if is_appimage and appimage_raw else None

    if sys.platform.startswith("linux"):
        os_name = "linux"
        if is_appimage and appimage_path:
            label = "Linux (AppImage)"
            exe_path = appimage_path
        elif is_frozen:
            label = "Linux (Paquete binario)"
            exe_path = Path(sys.executable).resolve()
        else:
            label = "Linux (Código fuente / desarrollo)"
            exe_path = PROJECT_DIR / "app.py"
        is_win_installer = False
        is_win_portable = False

    elif sys.platform == "win32":
        os_name = "windows"
        exe_path = Path(sys.executable).resolve() if is_frozen else (PROJECT_DIR / "app.py")
        if is_frozen:
            exe_dir = exe_path.parent
            # Inno Setup siempre deposita unins000.exe en la raíz de instalación
            is_win_installer = (exe_dir / "unins000.exe").is_file()
            is_win_portable = not is_win_installer
            label = "Windows (Instalador)" if is_win_installer else "Windows (ZIP portable)"
        else:
            is_win_installer = False
            is_win_portable = False
            label = "Windows (Código fuente / desarrollo)"

    else:
        os_name = sys.platform
        exe_path = Path(sys.executable).resolve() if is_frozen else (PROJECT_DIR / "app.py")
        is_win_installer = False
        is_win_portable = False
        label = f"{sys.platform.capitalize()} (No empaquetado)"

    return RuntimeEnvironment(
        os_name=os_name,
        is_frozen=is_frozen,
        is_appimage=is_appimage,
        appimage_path=appimage_path,
        is_windows_installer=is_win_installer,
        is_windows_portable=is_win_portable,
        executable_path=exe_path,
        label=label,
    )


# ── Rutas estándar ─────────────────────────────────────────────────────────

def get_linux_desktop_path() -> Path:
    """Ruta al archivo .desktop del usuario en Linux ($XDG_DATA_HOME/applications)."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "applications" / "melomaniac.desktop"


def get_linux_icon_path() -> Path:
    """Ruta al icono del usuario en Linux ($XDG_DATA_HOME/icons/hicolor/256x256/apps)."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "icons" / "hicolor" / "256x256" / "apps" / "melomaniac.png"


def get_windows_shortcut_path() -> Path:
    """Ruta al acceso directo en el Escritorio del usuario en Windows."""
    return Path.home() / "Desktop" / "Melomaniac.lnk"


# ── Generación de Icono ───────────────────────────────────────────────────

def ensure_icon_installed() -> Path | None:
    """Garantiza que el icono esté disponible en el sistema.

    Copia resources/icon.png a ~/.local/share/icons/hicolor/256x256/apps y
    a ~/.local/share/icons/melomaniac.png. Si no existe, genera uno con Pillow.
    """
    if not sys.platform.startswith("linux"):
        return None

    dest_hicolor = get_linux_icon_path()
    dest_simple = dest_hicolor.parent.parent.parent.parent / "icons" / "melomaniac.png"

    source_icon = PROJECT_DIR / "resources" / "icon.png"
    if not source_icon.is_file():
        # En binarios PyInstaller empotrados en sys._MEIPASS
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cand = Path(meipass) / "resources" / "icon.png"
            if cand.is_file():
                source_icon = cand

    dest_hicolor.parent.mkdir(parents=True, exist_ok=True)
    dest_simple.parent.mkdir(parents=True, exist_ok=True)

    if source_icon.is_file():
        shutil.copy2(source_icon, dest_hicolor)
        shutil.copy2(source_icon, dest_simple)
        return dest_hicolor

    # Generación al vuelo si Pillow está disponible
    try:
        from PIL import Image, ImageDraw

        size = (256, 256)
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bg_color = (22, 25, 34, 255)
        accent = (79, 139, 255, 255)
        draw.rounded_rectangle([(16, 16), (240, 240)], radius=48, fill=bg_color)
        draw.ellipse([(60, 155), (105, 195)], fill=accent)
        draw.ellipse([(145, 135), (190, 175)], fill=accent)
        draw.rectangle([(95, 80), (105, 175)], fill=accent)
        draw.rectangle([(180, 60), (190, 155)], fill=accent)
        draw.polygon([(95, 80), (190, 60), (190, 85), (95, 105)], fill=accent)

        img.save(dest_hicolor, format="PNG")
        img.save(dest_simple, format="PNG")
        return dest_hicolor
    except Exception as exc:
        logger.warning("No se pudo generar el icono PNG: %s", exc)
        return None


# ── Verificación de estado ─────────────────────────────────────────────────

def get_shortcut_status() -> ShortcutStatus:
    """Verifica si el acceso directo o lanzador está instalado y apunta a esta app."""
    env = get_runtime_environment()

    # macOS no tiene soporte de empaquetado standalone hoy
    if env.os_name not in ("linux", "windows"):
        return ShortcutStatus(
            supported=False,
            installed=False,
            target_path=None,
            is_current=False,
            is_broken=False,
            location_path=None,
            summary="Integración de lanzador no disponible para esta plataforma.",
            warning=None,
        )

    # Windows con instalador
    if env.is_windows_installer:
        return ShortcutStatus(
            supported=True,
            installed=True,
            target_path=str(env.executable_path),
            is_current=True,
            is_broken=False,
            location_path="Menú Inicio y Escritorio (Inno Setup)",
            summary="Gestionado por el instalador de Windows (Menú Inicio y Escritorio).",
            warning="Las actualizaciones mediante el instalador conservan los accesos existentes.",
        )

    # Windows Portable (ZIP) o desarrollo
    if env.os_name == "windows":
        shortcut_file = get_windows_shortcut_path()
        installed = shortcut_file.is_file()
        warning = (
            "Ubicación portable: si mueves la carpeta de Melomaniac o descomprimes "
            "una versión nueva en otra ruta, deberás volver a crear el acceso."
        ) if env.is_windows_portable else None

        if not installed:
            return ShortcutStatus(
                supported=True,
                installed=False,
                target_path=None,
                is_current=False,
                is_broken=False,
                location_path=str(shortcut_file),
                summary="Acceso directo en el Escritorio no instalado.",
                warning=warning,
            )

        # En Windows portable no leemos COM a menos que sea necesario, pero sabemos que existe
        return ShortcutStatus(
            supported=True,
            installed=True,
            target_path=str(env.executable_path),
            is_current=True,
            is_broken=False,
            location_path=str(shortcut_file),
            summary="Acceso directo presente en el Escritorio.",
            warning=warning,
        )

    # Linux (AppImage, paquete binario o script)
    desktop_file = get_linux_desktop_path()
    warning_appimage = (
        "Ubicación del AppImage: asegúrate de colocar el archivo en una carpeta "
        "estable (como ~/.local/bin o ~/Aplicaciones). Si lo mueves, el lanzador "
        "dejará de funcionar hasta que lo actualices aquí."
    ) if env.is_appimage else None

    if not desktop_file.is_file():
        return ShortcutStatus(
            supported=True,
            installed=False,
            target_path=None,
            is_current=False,
            is_broken=False,
            location_path=str(desktop_file),
            summary="Lanzador no instalado en el menú de aplicaciones.",
            warning=warning_appimage,
        )

    # Analizar el archivo .desktop existente
    target_path = None
    try:
        content = desktop_file.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("Exec="):
                exec_val = line[5:].strip()
                tokens = shlex.split(exec_val)
                if tokens:
                    target_path = tokens[0]
                    # Si corre vía python app.py, el segundo token es el script
                    if target_path.endswith(("python", "python3")) and len(tokens) > 1:
                        target_path = tokens[1]
                break
    except Exception as exc:
        logger.warning("Error leyendo %s: %s", desktop_file, exc)

    if not target_path:
        return ShortcutStatus(
            supported=True,
            installed=True,
            target_path=None,
            is_current=False,
            is_broken=True,
            location_path=str(desktop_file),
            summary="Lanzador instalado pero con formato inválido.",
            warning=warning_appimage,
        )

    target_exists = Path(target_path).is_file()
    current_target = str(env.executable_path)
    is_current = (Path(target_path).resolve() == env.executable_path.resolve())

    if not target_exists:
        return ShortcutStatus(
            supported=True,
            installed=True,
            target_path=target_path,
            is_current=False,
            is_broken=True,
            location_path=str(desktop_file),
            summary="Lanzador roto: apunta a un archivo que ya no existe.",
            warning=(
                f"El lanzador apuntaba a: {target_path}. "
                "Haz clic en «Actualizar acceso directo» para vincularlo a la versión actual."
            ),
        )

    if not is_current:
        return ShortcutStatus(
            supported=True,
            installed=True,
            target_path=target_path,
            is_current=False,
            is_broken=False,
            location_path=str(desktop_file),
            summary="Lanzador apunta a otra versión o ruta anterior.",
            warning=(
                f"Apunta a: {Path(target_path).name}. "
                "Haz clic en «Actualizar acceso directo» para sincronizarlo con esta versión."
            ),
        )

    return ShortcutStatus(
        supported=True,
        installed=True,
        target_path=target_path,
        is_current=True,
        is_broken=False,
        location_path=str(desktop_file),
        summary="Lanzador instalado y sincronizado con esta versión.",
        warning=warning_appimage,
    )


# ── Creación e Instalación ─────────────────────────────────────────────────

def install_shortcut() -> Tuple[bool, str]:
    """Crea o actualiza el acceso directo / lanzador según la plataforma."""
    env = get_runtime_environment()

    if not env.os_name in ("linux", "windows"):
        return False, f"Plataforma no soportada para accesos automáticos ({env.os_name})."

    # En Windows instalado no hace falta tocar nada
    if env.is_windows_installer:
        return True, "El instalador de Windows ya gestiona los accesos directos."

    # En Windows portátil / ZIP
    if env.os_name == "windows":
        shortcut_file = get_windows_shortcut_path()
        target_exe = str(env.executable_path)
        work_dir = str(env.executable_path.parent)

        ps_script = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{shortcut_file}'); "
            f"$s.TargetPath = '{target_exe}'; "
            f"$s.WorkingDirectory = '{work_dir}'; "
            f"$s.Description = 'Melomaniac Music Manager'; "
            f"$s.Save()"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode == 0 and shortcut_file.is_file():
                return True, f"Acceso directo creado en el Escritorio ({shortcut_file.name})."
            return False, f"PowerShell falló al crear el acceso: {res.stderr.strip()}"
        except Exception as exc:
            return False, f"No se pudo crear el acceso en Windows: {exc}"

    # En Linux
    desktop_file = get_linux_desktop_path()
    desktop_file.parent.mkdir(parents=True, exist_ok=True)
    ensure_icon_installed()

    if env.is_appimage and env.appimage_path:
        exec_cmd = f'"{env.appimage_path}"'
    elif env.is_frozen:
        exec_cmd = f'"{env.executable_path}"'
    else:
        exec_cmd = f'"{sys.executable}" "{PROJECT_DIR / "app.py"}"'

    desktop_content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Melomaniac\n"
        "GenericName=Gestor y reproductor musical\n"
        "Comment=Gestor y reproductor de música para plataformas de streaming y biblioteca local\n"
        f"Exec={exec_cmd} %U\n"
        "Icon=melomaniac\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;Player;\n"
        "StartupWMClass=Melomaniac\n"
    )

    try:
        desktop_file.write_text(desktop_content, encoding="utf-8")
        # Asegurar permisos de ejecución
        desktop_file.chmod(desktop_file.stat().st_mode | 0o755)

        # Actualizar base de datos de escritorio si la herramienta existe
        db_tool = shutil.which("update-desktop-database")
        if db_tool:
            subprocess.run([db_tool, str(desktop_file.parent)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

        return True, "Lanzador instalado con éxito en el menú de aplicaciones."
    except Exception as exc:
        return False, f"Error al escribir el archivo .desktop: {exc}"


def remove_shortcut() -> Tuple[bool, str]:
    """Elimina el lanzador o acceso directo existente."""
    env = get_runtime_environment()

    if env.os_name == "linux":
        desktop_file = get_linux_desktop_path()
        if desktop_file.is_file():
            try:
                desktop_file.unlink()
                db_tool = shutil.which("update-desktop-database")
                if db_tool:
                    subprocess.run([db_tool, str(desktop_file.parent)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                return True, "Lanzador eliminado del menú de aplicaciones."
            except Exception as exc:
                return False, f"No se pudo eliminar el lanzador: {exc}"
        return True, "El lanzador no estaba instalado."

    if env.os_name == "windows" and not env.is_windows_installer:
        shortcut_file = get_windows_shortcut_path()
        if shortcut_file.is_file():
            try:
                shortcut_file.unlink()
                return True, "Acceso directo del Escritorio eliminado."
            except Exception as exc:
                return False, f"No se pudo eliminar el acceso directo: {exc}"
        return True, "El acceso directo no estaba instalado."

    return False, "La eliminación no es aplicable en esta configuración."


def auto_sync_if_installed() -> bool:
    """Sincroniza silenciosamente el acceso si ya estaba instalado pero apunta a una ruta vieja.

    Se ejecuta al iniciar la aplicación para resolver el problema de actualizaciones:
    si el usuario descargó un nuevo AppImage o cambió la ruta, el archivo .desktop
    se actualiza automáticamente al nuevo ejecutable sin requerir acción manual.

    Solo actúa en builds empaquetadas (AppImage o binario): ejecutar desde el
    código fuente nunca debe reescribir el lanzador de una instalación real.
    """
    if not get_runtime_environment().is_frozen:
        return False
    status = get_shortcut_status()
    if status.installed and not status.is_current:
        ok, msg = install_shortcut()
        if ok:
            logger.info("Acceso directo actualizado automáticamente a la nueva ruta: %s", msg)
        return ok
    return False
