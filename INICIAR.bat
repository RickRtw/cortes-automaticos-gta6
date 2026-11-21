@echo off
chcp 65001 >nul
title Cortes GTA - Iniciar
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ============================================
echo    CORTES GTA - cortadores + postador
echo ============================================
echo.
echo Abre 1 janela de CORTADOR por streamer (config.json ^> streamers):
echo   - ao vivo jogando GTA  = corta a live em tempo real
echo   - offline              = corta a ultima live de GTA gravada
echo   - checa de novo a cada 10 min
echo E 1 janela de POSTADOR (YouTube + TikTok, 10 min entre posts).
echo Para parar: feche as janelas (ou CTRL+C nelas).
echo.

python -u cortador.py todos
start "POSTADOR" cmd /k python -u publicador.py rodar

timeout /t 5 >nul
exit
