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
