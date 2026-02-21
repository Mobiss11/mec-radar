# Phase 15: Chainstack gRPC + Vybe + TwitterAPI.io Integration

## Context

Переходим на платные API для ускорения обнаружения токенов и добавления новых сигналов:
- **Chainstack gRPC** ($49/мес) — замена PumpPortal WS, обнаружение токенов за <1s вместо 3-5s
- **Vybe Network** (free 25K credits) — holder PnL, top holders с уникальными данными
- **TwitterAPI.io** ($10 за 1M credits) — social signals (KOL mentions, viral tweets)

Ключи уже в `.env`, настройки в `settings.py`. Нужно написать клиенты и интегрировать в pipeline.

## Plan

### 1. Chainstack gRPC Client (`src/parsers/chainstack/`)

**Что делаем:** gRPC клиент на Yellowstone протоколе, слушает pump.fun транзакции напрямую с ноды.

**Файлы:**
- `src/parsers/chainstack/__init__.py`
- `src/parsers/chainstack/grpc_client.py` — основной клиент
- `src/parsers/chainstack/proto/` — сгенерированные protobuf файлы (geyser_pb2, solana_storage_pb2)
- `src/parsers/chainstack/decoder.py` — декодер pump.fun create/buy/sell инструкций

**Архитектура:**
```
ChainstackGrpcClient
├── connect() → auto-reconnect loop (как PumpPortalClient)
├── _subscribe() → geyser_pb2.SubscribeRequest с фильтром pump.fun program
├── _listen() → async for update in stub.Subscribe()
├── _decode_transaction() → парсинг protobuf → PumpPortalNewToken/Trade/Migration
├── stop()
└── callbacks: on_new_token, on_migration, on_trade (те же типы что PumpPortal)
```

**Ключевое:**
- Используем `grpcio` (новая зависимость)
- Proto файлы скачиваем из yellowstone-grpc repo и генерируем Python код
- Фильтр: `account_include=["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]`, `failed=False`, commitment=PROCESSED
- Декодер pump.fun инструкций: create (discriminator 8576854823835016728), buy, sell
- **Выходные модели — те же PumpPortalNewToken/Trade/Migration** → worker.py не меняется, просто подключаем другой источник
- Keepalive: 30s ping, 10s timeout

**Интеграция в worker.py:**
```python
if settings.enable_grpc_streaming and settings.chainstack_grpc_endpoint:
    grpc_client = ChainstackGrpcClient(
        endpoint=settings.chainstack_grpc_endpoint,
        token=settings.chainstack_grpc_token,
    )
    grpc_client.on_new_token = on_new_token  # те же handlers что у PumpPortal
    grpc_client.on_migration = on_migration
    grpc_client.on_trade = on_trade
    tasks.append(asyncio.create_task(grpc_client.connect(), name="grpc_streaming"))
```

Когда `enable_grpc_streaming=True` — PumpPortal WS остаётся как fallback, но gRPC будет primary (быстрее на 2-4s).

### 2. Vybe Network Client (`src/parsers/vybe/`)

**Что делаем:** HTTP клиент для Vybe API — holder PnL и top holders.

**Файлы:**
- `src/parsers/vybe/__init__.py`
- `src/parsers/vybe/client.py` — HTTP клиент
- `src/parsers/vybe/models.py` — Pydantic модели ответов

**API Endpoints (используем):**
1. `GET /token/top-holders?mintAddress={addr}` → top 1000 holders с балансами
2. `GET /account/pnl/{ownerAddress}` → PnL кошелька (realized + unrealized)

**Клиент:**
```
VybeClient
├── __init__(api_key, max_rps=0.06)  # Free: 4 RPM
├── _request(method, path) → dict
├── get_top_holders(mint_address) → list[VybeHolder]
├── get_wallet_pnl(address) → VybePnL
├── close()
```

**Модели:**
```python
class VybeHolder(BaseModel):
    ownerAddress: str
    tokenAccount: str
    balance: Decimal
    percentageOfSupply: Decimal | None = None

class VybePnL(BaseModel):
    ownerAddress: str
    realizedPnl: Decimal | None = None
    unrealizedPnl: Decimal | None = None
    totalPnl: Decimal | None = None
```

**Интеграция в enrichment:**
- В INITIAL Batch 2 (после holders) — берём top 10 holders, запрашиваем PnL каждого через Vybe
- Считаем `holders_in_profit_pct` — % холдеров с положительным PnL
- Добавляем в scoring: `holders_in_profit_pct >= 60%` → +2 pts

**Экономия кредитов:**
- Free tier: 25K credits/мес ≈ 830 вызовов/день
- 1 токен = 1 (top holders) + 10 (PnL per holder) = 11 вызовов
- ≈ 75 токенов/день с PnL — хватит для top-scored tokens (score >= 40)

### 3. TwitterAPI.io Client (`src/parsers/twitter/`)

**Что делаем:** поиск упоминаний токена в Twitter, подсчёт KOL-ов и вирусности.

**Файлы:**
- `src/parsers/twitter/__init__.py`
- `src/parsers/twitter/client.py` — HTTP клиент
- `src/parsers/twitter/models.py` — Pydantic модели

**API Endpoints:**
1. `GET /twitter/tweet/advanced_search?query={q}&queryType=Latest` → поиск твитов
2. `GET /twitter/user/info?userName={name}` → профиль юзера (followers)

**Клиент:**
```
TwitterClient
├── __init__(api_key, max_rps=1.0)
├── search_token(symbol, name, mint_address) → TwitterSearchResult
├── get_user_info(username) → TwitterUser
├── close()
```

