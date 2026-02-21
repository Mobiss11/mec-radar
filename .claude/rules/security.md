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
