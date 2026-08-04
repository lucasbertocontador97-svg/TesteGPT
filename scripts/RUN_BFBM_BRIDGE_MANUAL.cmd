@echo off
cd /d "%~dp0.."
echo Iniciando Ponte BFBM TesteGPT...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bfbm_bridge.ps1"
echo.
echo A ponte foi encerrada. Pressione qualquer tecla para fechar.
pause >nul
