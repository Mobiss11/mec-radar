# Memcoin Trading System — Рабочая спецификация для Claude Code

## Цель проекта

Автоматизированная система парсинга, анализа и торговли мемкоинами на Solana.
Стабильный доход через smart wallet following + scoring модель.
Поэтапная разработка: парсер → анализ → paper trading → live.

---

## Стек технологий

- **Python 3.11+**
- **FastAPI** — dashboard API
- **asyncio + aiohttp + httpx** — асинхронный парсинг
- **tls_client + fake_useragent** — обход Cloudflare gmgn.ai
- **websockets** — PumpPortal, gmgn.ai WS, Helius WS
- **PostgreSQL + asyncpg** — хранение данных
- **Redis + aioredis** — кэш и очереди сигналов
- **solders + solana-py** — Solana RPC и транзакции
- **aiogram 3** — Telegram бот
- **Pydantic v2** — валидация данных
- **loguru** — логирование
- **Docker + docker-compose** — деплой
- **Alembic** — миграции БД

---

## Структура проекта

```
memcoin-trader/
├── docker-compose.yml
├── .env.example
├── alembic/
│   ├── alembic.ini
│   └── versions/
├── config/
│   ├── __init__.py
│   ├── settings.py            # Pydantic Settings из .env
│   ├── risk_config.py         # параметры риск-менеджмента
│   └── scoring_rules.py       # правила скоринга
├── src/
│   ├── __init__.py
│   ├── main.py                # точка входа, запуск всех сервисов
│   │
│   ├── models/                # SQLAlchemy модели
│   │   ├── __init__.py
│   │   ├── base.py            # Base, engine, session factory
│   │   ├── token.py           # Token, TokenSnapshot, TokenSecurity
│   │   ├── wallet.py          # SmartWallet, WalletActivity
│   │   ├── signal.py          # Signal
│   │   └── trade.py           # Trade, Position
│   │
│   ├── parsers/               # сбор данных
│   │   ├── __init__.py
│   │   ├── gmgn/
│   │   │   ├── __init__.py
│   │   │   ├── client.py      # HTTP клиент gmgn.ai с TLS bypass
│   │   │   ├── endpoints.py   # все эндпоинты gmgn.ai
│   │   │   ├── ws_client.py   # WebSocket клиент gmgn.ai
│   │   │   └── models.py      # Pydantic модели ответов gmgn.ai
│   │   ├── pumpportal/
│   │   │   ├── __init__.py
│   │   │   ├── ws_client.py   # WebSocket клиент PumpPortal
│   │   │   └── models.py      # Pydantic модели событий
│   │   ├── dexscreener/
│   │   │   ├── __init__.py
│   │   │   └── client.py      # REST клиент DexScreener (бесплатный)
│   │   └── helius/
│   │       ├── __init__.py
│   │       ├── rpc_client.py  # Helius RPC (getTransaction, etc.)
│   │       └── ws_client.py   # Helius WebSocket (logsSubscribe)
│   │
│   ├── analyzer/              # анализ и скоринг
│   │   ├── __init__.py
│   │   ├── scoring.py         # scoring engine
│   │   ├── safety_filters.py  # anti-rugpull фильтры
│   │   ├── patterns.py        # детекция паттернов на истории
│   │   └── risk_manager.py    # риск-менеджмент
│   │
│   ├── executor/              # исполнение сделок
│   │   ├── __init__.py
│   │   ├── jupiter_client.py  # Jupiter Ultra/V6 API
│   │   ├── jito_client.py     # Jito bundles (фаза 5)
│   │   └── position_monitor.py # мониторинг открытых позиций
│   │
│   ├── bot/                   # Telegram интерфейс
│   │   ├── __init__.py
│   │   ├── bot.py             # aiogram 3 бот
│   │   └── handlers.py        # команды и алерты
│   │
│   ├── db/                    # подключения
│   │   ├── __init__.py
│   │   ├── database.py        # async PostgreSQL engine + session
│   │   └── redis.py           # async Redis connection
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py          # loguru конфиг
│       └── helpers.py         # вспомогательные функции
│
├── scripts/
│   ├── backfill_gmgn.py       # массовая загрузка исторических данных
│   ├── analyze_patterns.py    # анализ паттернов успешных токенов
│   ├── find_smart_wallets.py  # поиск и верификация smart wallets
│   └── backtest.py            # бэктестинг scoring модели
│
├── tests/
│   ├── test_gmgn_client.py
│   ├── test_scoring.py
│   ├── test_safety_filters.py
│   └── test_risk_manager.py
│
└── README.md
```

