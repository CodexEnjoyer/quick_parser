# Playwright parser with proxy rotation

Асинхронный парсер, который:

- принимает список прокси;
- распределяет прокси по задачам по кругу (round-robin);
- генерирует отдельный user-agent для каждого прокси;
- поднимает отдельный `browser context` на каждую задачу;
- ограничивает параллелизм через `max_contexts`, чтобы стабильно держать десятки context'ов одновременно.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Запуск

```bash
python parser.py
```

## Как это работает

1. `ProxyPool` выдаёт следующий прокси потокобезопасно (`asyncio.Lock`).
2. Для каждого прокси заранее генерируется консистентный `user-agent` (через `UserAgentFactory`).
3. `PlaywrightParser` держит семафор (`asyncio.Semaphore`) для ограничения одновременно открытых contexts.
4. Каждая задача:
   - получает прокси + user-agent;
   - создаёт изолированный context с этим прокси;
   - открывает страницу и собирает данные;
   - закрывает context.

Это даёт масштабирование на десятки одновременных задач без смешивания сессий.
