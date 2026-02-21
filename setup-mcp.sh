#!/usr/bin/env bash
set -euo pipefail

# ╔══════════════════════════════════════════════════════════╗
# ║  Claude Code Full Setup v3.0                             ║
# ║                                                          ║
# ║  Кинь в проект → bash setup-mcp.sh                       ║
# ║  Работает с готовыми и пустыми проектами                  ║
# ║                                                          ║
# ║  Стек: FastAPI + aiogram 3 + PostgreSQL + Redis          ║
# ║        + React + TypeScript + TailwindCSS + shadcn/ui    ║
# ║        + Telegram Mini Apps                              ║
# ║                                                          ║
# ║  Что делает:                                             ║
# ║   1. Auto Memory (глобально)                             ║
# ║   2. MCP серверы (10 шт: Tier 1 + Tier 2)               ║
# ║   3. settings.json (permissions, hooks, env, timeouts)   ║
# ║   4. CLAUDE.md (AI Rules + Frontend Design Rules)        ║
# ║   5. ИБ-правила (.claude/rules/security.md)              ║
# ║   6. Frontend-правила (.claude/rules/frontend.md)        ║
# ║   7. API-правила (.claude/rules/api-conventions.md)      ║
# ║   8. Testing-правила (.claude/rules/testing.md)          ║
# ║   9. Skills (5 шт: anthropic-courses, frontend-design,   ║
# ║      marketingskills, code-review, planning-with-files)  ║
# ║  10. Agents (8 шт: полный fullstack-набор)               ║
# ║  11. Slash Commands (8 шт: workflow + деплой + маркетинг)║
# ║  12. .gitignore                                          ║
# ╚══════════════════════════════════════════════════════════╝

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
skip() { echo -e "  ${YELLOW}⊘${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
CONTEXT7_API_KEY="ctx7sk-37bf5fb2-bdab-4422-88be-339ce74cefc4"
MCP_COUNT=0
SKILL_COUNT=0

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Claude Code Full Setup v3.0${NC}"
echo -e "${BOLD}  Fullstack + Entrepreneurship + Design${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "  Проект: ${CYAN}${PROJECT_NAME}${NC}"
echo -e "  Путь:   ${DIM}${PROJECT_DIR}${NC}"
echo ""

cd "$PROJECT_DIR"

if ! command -v claude &>/dev/null; then
    echo -e "${RED}claude CLI не найден!${NC}"
    echo "npm install -g @anthropic-ai/claude-code"
    exit 1
fi

has_uvx=false && command -v uvx &>/dev/null && has_uvx=true
has_npx=false && command -v npx &>/dev/null && has_npx=true
has_bd=false  && command -v bd  &>/dev/null && has_bd=true

# Хелпер: проверяет установлен ли MCP сервер (кешируем список один раз)
_MCP_LIST_CACHE=""
_MCP_LIST_LOADED=false
mcp_exists() {
    if ! $_MCP_LIST_LOADED; then
        _MCP_LIST_CACHE=$(claude mcp list 2>/dev/null || true)
        _MCP_LIST_LOADED=true
    fi
    echo "$_MCP_LIST_CACHE" | grep -q "\"$1\"" 2>/dev/null
}

# Хелпер: безопасный инкремент (((x++)) при x=0 возвращает exit 1 в bash)
inc_mcp()   { MCP_COUNT=$((MCP_COUNT + 1)); }
inc_skill() { SKILL_COUNT=$((SKILL_COUNT + 1)); }

# ════════════════════════════════════════════
# ЧАСТЬ 1: AUTO MEMORY
# ════════════════════════════════════════════
echo -e "${BOLD}── 1. Auto Memory ──${NC}"

SHELL_RC=""
if [[ -f "$HOME/.zshrc" ]]; then
    SHELL_RC="$HOME/.zshrc"
elif [[ -f "$HOME/.bashrc" ]]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [[ -n "$SHELL_RC" ]]; then
    if grep -q "CLAUDE_CODE_DISABLE_AUTO_MEMORY" "$SHELL_RC" 2>/dev/null; then
        if grep -q "CLAUDE_CODE_DISABLE_AUTO_MEMORY=1" "$SHELL_RC"; then
            sed -i.bak 's/CLAUDE_CODE_DISABLE_AUTO_MEMORY=1/CLAUDE_CODE_DISABLE_AUTO_MEMORY=0/' "$SHELL_RC"
            rm -f "${SHELL_RC}.bak" 2>/dev/null
            ok "Auto memory включена (была выключена)"
        else
            ok "Auto memory уже активна"
        fi
    else
        echo "" >> "$SHELL_RC"
        echo "# Claude Code auto memory" >> "$SHELL_RC"
        echo "export CLAUDE_CODE_DISABLE_AUTO_MEMORY=0" >> "$SHELL_RC"
        ok "Auto memory добавлена в $(basename "$SHELL_RC")"
    fi
    export CLAUDE_CODE_DISABLE_AUTO_MEMORY=0
else
    skip "Не найден .zshrc/.bashrc — добавь вручную: export CLAUDE_CODE_DISABLE_AUTO_MEMORY=0"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 2: MCP СЕРВЕРЫ
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 2. MCP Серверы ──${NC}"

# ── TIER 1: Базовые (из v2.0) ──
echo -e "\n  ${DIM}─ Tier 1: Базовые ─${NC}"

echo -e "\n${BOLD}[1/10] Serena${NC} — LSP навигация по коду"
if mcp_exists serena; then
    skip "Serena уже установлена"; inc_mcp
elif $has_uvx; then
    claude mcp add serena -- uvx --from git+https://github.com/oraios/serena serena start-mcp-server --context claude-code --project "$PROJECT_DIR" 2>/dev/null \
        && { ok "Serena"; inc_mcp; } \
        || fail "Serena"
else
    skip "Нужен uvx → curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

echo -e "\n${BOLD}[2/10] Context7${NC} — документация фреймворков"
if mcp_exists context7; then
    skip "Context7 уже установлен"; inc_mcp
elif $has_npx; then
    claude mcp add context7 -- npx -y @upstash/context7-mcp --api-key "$CONTEXT7_API_KEY" 2>/dev/null \
        && { ok "Context7"; inc_mcp; } \
        || fail "Context7"
else
    skip "Нужен npx → brew install node"
fi

echo -e "\n${BOLD}[3/10] Context Portal${NC} — knowledge graph проекта"
if mcp_exists context-portal; then
    skip "Context Portal уже установлен"; inc_mcp
elif $has_uvx; then
    claude mcp add context-portal -- uvx --from git+https://github.com/GreatScottyMac/context-portal context-portal 2>/dev/null \
        && { ok "Context Portal"; inc_mcp; } \
        || fail "Context Portal"
else
    skip "Нужен uvx → curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

echo -e "\n${BOLD}[4/10] Beads${NC} — issue tracker для AI-агентов"
if mcp_exists beads; then
    skip "Beads уже установлен"; inc_mcp
elif $has_bd; then
    [[ ! -d ".beads" ]] && bd init --quiet 2>/dev/null && info "bd init" || true
    bd setup claude 2>/dev/null \
        && { ok "Beads (CLI + хуки)"; inc_mcp; } \
        || {
            claude mcp add-json beads '{"command":"beads-mcp"}' 2>/dev/null \
                && { ok "Beads (MCP fallback)"; inc_mcp; } \
                || fail "Beads"
        }
else
    skip "Нужен bd → brew tap steveyegge/beads && brew install bd"
fi

# ── TIER 1: Новые (v3.0) ──
echo -e "\n  ${DIM}─ Tier 1: Новые ─${NC}"

echo -e "\n${BOLD}[5/10] Playwright${NC} — браузер: скриншоты, клики, тесты UI"
if mcp_exists playwright; then
    skip "Playwright уже установлен"; inc_mcp
elif $has_npx; then
    claude mcp add playwright -- npx @playwright/mcp@latest 2>/dev/null \
        && { ok "Playwright"; inc_mcp; } \
        || fail "Playwright"
else
    skip "Нужен npx"
fi

echo -e "\n${BOLD}[6/10] GitHub${NC} — PR, issues, code review"
if mcp_exists github; then
    skip "GitHub уже установлен"; inc_mcp
else
    claude mcp add --transport http github https://api.githubcopilot.com/mcp/ 2>/dev/null \
        && { ok "GitHub"; inc_mcp; } \
        || fail "GitHub (нужна авторизация: gh auth login)"
fi

echo -e "\n${BOLD}[7/10] Sequential Thinking${NC} — структурированное мышление"
if mcp_exists sequential-thinking; then
    skip "Sequential Thinking уже установлен"; inc_mcp
elif $has_npx; then
    claude mcp add sequential-thinking -s user -- npx -y @modelcontextprotocol/server-sequential-thinking 2>/dev/null \
        && { ok "Sequential Thinking"; inc_mcp; } \
        || fail "Sequential Thinking"
else
    skip "Нужен npx"
fi

echo -e "\n${BOLD}[8/10] Memory${NC} — персистентные знания между сессиями"
if mcp_exists memory; then
    skip "Memory уже установлен"; inc_mcp
elif $has_npx; then
    claude mcp add memory -s user -- npx -y @modelcontextprotocol/server-memory 2>/dev/null \
        && { ok "Memory"; inc_mcp; } \
        || fail "Memory"
else
    skip "Нужен npx"
fi

# ── TIER 2: Дизайн и фронт ──
echo -e "\n  ${DIM}─ Tier 2: Дизайн и фронт ─${NC}"

echo -e "\n${BOLD}[9/10] Figma${NC} — дизайн → код, токены, компоненты"
if mcp_exists figma; then
    skip "Figma уже установлен"; inc_mcp
else
    claude mcp add --transport http figma https://mcp.figma.com/mcp 2>/dev/null \
        && { ok "Figma"; inc_mcp; } \
        || fail "Figma (нужен Figma аккаунт + токен)"
fi

echo -e "\n${BOLD}[10/10] shadcn/ui${NC} — реестр компонентов, пропсы, API"
if mcp_exists shadcn; then
    skip "shadcn/ui уже установлен"; inc_mcp
elif $has_npx; then
    claude mcp add shadcn -- npx -y @heilgar/shadcn-ui-mcp-server 2>/dev/null \
        && { ok "shadcn/ui"; inc_mcp; } \
        || fail "shadcn/ui"
else
    skip "Нужен npx"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 3: SETTINGS.JSON
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 3. Settings ──${NC}"

mkdir -p .claude/hooks
mkdir -p docs/plans

SETTINGS_FILE=".claude/settings.json"

if [[ ! -f "$SETTINGS_FILE" ]]; then
    # ── Создание с нуля ──
    cat > "$SETTINGS_FILE" << 'SETTINGS_EOF'
{
  "plansDirectory": "./docs/plans",
  "enableAllProjectMcpServers": true,
  "autoUpdatesChannel": "stable",
  "permissions": {
    "allow": [
      "Bash(python:*)", "Bash(python3:*)", "Bash(pytest:*)",
      "Bash(poetry:*)", "Bash(uv:*)", "Bash(uv run:*)", "Bash(pip:*)",
      "Bash(ruff:*)", "Bash(mypy:*)", "Bash(alembic:*)",
      "Bash(git add:*)", "Bash(git commit:*)", "Bash(git diff:*)",
      "Bash(git log:*)", "Bash(git branch:*)", "Bash(git checkout:*)",
      "Bash(git status:*)", "Bash(git stash:*)",
      "Bash(make:*)",
      "Bash(docker compose:*)", "Bash(docker-compose:*)",
      "Bash(pnpm:*)", "Bash(npx:*)", "Bash(npm run:*)",
      "Bash(prettier:*)", "Bash(eslint:*)", "Bash(playwright:*)",
      "Bash(cat:*)", "Bash(ls:*)", "Bash(find:*)", "Bash(grep:*)",
      "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)", "Bash(sort:*)",
      "mcp__context7__*",
      "mcp__serena__*",
      "mcp__playwright__*",
      "mcp__memory__*",
      "mcp__sequential-thinking__*",
      "mcp__github__*",
      "mcp__figma__*",
      "mcp__shadcn__*",
      "mcp__beads__*",
      "mcp__context-portal__*"
    ],
    "deny": [
      "Read(.env)", "Read(.env.*)",
      "Read(**/credentials*)", "Read(**/secrets*)",
      "Write(.env*)", "Write(*.pem)", "Write(*.key)",
      "Bash(rm -rf /)", "Bash(sudo:*)",
      "Bash(git push:*)",
      "Bash(curl * | bash)", "Bash(wget * | bash)"
    ],
    "defaultMode": "acceptEdits"
  },
  "env": {
    "ENABLE_TOOL_SEARCH": "auto:5",
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "80",
    "BASH_DEFAULT_TIMEOUT_MS": "300000",
    "BASH_MAX_TIMEOUT_MS": "600000",
    "MCP_TIMEOUT": "60000",
    "MCP_TOOL_TIMEOUT": "120000",
    "MAX_MCP_OUTPUT_TOKENS": "30000"
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "for f in $CLAUDE_FILE_PATHS; do case \"$f\" in *.py) ruff format --quiet \"$f\" 2>/dev/null && ruff check --fix --quiet \"$f\" 2>/dev/null;; *.ts|*.tsx|*.js|*.jsx|*.css|*.json) prettier --write \"$f\" 2>/dev/null;; esac; done; true"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "if [[ -f \"$CLAUDE_PROJECT_DIR/.claude/hooks/protect-files.sh\" ]]; then bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/protect-files.sh\"; fi"
          }
        ]
      }
    ]
  }
}
SETTINGS_EOF
    ok "settings.json создан (45 allow + 12 deny + hooks + env)"