---

## Фаза 0: Инфраструктура

### docker-compose.yml

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: memcoin_trader
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U trader -d memcoin_trader"]
      interval: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redisdata:/data

  bot:
    build: .
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
```

### .env.example

```env
# Database
POSTGRES_PASSWORD=changeme
DATABASE_URL=postgresql+asyncpg://trader:changeme@postgres:5432/memcoin_trader
REDIS_URL=redis://redis:6379/0

# Helius (бесплатный план для старта)
HELIUS_API_KEY=your_helius_api_key
HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}
HELIUS_WS_URL=wss://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}

# Solana (НЕ класть реальные деньги до фазы 5!)
SOLANA_PRIVATE_KEY=

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_ADMIN_ID=your_telegram_id

# Trading (false = paper trading mode)
TRADING_ENABLED=false

# gmgn.ai
GMGN_PARSE_INTERVAL_SEC=60
GMGN_MAX_RPS=1.5
```

### config/settings.py

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str
    redis_url: str

    # Helius
    helius_api_key: str
    helius_rpc_url: str
    helius_ws_url: str

    # Solana
    solana_private_key: str = ""

    # Telegram
    telegram_bot_token: str
    telegram_admin_id: int

    # Trading
    trading_enabled: bool = False

    # gmgn.ai
    gmgn_parse_interval_sec: int = 60
    gmgn_max_rps: float = 1.5

    class Config:
        env_file = ".env"


settings = Settings()
```

---

## Фаза 1: Парсер gmgn.ai (НАЧИНАТЬ ЗДЕСЬ)

### Приоритет реализации (строго по порядку):

#### 1.1 HTTP клиент gmgn.ai с обходом Cloudflare

Файл: `src/parsers/gmgn/client.py`

