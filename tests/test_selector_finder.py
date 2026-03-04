from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from selector_finder import SelectorFinder, _default_common_fields


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def make_element(attrs: dict) -> MagicMock:
    el = MagicMock()
    full = {
        "type": attrs.get("type", "text"),
        "name": attrs.get("name", ""),
        "id": attrs.get("id", ""),
        "placeholder": attrs.get("placeholder", ""),
        "value": attrs.get("value", ""),
        "label": attrs.get("label", ""),
        "tagName": attrs.get("tagName", "input"),
    }
    el.evaluate = AsyncMock(return_value=full)
    el.query_all = AsyncMock(return_value=[])
    return el


def make_finder(page=None, common_fields=None) -> SelectorFinder:
    if page is None:
        page = MagicMock()
        page.query = AsyncMock(return_value=None)
        page.query_all = AsyncMock(return_value=[])
    finder = SelectorFinder(page)
    finder.common_fields = common_fields or _default_common_fields()
    return finder


# ---------------------------------------------------------------------------
# _generate_css_selector
# ---------------------------------------------------------------------------

def test_generate_selector_by_id():
    """Если есть id — возвращается #id."""
    finder = make_finder()
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=["myId"])  # первый вызов — el.id
    result = run(finder._generate_css_selector(el))
    assert result == "#myId"


def test_generate_selector_by_name():
    """Если нет id но есть name — возвращается tag[name='value']."""
    finder = make_finder()
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=["", "input", "username"])
    result = run(finder._generate_css_selector(el))
    assert result == "input[name='username']"


def test_generate_selector_nth_child_fallback():
    """Без id и name — путь через nth-child."""
    finder = make_finder()
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=["", "input", "", 2, "form"])
    result = run(finder._generate_css_selector(el))
    assert "nth-child" in result


def test_generate_selector_error_returns_unknown():
    """При исключении возвращает 'unknown'."""
    finder = make_finder()
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=Exception("JS error"))
    result = run(finder._generate_css_selector(el))
    assert result == "unknown"


# ---------------------------------------------------------------------------
# identify_fields — классификация полей
# ---------------------------------------------------------------------------

def test_identify_email_by_type():
    """Поле type=email → email."""
    email_el = MagicMock()
    email_el.evaluate = AsyncMock(side_effect=[
        {"type": "email", "name": "mail", "id": "em", "placeholder": "", "value": "", "label": "", "tagName": "input"},
        "em",  # _generate_css_selector: el.id
    ])
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[email_el], []])
    result = run(make_finder().identify_fields(form))
    assert "email" in result
    assert result["email"] == "#em"


def test_identify_email_by_name():
    """Поле name='email' → email."""
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=[
        {"type": "text", "name": "email", "id": "", "placeholder": "", "value": "", "label": "", "tagName": "input"},
        "", "input", "email",
    ])
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[el], []])
    result = run(make_finder().identify_fields(form))
    assert "email" in result


def test_identify_username_by_name():
    """Поле name='username' → username."""
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=[
        {"type": "text", "name": "username", "id": "", "placeholder": "", "value": "", "label": "", "tagName": "input"},
        "", "input", "username",
    ])
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[el], []])
    result = run(make_finder().identify_fields(form))
    assert "username" in result


# Должно быть:
def test_identify_password_single():
    """Одно поле type=password → только password, без confirm."""
    el = MagicMock()

    def evaluate_side_effect(js_code):
        if "siblings" in js_code:
            return 1
        if "parentElement" in js_code:
            return "form"
        if "el.name" in js_code and len(js_code) < 50:
            return "pwd"
        if "tagName" in js_code and "type" not in js_code:
            return "input"
        if "id" in js_code and "name" not in js_code:
            return ""
        return {"type": "password", "name": "pwd", "id": "", "placeholder": "", "value": "", "label": "", "tagName": "input"}

    el.evaluate = AsyncMock(side_effect=evaluate_side_effect)
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[el], []])
    result = run(make_finder().identify_fields(form))
    assert result.get("password") == "input[name='pwd']"
    assert "confirm_password" not in result


