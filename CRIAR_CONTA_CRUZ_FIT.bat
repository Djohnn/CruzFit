@echo off
setlocal EnableExtensions
title Cruz Fit - Criar conta
cd /d "%~dp0"

set "RUNTIME=%~dp0.runtime"
set "PYTHON_EXE=%RUNTIME%\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo O ambiente do Cruz Fit ainda nao foi instalado.
    echo Execute INSTALAR_CRUZ_FIT.bat primeiro.
    pause
    exit /b 1
)

"%PYTHON_EXE%" manage.py criar_conta_cruz_fit
if errorlevel 1 (
    echo.
    echo Nao foi possivel criar a conta.
    pause
    exit /b 1
)

echo.
pause
