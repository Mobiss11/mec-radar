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
