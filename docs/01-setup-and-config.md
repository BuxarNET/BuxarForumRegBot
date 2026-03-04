# Промт 1: Настройка проекта и окружения

## Цель
Создать базовую структуру проекта, файл зависимостей (`requirements.txt`), файл переменных окружения (`.env`) и настроить систему логирования. Этот этап — фундамент для всех последующих модулей.

## Задачи
1. Создать необходимые директории согласно структуре проекта.
2. Наполнить `requirements.txt` актуальными версиями библиотек.
3. Создать файл `.env` с переменными окружения (API-ключи, пути).
4. Настроить логирование с использованием `loguru` (с ротацией файлов, выводом в консоль и файл).
5. Написать небольшой тестовый скрипт `check_setup.py`, который проверит импорт библиотек и загрузку переменных окружения.

## Детальные инструкции

### 1. Структура директорий
В корне проекта (`/home/user/eclipse-workspace/forum_reg_bot/`) должны существовать следующие папки (если их ещё нет):
- `src/` — основной код (уже есть).
- `config/` — для файлов конфигурации.
- `extensions/` — для браузерных расширений.
- `data/` — для данных (профили, логи, прокси).
- `templates/` — для JSON-шаблонов.
- `logs/` — для файлов логов (будет создаваться автоматически).

Все эти папки (кроме `logs`) уже созданы ранее; убедитесь, что папка `logs` добавлена:
```bash
mkdir -p /home/user/eclipse-workspace/forum_reg_bot/logs
```

### 2. Файл зависимостей `src/requirements.txt`
Запишите в этот файл следующий список (версии актуальны на начало 2026 года, проверьте при необходимости):

```txt
pydoll-python>=2.20.2
aiohttp>=3.11.0
beautifulsoup4>=4.13.0
lxml>=5.3.0
python-dotenv>=1.0.0
loguru>=0.7.3
pytest>=8.3.0
aiofiles>=23.2.0
```

**Примечание:** `pydoll-python` — правильное название пакета на PyPI (не путать с `pydoll`).
`aiofiles` добавлена для асинхронной работы с файлами.

### 3. Файл переменных окружения `.env`
Создайте файл `.env` в корне проекта (рядом с `src/`) со следующим содержимым (заполните реальными ключами позже):

```dotenv
# API ключи для сервисов капчи (NopeCHA, 2Captcha, CapSolver)
NOPECHA_API_KEY=your_nopecha_api_key_here
TWOCAPTCHA_API_KEY=your_2captcha_api_key_here
CAPSOLVER_API_KEY=your_capsolver_api_key_here

# Путь к расширению для капчи (будет использоваться в BrowserController)
CAPTCHA_EXTENSION_PATH=/home/user/eclipse-workspace/forum_reg_bot/extensions/nopecha_solver

# Настройки прокси (если нужны)
# Формат: http://user:pass@ip:port или socks5://ip:port
DEFAULT_PROXY=

# Режим отладки (True/False)
DEBUG=True

# Путь к папке с профилями Chromium
PROFILES_DIR=/home/user/eclipse-workspace/forum_reg_bot/data/profiles

# Таймаут ожидания ответа пользователя в режиме обучения (секунд)
LEARN_TIMEOUT=60
```

### 4. Настройка логирования
Создайте файл `src/utils/logger.py` со следующим кодом:

```python
# src/utils/logger.py
import sys
from pathlib import Path
from loguru import logger

# Убедимся, что папка для логов существует
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Формат лога
log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"

# Удаляем стандартный стоковый вывод (чтобы заменить своим)
logger.remove()

# Добавляем вывод в консоль (с цветом)
logger.add(sys.stderr, format=log_format, level="DEBUG" if bool(sys.argv) else "INFO")

# Добавляем вывод в файл с ротацией
logger.add(
    LOG_DIR / "bot_{time:YYYY-MM-DD}.log",
    format=log_format,
    level="DEBUG",
    rotation="1 day",      # Новый файл каждый день
    retention="7 days",    # Хранить 7 дней
    compression="zip",     # Сжимать старые логи
    enqueue=True,          # Потокобезопасность
)

# Экспортируем настроенный логгер
__all__ = ["logger"]
```

### 5. Проверочный скрипт `check_setup.py`
Создайте в корне проекта (рядом с `src/`) файл `check_setup.py` для проверки установки:

```python
#!/usr/bin/env python3
# check_setup.py
import sys
from pathlib import Path

# Добавляем src в путь, чтобы импорты работали
sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from dotenv import load_dotenv
    import os
    from loguru import logger
    import aiohttp
    import bs4
    import lxml
    import pydoll
    import pytest
    import aiofiles
    print("✅ Все библиотеки успешно импортированы.")
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    sys.exit(1)

# Загружаем переменные окружения
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print("✅ .env файл загружен.")
    # Проверим наличие ключей
    nopecha = os.getenv("NOPECHA_API_KEY")
    if nopecha and nopecha != "your_nopecha_api_key_here":
        print("✅ NOPECHA_API_KEY установлен.")
    else:
        print("⚠️ NOPECHA_API_KEY не установлен или используется значение по умолчанию.")
else:
    print("❌ .env файл не найден.")
    sys.exit(1)

# Проверим создание лога
logger.debug("Это тестовое сообщение в лог.")
print("✅ Логирование работает (проверьте папку logs).")

print("✅ Установка прошла успешно.")
```

### 6. Установка зависимостей
Выполните в терминале:
```bash
cd /home/user/eclipse-workspace/forum_reg_bot
source .venv/bin/activate   # если используете виртуальное окружение
uv pip install -r src/requirements.txt
```

### 7. Проверка
Запустите проверочный скрипт:
```bash
python check_setup.py
```
Ожидаемый вывод: сообщения об успехе и созданный файл лога в `logs/`.

## Критерии готовности
- [ ] Все директории созданы.
- [ ] `requirements.txt` содержит указанные версии.
- [ ] `.env` файл создан и заполнен (хотя бы заглушками).
- [ ] `logger.py` реализован и работает.
- [ ] `check_setup.py` выполняется без ошибок.
- [ ] Зависимости установлены через uv.

После выполнения этого промта можно переходить к следующему этапу — созданию `BrowserController`.
```