def test_identify_password_two_fields():
    """Два поля type=password → password + confirm_password."""
    def make_pass():
        el = MagicMock()
        
        def evaluate_side_effect(js_code):
            if "id" in js_code and "name" not in js_code:
                return ""           # _JS_GET_ID
            if "tagName" in js_code and "type" not in js_code:
                return "input"      # _JS_GET_TAG
            if "el.name" in js_code and len(js_code) < 50:
                return "pwd"        # _JS_GET_NAME
            if "siblings" in js_code:
                return 1            # _JS_NTH_CHILD
            if "parentElement" in js_code:
                return "form"       # _JS_PARENT_TAG
            # _JS_GET_ATTRS (полный словарь)
            return {"type": "password", "name": "pwd", "id": "", "placeholder": "", "value": "", "label": "", "tagName": "input"}
        
        el.evaluate = AsyncMock(side_effect=evaluate_side_effect)
        return el

    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[make_pass(), make_pass()], []])
    result = run(make_finder().identify_fields(form))
    assert result.get("password") == "input[name='pwd']"
    assert result.get("confirm_password") == "input[name='pwd']"


def test_identify_agree_checkbox():
    """Чекбокс с label содержащим 'agree' → agree_checkbox."""
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=[
        {"type": "checkbox", "name": "agree", "id": "agree", "placeholder": "", "value": "", "label": "I agree to the terms", "tagName": "input"},
        "agree",
    ])
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[el], []])
    result = run(make_finder().identify_fields(form))
    assert "agree_checkbox" in result


def test_identify_submit_button():
    """Кнопка type=submit → submit_button."""
    btn = MagicMock()
    btn.evaluate = AsyncMock(side_effect=[
        {"type": "submit", "name": "", "id": "btn", "placeholder": "", "value": "Register", "label": "", "tagName": "button"},
        "btn",
    ])
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[], [btn]])
    result = run(make_finder().identify_fields(form))
    assert "submit_button" in result


def test_identify_custom_field():
    """Нераспознанное поле → custom_fields."""
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=[
        {"type": "text", "name": "birthday", "id": "", "placeholder": "dd.mm.yyyy", "value": "", "label": "", "tagName": "input"},
        "", "input", "birthday",
    ])
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[el], []])
    result = run(make_finder().identify_fields(form))
    assert len(result["custom_fields"]) == 1
    assert result["custom_fields"][0]["name"] == "birthday"


def test_identify_hidden_field_skipped():
    """Скрытые поля (type=hidden) не попадают в custom_fields."""
    el = MagicMock()
    el.evaluate = AsyncMock(return_value={
        "type": "hidden", "name": "token", "id": "", "placeholder": "", "value": "abc", "label": "", "tagName": "input"
    })
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[el], []])
    result = run(make_finder().identify_fields(form))
    assert result["custom_fields"] == []


# ---------------------------------------------------------------------------
# detect_captcha
# ---------------------------------------------------------------------------

def test_detect_captcha_recaptcha():
    page = MagicMock()
    page.query = AsyncMock(return_value=MagicMock())  # первый селектор найден
    finder = make_finder(page)
    result = run(finder.detect_captcha())
    assert result == 'iframe[src*="recaptcha"]'


def test_detect_captcha_none():
    page = MagicMock()
    page.query = AsyncMock(return_value=None)
    finder = make_finder(page)
    result = run(finder.detect_captcha())
    assert result is None


def test_detect_captcha_hcaptcha():
    """Если recaptcha нет, находит hcaptcha."""
    page = MagicMock()
    async def query_side(selector, **kwargs):
        return MagicMock() if "hcaptcha" in selector else None
    page.query = AsyncMock(side_effect=query_side)
    finder = make_finder(page)
    result = run(finder.detect_captcha())
    assert result == 'iframe[src*="hcaptcha"]'


def test_detect_captcha_g_recaptcha_class():
    """Находит .g-recaptcha если iframe не найден."""
    page = MagicMock()
    async def query_side(selector, **kwargs):
        return MagicMock() if selector == ".g-recaptcha" else None
    page.query = AsyncMock(side_effect=query_side)
    finder = make_finder(page)
    result = run(finder.detect_captcha())
    assert result == ".g-recaptcha"


