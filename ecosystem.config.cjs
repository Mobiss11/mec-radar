/**
 * PM2 Ecosystem config — Memecoin Radar
 *
 * Infrastructure (PostgreSQL, Redis) runs in Docker.
 * Application (backend + dashboard) runs via PM2.
 *
 * Usage:
 *   ./scripts/start.sh          # full pipeline: docker → tests → pm2
 *   pm2 start ecosystem.config.cjs
 *   pm2 logs memecoin-backend
 *   pm2 monit
 */

const path = require("path");

const PROJECT_ROOT = __dirname;
const VENV_PYTHON = path.join(PROJECT_ROOT, ".venv", "bin", "python");

module.exports = {
  apps: [
    {
      name: "memecoin-backend",
      script: VENV_PYTHON,
      args: "-m src.main",
      cwd: PROJECT_ROOT,
      interpreter: "none",

      // Environment
      env: {
        NODE_ENV: "production",
        PYTHONUNBUFFERED: "1",
      },

      // Process management
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_restarts: 10,
      min_uptime: "10s",
      restart_delay: 5000,

      // Logs
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      error_file: path.join(PROJECT_ROOT, "logs", "backend-error.log"),
      out_file: path.join(PROJECT_ROOT, "logs", "backend-out.log"),
      merge_logs: true,
      max_size: "50M",

      // Graceful shutdown
      kill_timeout: 10000,
      listen_timeout: 8000,
      shutdown_with_message: false,

      // Watch (disabled in prod — use pm2 restart manually)
      watch: false,
    },
  ],
};
