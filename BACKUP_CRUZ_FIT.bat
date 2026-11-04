@echo off
setlocal EnableExtensions
title Cruz Fit - Backup manual
cd /d "%~dp0"

set "RUNTIME=%~dp0.runtime"
set "PYTHON_EXE=%RUNTIME%\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo Ambiente do Cruz Fit nao encontrado.
    exit /b 1
)

echo Confirme que o Cruz Fit esta parado antes de continuar.
set /p "BACKUP_REASON=Motivo do backup: "
if not defined BACKUP_REASON set "BACKUP_REASON=Backup manual"
"%PYTHON_EXE%" -m cruzfit.backup.cli create --reason "%BACKUP_REASON%" --operation-id "manual-%RANDOM%-%RANDOM%"
if errorlevel 1 echo Backup recusado. Confirme que o sistema esta parado, o destino esta conectado e consulte .backup\logs\supervisor.log.
exit /b %errorlevel%
