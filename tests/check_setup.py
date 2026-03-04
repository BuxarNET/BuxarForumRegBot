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