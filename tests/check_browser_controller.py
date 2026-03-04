#!/usr/bin/env python3
# test_browser_controller.py

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pydoll.constants import Key

import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from controllers.browser_controller import BrowserController


async def test_basic_navigation():
    """Базовый тест: открыть Google, ввести запрос, нажать Enter."""
    logger.info("=== Запуск теста базовой навигации ===")
    async with BrowserController(
        proxy=None,
        user_data_dir=Path("./test_profile_basic"),
        extension_path=None
    ) as browser:
        await browser.goto("https://www.google.com")

        search_box = await browser.wait_for_element("textarea[name='q']")
        await browser.human_type(search_box, "pydoll python")
        await asyncio.sleep(0.5)
        await browser.press_key(search_box, Key.ENTER)

        # Ждём загрузки — просто даём время странице отрендериться
        await asyncio.sleep(3)

        # Диагностика: что сейчас на странице
        title = await browser._current_tab.title
        url = await browser._current_tab.current_url
        logger.info(f"URL после поиска: {url}")
        logger.info(f"Заголовок: {title}")

        # Мягкая проверка — не падаем если h3 нет (например Google показал капчу)
        result = await browser.find_element("h3", timeout=10, raise_if_not_found=False)
        if result:
            logger.success("Результаты поиска найдены (h3 присутствует).")
        else:
            logger.warning(
                "h3 не найден — возможно Google показал капчу или страницу подтверждения. "
                "Проверьте URL выше."
            )

    logger.success("Тест базовой навигации завершён.\n")


async def test_manual_captcha_mode():
    """Тест ручного режима капчи."""
    logger.info("=== Запуск теста ручного режима капчи ===")
    async with BrowserController(
        proxy=None,
        user_data_dir=Path("./test_profile_captcha"),
        extension_path=None
    ) as browser:
        await browser.goto("https://www.google.com/recaptcha/api2/demo")
        captcha = await browser.find_element("iframe[src*='recaptcha']", timeout=15, raise_if_not_found=False)
        if not captcha:
            logger.warning("iframe капчи не найден — страница могла не загрузиться.")
            return

        logger.info("Капча загружена. Решите её в браузере, затем нажмите Enter в консоли.")
        solved = await browser.wait_for_captcha_solved(manual_mode=True)
        if solved:
            logger.success("Капча решена (подтверждено пользователем).")
        else:
            logger.error("Капча не решена.")
    logger.success("Тест ручного режима завершён.\n")


async def test_auto_captcha_mode_with_extension():
    """Тест автоматического режима капчи (требуется расширение)."""
    logger.info("=== Запуск теста автоматического режима капчи ===")
    load_dotenv()
    extension_path = os.getenv("CAPTCHA_EXTENSION_PATH")
    if not extension_path or not Path(extension_path).exists():
        logger.warning("Расширение не найдено, тест автоматического режима пропускается.")
        return

    async with BrowserController(
        proxy=None,
        user_data_dir=Path("./test_profile_auto"),
        extension_path=extension_path
    ) as browser:
        await browser.goto("https://www.google.com/recaptcha/api2/demo")
        await browser.wait_for_element("iframe[src*='recaptcha']", timeout=15)
        logger.info("Капча загружена. Ожидаем автоматического решения...")
        solved = await browser.wait_for_captcha_solved(timeout=60, manual_mode=False)
        if solved:
            logger.success("Капча решена автоматически.")
        else:
            logger.error("Капча не решена за отведённое время.")
    logger.success("Тест автоматического режима завершён.\n")


async def run_test(name: str, coro):
    """Запускает тест и перехватывает исключения чтобы остальные тесты продолжились."""
    try:
        await coro
    except Exception as e:
        logger.error(f"Тест '{name}' упал с ошибкой: {e}")


async def main():
    await run_test("basic_navigation", test_basic_navigation())
    await run_test("manual_captcha", test_manual_captcha_mode())
    await run_test("auto_captcha", test_auto_captcha_mode_with_extension())


if __name__ == "__main__":
    asyncio.run(main())