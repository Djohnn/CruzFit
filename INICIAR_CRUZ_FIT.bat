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

"%PYTHON_EXE%" manage.py migrate --noinput
if errorlevel 1 (
    echo Nao foi possivel preparar o banco de dados.
    pause
    exit /b 1
)

echo Iniciando o servidor Cruz Fit na porta 15001...
start "Cruz Fit - servidor" /min "%PYTHON_EXE%" manage.py runserver 127.0.0.1:15001 --noreload

echo Aguardando o sistema ficar pronto...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$url='http://127.0.0.1:15001/auth/login/'; for($i=0; $i -lt 30; $i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1; if($r.StatusCode -eq 200){ Start-Process $url; exit 0 } } catch {} ; Start-Sleep -Milliseconds 500 }; Start-Process $url"

echo Cruz Fit aberto no navegador.
echo Para encerrar o servidor, use PARAR_CRUZ_FIT.bat.
exit /b 0
