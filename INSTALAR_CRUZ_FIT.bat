@echo off
setlocal EnableExtensions
title Instalar Cruz Fit
cd /d "%~dp0"

set "RUNTIME=%~dp0.runtime"
set "PYTHON_EXE=%RUNTIME%\Scripts\python.exe"
set "PY_CMD="

if exist "%PYTHON_EXE%" goto install_dependencies

where py >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if defined PY_CMD goto create_runtime

where python >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"
if defined PY_CMD goto create_runtime

echo Python 3.10 ou superior nao foi encontrado.
echo Instale o Python em https://www.python.org/downloads/windows/
echo Durante a instalacao, marque a opcao "Add python.exe to PATH".
pause
exit /b 1

:create_runtime
echo Criando o ambiente local do Cruz Fit...
%PY_CMD% -m venv "%RUNTIME%"
if errorlevel 1 goto failure

:install_dependencies
echo Instalando as dependencias do sistema...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto failure
"%PYTHON_EXE%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failure

if not exist "%~dp0.backup\config.json" (
    echo Configure agora o pendrive ou HD externo que recebera os backups.
    call "%~dp0CONFIGURAR_BACKUP_CRUZ_FIT.bat"
    if errorlevel 1 goto failure
)

echo Preparando o banco de dados com backup validado...
"%PYTHON_EXE%" -m construct.backup.cli runtime prepare-database --reason "Instalacao inicial" --operation-id "install-%RANDOM%-%RANDOM%"
if errorlevel 1 goto failure

echo.
echo Instalacao concluida. Agora use INICIAR_CRUZ_FIT.bat.
pause
exit /b 0

:failure
echo.
echo A instalacao nao foi concluida. Confira a mensagem acima e tente novamente.
pause
exit /b 1
