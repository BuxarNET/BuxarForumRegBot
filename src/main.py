#!/usr/bin/env python3
"""
main.py — точка входа для системы массовой регистрации на форумах.

Использование:
    python main.py                     # Запуск с настройками из config/settings.py
    python main.py --dry-run           # Проверка конфигурации без запуска браузеров
    python main.py --report            # Только сформировать отчёт по существующим результатам
    python main.py --check             # Проверить окружение (зависимости, файлы, прокси)
"""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# Добавляем src/ в путь импортов
sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.main_orchestrator import MainOrchestrator


def setup_logging() -> None:
    """Настраивает loguru — вывод в консоль и файл."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger.remove()  # убираем дефолтный handler

    # Консоль — INFO и выше
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        colorize=True,
    )

    # Файл — DEBUG и выше, ротация по размеру
    logger.add(
        log_dir / "forum_reg_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
        rotation="50 MB",
        retention="7 days",
        encoding="utf-8",
    )


def parse_args() -> dict:
    """Простой парсер аргументов командной строки без argparse.

    Returns:
        Словарь с флагами запуска.
    """
    args = sys.argv[1:]
    return {
        "dry_run": "--dry-run" in args,
        "report_only": "--report" in args,
        "check": "--check" in args,
        "help": "--help" in args or "-h" in args,
    }


def print_help() -> None:
    """Выводит справку по аргументам."""
    print("""
Система массовой регистрации на форумах

Использование:
    python main.py                  Запуск регистрации
    python main.py --dry-run        Проверка конфигурации без браузеров
    python main.py --report         Сформировать отчёт по существующим результатам
    python main.py --check          Проверить окружение и файлы
    python main.py --help           Эта справка

Конфигурация:
    config/settings.py              Основные настройки
    .env                            API-ключи капча-провайдеров
    data/accounts.json              Список аккаунтов
    data/proxies.txt                Список прокси
    data/results_new.txt            Список форумов для регистрации
