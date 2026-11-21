#!/usr/bin/env bash
# Linux/macOS: inicia 1 cortador por streamer (segundo plano, log em cortador_<id>.log) + o postador.
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
python3 -u cortador.py todos
python3 -u publicador.py rodar
