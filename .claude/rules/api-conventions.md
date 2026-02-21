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
