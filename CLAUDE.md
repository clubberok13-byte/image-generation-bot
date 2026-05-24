# Image Generation Bot — CLAUDE.md

## 📋 Обзор

Telegram бот который:
- Читает промпты из @IIFot и @gorbuzaksenia в реальном времени
- Чередует посты (FIFO из обоих каналов)
- Генерирует фото через DALL-E 3 + reference photo
- Отправляет результаты в @alexstai с оригинальным промптом в подписи
- Соблюдает rate limit: 10-15 постов в день

## 🏗️ Архитектура

## 📁 Компоненты

1. **channel_listener.py** — слушает @IIFot и @gorbuzaksenia, добавляет в очередь
2. **queue_manager.py** — управляет FIFO очередью, чередует между каналами
3. **rate_limiter.py** — соблюдает лимит 10-15 постов в день
4. **image_generator.py** — DALL-E 3 интеграция + reference photo
5. **post_manager.py** — отправляет в @alexstai с подписью (промпт + источник)

## 🛠️ Технологический стек

- **aiogram 3.x** — мониторинг Telegram каналов
- **OpenAI API (DALL-E 3)** — генерация изображений
- **Python 3.10+** — async/await везде
- **JSON файл** — очередь и статусы (queue.json)

## 📝 Правила кодирования

1. Все функции **async**
2. **Type hints** везде
3. **Docstrings** на русском
4. Обработка ошибок при API запросах
5. Rate limit соблюдается строго (не более 15 в день)

## 📊 Формат подписи при отправке в @alexstai

## 🎯 Статус

- [ ] Config и .env переменные
- [ ] Channel Listener (читать @IIFot и @gorbuzaksenia)
- [ ] Queue Manager (FIFO, чередование)
- [ ] Rate Limiter (10-15 в день)
- [ ] DALL-E 3 Integration (генерация + reference photo)
- [ ] Post Manager (отправка в @alexstai с подписью)
- [ ] Main.py (запуск и координация)

## 🤖 Инструкции для агентов

**code-explainer:** объясняй как работает rate limiting, FIFO очередь, чередование

**bot-builder:**
- Все операции должны быть async
- Queue хранится в queue.json для простоты (не БД)
- Чередование: берём из @IIFot, потом @gorbuzaksenia, потом снова @IIFot
- Rate limit: следующий пост только через (86400 сек / 15) = каждые ~5760 сек
- При отправке в @alexstai обязательно указываем оригинальный промпт и источник
