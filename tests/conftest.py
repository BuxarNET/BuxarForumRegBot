from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Маркеры
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: интеграционные тесты (требуют сети и реального браузера)"
    )
    # Регистрируем asyncio маркер чтобы не было warning (даже если плагин не установлен)
    config.addinivalue_line("markers", "asyncio: mark test as async")


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(reason="Запустите с --integration для интеграционных тестов")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


def pytest_addoption(parser):
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Запустить интеграционные тесты",
    )


# ---------------------------------------------------------------------------
# Фикстуры общего назначения
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_template() -> dict:
    return {
        "name": "XenForo",
        "domain": "xenforo.com",
        "detect": {
            "url_pattern": "register",
            "meta_tags": [{"name": "generator", "content": "XenForo"}],
            "html_contains": ["xf-register"],
        },
        "fields": {
            "username": "input[name='username']",
            "email": "input[name='email']",
            "password": "input[name='password']",
            "confirm_password": "input[name='password_confirm']",
            "agree_checkbox": "input[name='agree']",
            "submit_button": "button[type='submit']",
        },
        "custom_fields": [],
    }


@pytest.fixture
def xenforo_html() -> str:
    return """
    <html>
    <head>
        <meta name="generator" content="XenForo">
    </head>
    <body>
        <div class="xf-register">
            <form id="register-form">
                <input type="text" name="username" id="username">
                <input type="email" name="email" id="email">
                <input type="password" name="password">
                <input type="password" name="password_confirm">
                <input type="checkbox" name="agree" id="agree">
                <label for="agree">I agree to the terms and rules</label>
                <button type="submit">Register</button>
            </form>
        </div>
    </body>
    </html>
    """


from unittest.mock import AsyncMock

@pytest.fixture
def mock_page():
    """
    Мок объекта страницы Pydoll для RegistrationController.
    
    Важно: current_url — это свойство, которое при await должно
    возвращать значение. Но coroutine можно await-ить только один раз,
    поэтому нужно возвращать НОВУЮ корутину при каждом доступе.
    """
    from unittest.mock import AsyncMock, MagicMock, PropertyMock
    
    page = MagicMock()
    
    # Создаём PropertyMock, который возвращает новую корутину при каждом доступе
    page.get_url = AsyncMock(return_value="https://example.com/register")
    
    # Остальные методы — обычные AsyncMock
    page.evaluate = AsyncMock(return_value="<html><body>Test</body></html>")
    page.screenshot = AsyncMock(return_value=b"fake_png_data")
    page.query = AsyncMock(return_value=None)
    page.query_all = AsyncMock(return_value=[])
    
    return page


@pytest.fixture
def mock_element():
    element = MagicMock()
    element.evaluate = AsyncMock(return_value={
        "type": "text", "name": "", "id": "", "placeholder": "",
        "value": "", "label": "", "tagName": "input",
    })
    element.query_all = AsyncMock(return_value=[])
    return element


@pytest.fixture
def common_fields_data() -> dict:
    return {
        "agree_keywords": ["agree", "terms", "rules", "согласен", "правила"],
        "submit_keywords": ["register", "sign up", "create account", "зарегистрироваться"],
        "username_keywords": ["user", "login", "nick", "username", "логин"],
        "email_keywords": ["email", "mail", "e-mail"],
    }

# =============================================================================
# ФИКСТУРЫ ДЛЯ PROMPT 4: RegistrationController
# =============================================================================

@pytest.fixture
def mock_browser_controller():
    """
    Мок BrowserController для тестов RegistrationController.
    """
    browser = AsyncMock()
    
    # Методы управления браузером (из Промта 2)
    browser.human_type = AsyncMock()
    browser.human_click = AsyncMock()
    browser.goto = AsyncMock()
    browser.wait_for_captcha_solved = AsyncMock()
    
    # Метод получения текущей вкладки
    browser.get_current_tab = MagicMock()
    
    return browser


@pytest.fixture
def mock_template_manager():
    """
    Мок TemplateManager для тестов RegistrationController.
    По умолчанию detect_template возвращает None (нет шаблона).
    """
    manager = AsyncMock()
    manager.detect_template = AsyncMock(return_value=None)
    return manager


@pytest.fixture
def mock_selector_finder():
    """
    Мок SelectorFinder для тестов RegistrationController.
    По умолчанию analyze_current_page возвращает базовые селекторы.
    """
    finder = AsyncMock()
    finder.analyze_current_page = AsyncMock(return_value={
        "username": "input#username",
        "email": "input[name='email']",
        "password": "input[type='password']:nth-of-type(1)",
        "confirm_password": "input[type='password']:nth-of-type(2)",
        "agree_checkbox": "input#agree",
        "submit_button": "button[type='submit']",
        "form_selector": "form#register",
        "captcha_indicator": None,
        "custom_fields": [],
    })
    return finder


@pytest.fixture
def mock_aiofiles(mocker):
    """
    Мок aiofiles для тестов работы с файлами.
    """
    mock_file = AsyncMock()
    mock_file.__aenter__ = AsyncMock(return_value=mock_file)
    mock_file.__aexit__ = AsyncMock(return_value=None)
    mock_file.write = AsyncMock()
    
    mock_open = mocker.patch("aiofiles.open", return_value=mock_file)
    return mock_open


@pytest.fixture
def test_config():
    """
    Тестовая конфигурация для RegistrationController.
    """
    return {
        "success_indicators": ["thank you", "account created", "успешно"],
        "error_indicators": ["error", "already taken", "ошибка"],
        "manual_captcha_timeout": 300,
        "manual_field_fill_timeout": 120,
        "find_registration_page_timeout": 60,
        "max_retries": 3,
    }


@pytest.fixture
def test_account_data():
    return {
        "username": "testuser",
        "email": "test@example.com",
        "password": "TestPass123!",
        "proxy_id": 1,
        "custom_fields": {},
        "status": "pending",
        "attempts": 0,
        "last_attempt": None
    }


@pytest.fixture
def registration_controller(mock_browser_controller, mock_template_manager, 
                            mock_selector_finder, mock_page, test_config):
    """
    Фикстура для создания экземпляра RegistrationController.
    """
    import sys
    from pathlib import Path
    
    # Добавляем src в путь (как в test_registration_controller.py)
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    
    from controllers.registration_controller import RegistrationController
    
    return RegistrationController(
        browser_controller=mock_browser_controller,
        template_manager=mock_template_manager,
        selector_finder=mock_selector_finder,
        page=mock_page,
        config=test_config,
    )
# =============================================================================
# НАСТРОЙКИ PYTEST-ASYNCIO
# =============================================================================

import pytest

# Устанавливаем режим asyncio для всех тестов
pytestmark = pytest.mark.asyncio(scope="session")



