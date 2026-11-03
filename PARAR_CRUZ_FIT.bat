@echo off
setlocal EnableExtensions
title Cruz Fit - Parar sistema
cd /d "%~dp0"

set "RUNTIME=%~dp0.runtime"
set "PYTHON_EXE=%RUNTIME%\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Ambiente do Cruz Fit nao encontrado.
    exit /b 1
)

"%PYTHON_EXE%" -m construct.backup.cli runtime stop
if errorlevel 1 exit /b 1

echo Servidor Cruz Fit encerrado.
exit /b 0