```python
"""
HTTP клиент для gmgn.ai.

Gmgn.ai защищён Cloudflare с TLS fingerprinting.
Стандартный requests/httpx будет заблокирован.

Решение: tls_client с ротацией TLS-идентификаторов + fake_useragent.

Зависимости:
    pip install tls_client fake_useragent

Альтернатива: использовать готовый враппер https://github.com/1f1n/gmgnai-wrapper
(проверить актуальность — эндпоинты могут поменяться).

Rate limit: не более 1.5 запросов/сек без IP whitelist.
С whitelist (по заявке): до 2 запросов/сек.
"""

import asyncio
import random
from typing import Any

import tls_client
from fake_useragent import UserAgent

BASE_URL = "https://gmgn.ai"

# TLS-идентификаторы для ротации (имитируем разные браузеры)
TLS_IDENTIFIERS = [
    "chrome_120",
    "chrome_119",
    "safari_ios_17_0",
    "firefox_120",
]


class GmgnClient:
    """
    Async-обёртка над tls_client для gmgn.ai.

    Использование:
        client = GmgnClient(max_rps=1.5)
        token_info = await client.get_token_info("So11111111111111111111111111111111111111112")
        holders = await client.get_top_holders("TOKEN_ADDRESS")
        security = await client.get_token_security("TOKEN_ADDRESS")
        new_pairs = await client.get_new_pairs(limit=50)
        trending = await client.get_pump_trending(limit=50)
        smart_wallets = await client.get_smart_wallets(category="smart_degen")
    """

    def __init__(self, max_rps: float = 1.5):
        self.max_rps = max_rps
        self._min_interval = 1.0 / max_rps
        self._last_request_time = 0.0
        self._ua = UserAgent()
        self._session = self._create_session()

    def _create_session(self) -> tls_client.Session:
        session = tls_client.Session(
            client_identifier=random.choice(TLS_IDENTIFIERS),
            random_tls_extension_order=True,
        )
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://gmgn.ai/?chain=sol",
            "Origin": "https://gmgn.ai",
            "User-Agent": self._ua.random,
        })
        return session

    async def _rate_limited_request(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        """Выполнить запрос с rate limiting и ротацией TLS."""
        now = asyncio.get_event_loop().time()
        wait_time = self._min_interval - (now - self._last_request_time)
        if wait_time > 0:
            await asyncio.sleep(wait_time)

        # Ротация TLS fingerprint каждые ~10 запросов
        if random.random() < 0.1:
            self._session = self._create_session()

        # Выполняем в thread pool (tls_client синхронный)
        response = await asyncio.to_thread(
            getattr(self._session, method), url, **kwargs
        )
        self._last_request_time = asyncio.get_event_loop().time()

        if response.status_code == 403:
            raise CloudflareBlockedError(f"Cloudflare заблокировал запрос к {url}")
        if response.status_code != 200:
            raise GmgnApiError(f"HTTP {response.status_code}: {url}")

        data = response.json()
        if data.get("code") != 0:
            raise GmgnApiError(f"API error: {data}")
        return data.get("data", data)

    # === Эндпоинты ===

    async def get_token_info(self, address: str, chain: str = "sol") -> dict:
        """Полные данные токена: цена, маркеткап, объём, ликвидность."""
        url = f"{BASE_URL}/defi/quotation/v1/tokens/token_info/{chain}/{address}"
        return await self._rate_limited_request("get", url)

    async def get_top_holders(self, address: str, chain: str = "sol", limit: int = 100) -> dict:
        """Топ холдеров токена с PnL каждого."""
        url = f"{BASE_URL}/defi/quotation/v1/tokens/{chain}/{address}/top_holders"
        return await self._rate_limited_request("get", url, params={"limit": limit})

    async def get_token_security(self, address: str, chain: str = "sol") -> dict:
        """Security-анализ: locked LP, renounced, dev holdings, rugpull risk."""
        url = f"{BASE_URL}/defi/quotation/v1/tokens/{chain}/{address}/security"
        return await self._rate_limited_request("get", url)

    async def get_new_pairs(self, chain: str = "sol", limit: int = 50) -> dict:
        """Новые созданные пары."""
        url = f"{BASE_URL}/defi/quotation/v1/pairs/{chain}/new_pairs"
        return await self._rate_limited_request("get", url, params={"limit": limit})

    async def get_pump_trending(
        self,
        chain: str = "sol",
        limit: int = 50,
        orderby: str = "progress",
    ) -> dict:
        """Trending токены с Pump.fun, ранжированные по прогрессу bonding curve."""
        url = f"{BASE_URL}/defi/quotation/v1/rank/{chain}/pump"
        return await self._rate_limited_request(
            "get", url, params={"limit": limit, "orderby": orderby}
        )

    async def get_smart_wallets(
        self,
        chain: str = "sol",
        category: str = "smart_degen",
        limit: int = 50,
    ) -> dict:
        """
        Smart wallets по категориям.

        Категории: smart_degen, pump_smart, sniper, kol
        """
        url = f"{BASE_URL}/defi/quotation/v1/smartmoney/{chain}/walletNew/trendingWallets"
        return await self._rate_limited_request(
            "get", url, params={"type": category, "limit": limit}
        )

    async def get_wallet_info(self, wallet_address: str, chain: str = "sol") -> dict:
        """Информация о кошельке: PnL, винрейт, история."""
        url = f"{BASE_URL}/defi/quotation/v1/smartmoney/{chain}/walletNew/{wallet_address}"
        return await self._rate_limited_request("get", url)

    async def get_wallet_trades(
        self,
        wallet_address: str,
        chain: str = "sol",
        limit: int = 100,
    ) -> dict:
        """История сделок кошелька."""
        url = f"{BASE_URL}/defi/quotation/v1/smartmoney/{chain}/walletNew/{wallet_address}/trades"
        return await self._rate_limited_request("get", url, params={"limit": limit})


class GmgnApiError(Exception):
    pass


class CloudflareBlockedError(GmgnApiError):
    pass
```

**Первое что делаем**: запускаем клиент, проверяем что эндпоинты отвечают.
Если `1f1n/gmgnai-wrapper` актуален — можно использовать его вместо написания своего.
Обязательно проверить каждый эндпоинт — они могут меняться.

#### 1.2 PumpPortal WebSocket клиент

Файл: `src/parsers/pumpportal/ws_client.py`