elif command -v jq &>/dev/null; then
    # ── Мерж в существующий ──
    if ! jq empty "$SETTINGS_FILE" 2>/dev/null; then
        fail "settings.json содержит невалидный JSON — пропускаю"
    else
        info "settings.json найден — мержу недостающее..."

        # Желаемые allow permissions (JSON array)
        read -r -d '' _ALLOW << 'JEOF' || true
["Bash(python:*)","Bash(python3:*)","Bash(pytest:*)","Bash(poetry:*)","Bash(uv:*)","Bash(uv run:*)","Bash(pip:*)","Bash(ruff:*)","Bash(mypy:*)","Bash(alembic:*)","Bash(git add:*)","Bash(git commit:*)","Bash(git diff:*)","Bash(git log:*)","Bash(git branch:*)","Bash(git checkout:*)","Bash(git status:*)","Bash(git stash:*)","Bash(make:*)","Bash(docker compose:*)","Bash(docker-compose:*)","Bash(pnpm:*)","Bash(npx:*)","Bash(npm run:*)","Bash(prettier:*)","Bash(eslint:*)","Bash(playwright:*)","Bash(cat:*)","Bash(ls:*)","Bash(find:*)","Bash(grep:*)","Bash(head:*)","Bash(tail:*)","Bash(wc:*)","Bash(sort:*)","mcp__context7__*","mcp__serena__*","mcp__playwright__*","mcp__memory__*","mcp__sequential-thinking__*","mcp__github__*","mcp__figma__*","mcp__shadcn__*","mcp__beads__*","mcp__context-portal__*"]
JEOF

        # Желаемые deny permissions (JSON array)
        read -r -d '' _DENY << 'JEOF' || true