**Модели:**
```python
class TwitterTweet(BaseModel):
    id: str
    text: str
    likeCount: int = 0
    retweetCount: int = 0
    replyCount: int = 0
    viewCount: int = 0
    author: TwitterAuthor

class TwitterAuthor(BaseModel):
    userName: str
    followers: int = 0
    isBlueVerified: bool = False

class TwitterSearchResult(BaseModel):
    tweets: list[TwitterTweet] = []
    total_tweets: int = 0
    kol_mentions: int = 0  # authors with 50K+ followers
    max_likes: int = 0
    total_engagement: int = 0
```

**Логика поиска:**
```python
# Запрос: "$SYMBOL OR token_name min_faves:5 -filter:retweets"
# Фильтр KOL: author.followers >= 50,000
# Вирусность: max(tweet.likeCount) >= 1000 или total_engagement >= 5000
```

**Интеграция в enrichment:**
- В INITIAL Batch 2 — после получения symbol/name из Batch 1
- Новые поля в scoring:
  - `twitter_mentions_count` >= 10 → +2 pts
  - `twitter_kol_mentions` >= 1 → +5 pts (KOL = 50K+ followers)
  - `twitter_viral` (max likes >= 1000) → +4 pts
  - `no_twitter_activity` (0 mentions) → -2 pts

**Экономия кредитов:**
- 1M credits за $10, 1 tweet = 0.15 credits
- 1 поиск ≈ 20 tweets = 3 credits
- ≈ 333K поисков — хватит надолго

### 4. Scoring Updates (`src/parsers/scoring.py`)

**Новые параметры в `compute_score()`:**
```python
# Phase 15: Vybe
holders_in_profit_pct: float | None = None,  # % holders with positive PnL

# Phase 15: Twitter
twitter_mentions: int | None = None,
twitter_kol_mentions: int | None = None,
twitter_viral: bool = False,
```

**Новые правила:**
```python
# Vybe: Holders in profit
if holders_in_profit_pct is not None:
    if holders_in_profit_pct >= 60:
        score += 2
    elif holders_in_profit_pct <= 20:
        score -= 3  # mostly bagholders = bad sign

# Twitter signals
if twitter_kol_mentions and twitter_kol_mentions >= 1:
    score += 5
elif twitter_mentions and twitter_mentions >= 10:
    score += 2
if twitter_viral:
    score += 4
if twitter_mentions == 0:
    score -= 2  # no social presence
```

### 5. Model & DB Updates

**Token model** — новое поле для twitter link (если найден):
```python
twitter_mentions_count: int | None  # last known mention count
```

**TokenSnapshot model** — новые поля:
```python
# Phase 15: Vybe
holders_in_profit_pct: Mapped[Decimal | None]
vybe_top_holder_pct: Mapped[Decimal | None]

# Phase 15: Twitter
twitter_mentions: Mapped[int | None]
twitter_kol_mentions: Mapped[int | None]
twitter_max_likes: Mapped[int | None]
```

**Alembic migration** — одна миграция для всех новых полей.

### 6. Dependencies

Добавить в `pyproject.toml`:
```toml
grpcio = "^1.68"
grpcio-tools = "^1.68"  # dev dependency for proto generation
```

### 7. Feature Flags (.env)

```
ENABLE_GRPC_STREAMING=true
ENABLE_VYBE=true
ENABLE_TWITTER=true
```

## Порядок реализации

1. **gRPC proto generation** — скачать proto, сгенерировать Python код
2. **ChainstackGrpcClient** — клиент + decoder + тест подключения
3. **VybeClient** — HTTP клиент + модели
4. **TwitterClient** — HTTP клиент + модели
5. **worker.py integration** — подключить все три в enrichment pipeline
6. **scoring.py** — новые сигналы
7. **Alembic migration** — новые поля
8. **Тест** — запуск pipeline, проверка что gRPC ловит токены

## Файлы для изменения

| Файл | Действие |
|------|----------|
| `config/settings.py` | Уже обновлён (Phase 15 settings) |
| `src/parsers/chainstack/grpc_client.py` | **NEW** — gRPC клиент |
| `src/parsers/chainstack/decoder.py` | **NEW** — pump.fun tx decoder |
| `src/parsers/chainstack/proto/*` | **NEW** — сгенерированные protobuf |
| `src/parsers/vybe/client.py` | **NEW** — Vybe HTTP клиент |
| `src/parsers/vybe/models.py` | **NEW** — Pydantic модели |
| `src/parsers/twitter/client.py` | **NEW** — Twitter HTTP клиент |
| `src/parsers/twitter/models.py` | **NEW** — Pydantic модели |
| `src/parsers/worker.py` | **EDIT** — подключение клиентов |
| `src/parsers/scoring.py` | **EDIT** — новые сигналы |
| `src/models/token.py` | **EDIT** — новые поля |
| `src/parsers/persistence.py` | **EDIT** — save functions |
| `pyproject.toml` | **EDIT** — grpcio dependency |
| `alembic/versions/xxx_phase15.py` | **NEW** — миграция |

## Verification

1. `poetry add grpcio grpcio-tools` — установка зависимостей
2. Генерация proto → проверка импортов
3. Запуск gRPC клиента standalone: подключение к Chainstack, получение первого pump.fun токена
4. Запуск VybeClient standalone: запрос top holders для известного токена
5. Запуск TwitterClient standalone: поиск "$SOL" → проверка ответа
6. `poetry run pytest tests/ -v` — существующие тесты не сломаны
7. Полный pipeline run: `python -m src.parsers.worker` — проверка что gRPC + Vybe + Twitter работают в enrichment
