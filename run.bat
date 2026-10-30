@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo                 Iniciando Melomaniac
echo ========================================================

:: Comprobar si Python esta instalado y en PATH
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] No se encontro Python en el PATH del sistema.
    echo.
    echo Puedes instalarlo desde PowerShell con:
    echo   winget install Python.Python.3.12
    echo Recuerda marcar la opcion "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

:: Crear el entorno virtual si no existe
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creando entorno virtual .venv...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo [*] Instalando dependencias desde requirements.txt...
    call .venv\Scripts\pip install --upgrade pip
    call .venv\Scripts\pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
    copy /y requirements.txt .venv\requirements.installed >nul
) else (
    :: Comprobar si requirements.txt cambio desde la ultima instalacion
    set "REQ_UPDATED=0"
    if not exist ".venv\requirements.installed" (
        set "REQ_UPDATED=1"
    ) else (
        fc /b requirements.txt .venv\requirements.installed >nul 2>nul
        if errorlevel 1 set "REQ_UPDATED=1"
    )
    if "%REQ_UPDATED%"=="1" (
        echo [*] Detectados cambios en dependencias. Actualizando...
        call .venv\Scripts\pip install -r requirements.txt
        copy /y requirements.txt .venv\requirements.installed >nul
    )
)

:: Ejecutar la aplicacion directamente con el Python del venv
echo [*] Arrancando aplicacion...
call .venv\Scripts\python.exe app.py
if %errorlevel% neq 0 (
    echo.
    echo [AVISO] La aplicacion finalizo con errores.
    pause
)
