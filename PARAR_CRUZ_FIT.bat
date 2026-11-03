@echo off
setlocal EnableExtensions
title Cruz Fit - Parar sistema

powershell -NoProfile -ExecutionPolicy Bypass -Command "$connections=Get-NetTCPConnection -LocalPort 15001 -State Listen -ErrorAction SilentlyContinue; foreach($connection in $connections){ Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo Servidor Cruz Fit encerrado.
pause
