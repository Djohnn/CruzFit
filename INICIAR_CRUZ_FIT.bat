@echo off
setlocal EnableExtensions
title Cruz Fit - Iniciar sistema
cd /d "%~dp0"

set "RUNTIME=%~dp0.runtime"
set "PYTHON_EXE=%RUNTIME%\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Primeira execucao detectada. Iniciando a instalacao...
    call "%~dp0INSTALAR_CRUZ_FIT.bat"
    if errorlevel 1 exit /b 1
)

if not exist "%~dp0.backup\config.json" (
    echo Configure primeiro o pendrive ou HD externo de backup.
    call "%~dp0CONFIGURAR_BACKUP_CRUZ_FIT.bat"
    if errorlevel 1 exit /b 1
)

echo Fazendo backup, aplicando migracoes e iniciando o servidor Cruz Fit...
start "Cruz Fit - supervisor" /min "%PYTHON_EXE%" -m cruzfit.backup.cli runtime start --reason "Abertura do sistema" --operation-id "start-%RANDOM%-%RANDOM%"

echo Aguardando o sistema ficar pronto...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$url='http://127.0.0.1:15001/auth/login/'; for($i=0; $i -lt 60; $i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1; if($r.StatusCode -eq 200){ exit 0 } } catch {} ; Start-Sleep -Milliseconds 500 }; exit 1"
if errorlevel 1 (
    echo O Cruz Fit nao ficou pronto. Confira .backup\logs\supervisor.log e o dispositivo de backup.
    exit /b 1
)
start "" "http://127.0.0.1:15001/auth/login/"

echo Cruz Fit aberto no navegador.
echo Para encerrar o servidor, use PARAR_CRUZ_FIT.bat.
exit /b 0
