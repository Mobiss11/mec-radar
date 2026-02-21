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
