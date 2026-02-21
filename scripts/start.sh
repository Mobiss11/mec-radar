#!/usr/bin/env bash
#
# start.sh — Full startup pipeline:
#   1. Docker (PostgreSQL + Redis)
#   2. DB migrations
#   3. Frontend build
#   4. Backend tests — if any fail, ABORT
#   5. PM2 start
#
# Usage:
#   ./scripts/start.sh           # full pipeline
#   ./scripts/start.sh --skip-tests   # skip tests (dev only)
#   ./scripts/start.sh --restart      # just restart PM2 (no docker/tests)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

SKIP_TESTS=false
RESTART_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=true ;;
    --restart)    RESTART_ONLY=true ;;
  esac
done

log() { echo -e "${CYAN}[$(date '+%H:%M:%S')]${NC} $1"; }
ok()  { echo -e "${GREEN}  ✓${NC} $1"; }
err() { echo -e "${RED}  ✗${NC} $1"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $1"; }

cd "$PROJECT_ROOT"

# ── Quick restart mode ──────────────────────────────────────────────
if $RESTART_ONLY; then
  log "Restarting PM2 processes..."
  pm2 restart ecosystem.config.cjs --update-env 2>/dev/null || pm2 start ecosystem.config.cjs
  ok "PM2 restarted"
  pm2 list
  exit 0
fi

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}       Memecoin Radar — Startup Pipeline${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""

# ── Step 1: Check .env ──────────────────────────────────────────────
log "Step 1/6: Checking environment..."

if [ ! -f .env ]; then
  err ".env file not found. Copy .env.example and configure."
  exit 1
fi
ok ".env exists"

if [ ! -f "$VENV_PYTHON" ]; then
  err "Python venv not found at $VENV_PYTHON"
  exit 1
fi
ok "Python venv OK"

if ! command -v pm2 &>/dev/null; then
  err "PM2 not installed. Run: npm install -g pm2"
  exit 1
fi
ok "PM2 installed"

if ! command -v docker &>/dev/null; then
  err "Docker not installed"
  exit 1
fi
ok "Docker installed"

# ── Step 2: Docker infrastructure ───────────────────────────────────
log "Step 2/6: Starting Docker infrastructure..."

docker compose up -d --wait 2>&1 | while read -r line; do echo "  $line"; done

# Verify services are healthy
if docker compose ps --format json 2>/dev/null | grep -q '"Health":"healthy"' 2>/dev/null; then
  ok "PostgreSQL healthy"
  ok "Redis healthy"
else
  # Fallback check for older docker compose
  sleep 3
  if docker compose exec -T postgres pg_isready -U trader -d memcoin_trader >/dev/null 2>&1; then
    ok "PostgreSQL ready"
  else
    err "PostgreSQL not ready"
    exit 1
  fi
  if docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then
    ok "Redis ready"
  else
    err "Redis not ready"
    exit 1
  fi
fi

# ── Step 3: DB Migrations ──────────────────────────────────────────
log "Step 3/6: Running database migrations..."

if "$VENV_PYTHON" -m alembic upgrade head 2>&1 | tail -3 | while read -r line; do echo "  $line"; done; then
  ok "Migrations applied"
else
  err "Migration failed"
  exit 1
fi

# ── Step 4: Frontend build ──────────────────────────────────────────
log "Step 4/6: Building frontend..."

if [ -d "frontend" ]; then
  cd frontend
  if [ ! -d "node_modules" ]; then
    npm install --silent 2>&1 | tail -1
  fi
  if npm run build 2>&1 | tail -3 | while read -r line; do echo "  $line"; done; then
    ok "Frontend built ($(du -sh dist 2>/dev/null | cut -f1) total)"
  else
    err "Frontend build failed"
    exit 1
  fi
  cd "$PROJECT_ROOT"
else
  warn "frontend/ not found, skipping build"
fi

# ── Step 5: Tests ───────────────────────────────────────────────────
if $SKIP_TESTS; then
  warn "Step 5/6: Tests SKIPPED (--skip-tests flag)"
else
  log "Step 5/6: Running tests..."

  TEST_OUTPUT=$("$VENV_PYTHON" -m pytest tests/ -x -q --tb=short 2>&1)
  TEST_EXIT=$?

  # Show last few lines
  echo "$TEST_OUTPUT" | tail -5 | while read -r line; do echo "  $line"; done

  if [ $TEST_EXIT -ne 0 ]; then
    echo ""
    err "═══════════════════════════════════════════════"
    err " TESTS FAILED — System will NOT start"
    err "═══════════════════════════════════════════════"
    err ""
    err "Fix failing tests and re-run: ./scripts/start.sh"
    echo ""
    echo "Full output:"
    echo "$TEST_OUTPUT" | tail -30
    exit 1
  fi

  ok "All tests passed"
fi

# ── Step 6: PM2 Start ──────────────────────────────────────────────
log "Step 6/6: Starting application via PM2..."

mkdir -p logs

# Stop existing if running
pm2 delete memecoin-backend 2>/dev/null || true

# Start
pm2 start ecosystem.config.cjs 2>&1 | while read -r line; do echo "  $line"; done

# Wait and verify
sleep 3
if pm2 pid memecoin-backend >/dev/null 2>&1; then
  ok "Backend started (PID: $(pm2 pid memecoin-backend))"
else
  err "Backend failed to start. Check: pm2 logs memecoin-backend"
  exit 1
fi

# Save PM2 process list for auto-start on reboot
pm2 save --force 2>/dev/null

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}       Memecoin Radar — Started Successfully${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Dashboard:  ${CYAN}http://localhost:8080${NC}"
echo -e "  PM2 logs:   ${CYAN}pm2 logs memecoin-backend${NC}"
echo -e "  PM2 monit:  ${CYAN}pm2 monit${NC}"
echo -e "  Stop:       ${CYAN}./scripts/stop.sh${NC}"
echo ""

pm2 list
