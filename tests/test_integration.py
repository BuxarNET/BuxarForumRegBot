from __future__ import annotations

"""
Интеграционные тесты с реальным браузером.

Запуск через pytest:  pytest tests/test_integration.py -v
Запуск напрямую:      python tests/test_integration.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger
from template_manager import TemplateManager
from selector_finder import SelectorFinder


REGISTER_URL = "https://www.phpbb.com/community/ucp.php?mode=register"


def run(coro):
    """Запускает корутину синхронно — не требует pytest-asyncio."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Тест 1: TemplateManager без браузера
# ---------------------------------------------------------------------------

def test_template_manager_standalone():
    """TemplateManager без браузера: загрузка шаблонов, детектирование, add_template."""
    async def _inner():
        logger.info("=== TemplateManager: загрузка шаблонов ===")

        # Загружаем реальные шаблоны из проекта
        manager = TemplateManager("templates/known_forums")
        templates = await manager.get_all_templates()
        logger.info(f"Загружено шаблонов: {len(templates)}")
        for t in templates:
            logger.info(f"  - {t.get('name')} ({t.get('domain')})")

        # Детектирование по имитированному HTML
        mock_html = """<html><head><meta name="generator" content="XenForo"></head>
                       <body><div class="xf-register">form</div></body></html>"""
        detected = await manager.detect_template("https://forum.example.com/register", mock_html)
        if detected:
            logger.success(f"Определён движок: {detected['name']}")
        else:
            logger.warning("Движок не определён (HTML не совпал полностью)")

        # Тест add_template во временную папку
        with tempfile.TemporaryDirectory() as tmp:
            test_manager = TemplateManager(tmp)
            new_template = {
                "name": "TestForum",
                "domain": "testforum.example.com",
                "detect": {"url_pattern": "register"},
                "fields": {"username": "input[name='user']"},
            }
            path = await test_manager.add_template(new_template, filename="test_forum")
            assert Path(path).exists(), "Файл шаблона должен быть создан"
            found = await test_manager.get_template_by_name("TestForum")
            assert found is not None, "Шаблон должен быть найден после добавления"
            logger.success("add_template + get_template_by_name — OK")

    run(_inner())


# ---------------------------------------------------------------------------
# Тест 2: SelectorFinder с реальным браузером
# ---------------------------------------------------------------------------

def test_selector_finder_with_browser():
    """SelectorFinder: открывает phpBB и анализирует форму регистрации."""
    from controllers.browser_controller import BrowserController

    async def _inner():
        logger.info("=== SelectorFinder: анализ реальной страницы ===")
        async with BrowserController(
            user_data_dir=Path("./test_profile_selector"),
            headless=False,
        ) as browser:
            await browser.goto(REGISTER_URL)
            await asyncio.sleep(2)

            finder = SelectorFinder(
                browser._current_tab,
                common_fields_path="templates/common_fields.json",
            )
            result = await finder.analyze_current_page()

            if result is None:
                logger.warning("Форма регистрации не найдена на странице.")
            else:
                logger.success("Анализ завершён. Результат:")
                for key, value in result.items():
                    if key != "custom_fields":
                        logger.info(f"  {key}: {value}")
                if result.get("custom_fields"):
                    for cf in result["custom_fields"]:
                        logger.info(f"  custom: {cf}")

            captcha = await finder.detect_captcha()
            logger.info(f"Капча: {captcha}")

    run(_inner())


# ---------------------------------------------------------------------------
# Тест 3: TemplateManager с реальной страницей через браузер
# ---------------------------------------------------------------------------

def test_template_manager_with_browser():
    """TemplateManager: детектирование движка по HTML реальной страницы."""
    from controllers.browser_controller import BrowserController

    async def _inner():
        logger.info("=== TemplateManager: детектирование на реальной странице ===")
        async with BrowserController(
            user_data_dir=Path("./test_profile_template"),
            headless=False,
        ) as browser:
            await browser.goto(REGISTER_URL)
            await asyncio.sleep(2)

            page_source = await browser._current_tab.page_source
            current_url = await browser._current_tab.current_url
            logger.info(f"URL: {current_url}")
            logger.info(f"Длина HTML: {len(page_source)} символов")

            manager = TemplateManager("templates/known_forums")
            detected = await manager.detect_template(current_url, page_source)
            if detected:
                logger.success(f"Определён движок: {detected['name']}")
            else:
                logger.warning("Движок не определён — нужен новый шаблон или SelectorFinder.")

    run(_inner())


# ---------------------------------------------------------------------------
# Прямой запуск: python tests/test_integration.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    def safe_run(name: str, fn):
        logger.info(f"\n{'='*60}")
        try:
            fn()
            logger.success(f"{name} — PASSED")
        except Exception as e:
            logger.error(f"{name} — FAILED: {e}")
            import traceback
            traceback.print_exc()

    safe_run("test_template_manager_standalone", test_template_manager_standalone)
    safe_run("test_selector_finder_with_browser", test_selector_finder_with_browser)
    safe_run("test_template_manager_with_browser", test_template_manager_with_browser)
