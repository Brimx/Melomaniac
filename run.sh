#!/usr/bin/env bash
set -e

# Cambiar al directorio del script
cd "$(dirname "$0")"

# Detectar el comando de Python 3
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "[ERROR] No se encontró Python en el sistema." >&2
    echo "Instala Python 3.10+ (ej. 'sudo apt install python3 python3-venv python3-pip' o 'sudo pacman -S python')." >&2
    exit 1
fi

# Crear entorno virtual si no existe
if [ ! -d ".venv" ]; then
    echo "==> Creando entorno virtual en .venv..."
    $PYTHON_BIN -m venv .venv
    echo "==> Instalando dependencias desde requirements.txt..."
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

# Ejecutar la aplicación usando el entorno virtual
echo "==> Iniciando Melomaniac..."
exec .venv/bin/python app.py
