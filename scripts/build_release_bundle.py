#!/usr/bin/env python3
"""Construye el ZIP portátil de un release de Melomaniac.

Uso:
    python scripts/build_release_bundle.py --version 4.8.2 [--out-dir /tmp/opencode]

Incluye solo archivos trackeados necesarios en runtime:
app.py, run.*, requirements.txt, LICENSE, README*, CHANGELOG.md,
config/.gitkeep, core/, engine/, services/, ui/, resources/.
Excluye tests/, docs internas, .venv, config/ real y cachés.
Imprime el SHA-256 del ZIP por consola (GitHub lo muestra por asset).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import sys
import zipfile

BUNDLE_PATHS = [
    "app.py",
    "run.sh",
    "run.bat",
    "requirements.txt",
    "LICENSE",
    "README.md",
    "README.en.md",
    "CHANGELOG.md",
    "config/.gitkeep",
    "core",
    "engine",
    "services",
    "ui",
    "resources",
]


def tracked_files(root: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", *BUNDLE_PATHS],
        cwd=root, capture_output=True, check=True,
    )
    return [p for p in out.stdout.decode().split("\0") if p]


def build(version: str, out_dir: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder = f"Melomaniac-v{version}"
    zip_path = os.path.join(out_dir, f"{folder}.zip")

    files = tracked_files(root)
    if not files:
        print("ERROR: sin archivos trackeados para el bundle.", file=sys.stderr)
        sys.exit(1)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in sorted(files):
            fp = os.path.join(root, rel)
            arc = f"{folder}/{rel}"
            zi = zipfile.ZipInfo(arc)
            zi.date_time = (2026, 11, 6, 0, 0, 0)
            zi.compress_type = zipfile.ZIP_DEFLATED
            st = os.stat(fp)
            zi.external_attr = (stat.S_IMODE(st.st_mode) & 0xFFFF) << 16
            if os.access(fp, os.X_OK):
                zi.external_attr |= 0x10000000
            with open(fp, "rb") as fh:
                z.writestr(zi, fh.read())

    digest = hashlib.sha256()
    with open(zip_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"{folder}.zip: {size_mb:.1f} MB, {len(files)} archivos")
    print(f"{digest.hexdigest()}  {folder}.zip")
    return zip_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True, help="Versión sin 'v', ej. 4.8.2")
    ap.add_argument("--out-dir", default="/tmp/opencode", help="Carpeta de salida")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    build(args.version, args.out_dir)
