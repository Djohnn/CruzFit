@echo off
setlocal EnableExtensions
title Cruz Fit - Criar atalho
cd /d "%~dp0"

if not exist "%~dp0INICIAR_CRUZ_FIT.bat" (
    echo INICIAR_CRUZ_FIT.bat nao foi encontrado.
    pause
    exit /b 1
)

if not exist "%~dp0cruz-fit-icon.ico" (
    echo cruz-fit-icon.ico nao foi encontrado.
    pause
    exit /b 1
)

set "CRUZFIT_TARGET=%~dp0INICIAR_CRUZ_FIT.bat"
set "CRUZFIT_WORKDIR=%~dp0"
set "CRUZFIT_ICON=%~dp0cruz-fit-icon.ico"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$shell=New-Object -ComObject WScript.Shell; $desktop=[Environment]::GetFolderPath('Desktop'); $shortcut=$shell.CreateShortcut((Join-Path $desktop 'Cruz Fit.lnk')); $shortcut.TargetPath=[Environment]::GetEnvironmentVariable('CRUZFIT_TARGET'); $shortcut.WorkingDirectory=[Environment]::GetEnvironmentVariable('CRUZFIT_WORKDIR'); $shortcut.IconLocation=([Environment]::GetEnvironmentVariable('CRUZFIT_ICON') + ',0'); $shortcut.Description='Iniciar Cruz Fit'; $shortcut.Save()"

echo.
echo Atalho Cruz Fit criado na Area de Trabalho.
pause