# ---------------------------------------------------------------------------
# find_registration_form
# ---------------------------------------------------------------------------

def test_find_registration_form_no_forms():
    page = MagicMock()
    page.query_all = AsyncMock(return_value=[])
    finder = make_finder(page)
    result = run(finder.find_registration_form())
    assert result is None


def test_find_registration_form_picks_most_passwords():
    """Выбирает форму с наибольшим числом password-полей."""
    form1 = MagicMock()
    form1.query_all = AsyncMock(side_effect=[[], []])  # 0 паролей, нет submit

    form2 = MagicMock()
    form2.query_all = AsyncMock(side_effect=[
        [MagicMock(), MagicMock()],  # 2 пароля
        [MagicMock()],               # есть submit
    ])
    form2.evaluate = AsyncMock(side_effect=["reg", ""])

    page = MagicMock()
    page.query_all = AsyncMock(return_value=[form1, form2])

    finder = make_finder(page)
    result = run(finder.find_registration_form())
    assert result is not None
    assert result["form_element"] is form2


def test_find_registration_form_returns_selector():
    """Результат содержит form_selector и form_element."""
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[[MagicMock()], [MagicMock()]])
    form.evaluate = AsyncMock(side_effect=["my-form", ""])

    page = MagicMock()
    page.query_all = AsyncMock(return_value=[form])

    finder = make_finder(page)
    result = run(finder.find_registration_form())
    assert result is not None
    assert "form_selector" in result
    assert "form_element" in result


# ---------------------------------------------------------------------------
# analyze_current_page
# ---------------------------------------------------------------------------

def test_analyze_current_page_no_form():
    page = MagicMock()
    page.query_all = AsyncMock(return_value=[])
    page.query = AsyncMock(return_value=None)
    finder = make_finder(page)
    result = run(finder.analyze_current_page())
    assert result is None


def test_analyze_current_page_has_required_keys():
    """Результат содержит form_selector, captcha_indicator и custom_fields."""
    email_el = MagicMock()
    email_el.evaluate = AsyncMock(side_effect=[
        {"type": "email", "name": "email", "id": "email", "placeholder": "", "value": "", "label": "", "tagName": "input"},
        "email",
    ])
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[
        [MagicMock()],  # password (find_registration_form)
        [MagicMock()],  # submit  (find_registration_form)
        [email_el],     # inputs  (identify_fields)
        [],             # buttons (identify_fields)
    ])
    form.evaluate = AsyncMock(side_effect=["register-form", ""])

    page = MagicMock()
    page.query_all = AsyncMock(return_value=[form])
    page.query = AsyncMock(return_value=None)

    finder = make_finder(page)
    result = run(finder.analyze_current_page())
    assert result is not None
    assert "form_selector" in result
    assert "captcha_indicator" in result
    assert "custom_fields" in result


def test_analyze_current_page_with_captcha():
    """captcha_indicator заполнен если капча найдена."""
    form = MagicMock()
    form.query_all = AsyncMock(side_effect=[
        [MagicMock()], [MagicMock()],  # find_registration_form
        [], [],                          # identify_fields
    ])
    form.evaluate = AsyncMock(side_effect=["form", ""])

    page = MagicMock()
    page.query_all = AsyncMock(return_value=[form])
    page.query = AsyncMock(return_value=MagicMock())  # капча найдена

    finder = make_finder(page)
    result = run(finder.analyze_current_page())
    assert result is not None
    assert result["captcha_indicator"] == 'iframe[src*="recaptcha"]'


# ---------------------------------------------------------------------------
# Интеграционные тесты
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_analyze_phpbb_demo():
    async def _run():
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from controllers.browser_controller import BrowserController
        async with BrowserController(headless=True) as browser:
            await browser.goto("https://www.phpbb.com/community/ucp.php?mode=register")
            finder = SelectorFinder(browser._current_tab)
            return await finder.analyze_current_page()
    result = run(_run())
    assert result is not None
    assert "form_selector" in result