```python
"""
WebSocket клиент для PumpPortal — бесплатный real-time поток событий Pump.fun.

Эндпоинт: wss://pumpportal.fun/api/data

Подписки:
  - subscribeNewToken: новые токены на Pump.fun
  - subscribeMigration: выпуск токена на Raydium (graduation)
  - subscribeAccountTrade: сделки конкретного кошелька
  - subscribeTokenTrade: сделки по конкретному токену

Бесплатно, без API ключа.
Нужен heartbeat/ping для поддержания соединения.
"""

import asyncio
import json
from typing import Callable, Awaitable

import websockets

PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"


class PumpPortalClient:
    """
    Использование:
        client = PumpPortalClient()
        client.on_new_token = my_handler  # async def my_handler(data: dict)
        client.on_migration = my_migration_handler
        await client.connect()
    """

    def __init__(self):
        self.ws = None
        self.on_new_token: Callable[[dict], Awaitable] | None = None
        self.on_migration: Callable[[dict], Awaitable] | None = None
        self.on_account_trade: Callable[[dict], Awaitable] | None = None
        self._running = False
        self._tracked_wallets: list[str] = []

    async def connect(self):
        """Подключиться и слушать события. Автопереподключение при обрыве."""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(PUMPPORTAL_WS_URL) as ws:
                    self.ws = ws
                    await self._subscribe(ws)
                    await self._listen(ws)
            except (websockets.ConnectionClosed, ConnectionError) as e:
                if self._running:
                    await asyncio.sleep(5)  # переподключение через 5 сек

    async def _subscribe(self, ws):
        """Отправить подписки после подключения."""
        # Новые токены
        await ws.send(json.dumps({"method": "subscribeNewToken"}))

        # Миграции на Raydium
        await ws.send(json.dumps({"method": "subscribeMigration"}))

        # Сделки отслеживаемых кошельков
        if self._tracked_wallets:
            await ws.send(json.dumps({
                "method": "subscribeAccountTrade",
                "keys": self._tracked_wallets,
            }))

    async def _listen(self, ws):
        """Слушать и обрабатывать сообщения."""
        async for message in ws:
            try:
                data = json.loads(message)
                event_type = data.get("txType") or data.get("method")

                if event_type == "create" and self.on_new_token:
                    await self.on_new_token(data)
                elif event_type == "migration" and self.on_migration:
                    await self.on_migration(data)
                elif event_type in ("buy", "sell") and self.on_account_trade:
                    await self.on_account_trade(data)
            except json.JSONDecodeError:
                continue

    def track_wallets(self, wallet_addresses: list[str]):
        """Добавить кошельки для отслеживания."""
        self._tracked_wallets = wallet_addresses

    async def stop(self):
        self._running = False
        if self.ws:
            await self.ws.close()
```

#### 1.3 Модели БД

Файл: `src/models/base.py`

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from config.settings import settings

engine = create_async_engine(settings.database_url, echo=False, pool_size=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
```

Файл: `src/models/token.py`

```python
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Numeric, Boolean, DateTime, JSON,
    UniqueConstraint, Index, ForeignKey,
)
from .base import Base


class Token(Base):
    __tablename__ = "tokens"

    id = Column(Integer, primary_key=True)
    address = Column(String(64), nullable=False)
    chain = Column(String(10), nullable=False, default="sol")
    name = Column(String(255))
    symbol = Column(String(50))
    created_at = Column(DateTime)              # время создания токена
    first_seen_at = Column(DateTime, default=datetime.utcnow)  # когда мы впервые увидели
    source = Column(String(50))                # "pumpportal", "gmgn", "dexscreener"

    __table_args__ = (
        UniqueConstraint("address", "chain", name="uq_token_address_chain"),
    )


class TokenSnapshot(Base):
    """Снапшот метрик токена. Делаем каждые 5-10 мин для активных токенов."""
    __tablename__ = "token_snapshots"

    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    price = Column(Numeric)
    market_cap = Column(Numeric)
    liquidity_usd = Column(Numeric)
    volume_5m = Column(Numeric)
    volume_1h = Column(Numeric)
    volume_24h = Column(Numeric)
    holders_count = Column(Integer)
    top10_holders_pct = Column(Numeric)       # % токенов у топ-10 холдеров
    dev_holds_pct = Column(Numeric)
    smart_wallets_count = Column(Integer)     # кол-во smart wallets вошедших
    score = Column(Integer)                    # рассчитанный скор

    __table_args__ = (
        Index("idx_snapshots_token_time", "token_id", "timestamp"),
    )


class TokenSecurity(Base):
    """Security-данные из gmgn.ai /security endpoint."""
    __tablename__ = "token_security"

    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=False, unique=True)
    is_open_source = Column(Boolean)
    is_proxy = Column(Boolean)
    is_mintable = Column(Boolean)
    lp_burned = Column(Boolean)
    lp_locked = Column(Boolean)
    lp_lock_duration_days = Column(Integer)
    contract_renounced = Column(Boolean)
    top10_holders_pct = Column(Numeric)
    dev_holds_pct = Column(Numeric)
    dev_token_balance = Column(Numeric)
    is_honeypot = Column(Boolean)
    buy_tax = Column(Numeric)
    sell_tax = Column(Numeric)
    raw_data = Column(JSON)                    # сырой ответ для будущего анализа
    checked_at = Column(DateTime, default=datetime.utcnow)
