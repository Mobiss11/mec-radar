#!/usr/bin/env bash
#
# stop.sh — Graceful shutdown
#
# Usage:
#   ./scripts/stop.sh            # stop app only (keep Docker)
#   ./scripts/stop.sh --all      # stop app + Docker infra
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

STOP_ALL=false
for arg in "$@"; do
  case "$arg" in
    --all) STOP_ALL=true ;;
  esac
done

log() { echo -e "${CYAN}[$(date '+%H:%M:%S')]${NC} $1"; }
ok()  { echo -e "${GREEN}  ✓${NC} $1"; }

cd "$PROJECT_ROOT"

log "Stopping PM2 processes..."
pm2 stop memecoin-backend 2>/dev/null && ok "Backend stopped" || ok "Backend was not running"
pm2 delete memecoin-backend 2>/dev/null || true
pm2 save --force 2>/dev/null

if $STOP_ALL; then
  log "Stopping Docker infrastructure..."
  docker compose down 2>&1 | while read -r line; do echo "  $line"; done
  ok "Docker stopped (volumes preserved)"
fi

echo ""
echo -e "${GREEN}Shutdown complete.${NC}"
echo ""

if $STOP_ALL; then
  echo "  Note: Docker volumes (pgdata, redisdata) are preserved."
  echo "  To remove ALL data: docker compose down -v"
fi
