from __future__ import annotations

import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock

# Добавляем src в путь (как в предыдущих тестах)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from controllers.registration_controller import RegistrationController


@pytest.mark.asyncio
async def test_registration_success(registration_controller, mock_browser_controller, 
                                     test_account_data):
    """
    Тест успешной регистрации.
    """
    result = await registration_controller.register(test_account_data)
    
    assert result["success"] is True
    assert result["reason"] is None
    assert result["form_data"] == test_account_data
    
    # Проверяем, что методы браузера были вызваны
    assert mock_browser_controller.human_type.called
    assert mock_browser_controller.human_click.called


@pytest.mark.asyncio
async def test_registration_no_form(registration_controller, mock_selector_finder, 
                                     test_account_data):
    """
    Тест: форма регистрации не найдена.
    """
    # SelectorFinder возвращает None
    mock_selector_finder.analyze_current_page = AsyncMock(return_value=None)
    
    result = await registration_controller.register(test_account_data)
    
    assert result["success"] is False
    assert result["reason"] == "no_form_detected"


@pytest.mark.asyncio
async def test_registration_captcha_timeout(registration_controller,
                                             mock_browser_controller,
                                             mock_selector_finder,
                                             test_account_data):
    """
    Тест: таймаут капчи.
    """
    # Капча не решена
    mock_browser_controller.wait_for_captcha_solved = AsyncMock(side_effect=TimeoutError())
    
    # Подменяем analyze_current_page, чтобы возвращала селектор капчи
    mock_selector_finder.analyze_current_page = AsyncMock(return_value={
        "username": "input#username",
        "email": "input[name='email']",
        "password": "input[type='password']:nth-of-type(1)",
        "confirm_password": "input[type='password']:nth-of-type(2)",
        "agree_checkbox": "input#agree",
        "submit_button": "button[type='submit']",
        "form_selector": "form#register",
        "captcha_indicator": "iframe[src*='recaptcha']",  # теперь капча есть
        "custom_fields": [],
    })

    result = await registration_controller.register(test_account_data)

    assert result["success"] is False
    assert result["reason"] == "captcha_timeout"


@pytest.mark.asyncio
async def test_registration_with_template(registration_controller, mock_template_manager, 
                                           mock_browser_controller, test_account_data):
    """
    Тест: регистрация с использованием шаблона.
    """
    # TemplateManager возвращает шаблон
    mock_template_manager.detect_template = AsyncMock(return_value={
        "name": "XenForo",
        "fields": {
            "username": "input[name='username']",
            "email": "input[name='email']",
            "password": "input[name='password']",
            "submit_button": "button[type='submit']",
        },
        "registration_page": {"url": "/register"},
    })
    
    result = await registration_controller.register(test_account_data)
    
    assert result["template_used"] == "XenForo"
    
    # Проверяем, что был вызван goto (переход на страницу регистрации)
    assert mock_browser_controller.goto.called


@pytest.mark.asyncio
async def test_registration_exception(registration_controller, mock_page,
                                       test_account_data):
    """
    Тест: исключение при получении HTML приводит к 'no_form_detected'.
    """
    mock_page.evaluate = AsyncMock(side_effect=Exception("Test exception"))
    
    result = await registration_controller.register(test_account_data)
    
    assert result["success"] is False
    assert result["reason"] == "no_form_detected"


@pytest.mark.asyncio
async def test_take_screenshot(registration_controller, mock_page, mock_aiofiles):
    """
    Тест: создание скриншота.
    """
    filepath = await registration_controller._take_screenshot(prefix="test", username="user1")
    
    assert filepath is not None
    assert "test_user1_" in filepath
    assert filepath.endswith(".png")
    
    # Проверяем, что aiofiles.open был вызван
    assert mock_aiofiles.called


@pytest.mark.asyncio
async def test_check_success(registration_controller):
    """
    Тест: проверка индикаторов успеха.
    """
    page_source = "<html><body>Thank you for registering!</body></html>"
    indicators = ["thank you", "account created"]
    
    result = registration_controller._check_success(page_source, indicators)
    assert result is True


@pytest.mark.asyncio
async def test_check_error(registration_controller):
    """
    Тест: проверка индикаторов ошибки.
    """
    page_source = "<html><body>This email is already taken</body></html>"
    indicators = ["already taken", "invalid email"]
    
    result = registration_controller._check_error(page_source, indicators)
    assert result == "already taken"