```

Файл: `src/models/wallet.py`

```python
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, ForeignKey, Index,
)
from .base import Base


class SmartWallet(Base):
    """Отслеживаемые smart wallets с доказанной историей."""
    __tablename__ = "smart_wallets"

    id = Column(Integer, primary_key=True)
    address = Column(String(64), nullable=False, unique=True)
    chain = Column(String(10), default="sol")
    category = Column(String(50))              # smart_degen, pump_smart, sniper, kol
    label = Column(String(255))                # человекочитаемое имя если есть
    win_rate = Column(Numeric)
    avg_profit_pct = Column(Numeric)
    total_trades = Column(Integer)
    total_pnl_usd = Column(Numeric)
    is_active = Column(Integer, default=1)     # 1 = отслеживаем, 0 = отключён
    discovered_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class WalletActivity(Base):
    """Конкретные сделки smart wallets."""
    __tablename__ = "wallet_activity"

    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey("smart_wallets.id"), nullable=False)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    token_address = Column(String(64))         # на случай если токена ещё нет в БД
    action = Column(String(10))                # buy, sell
    amount_sol = Column(Numeric)
    amount_usd = Column(Numeric)
    amount_token = Column(Numeric)
    tx_hash = Column(String(128))
    timestamp = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_wallet_activity_time", "timestamp"),
        Index("idx_wallet_activity_wallet", "wallet_id"),
    )
```

Файл: `src/models/signal.py`

```python
from datetime import datetime
from sqlalchemy import Column, Integer, String, Numeric, DateTime, JSON, ForeignKey
from .base import Base


class Signal(Base):
    """Торговый сигнал сгенерированный scoring engine."""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=False)
    token_address = Column(String(64), nullable=False)
    score = Column(Integer, nullable=False)
    reasons = Column(JSON)                     # {"smart_wallets": 3, "volume_spike": "8x", ...}
    token_price_at_signal = Column(Numeric)
    token_mcap_at_signal = Column(Numeric)
    liquidity_at_signal = Column(Numeric)
    status = Column(String(20), default="pending")  # pending, executed, skipped, expired
    created_at = Column(DateTime, default=datetime.utcnow)
```

Файл: `src/models/trade.py`

```python
from datetime import datetime
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Index
from .base import Base


class Trade(Base):
    """Исполненная сделка (реальная или paper)."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer, ForeignKey("signals.id"))
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=False)
    token_address = Column(String(64), nullable=False)
    side = Column(String(10), nullable=False)  # buy, sell
    amount_sol = Column(Numeric)
    amount_token = Column(Numeric)
    price = Column(Numeric)
    slippage_pct = Column(Numeric)
    fee_sol = Column(Numeric)
    tx_hash = Column(String(128))              # пусто для paper trades
    is_paper = Column(Integer, default=1)      # 1 = paper, 0 = real
    status = Column(String(20))                # success, failed, pending
    executed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_trades_time", "executed_at"),
    )


class Position(Base):
    """Агрегированная позиция."""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=False)
    token_address = Column(String(64), nullable=False)
    entry_price = Column(Numeric)
    current_price = Column(Numeric)
    amount_token = Column(Numeric)
    amount_sol_invested = Column(Numeric)
    pnl_pct = Column(Numeric, default=0)
    pnl_usd = Column(Numeric, default=0)
    max_price = Column(Numeric)                # для trailing stop
    status = Column(String(20), default="open")  # open, closed_tp, closed_sl, closed_trailing
    is_paper = Column(Integer, default=1)
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime)

    __table_args__ = (
        Index("idx_positions_status", "status"),
    )
```

#### 1.4 Воркер-парсер (главный цикл фазы 1)

Файл: `src/parsers/worker.py`

```python
"""
Главный воркер парсера. Запускается как asyncio task.

Что делает каждые GMGN_PARSE_INTERVAL_SEC секунд:
1. Тянет новые пары с gmgn.ai
2. Тянет trending Pump.fun токены
3. Для каждого нового токена — парсит security + top_holders
4. Сохраняет снапшоты в БД

