#!/bin/bash
set -euo pipefail

echo "=========================================="
echo "  BOTS INTEGRADOS - VERDINHA → CULTO"
echo "  (modo direto - sem Docker)"
echo "=========================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR"

# Guardar PIDs para parar tudo com Ctrl+C
PIDS=()
cleanup() {
  echo ""
  echo "[STOP] Encerrando serviços..."
  for pid in "${PIDS[@]}"; do
    if [ -n "${pid}" ]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}
trap cleanup EXIT INT TERM


DOWNLOAD_PORT=5000
UPLOAD_PORT=5001

kill_port() {
  local port="$1"
  # tenta achar pids escutando na porta e mata
  local pids
  pids="$(ss -ltnp 2>/dev/null | grep -E ":${port}\s" | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u || true)"
  if [[ -n "${pids}" ]]; then
    echo "  Matando processos na porta ${port}: ${pids}"
    kill -9 ${pids} 2>/dev/null || true
  fi
}

check_port() {
  local port="$1"
  local name="$2"
  local logfile="$3"
  # espera até 2.5s
  for i in {1..25}; do
    if ss -ltn 2>/dev/null | grep -q ":${port}\s"; then
      echo "  ✅ ${name} em http://localhost:${port}"
      return 0
    fi
    sleep 0.1
  done
  echo "  ❌ ${name} NÃO subiu na porta ${port}"
  if [[ -f "${logfile}" ]]; then
    echo "  --- últimas linhas de ${logfile} ---"
    tail -n 80 "${logfile}" || true
    echo "  -----------------------------------"
  else
    echo "  (log ${logfile} não encontrado)"
  fi
  return 1
}

echo "Limpando portas..."
kill_port "$DOWNLOAD_PORT"
kill_port "$UPLOAD_PORT"

# também encerra workers antigos (não escutam porta)
pkill -f "download/app.py worker" 2>/dev/null || true
pkill -f "upload/app.py worker" 2>/dev/null || true

# garantir pastas
mkdir -p data downloads

echo ""
echo "Iniciando Download Dashboard (API) ..."
python3 -u download/app.py api > download_dashboard.log 2>&1 &
DOWNLOAD_API_PID=$!
PIDS+=("${DOWNLOAD_API_PID}")

echo "Iniciando Download Worker ..."
python3 -u download/app.py worker > download_worker.log 2>&1 &
DOWNLOAD_WORKER_PID=$!
PIDS+=("${DOWNLOAD_WORKER_PID}")

# Verifica se o worker não caiu na largada
sleep 0.6
if ! kill -0 "$DOWNLOAD_WORKER_PID" 2>/dev/null; then
  echo "  ❌ Worker de Download morreu ao iniciar. Veja o log:"
  tail -n 120 download_worker.log || true
  exit 1
fi


echo "Iniciando Upload Dashboard (API) ..."
python3 -u upload/app.py > upload_dashboard.log 2>&1 &
UPLOAD_DASH_PID=$!
PIDS+=("${UPLOAD_DASH_PID}")

echo "Iniciando Upload Worker ..."
python3 -u upload/worker.py > upload_worker.log 2>&1 &
UPLOAD_WORKER_PID=$!
PIDS+=("${UPLOAD_WORKER_PID}")

# Verifica se o worker não caiu na largada
sleep 0.6
if ! kill -0 "$UPLOAD_WORKER_PID" 2>/dev/null; then
  echo "  ❌ Worker de Upload morreu ao iniciar. Veja o log:"
  tail -n 120 upload_worker.log || true
  exit 1
fi


echo ""
echo "Health-check..."
check_port "$DOWNLOAD_PORT" "Download" "download_dashboard.log" || true
check_port "$UPLOAD_PORT" "Upload" "upload_dashboard.log" || true

echo ""
echo "=========================================="
echo "Serviços:"
echo "  📥 Download: http://localhost:$DOWNLOAD_PORT"
echo "  📤 Upload:   http://localhost:$UPLOAD_PORT"
echo ""
echo "Logs:"
echo "  - download_dashboard.log"
echo "  - download_worker.log"
echo "  - upload_dashboard.log"
echo "  - upload_worker.log"
echo ""
echo "Para parar tudo:"
echo "  kill $DOWNLOAD_API_PID $DOWNLOAD_WORKER_PID $UPLOAD_DASH_PID $UPLOAD_WORKER_PID"
echo "=========================================="
echo ""

wait $DOWNLOAD_API_PID $DOWNLOAD_WORKER_PID $UPLOAD_DASH_PID $UPLOAD_WORKER_PID
