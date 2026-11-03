@echo off
setlocal EnableExtensions
title Cruz Fit - Restaurar backup
cd /d "%~dp0"

set "RUNTIME=%~dp0.runtime"
set "PYTHON_EXE=%RUNTIME%\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo Ambiente do Cruz Fit nao encontrado.
    exit /b 1
)

echo O sistema deve estar parado.
set /p "BACKUP_FILE=Arquivo ZIP validado: "
if not defined BACKUP_FILE exit /b 1
set /p "RESTORE_REASON=Motivo da restauracao: "
if not defined RESTORE_REASON set "RESTORE_REASON=Restauracao manual"
"%PYTHON_EXE%" -m construct.backup.cli restore --arquivo "%BACKUP_FILE%" --reason "%RESTORE_REASON%" --operation-id "restore-%RANDOM%-%RANDOM%"
if errorlevel 1 echo Restauracao recusada ou revertida. Confirme que o sistema esta parado e consulte .backup\logs\supervisor.log.
exit /b %errorlevel%