Параллельно слушает PumpPortal WebSocket для real-time событий.
"""

import asyncio
from loguru import logger

from src.parsers.gmgn.client import GmgnClient
from src.parsers.pumpportal.ws_client import PumpPortalClient
from src.models.base import async_session
from config.settings import settings


async def run_parser():
    """Точка входа парсера."""
    gmgn = GmgnClient(max_rps=settings.gmgn_max_rps)
    pumpportal = PumpPortalClient()

    # Обработчики PumpPortal событий
    async def on_new_token(data: dict):
        logger.info(f"New token: {data.get('name')} ({data.get('mint')})")
        # TODO: сохранить в БД, поставить в очередь на парсинг security

    async def on_migration(data: dict):
        logger.info(f"Migration to Raydium: {data.get('mint')}")
        # TODO: высокий приоритет — токен "выпустился"

    pumpportal.on_new_token = on_new_token
    pumpportal.on_migration = on_migration

    # Запуск параллельных задач
    await asyncio.gather(
        pumpportal.connect(),           # WebSocket listener
        _polling_loop(gmgn),            # REST polling loop
    )


async def _polling_loop(gmgn: GmgnClient):
    """Периодический парсинг gmgn.ai REST endpoints."""
    while True:
        try:
            # 1. Новые пары
            new_pairs = await gmgn.get_new_pairs(limit=50)
            logger.info(f"Fetched {len(new_pairs) if isinstance(new_pairs, list) else '?'} new pairs")
            # TODO: обработать и сохранить

            # 2. Trending Pump.fun
            trending = await gmgn.get_pump_trending(limit=50)
            logger.info(f"Fetched trending pump tokens")
            # TODO: обработать и сохранить

            # 3. Для новых токенов — security + holders
            # TODO: для каждого нового токена вызвать:
            #   await gmgn.get_token_security(address)
            #   await gmgn.get_top_holders(address)
            # Сохранить в token_security и token_snapshots

        except Exception as e:
            logger.error(f"Parser error: {e}")

        await asyncio.sleep(settings.gmgn_parse_interval_sec)
```

---

## Фаза 2: Сбор Smart Wallets

Файл: `scripts/find_smart_wallets.py`

```python
"""
Скрипт поиска и верификации smart wallets.

Алгоритм:
1. Парсим gmgn.ai /smartmoney/ по всем категориям
2. Для каждого кошелька получаем историю сделок
3. Считаем винрейт, средний PnL, количество сделок
4. Фильтруем: >50 сделок И >50% винрейт И avg_profit >20%
5. Сохраняем шорт-лист в БД

Запуск: python scripts/find_smart_wallets.py
"""

# TODO: реализовать по описанному алгоритму
# Категории для парсинга: smart_degen, pump_smart, sniper, kol
# Критерии фильтрации:
#   - total_trades >= 50
#   - win_rate >= 0.50
#   - avg_profit_pct >= 20.0
#   - активен в последние 7 дней
```

---

## Фаза 3: Scoring Engine

Файл: `src/analyzer/scoring.py`

```python
"""
Rule-based scoring engine.
Оценивает каждый токен по шкале 0-11.
Порог для сигнала: score >= 5.