""")


async def check_environment() -> bool:
    """Проверяет готовность окружения к работе.

    Returns:
        True если всё готово, False если есть критические проблемы.
    """
    ok = True

    logger.info("=== Проверка окружения ===")

    # Проверяем обязательные файлы
    required_files = {
        "data/accounts.json": "Список аккаунтов",
        "data/proxies.txt": "Список прокси",
        "data/results_new.txt": "Список форумов",
        "config/settings.py": "Конфигурация",
        ".env": "API-ключи",
    }

    for filepath, description in required_files.items():
        path = Path(filepath)
        if path.exists():
            size = path.stat().st_size
            logger.info(f"  ✓ {description}: {filepath} ({size} байт)")
        else:
            logger.error(f"  ✗ {description} НЕ НАЙДЕН: {filepath}")
            ok = False

    # Проверяем зависимости
    logger.info("Проверка зависимостей...")
    deps = {
        "pydoll": "pydoll-python",
        "loguru": "loguru",
        "aiohttp": "aiohttp",
        "aiofiles": "aiofiles",
        "dotenv": "python-dotenv",
    }

    for module, package in deps.items():
        try:
            __import__(module)
            logger.info(f"  ✓ {package}")
        except ImportError:
            logger.error(f"  ✗ {package} — не установлен: pip install {package}")
            ok = False

    # Проверяем accounts.json
    accounts_file = Path("data/accounts.json")
    if accounts_file.exists():
        try:
            
            import json
            accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
            logger.info(f"  ✓ Аккаунтов загружено: {len(accounts)}")
            # Проверяем обязательные поля
            required_fields = {"username", "email", "password", "proxy_id"}
            for acc in accounts[:3]:  # проверяем первые 3
                missing = required_fields - set(acc.keys())
                if missing:
                    logger.warning(f"  ⚠ Аккаунт {acc.get('username', '?')}: отсутствуют поля {missing}")
        except Exception as e:
            logger.error(f"  ✗ Ошибка чтения accounts.json: {e}")
            ok = False

    # Проверяем proxies.txt
    proxies_file = Path("data/proxies.txt")
    if proxies_file.exists():
        lines = [
            l.strip() for l in proxies_file.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")
        ]
        logger.info(f"  ✓ Прокси загружено: {len(lines)}")

    # Проверяем results_new.txt
    forums_file = Path("data/results_new.txt")
    if forums_file.exists():
        lines = [
            l.strip() for l in forums_file.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")
        ]
        logger.info(f"  ✓ Форумов в очереди: {len(lines)}")

    if ok:
        logger.success("Окружение готово к работе.")
    else:
        logger.error("Обнаружены проблемы. Исправьте их перед запуском.")

    return ok


async def report_only() -> None:
    """Формирует отчёт по существующим файлам результатов без запуска регистрации."""
    logger.info("Формирование отчёта по существующим результатам...")

    orchestrator = MainOrchestrator()
    all_forums = await orchestrator._load_forums()
    users = await orchestrator._load_accounts()

    if not all_forums or not users:
        logger.error("Нет данных для отчёта")
        return

    report = orchestrator._generate_final_report(all_forums, users)
    print(report)


async def dry_run() -> None:
    """Проверяет конфигурацию и выводит план без запуска браузеров."""
    logger.info("=== Dry Run — проверка конфигурации ===")

    orchestrator = MainOrchestrator()
    all_forums = await orchestrator._load_forums()
    users = await orchestrator._load_accounts()

    if not all_forums:
        logger.error("Список форумов пуст")
        return
    if not users:
        logger.error("Список аккаунтов пуст")
        return

    logger.info(f"Форумов: {len(all_forums)}")
    logger.info(f"Пользователей: {len(users)}")
    logger.info(f"Всего регистраций: {len(all_forums) * len(users)}")
    logger.info("")

    # Resume индексы
    for user in users:
        username = user["username"]
        start_idx = await orchestrator._get_resume_index(username, all_forums)
        remaining = len(all_forums) - start_idx
        logger.info(
            f"  {username}: начнёт с форума #{start_idx + 1}, "
            f"осталось {remaining} из {len(all_forums)}"
        )

    logger.info("")
    logger.success("Dry run завершён. Запустите без --dry-run для старта регистрации.")


async def main() -> None:
    """Главная асинхронная функция."""
    # Загружаем .env
    load_dotenv()

    # Настраиваем логирование
    setup_logging()

    # Парсим аргументы
    flags = parse_args()

    if flags["help"]:
        print_help()
        return

    if flags["check"]:
        await check_environment()
        return

    if flags["report_only"]:
        await report_only()
        return

    if flags["dry_run"]:
        await dry_run()
        return

    # === Основной запуск ===
    logger.info("=" * 50)
    logger.info("  Запуск системы регистрации на форумах")
    logger.info("=" * 50)

    # Проверяем окружение перед стартом
    env_ok = await check_environment()
    if not env_ok:
        logger.error("Запуск отменён из-за ошибок окружения.")
        sys.exit(1)

    orchestrator = MainOrchestrator()

    # Graceful shutdown по Ctrl+C
    loop = asyncio.get_running_loop()

    def handle_shutdown():
        logger.warning("Получен сигнал остановки (Ctrl+C), завершаем работу...")
        asyncio.create_task(orchestrator.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_shutdown)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    try:
        stats = await orchestrator.run()
        logger.success(
            f"Завершено. Успешно: {stats['success']}, "
            f"Неудач: {stats['failed']}, "
            f"Обработано: {stats['processed']}"
        )
    except KeyboardInterrupt:
        logger.warning("Прервано пользователем")
        await orchestrator.shutdown()
    except Exception as e:
        logger.critical(f"Критическая ошибка: {type(e).__name__}: {e}")
        await orchestrator.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
