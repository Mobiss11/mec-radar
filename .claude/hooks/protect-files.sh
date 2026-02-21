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
