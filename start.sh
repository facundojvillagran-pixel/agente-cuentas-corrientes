#!/usr/bin/env bash
# Arranque en un solo paso: crea el entorno si falta, instala dependencias
# y levanta la aplicación local en http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creando entorno virtual en .venv ..."
  python3 -m venv .venv
fi

echo "Instalando dependencias ..."
./.venv/bin/pip install -q -e ".[dev]"

echo "Iniciando el servidor local en http://127.0.0.1:8000 (Ctrl+C para detener) ..."
./.venv/bin/uvicorn cuentas_corrientes.api:app --host 127.0.0.1 --port 8000 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

sleep 1
if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:8000" || true
else
  echo "Abrí http://127.0.0.1:8000 en tu navegador."
fi

wait "$SERVER_PID"
