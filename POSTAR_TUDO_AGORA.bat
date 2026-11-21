@echo off
chcp 65001 >nul
title Cortes GTA - Postar tudo agora
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo Postando agora todos os cortes aprovados da fila (ate acabar a cota do dia)...
echo.
python -u publicador.py agora
echo.
python publicador.py listar
echo.
pause
