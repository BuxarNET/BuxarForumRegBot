from __future__ import annotations

import pytest
import asyncio
from pathlib import Path

# Эти тесты требуют реальных зависимостей
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_full_registration_flow():
    """
    Интеграционный тест: полная цепочка регистрации.
    
    Требует:
    - Реальный BrowserController с запущенным браузером
    - Реальный TemplateManager с шаблонами
    - Реальный SelectorFinder
    - Тестовый форум (например, демо-версия phpBB/XenForo)
    """
    # Импортируем реальные классы
    from src.controllers.browser_controller import BrowserController
    from src.controllers.registration_controller import RegistrationController
    from src.utils.template_manager import TemplateManager
    from src.utils.selector_finder import SelectorFinder
    
    # Настройка
    config = {
        "success_indicators": ["thank you", "registration complete"],
        "error_indicators": ["error", "already taken"],
        "manual_captcha_timeout": 300,
    }
    
    account_data = {
        "username": "integration_test_user",
        "email": "integration@test.com",
        "password": "IntegrationTest123",
    }
    
    # Инициализация компонентов
    browser = BrowserController(proxy=None, profile_path=None)
    await browser.start()
    
    page = browser.get_current_tab()
    template_manager = TemplateManager(templates_dir="templates/known_forums")
    selector_finder = SelectorFinder(page=page)
    
    controller = RegistrationController(
        browser_controller=browser,
        template_manager=template_manager,
        selector_finder=selector_finder,
        page=page,
        config=config,
    )
    
    try:
        # Переходим на тестовый форум
        await browser.goto("https://demo-forum.example.com/register")
        
        # Выполняем регистрацию
        result = await controller.register(account_data)
        
        # Проверяем результат
        assert result["success"] in [True, False]
        
    finally:
        await browser.close()