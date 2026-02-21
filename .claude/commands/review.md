Проведи полное code review текущих изменений (git diff):

1. Запусти `git diff --cached --stat` (или `git diff --stat` если нет staged)
2. Для каждого изменённого файла:
   - Проверь ИБ-правила из @.claude/rules/security.md
   - Проверь код на N+1, async anti-patterns, missing validation
   - Проверь наличие тестов для новой логики
3. Выдай отчёт по приоритетам P0-P3
4. Предложи конкретные фиксы для P0 и P1
