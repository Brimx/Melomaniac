#!/usr/bin/env python3
"""Construye los paquetes standalone de Melomaniac para Linux (.tar.gz y .AppImage).

Uso:
    python scripts/build_linux.py --version 4.8.0 [--out-dir dist-release] [--skip-pack]

Requisitos:
    - Entorno virtual con dependencias de requirements.txt y pyinstaller.
    - Conexión a internet si appimagetool no está en el sistema (se descarga automáticamente en /tmp).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
APPIMAGETOOL_URL = "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"


def generate_default_icon(dest_path: Path) -> None:
    """Genera un icono PNG 256x256 por defecto si no existe uno personalizado."""
    try:
        from PIL import Image, ImageDraw

        size = (256, 256)
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Fondo con bordes redondeados (estilo moderno de escritorio)
        bg_color = (22, 25, 34, 255)      # Slate oscuro (#161922)
        accent = (79, 139, 255, 255)       # Brand accent (#4F8BFF)
        draw.rounded_rectangle([(16, 16), (240, 240)], radius=48, fill=bg_color)

        # Nota musical simplificada en acento
        # Dos cabezas de nota
        draw.ellipse([(60, 155), (105, 195)], fill=accent)
        draw.ellipse([(145, 135), (190, 175)], fill=accent)
        # Pliegues verticales
        draw.rectangle([(95, 80), (105, 175)], fill=accent)
        draw.rectangle([(180, 60), (190, 155)], fill=accent)
        # Barra superior horizontal inclinada
        draw.polygon([(95, 80), (190, 60), (190, 85), (95, 105)], fill=accent)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest_path, format="PNG")
        print(f"[*] Icono por defecto generado en: {dest_path}")
    except Exception as exc:
        print(f"[!] No se pudo generar el icono con PIL ({exc}). Se usará un marcador.")
        dest_path.write_bytes(b"")


def ensure_appimagetool() -> Path:
    """Busca appimagetool en el sistema o lo descarga en /tmp."""
    # 1. PATH del sistema
    system_tool = shutil.which("appimagetool")
    if system_tool:
        return Path(system_tool)

    # 2. Caché en /tmp
    cached_tool = Path("/tmp/appimagetool-x86_64.AppImage")
    if not cached_tool.exists():
        print(f"[*] Descargando appimagetool desde {APPIMAGETOOL_URL}...")
        urllib.request.urlretrieve(APPIMAGETOOL_URL, cached_tool)
        st = cached_tool.stat()
        cached_tool.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        print("[*] appimagetool listo en /tmp.")
    return cached_tool


def run_flet_pack(root: Path) -> None:
    """Ejecuta flet pack para compilar el árbol onedir."""
    cmd = [
        sys.executable,
        "-m",
        "flet_cli",
        "pack",
        "app.py",
        "--onedir",
        "--name",
        "Melomaniac",
        "--add-data",
        "resources:resources",
        "--hidden-import",
        "dbus_next",
        "--hidden-import",
        "mutagen",
        "-y",
        "--",
        "--collect-data",
        "ytmusicapi",
    ]
    print(f"[*] Ejecutando: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=root)
    if res.returncode != 0:
        print(f"[ERROR] flet pack falló con código {res.returncode}", file=sys.stderr)
        sys.exit(res.returncode)


def build_tar_gz(source_dir: Path, out_file: Path, folder_name: str) -> None:
    """Crea un archivo tar.gz portable con permisos preservados."""
    print(f"[*] Creando tar.gz portable en: {out_file}...")
    with tarfile.open(out_file, "w:gz", compresslevel=9) as tar:
        tar.add(source_dir, arcname=folder_name)
    size_mb = out_file.stat().st_size / (1024 * 1024)
    print(f"[✓] tar.gz generado: {out_file.name} ({size_mb:.1f} MB)")


def build_appimage(dist_dir: Path, out_appimage: Path, version: str) -> None:
    """Prepara la estructura AppDir y compila el AppImage."""
    print("[*] Preparando estructura AppDir...")
    appdir = dist_dir / "Melomaniac.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)

    usr_bin = appdir / "usr" / "bin"
    usr_bin.mkdir(parents=True, exist_ok=True)

    # 1. Copiar contenido de dist/Melomaniac a usr/bin/
    src_dist = dist_dir / "Melomaniac"
    for item in src_dist.iterdir():
        target = usr_bin / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

    # 2. AppRun
    apprun = appdir / "AppRun"
    apprun_content = (
        "#!/bin/sh\n"
        "set -e\n"
        'HERE="$(dirname "$(readlink -f "${0}")")"\n'
        'export APPIMAGE_DIR="${HERE}"\n'
        'export PATH="${HERE}/usr/bin:${PATH}"\n'
        'export LD_LIBRARY_PATH="${HERE}/usr/bin:${HERE}/usr/bin/_internal:${LD_LIBRARY_PATH}"\n'
        'exec "${HERE}/usr/bin/Melomaniac" "$@"\n'
    )
    apprun.write_text(apprun_content, encoding="utf-8")
    apprun.chmod(apprun.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # 3. melomaniac.desktop
    desktop = appdir / "melomaniac.desktop"
    desktop_content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Melomaniac\n"
        "GenericName=Gestor y reproductor musical\n"
        "Comment=Gestor y reproductor de música para plataformas de streaming y biblioteca local\n"
        "Exec=Melomaniac %U\n"
        "Icon=melomaniac\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;Player;\n"
        "StartupWMClass=Melomaniac\n"
    )
    desktop.write_text(desktop_content, encoding="utf-8")

    # 4. Icono
    custom_icon = ROOT_DIR / "resources" / "icon.png"
    target_icon = appdir / "melomaniac.png"
    if custom_icon.is_file():
        shutil.copy2(custom_icon, target_icon)
    else:
        generate_default_icon(target_icon)

    # Enlace simbólico .DirIcon requerido por AppImage spec
    dir_icon = appdir / ".DirIcon"
    if dir_icon.exists() or dir_icon.is_symlink():
        dir_icon.unlink()
    dir_icon.symlink_to("melomaniac.png")

    # 5. Ejecutar appimagetool
    appimagetool = ensure_appimagetool()
    print(f"[*] Empaquetando AppImage con: {appimagetool}...")

    cmd = [
        str(appimagetool),
        "--appimage-extract-and-run",
        str(appdir),
        str(out_appimage),
    ]
    env = os.environ.copy()
    env["ARCH"] = "x86_64"

    res = subprocess.run(cmd, env=env, cwd=ROOT_DIR)
    if res.returncode != 0:
        print(f"[ERROR] appimagetool falló con código {res.returncode}", file=sys.stderr)
        sys.exit(res.returncode)

    size_mb = out_appimage.stat().st_size / (1024 * 1024)
    print(f"[✓] AppImage generado: {out_appimage.name} ({size_mb:.1f} MB)")


def compute_sha256(file_path: Path) -> str:
    """Calcula hash SHA256 de un archivo."""
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="4.8.0", help="Versión de release (ej. 4.8.0)")
    parser.add_argument("--out-dir", default="dist-release", help="Directorio de salida para los paquetes")
    parser.add_argument("--skip-pack", action="store_true", help="Omitir flet pack y usar dist/ existente")
    args = parser.parse_args()

    version = args.version.lstrip("v")
    out_dir = (ROOT_DIR / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    dist_dir = ROOT_DIR / "dist"
    bundled_app = dist_dir / "Melomaniac"

    if not args.skip_pack or not bundled_app.exists():
        run_flet_pack(ROOT_DIR)

    # 1. Crear tar.gz
    tar_name = f"Melomaniac-v{version}-linux-x64.tar.gz"
    tar_path = out_dir / tar_name
    build_tar_gz(bundled_app, tar_path, folder_name=f"Melomaniac-v{version}")

    # 2. Crear AppImage
    appimage_name = f"Melomaniac-v{version}-x86_64.AppImage"
    appimage_path = out_dir / appimage_name
    build_appimage(dist_dir, appimage_path, version)

    # 3. Generar SHA256SUMS-linux.txt
    sums_path = out_dir / "SHA256SUMS-linux.txt"
    lines = [
        f"{compute_sha256(tar_path)}  {tar_name}",
        f"{compute_sha256(appimage_path)}  {appimage_name}",
    ]
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "=" * 60)
    print("RESUMEN DE ARTEFACTOS GENERADOS:")
    print("=" * 60)
    print(sums_path.read_text().strip())
    print("=" * 60)


if __name__ == "__main__":
    main()