Калибровать после фазы 1 по реальным историческим данным!
"""

from dataclasses import dataclass


@dataclass
class TokenMetrics:
    liquidity_usd: float
    top10_holders_pct: float
    dev_holds_pct: float
    lp_burned_or_locked: bool
    holders_count: int
    token_age_minutes: float
    smart_wallets_count: int
    volume_spike_ratio: float      # текущий объём / средний за час
    holder_growth_1h: int          # прирост холдеров за час
    is_honeypot: bool
    buy_tax: float
    sell_tax: float


# Safety gates — если не проходит, скор = 0
SAFETY_FILTERS = {
    "min_liquidity_usd": 20_000,
    "max_top10_holders_pct": 50.0,
    "max_dev_holds_pct": 10.0,
    "require_lp_burned_or_locked": True,
    "min_holders": 50,
    "min_age_minutes": 10,
    "max_age_hours": 24,
    "max_buy_tax": 5.0,
    "max_sell_tax": 5.0,
    "reject_honeypot": True,
}


def score_token(metrics: TokenMetrics) -> tuple[int, dict[str, str]]:
    """
    Вернуть (скор, причины).
    Скор 0 = не прошёл safety фильтры.
    """
    reasons = {}

    # === Safety Gates ===
    if metrics.liquidity_usd < SAFETY_FILTERS["min_liquidity_usd"]:
        return 0, {"rejected": f"liquidity ${metrics.liquidity_usd:.0f} < ${SAFETY_FILTERS['min_liquidity_usd']}"}
    if metrics.top10_holders_pct > SAFETY_FILTERS["max_top10_holders_pct"]:
        return 0, {"rejected": f"top10 holders {metrics.top10_holders_pct:.1f}% > {SAFETY_FILTERS['max_top10_holders_pct']}%"}
    if metrics.dev_holds_pct > SAFETY_FILTERS["max_dev_holds_pct"]:
        return 0, {"rejected": f"dev holds {metrics.dev_holds_pct:.1f}% > {SAFETY_FILTERS['max_dev_holds_pct']}%"}
    if SAFETY_FILTERS["require_lp_burned_or_locked"] and not metrics.lp_burned_or_locked:
        return 0, {"rejected": "LP not burned/locked"}
    if metrics.holders_count < SAFETY_FILTERS["min_holders"]:
        return 0, {"rejected": f"holders {metrics.holders_count} < {SAFETY_FILTERS['min_holders']}"}
    if metrics.token_age_minutes < SAFETY_FILTERS["min_age_minutes"]:
        return 0, {"rejected": f"too young ({metrics.token_age_minutes:.0f}min)"}
    if metrics.token_age_minutes > SAFETY_FILTERS["max_age_hours"] * 60:
        return 0, {"rejected": f"too old ({metrics.token_age_minutes / 60:.0f}h)"}
    if metrics.is_honeypot:
        return 0, {"rejected": "honeypot detected"}
    if metrics.buy_tax > SAFETY_FILTERS["max_buy_tax"]:
        return 0, {"rejected": f"buy tax {metrics.buy_tax}%"}
    if metrics.sell_tax > SAFETY_FILTERS["max_sell_tax"]:
        return 0, {"rejected": f"sell tax {metrics.sell_tax}%"}

    # === Scoring ===
    score = 0

    # Smart wallets (макс +3)
    if metrics.smart_wallets_count >= 5:
        score += 3
        reasons["smart_wallets"] = f"{metrics.smart_wallets_count} wallets (5+)"
    elif metrics.smart_wallets_count >= 3:
        score += 2
        reasons["smart_wallets"] = f"{metrics.smart_wallets_count} wallets (3-4)"
    elif metrics.smart_wallets_count >= 1:
        score += 1
        reasons["smart_wallets"] = f"{metrics.smart_wallets_count} wallet(s)"

    # Volume spike (макс +3)
    if metrics.volume_spike_ratio >= 10:
        score += 3
        reasons["volume"] = f"{metrics.volume_spike_ratio:.0f}x spike (10x+)"
    elif metrics.volume_spike_ratio >= 5:
        score += 2
        reasons["volume"] = f"{metrics.volume_spike_ratio:.0f}x spike (5-10x)"
    elif metrics.volume_spike_ratio >= 2:
        score += 1
        reasons["volume"] = f"{metrics.volume_spike_ratio:.0f}x spike (2-5x)"

    # Holder growth (макс +3)
    if metrics.holder_growth_1h >= 200:
        score += 3
        reasons["holders"] = f"+{metrics.holder_growth_1h} holders/h (200+)"
    elif metrics.holder_growth_1h >= 50:
        score += 2
        reasons["holders"] = f"+{metrics.holder_growth_1h} holders/h (50-200)"
    elif metrics.holder_growth_1h >= 10:
        score += 1
        reasons["holders"] = f"+{metrics.holder_growth_1h} holders/h (10-50)"

    # Liquidity depth (макс +2)
    if metrics.liquidity_usd >= 500_000:
        score += 2
        reasons["liquidity"] = f"${metrics.liquidity_usd / 1000:.0f}K (500K+)"
    elif metrics.liquidity_usd >= 100_000:
        score += 1
        reasons["liquidity"] = f"${metrics.liquidity_usd / 1000:.0f}K (100-500K)"

    return score, reasons
```

---

## Фаза 4: Risk Manager

Файл: `config/risk_config.py`

```python
"""Параметры риск-менеджмента. НЕ ОБСУЖДАЮТСЯ — строго соблюдать."""

RISK_CONFIG = {
    # Размер позиции
    "max_position_pct": 3,              # макс. 3% депозита на сделку
    "max_position_usd": 30,             # или макс. $30 (при $1000 депо)
    "max_open_positions": 5,            # макс. 5 одновременно

    # Стоп-лоссы
    "stop_loss_pct": 25,                # стоп -25%
    "daily_loss_limit_pct": 10,         # стоп на день при -10% от депозита

    # Тейк-профиты
    "take_profit_1_pct": 30,            # первый тейк +30% — закрываем 50% позиции
    "take_profit_2_pct": 60,            # второй тейк +60% — закрываем остаток
    "trailing_stop_pct": 15,            # или trailing stop 15% от максимума

    # Фильтры на вход
    "min_liquidity_for_entry": 20_000,  # не входить если ликвидность < $20K
    "min_liquidity_for_exit": 10_000,   # не выходить если ликвидность < $10K (ждать)
    "max_slippage_pct": 5,              # макс. допустимый slippage

    # Лимиты по возрасту
    "max_token_age_hours": 24,          # не входить в токены старше 24ч
    "min_token_age_minutes": 10,        # не входить в первые 10 мин (пусть стабилизируется)

    # Scoring
    "min_score_for_entry": 5,           # минимальный скор для входа
}
```

---

## Порядок реализации (чеклист для Claude Code)

### Неделя 1-2: Парсер (НАЧАТЬ ЗДЕСЬ)
- [ ] `docker-compose.yml` — поднять PostgreSQL + Redis
- [ ] `config/settings.py` — Pydantic Settings
- [ ] `src/models/` — все SQLAlchemy модели
- [ ] Alembic init + первая миграция (создание таблиц)
- [ ] `src/parsers/gmgn/client.py` — HTTP клиент с TLS bypass
- [ ] Тест: вызвать каждый эндпоинт gmgn.ai, убедиться что данные приходят
- [ ] `src/parsers/pumpportal/ws_client.py` — WebSocket клиент
- [ ] Тест: подключиться к PumpPortal WS, получить события new_token
- [ ] `src/parsers/worker.py` — главный цикл парсера
- [ ] Запустить парсер на 24-48 часов, проверить что данные копятся в БД

### Неделя 2: Smart Wallets
- [ ] `scripts/find_smart_wallets.py` — поиск кошельков по категориям
- [ ] Запуск: собрать 200-500 кошельков, отфильтровать до 30-80
- [ ] Добавить отслеживание в PumpPortal WS (`subscribeAccountTrade`)

### Неделя 3: Scoring + Анализ
- [ ] `scripts/backfill_gmgn.py` — массовый парсинг истории (если нужно больше данных)
- [ ] `scripts/analyze_patterns.py` — SQL-анализ паттернов успешных токенов
- [ ] `src/analyzer/scoring.py` — scoring engine
- [ ] `src/analyzer/safety_filters.py` — anti-rugpull фильтры
- [ ] `scripts/backtest.py` — бэктест scoring модели на исторических данных
- [ ] Откалибровать параметры по результатам бэктеста

### Неделя 4: Telegram бот + Paper Trading
- [ ] `src/bot/bot.py` — aiogram 3 бот
- [ ] Команды: /start, /positions, /history, /stats, /pause, /resume
- [ ] Push-алерты: новый сигнал, тейк сработал, стоп сработал
- [ ] Paper trading engine: виртуальные входы/выходы, PnL трекинг
- [ ] Запуск paper trading на 1-2 недели

### Неделя 5-6: Live Trading (только если paper показал edge)
- [ ] `src/executor/jupiter_client.py` — Jupiter Ultra/V6 API
- [ ] `src/executor/position_monitor.py` — мониторинг цен для стоп/тейк
- [ ] `src/analyzer/risk_manager.py` — полный риск-менеджмент
- [ ] Тест на devnet / минимальные суммы ($5-10)
- [ ] Запуск live с лимитом $50-100

---

## Ключевые ресурсы

| Ресурс | URL | Зачем |
|--------|-----|-------|
| gmgnai-wrapper | github.com/1f1n/gmgnai-wrapper | Готовый Python-клиент gmgn.ai |
| GmGnAPI docs | chipadevteam.github.io/GmGnAPI | Документация эндпоинтов |
| PumpPortal API | pumpportal.fun/data-api/real-time | WebSocket для Pump.fun |
| Jupiter Ultra | dev.jup.ag/docs/ultra | Swap API |
| Helius docs | helius.dev/docs | RPC + WebSocket |
| DexScreener API | docs.dexscreener.com/api/reference | Бесплатные данные по парам |

## Критические заметки

1. **TRADING_ENABLED=false** пока не пройдена фаза paper trading
2. **Отдельный кошелёк** — НИКОГДА не использовать основной
3. **Логировать ВСЁ** — каждый сигнал, каждый скор, каждое решение. Данные = улучшение стратегии
4. **Эндпоинты gmgn.ai ломаются** — иметь план Б (DexScreener + прямой on-chain парсинг)
5. **1.5 RPS к gmgn.ai максимум** — иначе бан
6. **Не гнаться за каждым токеном** — лучше пропустить 10 сделок чем попасть в 1 рагпулл