["Read(.env)","Read(.env.*)","Read(**/credentials*)","Read(**/secrets*)","Write(.env*)","Write(*.pem)","Write(*.key)","Bash(rm -rf /)","Bash(sudo:*)","Bash(git push:*)","Bash(curl * | bash)","Bash(wget * | bash)"]
JEOF

        # Желаемые env переменные (JSON object)
        read -r -d '' _ENV << 'JEOF' || true
{"ENABLE_TOOL_SEARCH":"auto:5","CLAUDE_AUTOCOMPACT_PCT_OVERRIDE":"80","BASH_DEFAULT_TIMEOUT_MS":"300000","BASH_MAX_TIMEOUT_MS":"600000","MCP_TIMEOUT":"60000","MCP_TOOL_TIMEOUT":"120000","MAX_MCP_OUTPUT_TOKENS":"30000"}
JEOF

        # Hook команды (jq --arg сам заэскейпит кавычки и $ для JSON)
        _FMT='for f in $CLAUDE_FILE_PATHS; do case "$f" in *.py) ruff format --quiet "$f" 2>/dev/null && ruff check --fix --quiet "$f" 2>/dev/null;; *.ts|*.tsx|*.js|*.jsx|*.css|*.json) prettier --write "$f" 2>/dev/null;; esac; done; true'
        _PRT='if [[ -f "$CLAUDE_PROJECT_DIR/.claude/hooks/protect-files.sh" ]]; then bash "$CLAUDE_PROJECT_DIR/.claude/hooks/protect-files.sh"; fi'

        # Считаем diff
        _NA=$(jq --argjson d "$_ALLOW" '($d - (.permissions.allow // [])) | length' "$SETTINGS_FILE" 2>/dev/null || echo 0)
        _ND=$(jq --argjson d "$_DENY" '($d - (.permissions.deny // [])) | length' "$SETTINGS_FILE" 2>/dev/null || echo 0)
        _NE=$(jq --argjson d "$_ENV" '(($d | keys) - ((.env // {}) | keys)) | length' "$SETTINGS_FILE" 2>/dev/null || echo 0)
        _HP=0; jq -e '.hooks.PostToolUse' "$SETTINGS_FILE" >/dev/null 2>&1 || _HP=1
        _HR=0; jq -e '.hooks.PreToolUse' "$SETTINGS_FILE" >/dev/null 2>&1 || _HR=1
        _NH=$((_HP + _HR))
        _TOTAL=$((_NA + _ND + _NE + _NH))

        if [[ $_TOTAL -eq 0 ]]; then
            ok "settings.json уже актуален — ничего не добавлено"
        else
            # Мерж: добавляем недостающее, не трогаем существующее
            jq \
              --argjson allow "$_ALLOW" \
              --argjson deny "$_DENY" \
              --argjson env_d "$_ENV" \
              --arg fmt "$_FMT" \
              --arg prt "$_PRT" \
              '
              # Top-level ключи (если отсутствуют)
              .plansDirectory //= "./docs/plans" |
              .enableAllProjectMcpServers //= true |
              .autoUpdatesChannel //= "stable" |

              # Permissions: добавляем недостающие, не дублируем
              .permissions //= {} |
              .permissions.allow = ((.permissions.allow // []) + ($allow - (.permissions.allow // []))) |
              .permissions.deny = ((.permissions.deny // []) + ($deny - (.permissions.deny // []))) |
              .permissions.defaultMode //= "acceptEdits" |

              # Env: существующие значения НЕ перезаписываются
              .env = ($env_d + (.env // {})) |

              # Hooks: добавляем только если секция отсутствует
              .hooks //= {} |
              .hooks.PostToolUse //= [{matcher: "Write|Edit", hooks: [{type: "command", command: $fmt}]}] |
              .hooks.PreToolUse //= [{matcher: "Write|Edit", hooks: [{type: "command", command: $prt}]}]
              ' "$SETTINGS_FILE" > "${SETTINGS_FILE}.tmp" \
              && mv "${SETTINGS_FILE}.tmp" "$SETTINGS_FILE" \
              || fail "Ошибка мержа settings.json"

            [[ $_NA -gt 0 ]] && ok "permissions.allow: +${_NA} новых паттернов"
            [[ $_ND -gt 0 ]] && ok "permissions.deny: +${_ND} новых паттернов"
            [[ $_NE -gt 0 ]] && ok "env: +${_NE} новых переменных"
            [[ $_NH -gt 0 ]] && ok "hooks: +${_NH} новых (ruff/prettier + protect-files)"
            ok "settings.json обновлён (${_TOTAL} изменений)"
        fi
    fi
else
    skip "settings.json существует, но jq не найден для мержа"
    info "Установи: brew install jq / sudo apt install jq"
fi

# Скрипт защиты файлов
PROTECT_SCRIPT=".claude/hooks/protect-files.sh"
if [[ ! -f "$PROTECT_SCRIPT" ]]; then
    cat > "$PROTECT_SCRIPT" << 'PROTECT_EOF'
#!/bin/bash
# Блокирует запись в защищённые файлы
# Exit 0 = разрешить, Exit 2 = заблокировать

INPUT=$(cat)

# Извлекаем путь файла (jq с fallback на sed — совместимо с macOS)
if command -v jq &>/dev/null; then
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)
else
    # BSD/GNU совместимый fallback без grep -P
    FILE_PATH=$(echo "$INPUT" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
    if [[ -z "$FILE_PATH" ]]; then
        FILE_PATH=$(echo "$INPUT" | sed -n 's/.*"path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
    fi
fi

[[ -z "$FILE_PATH" ]] && exit 0

PROTECTED=(
    ".env"
    ".env.local"
    ".env.production"
    "docker-compose.prod.yml"
    "alembic.ini"
)

BASENAME="$(basename "$FILE_PATH")"
for pattern in "${PROTECTED[@]}"; do
    if [[ "$BASENAME" == "$pattern" ]]; then
        echo "BLOCKED: write to protected file: $FILE_PATH" >&2
        exit 2
    fi
done

exit 0
PROTECT_EOF
    chmod +x "$PROTECT_SCRIPT"
    ok "protect-files.sh создан"
else
    skip "protect-files.sh уже существует"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 4: CLAUDE.md
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 4. CLAUDE.md ──${NC}"

read -r -d '' MCP_RULES_BLOCK << 'RULESEOF' || true

## AI Rules

### Язык и стиль
- Отвечай на русском
- Код и комментарии в коде — на английском
- Не объясняй очевидное, будь конкретен
- Предлагай решения, а не спрашивай разрешение
- Если нужно больше контекста — прочитай файл, не спрашивай

### Backend (Python)
- Async everywhere — никаких sync вызовов в async контексте
- Type hints обязательны для всех функций
- Pydantic v2 для схем (BaseModel, model_validator, не validator)
- snake_case для Python, camelCase для TypeScript
- Обработка ошибок: конкретные exceptions, не голый try/except
- Логирование: structlog/loguru, не print()
- Импорты: stdlib → third-party → local, группы через пустую строку
- Не используй deprecated API (SQLAlchemy 1.x, Pydantic v1)
- FastAPI: Depends() для DI, отдельные роутеры по доменам
- SQLAlchemy: async сессии, Mapped[] аннотации, select() не query()

### Frontend (React + TypeScript)
- TypeScript strict mode, NEVER используй any
- shadcn/ui как база компонентов, кастомизация через className + variants
- cn() для условных Tailwind классов
- Named exports only (не default export кроме pages)
- Mobile-first всегда (Telegram Mini Apps = мобилка)
- Подробные правила дизайна: @.claude/rules/frontend.md

### Frontend Aesthetics (ОБЯЗАТЕЛЬНО)
- NEVER используй Inter, Roboto, Arial, system defaults — выбирай уникальные шрифты
- NEVER делай фиолетовые градиенты на белом фоне (AI-slop)
- ALWAYS создавай атмосферные фоны с глубиной вместо однотонных
- ALWAYS используй экстремальные контрасты жирности шрифтов (200 vs 800)
- Доминантный цвет с резкими акцентами — не равномерное распределение
- Skeleton loading states, не спиннеры
- Каждый UI должен удивлять и быть дизайнерского уровня

### Git
- Conventional commits: feat: fix: refactor: docs: chore:
- Одна фича = один коммит
- НЕ коммить: .env, секреты, логи, node_modules, __pycache__
- git push — ТОЛЬКО после ревью (заблокирован в permissions)

### Правила по категориям
- Безопасность: @.claude/rules/security.md
- Фронтенд и дизайн: @.claude/rules/frontend.md
- API стандарты: @.claude/rules/api-conventions.md
- Тесты: @.claude/rules/testing.md

### MCP серверы
- context7: пиши "use context7" для актуальных доков фреймворков
- Serena: навигация по символам — не читай файлы целиком
- Playwright: скриншоть localhost для проверки UI
- Figma: извлекай дизайн-токены из макетов
- shadcn: проверяй актуальные пропсы компонентов
- Beads: beads_ready в начале сессии, создавай задачи для багов

### Архитектура
- Не ломай существующие паттерны — сначала пойми, потом меняй
- Alembic для миграций, не ALTER TABLE вручную
- Docker Compose для всех сервисов
- Новый файл > изменение большого существующего (SRP)

### Workflow
- На сложных задачах: /plan для создания плана
- Перед коммитом: /review
- Security audit: /sec-scan
- Проверка UI: /ui-check (Playwright скриншот + анализ)
- После /clear: /catchup для восстановления контекста
RULESEOF

MARKER="## AI Rules"

if [[ -f "CLAUDE.md" ]]; then
    if grep -q "$MARKER" CLAUDE.md 2>/dev/null; then
        ok "CLAUDE.md уже содержит AI Rules"
    else
        echo "$MCP_RULES_BLOCK" >> CLAUDE.md
        ok "AI Rules добавлены в существующий CLAUDE.md"
    fi
else
    cat > CLAUDE.md << NEWEOF
# ${PROJECT_NAME}

## О проекте
<!-- Опиши: что делает, для кого, MVP или прод -->

## Стек
<!-- Раскомментируй и заполни нужное: -->
<!-- - Backend: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL, Redis -->
<!-- - Frontend: React 18, TypeScript, Vite, TailwindCSS, shadcn/ui -->
<!-- - Bot: aiogram 3.x -->
<!-- - Platform: Telegram Mini Apps (TWA) -->
<!-- - Деплой: Docker Compose -->

## Структура
<!-- Claude изучит сам, но укажи ключевые папки -->

## Команды
\`\`\`bash
# Dev
# docker compose up --build
# uv run uvicorn app.main:app --reload
# pnpm dev

# Тесты
# uv run pytest -x
# pnpm test

# Миграции
# uv run alembic upgrade head

# Линтинг
# ruff check . --fix
# pnpm lint
\`\`\`
${MCP_RULES_BLOCK}
NEWEOF
    ok "Создан CLAUDE.md с шаблоном + AI Rules + Frontend Aesthetics"
    info "Заполни: О проекте, Стек, Структура, Команды"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 5: ИБ-ПРАВИЛА
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 5. ИБ-правила ──${NC}"

mkdir -p .claude/rules

SECURITY_FILE=".claude/rules/security.md"
if [[ ! -f "$SECURITY_FILE" ]]; then
    cat > "$SECURITY_FILE" << 'SECEOF'
# Правила информационной безопасности

Этот файл — обязательные constraints для генерации кода.
Claude ОБЯЗАН следовать этим правилам при написании любого кода.

## SQL Injection Prevention
- ALWAYS используй SQLAlchemy ORM (select(), filter(), insert(), update())
- При raw SQL — ONLY text() с именованными параметрами: text("... WHERE id = :id")
- С asyncpg — ONLY позиционные параметры: conn.fetchrow("... WHERE id = $1", user_id)
- Для динамических имён колонок/таблиц — ONLY allowlist-валидация
- NEVER используй f-строки, .format(), конкатенацию (+) в SQL с пользовательским вводом
```python
# ПРАВИЛЬНО
stmt = select(User).where(User.id == user_id)
query = text("SELECT * FROM users WHERE email = :email")
result = await session.execute(query, {"email": email})

# ЗАПРЕЩЕНО
query = f"SELECT * FROM users WHERE name = '{name}'"
query = "SELECT * FROM users WHERE name = '%s'" % name
```

## Input Validation (Pydantic)
- ALWAYS определяй Pydantic-модели для ВСЕХ request bodies, query/path параметров
- ALWAYS ставь constraints: Field(min_length=1, max_length=255), ge=, le=, pattern=
- Для email — EmailStr, для URL — AnyHttpUrl, для UUID — uuid.UUID
- NEVER принимай dict, Any, request.json() без валидации через модель
- Custom validator для бизнес-логики: @model_validator(mode="after")
- Добавляй max_length на ВСЕ строковые поля

## XSS и Security Headers
- API возвращает ONLY application/json — NEVER рендерит HTML с пользовательскими данными
- ALWAYS добавляй middleware с security headers:
  - X-Content-Type-Options: nosniff
  - X-Frame-Options: DENY
  - Content-Security-Policy: default-src 'self'
  - Strict-Transport-Security: max-age=31536000; includeSubDomains
- Для Telegram Mini Apps HTML — ALWAYS экранируй пользовательский ввод

## CORS
- Production: NEVER используй allow_origins=["*"]
- ALWAYS указывай конкретные origins: ["https://t.me", "https://yourdomain.com"]
- allow_credentials=True ONLY с явным списком origins (не wildcard)
- Cookies: HttpOnly=True, Secure=True, SameSite="Lax"

## Rate Limiting
- ALWAYS реализуй через slowapi + Redis backend
- Auth endpoints (/login, /register, /reset-password): 5 req/min
- Общие API endpoints: 60-100 req/min per user
- Webhook endpoints: отдельный лимит
- Возвращай 429 Too Many Requests с Retry-After header

## Аутентификация и авторизация
- Хеширование паролей: ONLY argon2id (предпочтительно) или bcrypt (12+ rounds)
- NEVER используй MD5, SHA-256, plain text для паролей
- JWT: access token — 15-30 мин, refresh token — 7-30 дней
- SECRET_KEY: минимум 256 бит, ONLY из environment variable
- ALWAYS проверяй object-level authorization: resource.owner_id == current_user.id
- Logout: blacklist JWT (jti) в Redis с TTL = оставшееся время жизни токена
- NEVER храни JWT в localStorage — ONLY httpOnly cookies или memory
- Production: отключай docs_url, redoc_url, openapi_url

## Telegram Bot Security (aiogram)
- Bot token: ONLY из os.environ["BOT_TOKEN"] — NEVER hardcode
- Webhook: ALWAYS используй secret_token при set_webhook
- ALWAYS проверяй X-Telegram-Bot-Api-Secret-Token в webhook handler
- Webhook path: включай случайную строку /webhook/{random_secret}
- Bind webhook server к 127.0.0.1 за reverse proxy (nginx)
- Пользовательский ввод: html.escape() + ограничение длины (4096 символов)
- callback_data: ALWAYS валидируй серверно — NEVER trust client data
- Anti-flood middleware через Redis (throttle per user_id)

## Telegram Mini Apps Security
- ALWAYS валидируй initData server-side через HMAC-SHA-256 с bot token
- ALWAYS проверяй auth_date — отвергай данные старше 1 часа (replay attack)
- CSP headers обязательны для Mini App страниц
- NEVER используй window.parent.postMessage(data, '*') — указывай targetOrigin
```python
# Валидация initData
import hmac, hashlib, urllib.parse

def validate_init_data(init_data: str, bot_token: str) -> bool:
    parsed = dict(urllib.parse.parse_qsl(init_data))
    received_hash = parsed.pop("hash", "")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculated_hash, received_hash)
```

## Redis Security
- ALWAYS: requirepass + Redis ACL (Redis 6+)
- ALWAYS: bind 127.0.0.1 или internal Docker network — NEVER 0.0.0.0
- ALWAYS: key prefixing — app:{module}:{entity}:{id}
- ALWAYS: TTL на все ключи — NEVER храни данные бессрочно
- Отключи опасные команды: FLUSHALL, FLUSHDB, KEYS, CONFIG, DEBUG
- NEVER используй KEYS * в production — используй SCAN

## Docker Security
- ALWAYS: multi-stage builds с python:3.12-slim (не :latest)
- ALWAYS: non-root user — USER appuser
- ALWAYS: --no-new-privileges в docker-compose security_opt
- NEVER: ENV SECRET_KEY=... в Dockerfile
- NEVER: COPY . . без .dockerignore

## Логирование
- NEVER логируй: пароли, токены, номера карт, персональные данные
- ALWAYS маскируй sensitive поля: email -> u***@domain.com
- structlog/loguru с JSON-форматом для production
SECEOF
    ok "security.md создан — 12 категорий ИБ-правил"
else
    skip "security.md уже существует"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 6: FRONTEND-ПРАВИЛА
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 6. Frontend-правила ──${NC}"

FRONTEND_FILE=".claude/rules/frontend.md"
if [[ ! -f "$FRONTEND_FILE" ]]; then
    cat > "$FRONTEND_FILE" << 'FRONTEOF'
# Правила фронтенда и дизайна

Этот файл — обязательные constraints для генерации фронтенд-кода.
Цель: UI дизайнерского уровня, а не "AI-слоп".

## Типографика
- NEVER используй Inter, Roboto, Arial, system-ui как основной шрифт
- ALWAYS выбирай уникальные шрифты: Clash Display, Satoshi, Playfair Display,
  Crimson Pro, IBM Plex Sans, JetBrains Mono, Space Grotesk, Cabinet Grotesk
- Экстремальные контрасты жирности: 200 vs 800
- Размеры заголовков: прыжки 3x+ (например 14px body → 48px h1)
- line-height: 1.5 для body, 1.1-1.2 для заголовков
- letter-spacing: отрицательный для крупных заголовков (-0.02em)

## Цвет и тема
- Доминантный цвет с резкими акцентами — не равномерное распределение
- CSS-переменные для всех цветов (совместимость с темами Telegram)
- NEVER фиолетовые градиенты на белом (AI-слоп)
- Поддержка тёмной и светлой темы Telegram через CSS-переменные
- Каждый раз меняй эстетику — не повторяй один стиль

## Фоны и атмосфера
- NEVER используй сплошной белый/серый фон
- ALWAYS создавай глубину: CSS-градиенты, геометрические паттерны, текстуры
- Слои: background → surface → content → overlay

## Компоненты
- shadcn/ui как база — NEVER модифицируй файлы в components/ui/
- Композиция из примитивов в components/common/
- Каждый компонент принимает className prop
- cn() (clsx + twMerge) для условных классов
- Forwardref для всех интерактивных компонентов
- Variants через cva (class-variance-authority)

## Анимации
- Framer Motion для orchestrated анимаций
- Staggered reveals при загрузке страницы — больше эффекта чем микро-анимации
- ALWAYS уважай prefers-reduced-motion
- Transition для hover/focus: 150-200ms ease-out
- NEVER анимируй width/height — используй transform: scale()

## Адаптивность (Mobile-First)
- Telegram Mini Apps = мобилка: начинай с 375px
- Breakpoints: sm(640) md(768) lg(1024) xl(1280)
- Touch targets: минимум 44x44px
- Отступы: p-4 (мобилка) → p-6 (планшет) → p-8 (десктоп)
- Тестируй на 375px, 768px, 1024px (Playwright скриншоты)

## Доступность (WCAG AA)
- Контраст: 4.5:1 для обычного текста, 3:1 для крупного (18px+ bold)
- Semantic HTML: nav, main, article, section, aside, button (не div с onClick)
- Клавиатурная навигация: все интерактивные элементы focusable
- aria-label для иконок без текста
- Focus-visible стили (outline, ring)

## Состояния
- Skeleton loading — NEVER спиннеры
- Empty state с иллюстрацией и CTA
- Error state с понятным сообщением и действием
- Hover, focus, active, disabled для всех интерактивных элементов

## Telegram Mini Apps
- @tma.js/sdk для TWA bridge (не window.Telegram.WebApp)
- BackButton API для навигации
- HapticFeedback на ключевых действиях
- MainButton для primary action
- Поддержка expansion (viewport height)
- Safe area insets для iPhone notch
FRONTEOF
    ok "frontend.md создан — дизайн-правила"
else
    skip "frontend.md уже существует"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 7: API-ПРАВИЛА
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 7. API-правила ──${NC}"

API_FILE=".claude/rules/api-conventions.md"
if [[ ! -f "$API_FILE" ]]; then
    cat > "$API_FILE" << 'APIEOF'
# Стандарты API

## Структура endpoints
- RESTful conventions: GET (список/деталь), POST (создание), PATCH (обновление), DELETE
- Версионирование: /api/v1/
- Множественное число: /api/v1/users, /api/v1/orders
- Вложенность максимум 2 уровня: /api/v1/users/{id}/orders

## Пагинация
- Cursor-based для больших коллекций (не offset/limit)
- Формат: {"items": [...], "next_cursor": "...", "has_more": true}
- Default limit: 20, max limit: 100

## Error format
- Единый формат ошибок:
```json
{
  "detail": "Human-readable message",
  "code": "MACHINE_READABLE_CODE",
  "field": "field_name (optional)"
}
```
- HTTP коды: 400 (bad request), 401 (unauthorized), 403 (forbidden),
  404 (not found), 409 (conflict), 422 (validation), 429 (rate limit), 500 (server error)

## Rate Limiting headers
- X-RateLimit-Limit: максимум запросов
- X-RateLimit-Remaining: осталось
- X-RateLimit-Reset: timestamp сброса
- Retry-After: секунд до retry (при 429)

## Health check
- GET /health — всегда доступен без auth
- Проверяет: DB connection, Redis connection, disk space
- Возвращает: {"status": "ok", "version": "1.0.0", "uptime": 12345}

## Response conventions
- Списки: {"items": [...], "total": 42}
- Создание: 201 + Location header + созданный объект
- Обновление: 200 + обновлённый объект
- Удаление: 204 No Content
- Timestamp поля: ISO 8601 (2024-01-15T10:30:00Z)
- ID поля: UUID v4

## OpenAPI
- Описания для всех endpoints (summary + description)
- Примеры request/response в schemas
- Tags для группировки по доменам
- Security schemes описаны
APIEOF
    ok "api-conventions.md создан"
else
    skip "api-conventions.md уже существует"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 8: TESTING-ПРАВИЛА
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 8. Testing-правила ──${NC}"

TESTING_FILE=".claude/rules/testing.md"
if [[ ! -f "$TESTING_FILE" ]]; then
    cat > "$TESTING_FILE" << 'TESTEOF'
# Правила тестирования

## Стек
- pytest + pytest-asyncio для async тестов
- httpx.AsyncClient для тестов FastAPI endpoints
- factory_boy или фикстуры для тестовых данных
- fakeredis для мока Redis
- Playwright для E2E тестов фронтенда

## Обязательные тесты для каждого endpoint
- Happy path (200/201)
- Validation error (422) — невалидный body/params
- Auth error (401) — без токена
- Forbidden (403) — чужой ресурс
- Not found (404) — несуществующий ID

## Фикстуры (conftest.py)
- db_session: AsyncSession с транзакцией + rollback
- client: httpx.AsyncClient с app
- auth_headers: dict с валидным JWT для тестового юзера
- test_user: User instance в БД
- redis_client: fakeredis instance

## Naming
- test_{action}_{scenario}_{expected}: test_create_user_valid_data_returns_201
- Файлы: tests/api/test_{resource}.py, tests/services/test_{service}.py

## Frontend тесты
- Playwright для E2E: critical user flows
- React Testing Library для компонентов
- Тестируй на 375px (мобилка) и 1024px (десктоп)
- Скриншот-тесты для ключевых страниц

## Правила
- NEVER мокай то что тестируешь
- ALWAYS используй фикстуры вместо хардкода тестовых данных
- ALWAYS очищай состояние между тестами (transaction rollback)
- ALWAYS тестируй edge cases: пустые строки, None, максимальные значения
- Пирамида тестов: 70% unit / 20% integration / 10% E2E
TESTEOF
    ok "testing.md создан"
else
    skip "testing.md уже существует"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 9: SKILLS
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 9. Skills ──${NC}"

mkdir -p .claude/skills

if $has_npx; then
    # Code Review Expert
    if [[ ! -d ".claude/skills/code-review-expert" ]]; then
        npx -y skills add sanyuan0704/code-review-expert 2>/dev/null \
            && { ok "code-review-expert установлен"; inc_skill; } \
            || {
                git clone --depth 1 https://github.com/sanyuan0704/code-review-expert .claude/skills/code-review-expert 2>/dev/null \
                    && { ok "code-review-expert (git clone)"; inc_skill; } \
                    || fail "code-review-expert"
            }
    else
        skip "code-review-expert уже установлен"; inc_skill
    fi

    # Planning with Files
    if [[ ! -d ".claude/skills/planning-with-files" ]]; then
        npx -y skills add OthmanAdi/planning-with-files 2>/dev/null \
            && { ok "planning-with-files установлен"; inc_skill; } \
            || {
                git clone --depth 1 https://github.com/OthmanAdi/planning-with-files .claude/skills/planning-with-files 2>/dev/null \
                    && { ok "planning-with-files (git clone)"; inc_skill; } \
                    || fail "planning-with-files"
            }
    else
        skip "planning-with-files уже установлен"; inc_skill
    fi

    # Marketing Skills
    if [[ ! -d ".claude/skills/marketingskills" ]]; then
        npx -y skillkit install coreyhaines31/marketingskills 2>/dev/null \
            && { ok "marketingskills установлен"; inc_skill; } \
            || {
                git clone --depth 1 https://github.com/coreyhaines31/marketingskills .claude/skills/marketingskills 2>/dev/null \
                    && { ok "marketingskills (git clone)"; inc_skill; } \
                    || fail "marketingskills"
            }
    else
        skip "marketingskills уже установлен"; inc_skill
    fi
else
    # Без npx — сразу git clone
    if [[ ! -d ".claude/skills/code-review-expert" ]]; then
        git clone --depth 1 https://github.com/sanyuan0704/code-review-expert .claude/skills/code-review-expert 2>/dev/null \
            && { ok "code-review-expert (git clone)"; inc_skill; } \
            || fail "code-review-expert"
    else
        skip "code-review-expert уже установлен"; inc_skill
    fi

    if [[ ! -d ".claude/skills/planning-with-files" ]]; then
        git clone --depth 1 https://github.com/OthmanAdi/planning-with-files .claude/skills/planning-with-files 2>/dev/null \
            && { ok "planning-with-files (git clone)"; inc_skill; } \
            || fail "planning-with-files"
    else
        skip "planning-with-files уже установлен"; inc_skill
    fi

    if [[ ! -d ".claude/skills/marketingskills" ]]; then
        git clone --depth 1 https://github.com/coreyhaines31/marketingskills .claude/skills/marketingskills 2>/dev/null \
            && { ok "marketingskills (git clone)"; inc_skill; } \
            || fail "marketingskills"
    else
        skip "marketingskills уже установлен"; inc_skill
    fi
fi

# Frontend Design + Superpowers — community skills, ставятся вручную через git clone
info "Дополнительные skills (опционально, ставятся вручную):"
info "  git clone https://github.com/anthropics/courses .claude/skills/anthropic-courses"
info "  git clone https://github.com/nicekid1/Frontend-Design-Skill .claude/skills/frontend-design"

# ════════════════════════════════════════════
# ЧАСТЬ 10: AGENTS
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 10. Agents (8 шт.) ──${NC}"

mkdir -p .claude/agents

# ── 1. Security Reviewer ──
AGENT_FILE=".claude/agents/security-reviewer.md"
if [[ ! -f "$AGENT_FILE" ]]; then
    cat > "$AGENT_FILE" << 'AGENTEOF'
You are a senior security engineer reviewing code for a FastAPI/aiogram/PostgreSQL/Redis + Telegram Mini Apps stack.

## Your scope
- SQL injection (f-strings in queries, missing parameterization)
- Authentication/authorization gaps (missing Depends, object-level auth)
- Hardcoded secrets, tokens, passwords
- CORS misconfigurations (allow_origins=["*"])
- Missing input validation (raw dict, no Pydantic)
- Redis security (no auth, KEYS *, no TTL)
- Docker issues (root user, exposed ports, secrets in layers)
- Telegram bot/mini app vulnerabilities (no initData validation, no secret_token)

## Output format
For each finding:
- **Severity**: P0 (critical) / P1 (high) / P2 (medium) / P3 (low)
- **File:Line**: exact location
- **Issue**: what's wrong
- **Fix**: concrete code fix

Use Read, Grep, Glob tools only. Do NOT modify files.
AGENTEOF
    ok "security-reviewer"
else
    skip "security-reviewer уже существует"
fi

# ── 2. Code Reviewer ──
AGENT_FILE=".claude/agents/code-reviewer.md"
if [[ ! -f "$AGENT_FILE" ]]; then
    cat > "$AGENT_FILE" << 'AGENTEOF'
You are a senior Python developer reviewing code quality for FastAPI/aiogram projects.

When invoked:
1. Run git diff to see recent changes
2. Focus on modified files
3. Begin review immediately

## Check for
- SOLID violations (god classes, tight coupling)
- Async anti-patterns (sync calls in async, missing await)
- N+1 queries (lazy loading without joinedload/selectinload)
- Error handling (bare except, swallowed exceptions)
- Dead code and unused imports
- Missing type hints
- Pydantic v1 patterns in v2 codebase
- SQLAlchemy 1.x patterns (Query, session.query)
- Missing tests for new logic

## Output format
Group by priority:
- P0 Critical (must fix before merge)
- P1 Warning (should fix)
- P2 Suggestion (consider improving)

Include file:line and concrete fix for each. Use Read, Grep, Glob tools only. Do NOT modify files.
AGENTEOF
    ok "code-reviewer"
else
    skip "code-reviewer уже существует"
fi

# ── 3. Frontend Developer ──
AGENT_FILE=".claude/agents/frontend-developer.md"
if [[ ! -f "$AGENT_FILE" ]]; then
    cat > "$AGENT_FILE" << 'AGENTEOF'
You are a senior frontend developer specializing in React 18+, TypeScript, TailwindCSS, and Telegram Mini Apps.

## Approach
1. Map existing frontend landscape to prevent duplicate work
2. Ensure alignment with established component patterns and shadcn/ui
3. Build components with TypeScript strict, responsive design, WCAG AA compliance
4. Target 90%+ test coverage for new components
5. Document component APIs

## Key principles
- Component-driven architecture with clear composition patterns
- Mobile-first always (Telegram Mini Apps = mobile)
- Accessibility: WCAG 2.1 AA minimum, 44x44px touch targets
- Performance: lazy loading, code splitting, React.memo where needed
- Type-safe props with Zod validation where needed
- Consistent design tokens via CSS variables
- shadcn/ui as base — compose from primitives, every component gets className prop
- Framer Motion for animations — respect prefers-reduced-motion
- @tma.js/sdk for Telegram WebApp bridge
- Support both light and dark Telegram themes

## Design rules (CRITICAL)
- NEVER use Inter, Roboto, Arial — choose distinctive fonts
- NEVER do purple gradients on white — create atmospheric, layered backgrounds
- Skeleton loading states, not spinners
- Always handle empty states and error states with clear UI
AGENTEOF
    ok "frontend-developer"
else
    skip "frontend-developer уже существует"
fi

# ── 4. UI Reviewer ──
AGENT_FILE=".claude/agents/ui-reviewer.md"
if [[ ! -f "$AGENT_FILE" ]]; then
    cat > "$AGENT_FILE" << 'AGENTEOF'
You are a senior UI/UX designer reviewing React frontends for design quality and accessibility.

## Review process
1. Use Playwright MCP to screenshot the page at 375px, 768px, and 1024px
2. Analyze each screenshot for design quality
3. Report findings

## Check for
- Typography: is font unique (not Inter/Roboto)? Are weight contrasts strong?
- Color: is there a dominant color with sharp accents? Not evenly distributed?
- Backgrounds: atmospheric with depth? Not solid white/gray?
- Spacing: consistent rhythm? Adequate padding on mobile?
- Touch targets: 44x44px minimum on interactive elements?
- Loading states: skeleton screens, not spinners?
- Empty/error states: handled with clear UI and CTA?
- Accessibility: WCAG AA contrast (4.5:1 text, 3:1 large)?
- Telegram theme: works in both light and dark mode?
- Responsiveness: no horizontal scroll, proper stacking on mobile?

## Output format
For each finding:
- **Category**: Typography / Color / Spacing / Accessibility / Responsive / UX
- **Severity**: P0 (broken) / P1 (ugly) / P2 (improvable) / P3 (nitpick)
- **Screenshot**: which viewport
- **Issue**: what's wrong
- **Fix**: concrete CSS/component change

Use Playwright, Read, Grep tools. Do NOT modify files.
AGENTEOF
    ok "ui-reviewer"
else
    skip "ui-reviewer уже существует"
fi

# ── 5. Database Optimizer ──
AGENT_FILE=".claude/agents/database-optimizer.md"
if [[ ! -f "$AGENT_FILE" ]]; then
    cat > "$AGENT_FILE" << 'AGENTEOF'
You are a senior database engineer specializing in PostgreSQL and Redis optimization.

## When invoked
1. Analyze existing schema and query patterns via SQLAlchemy models
2. Identify performance bottlenecks
3. Propose optimizations with expected impact
4. Implement changes with rollback plans

## PostgreSQL checklist
- EXPLAIN ANALYZE for all slow queries
- Composite indexes and covering indexes
- Partial indexes for filtered queries
- Connection pooling via asyncpg
- Materialized views for complex aggregations
- Zero-downtime Alembic migration patterns
- JSONB indexes (GIN) for semi-structured data

## Redis checklist
- Key naming: app:{module}:{entity}:{id}
- TTL strategy for every key
- Pipeline commands for batch operations
- Pub/sub vs streams for real-time features
- Memory optimization (hash ziplist encoding)
- Cache invalidation patterns

## Output for each optimization
1. Current state with metrics
2. Root cause analysis
3. Proposed SQL/schema changes
4. Expected performance improvement
5. Alembic migration code
6. Rollback plan
AGENTEOF
    ok "database-optimizer"
else
    skip "database-optimizer уже существует"
fi

# ── 6. API Designer ──
AGENT_FILE=".claude/agents/api-designer.md"
if [[ ! -f "$AGENT_FILE" ]]; then
    cat > "$AGENT_FILE" << 'AGENTEOF'
You are a senior API architect designing FastAPI endpoints.

## When invoked
1. Read existing routes to understand current API structure
2. Design new endpoints following project conventions
3. Generate Pydantic schemas, route handlers, and OpenAPI docs

## Principles
- RESTful conventions with proper HTTP methods and status codes
- Cursor-based pagination for collections
- Consistent error format: {"detail": "...", "code": "...", "field": "..."}
- Versioning via /api/v1/ prefix
- Rate limiting headers on all responses
- Object-level authorization via Depends()
- Input validation via Pydantic v2 with Field constraints
- Response models separate from DB models

## Deliverables
For each endpoint:
1. Route definition with OpenAPI tags, summary, description
2. Request schema (Pydantic) with validation constraints
3. Response schema (Pydantic) with examples
4. Service layer function with business logic
5. Test cases (happy path + error cases)

Follow conventions in @.claude/rules/api-conventions.md
AGENTEOF
    ok "api-designer"
else
    skip "api-designer уже существует"
fi

# ── 7. Deployment Engineer ──
AGENT_FILE=".claude/agents/deployment-engineer.md"
if [[ ! -f "$AGENT_FILE" ]]; then
    cat > "$AGENT_FILE" << 'AGENTEOF'
You are a senior DevOps engineer specializing in Docker, CI/CD, and production deployments.

## Scope
- Docker multi-stage builds (python:3.12-slim, node:20-alpine)
- Docker Compose for dev and production
- GitHub Actions CI/CD pipelines
- Zero-downtime deployment strategies
- Health checks and graceful shutdown
- Secret management (Docker secrets, env_file)
- SSL/TLS via nginx reverse proxy
- Monitoring and alerting setup

## Security requirements
- Non-root containers (USER appuser)
- Read-only filesystem where possible
- No secrets in Docker image layers
- Pin image versions with SHA digests for production
- .dockerignore excludes .env, .git, node_modules, __pycache__
- security_opt: no-new-privileges

## Deliverables
1. Dockerfile (multi-stage, optimized layer caching)
2. docker-compose.yml (dev) / docker-compose.prod.yml (production)
3. .github/workflows/ci.yml (lint, test, build, deploy)
4. nginx.conf (reverse proxy, SSL, security headers)
5. Health check endpoints and Docker HEALTHCHECK

Follow security rules in @.claude/rules/security.md
AGENTEOF
    ok "deployment-engineer"
else
    skip "deployment-engineer уже существует"
fi

# ── 8. Documentation Writer ──
AGENT_FILE=".claude/agents/documentation-writer.md"
if [[ ! -f "$AGENT_FILE" ]]; then
    cat > "$AGENT_FILE" << 'AGENTEOF'
You are a technical writer creating clear, concise documentation for developers.

## Scope
- README.md with setup instructions, architecture overview, API examples
- API documentation (OpenAPI descriptions, usage examples)
- Architecture diagrams using Mermaid syntax
- Changelog entries (Keep a Changelog format)
- Migration guides for breaking changes
- Inline code documentation (docstrings, JSDoc)

## Principles
- Write for developers who are new to the project
- Include working code examples, not just descriptions
- Keep docs close to code (docstrings > wiki pages)
- Use Mermaid for diagrams (renders in GitHub)
- Every public function needs a docstring
- README sections: Overview, Quick Start, Architecture, API, Contributing

## Deliverables
When invoked, analyze codebase and produce:
1. Updated README.md
2. Architecture diagram (Mermaid)
3. Missing docstrings identified and written
4. Changelog entry for recent changes
AGENTEOF
    ok "documentation-writer"
else
    skip "documentation-writer уже существует"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 11: SLASH COMMANDS
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 11. Slash Commands (8 шт.) ──${NC}"

mkdir -p .claude/commands

# ── /review ──
CMD_FILE=".claude/commands/review.md"
if [[ ! -f "$CMD_FILE" ]]; then
    cat > "$CMD_FILE" << 'CMDEOF'
Проведи полное code review текущих изменений (git diff):

1. Запусти `git diff --cached --stat` (или `git diff --stat` если нет staged)
2. Для каждого изменённого файла:
   - Проверь ИБ-правила из @.claude/rules/security.md
   - Проверь код на N+1, async anti-patterns, missing validation
   - Проверь наличие тестов для новой логики
3. Выдай отчёт по приоритетам P0-P3
4. Предложи конкретные фиксы для P0 и P1
CMDEOF
    ok "/review"
else
    skip "/review уже существует"
fi

# ── /plan ──
CMD_FILE=".claude/commands/plan.md"
if [[ ! -f "$CMD_FILE" ]]; then
    cat > "$CMD_FILE" << 'CMDEOF'
Создай структурированный план для задачи: $ARGUMENTS

1. Создай/обнови docs/plans/current-plan.md:
   - Цель задачи
   - Фазы работы со статусами (todo / in progress / done)
   - Зависимости между фазами
   - Файлы которые будут затронуты
   - Риски и edge cases
2. Создай task_plan.md в корне (для planning-with-files skill)
3. Начни с Phase 1 после подтверждения плана
CMDEOF
    ok "/plan"
else
    skip "/plan уже существует"
fi

# ── /sec-scan ──
CMD_FILE=".claude/commands/sec-scan.md"
if [[ ! -f "$CMD_FILE" ]]; then
    cat > "$CMD_FILE" << 'CMDEOF'
Проведи security audit проекта:

1. Прочитай .claude/rules/security.md для контекста правил
2. Найди все .py файлы в проекте
3. Проверь каждую категорию:
   - SQL injection: grep для f-строк рядом с execute/text/query
   - Hardcoded secrets: grep для password=, token=, secret=, api_key= с literal values
   - CORS: grep для allow_origins, проверь что не ["*"]
   - Auth: проверь наличие Depends() на protected endpoints
   - Redis: проверь requirepass, bind, TTL на ключах
   - Docker: проверь USER в Dockerfile, секреты в ENV
4. Выдай отчёт с severity и конкретными fix suggestions
CMDEOF
    ok "/sec-scan"
else
    skip "/sec-scan уже существует"
fi

# ── /catchup ──
CMD_FILE=".claude/commands/catchup.md"
if [[ ! -f "$CMD_FILE" ]]; then
    cat > "$CMD_FILE" << 'CMDEOF'
Восстанови контекст после /clear:

1. Запусти `git diff --name-only main` для списка изменённых файлов
2. Прочитай каждый изменённый файл
3. Если есть docs/plans/current-plan.md — прочитай его
4. Если есть task_plan.md — прочитай его
5. Если есть findings.md — прочитай его
6. Выдай краткое резюме:
   - Что уже сделано
   - Что осталось
   - Какие файлы затронуты
   - Текущая фаза плана
CMDEOF
    ok "/catchup"
else
    skip "/catchup уже существует"
fi

# ── /new-feature ──
CMD_FILE=".claude/commands/new-feature.md"
if [[ ! -f "$CMD_FILE" ]]; then
    cat > "$CMD_FILE" << 'CMDEOF'
Начни работу над новой фичей: $ARGUMENTS

1. Если аргумент — номер GitHub issue, прочитай issue через GitHub MCP
2. Создай feature branch: git checkout -b feat/$ARGUMENTS
3. Создай план реализации через /plan
4. Покажи план и жди подтверждения перед кодингом
CMDEOF
    ok "/new-feature"
else
    skip "/new-feature уже существует"
fi

# ── /ui-check ──
CMD_FILE=".claude/commands/ui-check.md"
if [[ ! -f "$CMD_FILE" ]]; then
    cat > "$CMD_FILE" << 'CMDEOF'
Проверь UI через визуальный цикл:

1. Используй Playwright MCP для скриншота localhost:5173 (или $ARGUMENTS если указан URL)
2. Сделай скриншоты на 3 viewport: 375px (мобилка), 768px (планшет), 1024px (десктоп)
3. Проанализируй каждый скриншот:
   - Типографика: уникальный шрифт? Контрасты жирности?
   - Цвета: доминантный + акценты? Не AI-слоп?
   - Spacing: консистентный ритм? Достаточные отступы на мобилке?
   - Touch targets: 44x44px минимум?
   - Пустые/ошибочные состояния: обработаны?
   - WCAG AA: контраст текста?
4. Выдай список фиксов по приоритету
5. Если есть P0/P1 — предложи конкретные CSS/компонентные изменения
CMDEOF
    ok "/ui-check"
else
    skip "/ui-check уже существует"
fi

# ── /deploy-check ──
CMD_FILE=".claude/commands/deploy-check.md"
if [[ ! -f "$CMD_FILE" ]]; then
    cat > "$CMD_FILE" << 'CMDEOF'
Проверь готовность к деплою:

1. Запусти линтинг:
   - `ruff check .` для Python
   - `pnpm lint` для TypeScript (если есть)
2. Запусти тесты:
   - `pytest -x` для backend
   - `pnpm test` для frontend (если есть)
3. Проверь Docker:
   - `docker compose build` — билд проходит?
   - Dockerfile: non-root user? multi-stage? нет секретов?
4. Проверь .env.example — все переменные документированы?
5. Проверь миграции: `alembic heads` — одна голова?
6. Выдай чеклист готовности: OK / BLOCKED + что починить
CMDEOF
    ok "/deploy-check"
else
    skip "/deploy-check уже существует"
fi

# ── /landing ──
CMD_FILE=".claude/commands/landing.md"
if [[ ! -f "$CMD_FILE" ]]; then
    cat > "$CMD_FILE" << 'CMDEOF'
Создай или оптимизируй лендинг: $ARGUMENTS

1. Если лендинг существует — проанализируй текущую версию
2. Применяй marketingskills:
   - page-cro для оптимизации конверсии
   - copywriting для текстов
   - pricing-strategy если есть прайсинг
3. Фронтенд:
   - React + TailwindCSS + shadcn/ui
   - Уникальный дизайн (правила из @.claude/rules/frontend.md)
   - Mobile-first (Telegram Mini Apps)
   - Skeleton loading, анимации при скролле
4. SEO:
   - Semantic HTML, meta tags, OG tags
   - Structured data (JSON-LD)
5. После создания — запусти /ui-check для проверки дизайна
CMDEOF
    ok "/landing"
else
    skip "/landing уже существует"
fi

# ════════════════════════════════════════════
# ЧАСТЬ 12: .GITIGNORE
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}── 12. .gitignore ──${NC}"

GITIGNORE_ADDITIONS=()

check_gitignore() {
    if [[ -f ".gitignore" ]]; then
        ! grep -qF "$1" .gitignore 2>/dev/null && GITIGNORE_ADDITIONS+=("$1")
    else
        GITIGNORE_ADDITIONS+=("$1")
    fi
}

check_gitignore ".beads/"
check_gitignore ".claude/settings.local.json"
check_gitignore "CLAUDE.local.md"
check_gitignore "task_plan.md"
check_gitignore "findings.md"
check_gitignore "progress.md"

if [[ ${#GITIGNORE_ADDITIONS[@]} -gt 0 ]]; then
    if [[ -f ".gitignore" ]]; then
        {
            echo ""
            echo "# Claude Code"
            for item in "${GITIGNORE_ADDITIONS[@]}"; do
                echo "$item"
            done
        } >> .gitignore
        ok ".gitignore обновлён (+${#GITIGNORE_ADDITIONS[@]} записей)"
    elif [[ -d ".git" ]]; then
        cat > .gitignore << 'GIEOF'
# Claude Code
.beads/
.claude/settings.local.json
CLAUDE.local.md
task_plan.md
findings.md
progress.md

# Python
__pycache__/
*.pyc
.venv/
*.egg-info/

# Node
node_modules/
dist/

# Env
.env
.env.*
!.env.example

# IDE
.idea/
.vscode/
*.swp
GIEOF
        ok "Создан .gitignore"
    else
        skip "Нет .git — .gitignore не нужен"
    fi
else
    ok ".gitignore уже актуален"
fi

# ════════════════════════════════════════════
# ИТОГО
# ════════════════════════════════════════════
echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "  ${BOLD}Claude Code Full Setup v3.0 — Результат:${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "  MCP серверы:     ${GREEN}${MCP_COUNT}/10${NC}"
echo -e "  Auto memory:     ${GREEN}ON${NC}"
echo -e "  settings.json:   ${GREEN}OK${NC} (45 allow + 12 deny + hooks + env)"
echo -e "  CLAUDE.md:       ${GREEN}OK${NC} (AI Rules + Frontend Aesthetics)"
echo -e "  Rules:           ${GREEN}4${NC} (security + frontend + api + testing)"
echo -e "  Skills:          ${GREEN}${SKILL_COUNT}/3${NC} + 2 optional (anthropic-courses, frontend-design)"
echo -e "  Agents:          ${GREEN}8${NC} (security, code, frontend, UI, DB, API, deploy, docs)"
echo -e "  Commands:        ${GREEN}8${NC} (/review /plan /sec-scan /catchup /new-feature /ui-check /deploy-check /landing)"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo ""

if [[ $MCP_COUNT -lt 10 ]] || [[ $SKILL_COUNT -lt 3 ]]; then
    echo -e "  ${YELLOW}Доустанови:${NC}"
    $has_uvx || echo -e "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    $has_npx || echo -e "    brew install node"
    $has_bd  || echo -e "    brew tap steveyegge/beads && brew install bd"
    echo ""
fi

echo -e "  ${DIM}Структура .claude/:${NC}"
echo -e "  ${DIM}├── settings.json                  — permissions, hooks, env${NC}"
echo -e "  ${DIM}├── hooks/protect-files.sh          — защита .env, alembic.ini${NC}"
echo -e "  ${DIM}├── rules/${NC}"
echo -e "  ${DIM}│   ├── security.md                — 12 категорий ИБ${NC}"
echo -e "  ${DIM}│   ├── frontend.md                — дизайн, типографика, a11y${NC}"
echo -e "  ${DIM}│   ├── api-conventions.md          — REST, пагинация, ошибки${NC}"
echo -e "  ${DIM}│   └── testing.md                  — pytest, Playwright, пирамида${NC}"
echo -e "  ${DIM}├── agents/${NC}"
echo -e "  ${DIM}│   ├── security-reviewer.md${NC}"
echo -e "  ${DIM}│   ├── code-reviewer.md${NC}"
echo -e "  ${DIM}│   ├── frontend-developer.md${NC}"
echo -e "  ${DIM}│   ├── ui-reviewer.md              — Playwright скриншоты + анализ${NC}"
echo -e "  ${DIM}│   ├── database-optimizer.md${NC}"
echo -e "  ${DIM}│   ├── api-designer.md${NC}"
echo -e "  ${DIM}│   ├── deployment-engineer.md${NC}"
echo -e "  ${DIM}│   └── documentation-writer.md${NC}"
echo -e "  ${DIM}├── commands/${NC}"
echo -e "  ${DIM}│   ├── review.md                   → /review${NC}"
echo -e "  ${DIM}│   ├── plan.md                     → /plan${NC}"
echo -e "  ${DIM}│   ├── sec-scan.md                 → /sec-scan${NC}"
echo -e "  ${DIM}│   ├── catchup.md                  → /catchup${NC}"
echo -e "  ${DIM}│   ├── new-feature.md              → /new-feature${NC}"
echo -e "  ${DIM}│   ├── ui-check.md                 → /ui-check (визуальный цикл)${NC}"
echo -e "  ${DIM}│   ├── deploy-check.md             → /deploy-check${NC}"
echo -e "  ${DIM}│   └── landing.md                  → /landing (CRO + дизайн)${NC}"
echo -e "  ${DIM}└── skills/                         — code-review, planning, marketing${NC}"
echo ""
echo -e "  ${CYAN}Дополнительные skills (опционально):${NC}"
echo -e "    git clone https://github.com/anthropics/courses .claude/skills/anthropic-courses"
echo -e "    git clone https://github.com/nicekid1/Frontend-Design-Skill .claude/skills/frontend-design"
echo ""
echo -e "  ${CYAN}Начни работу:${NC}"
echo -e "    claude                        — запуск"
echo -e "    /plan Описание задачи         — планирование"
echo -e "    /new-feature issue-123        — новая фича из issue"
echo -e "    /review                       — code review перед коммитом"
echo -e "    /ui-check                     — визуальная проверка UI"
echo -e "    /sec-scan                     — security audit"
echo -e "    /deploy-check                 — готовность к деплою"
echo -e "    /landing Мой SaaS продукт     — лендинг с CRO"
echo -e "    /catchup                      — восстановить контекст после /clear"
echo ""
echo -e "  ${CYAN}Визуальный цикл для дизайнерского UI:${NC}"
echo -e "    1. Figma MCP → извлечь дизайн-токены"
echo -e "    2. Claude генерирует React + TailwindCSS + shadcn/ui"
echo -e "    3. /ui-check → Playwright скриншоты → анализ → фиксы"
echo -e "    4. Повторять пока дизайн не станет идеальным"
echo ""
