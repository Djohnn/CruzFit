@echo off
setlocal EnableExtensions
title Cruz Fit - Configurar backup
cd /d "%~dp0"

set "RUNTIME=%~dp0.runtime"
set "PYTHON_EXE=%RUNTIME%\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo Instale o Cruz Fit antes de configurar o backup.
    exit /b 1
)

echo Informe a pasta dentro do pendrive ou HD externo montado.
echo Nao informe apenas uma letra de unidade ausente.
echo Reconfiguracoes preservam o installation_id dos backups existentes.
echo Para uma instalacao nova, use a CLI com --new-installation-id.
set /p "BACKUP_DEST=Destino do backup: "
if not defined BACKUP_DEST (
    echo O destino e obrigatorio.
    exit /b 1
)

"%PYTHON_EXE%" -m construct.backup.cli configure --destino "%BACKUP_DEST%"
exit /b %errorlevel%
