#!/usr/bin/env bash
set -euo pipefail

# Uso:
#   ./continuar.sh "NOME_DA_OBRA" "URL_DO_CAPITULO" [TOTAL_CAPITULOS] [BATCH_SIZE]
#
# Exemplo:
#   ./continuar.sh "Solo_Leveling" "https://.../algum-capitulo" 304 0

OBRA="${1:-}"
URL="${2:-}"
TOTAL="${3:-0}"
BATCH="${4:-0}"
HOST="${VERDINHA_HOST:-http://localhost:5000}"

if [[ -z "$OBRA" || -z "$URL" ]]; then
  echo 'Uso: ./continuar.sh "NOME_DA_OBRA" "URL_DO_CAPITULO" [TOTAL_CAPITULOS] [BATCH_SIZE]'
  exit 1
fi

curl -sS -X POST "$HOST/api/download"   -H "Content-Type: application/json"   -d "{"nome":"$OBRA","url":"$URL","force_url":true,"expected_total":$TOTAL,"batch_size":$BATCH}" | cat

echo
echo "OK. Acompanhe em $HOST (dashboard) ou veja logs no terminal do container."
