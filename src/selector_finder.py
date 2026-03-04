from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiofiles
from loguru import logger


CAPTCHA_SELECTORS = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="turnstile"]',
    '.g-recaptcha',
    '.h-captcha',
    '#captcha',
]

_JS_GET_ATTRS = """
(function(el) {
    return {
        type: el.type || '',
        name: el.name || '',
        id: el.id || '',
        placeholder: el.placeholder || '',
        value: el.value || '',
        label: (el.labels && el.labels[0]) ? el.labels[0].innerText : '',
        tagName: el.tagName.toLowerCase()
    };
})(this)
"""

_JS_GET_TAG = "(function(el) { return el.tagName.toLowerCase(); })(this)"
_JS_GET_ID = "(function(el) { return el.id || ''; })(this)"
_JS_GET_NAME = "(function(el) { return el.name || ''; })(this)"
_JS_NTH_CHILD = """
(function(el) {
    if (!el.parentElement) return 1;
    var siblings = el.parentElement.children;
    for (var i = 0; i < siblings.length; i++) {
        if (siblings[i] === el) return i + 1;
    }
    return 1;
})(this)
"""
_JS_PARENT_TAG = """
(function(el) {
    return el.parentElement ? el.parentElement.tagName.toLowerCase() : '';
})(this)
"""


class SelectorFinder:
    """Эвристический анализатор полей регистрационной формы.

    Принимает объект страницы (таба) Pydoll и выполняет анализ DOM
    для определения полей формы регистрации и их CSS-селекторов.
    """

    def __init__(
        self,
        page,
        common_fields_path: str = "templates/common_fields.json",
    ) -> None:
        """Инициализация анализатора.

        Args:
            page: Объект текущей страницы (таб) из Pydoll.
            common_fields_path: Путь к JSON-файлу с ключевыми словами для полей.
        """
        self.page = page
        self._common_fields_path = Path(common_fields_path)
        self.common_fields: dict = {}

    async def _ensure_common_fields(self) -> None:
        """Загружает common_fields.json если ещё не загружен."""
        if self.common_fields:
            return
        try:
            async with aiofiles.open(self._common_fields_path, encoding="utf-8") as f:
                content = await f.read()
            self.common_fields = json.loads(content)
            logger.debug(f"Загружен common_fields: {self._common_fields_path}")
        except FileNotFoundError:
            logger.warning(
                f"Файл common_fields не найден: {self._common_fields_path}. "
                "Используются значения по умолчанию."
            )
            self.common_fields = _default_common_fields()
        except json.JSONDecodeError as e:
            logger.warning(f"Ошибка парсинга common_fields: {e}. Используются значения по умолчанию.")
            self.common_fields = _default_common_fields()

    async def find_registration_form(self) -> dict | None:
        """Ищет форму регистрации на текущей странице.

        Выбирает форму с максимальным количеством полей пароля.
        При равенстве предпочитает форму с кнопкой отправки.

        Returns:
            Словарь {"form_selector": str, "form_element": element} или None.
        """
        try:
            forms = await self.page.query_all("form")
        except Exception as e:
            logger.error(f"Ошибка получения форм: {e}")
            return None

        if not forms:
            logger.debug("Формы на странице не найдены.")
            return None

        best_form = None
        best_score = (-1, False)  # (password_count, has_submit)

        for form in forms:
            try:
                password_fields = await form.query_all('input[type="password"]')
                submit_buttons = await form.query_all(
                    'input[type="submit"], button[type="submit"]'
                )
                password_count = len(password_fields)
                has_submit = len(submit_buttons) > 0
                score = (password_count, has_submit)

                if score > best_score:
                    best_score = score
                    best_form = form
            except Exception as e:
                logger.warning(f"Ошибка анализа формы: {e}")
                continue

        if best_form is None:
            logger.debug("Подходящая форма регистрации не найдена.")
            return None

        selector = await self._generate_css_selector(best_form)
        logger.info(f"Найдена форма регистрации: {selector}")
        return {"form_selector": selector, "form_element": best_form}

    async def identify_fields(self, form_element) -> dict:
        """Анализирует поля внутри формы и классифицирует их.

        Args:
            form_element: Объект элемента формы из Pydoll.

        Returns:
            Словарь с селекторами стандартных полей и списком custom_fields.
            Пример:
            {
                "username": "input#username",
                "email": "input[name='email']",
                "password": "input[type='password']:nth-of-type(1)",
                "confirm_password": "input[type='password']:nth-of-type(2)",
                "agree_checkbox": "input#agree",
                "submit_button": "button[type='submit']",
                "custom_fields": [{"name": "birthday", "selector": "...", "type": "text"}]
            }
        """
        await self._ensure_common_fields()

        result: dict = {"custom_fields": []}
        password_count = 0

        try:
            inputs = await form_element.query_all("input, textarea, select")
        except Exception as e:
            logger.error(f"Ошибка получения полей формы: {e}")
            return result

        try:
            buttons = await form_element.query_all(
                'button[type="submit"], input[type="submit"]'
            )
        except Exception as e:
            logger.warning(f"Ошибка получения кнопок: {e}")
            buttons = []

        # Обрабатываем кнопки отправки
        submit_keywords = [k.lower() for k in self.common_fields.get("submit_keywords", [])]
        for button in buttons:
            try:
                attrs = await button.evaluate(_JS_GET_ATTRS)
                selector = await self._generate_css_selector(button)
                btn_text = (attrs.get("value") or attrs.get("label") or "").lower()
                if attrs.get("type") == "submit" or any(kw in btn_text for kw in submit_keywords):
                    if "submit_button" not in result:
                        result["submit_button"] = selector
            except Exception as e:
                logger.warning(f"Ошибка обработки кнопки: {e}")

        agree_keywords = [k.lower() for k in self.common_fields.get("agree_keywords", [])]

        for element in inputs:
            try:
                attrs = await element.evaluate(_JS_GET_ATTRS)
                selector = await self._generate_css_selector(element)

                field_type = attrs.get("type", "").lower()
                name = attrs.get("name", "").lower()
                el_id = attrs.get("id", "").lower()
                placeholder = attrs.get("placeholder", "").lower()
                label = attrs.get("label", "").lower()
                combined = f"{name} {el_id} {placeholder}"

                # Password
                if field_type == "password":
                    password_count += 1
                    if password_count == 1:
                        result["password"] = selector  # selector из _generate_css_selector(element)
                    elif password_count == 2:
                        result["confirm_password"] = selector
                    continue

                # Email
                if field_type == "email" or "email" in combined or "mail" in combined:
                    if "email" not in result:
                        result["email"] = selector
                    continue

                # Username
                username_keywords = self.common_fields.get(
                    "username_keywords", ["user", "login", "nick"]
                )
                if any(kw in combined for kw in username_keywords):
                    if "username" not in result:
                        result["username"] = selector
                    continue

                # Agree checkbox
                if field_type == "checkbox":
                    if any(kw in label for kw in agree_keywords):
                        if "agree_checkbox" not in result:
                            result["agree_checkbox"] = selector
                        continue

                # Submit button (input type=submit)
                if field_type == "submit":
                    if "submit_button" not in result:
                        result["submit_button"] = selector
                    continue

                # Пропускаем скрытые поля
                if field_type == "hidden":
                    continue

                # Custom field
                result["custom_fields"].append({
                    "name": attrs.get("name") or attrs.get("id") or "unknown",
                    "selector": selector,
                    "type": field_type or attrs.get("tagName", "input"),
                })

            except Exception as e:
                logger.warning(f"Ошибка обработки поля: {e}")
                continue

        return result

    async def _generate_css_selector(self, element) -> str:
        """Генерирует уникальный CSS-селектор для элемента.

        Приоритет: #id → tag[name='value'] → tag:nth-child(n) в родителе.

        Args:
            element: Объект элемента Pydoll.

        Returns:
            CSS-селектор в виде строки.
        """
        try:
            el_id = await element.evaluate(_JS_GET_ID)
            if el_id:
                return f"#{el_id}"

            tag = await element.evaluate(_JS_GET_TAG)
            name = await element.evaluate(_JS_GET_NAME)
            if name:
                return f"{tag}[name='{name}']"

            # Fallback: tag:nth-child внутри родителя
            nth = await element.evaluate(_JS_NTH_CHILD)
            parent_tag = await element.evaluate(_JS_PARENT_TAG)
            if parent_tag:
                return f"{parent_tag} > {tag}:nth-child({nth})"
            return f"{tag}:nth-child({nth})"

        except Exception as e:
            logger.warning(f"Ошибка генерации CSS-селектора: {e}")
            return "unknown"

    async def detect_captcha(self) -> str | None:
        """Проверяет наличие капчи на текущей странице.

        Returns:
            Строка-селектор первой найденной капчи или None.
        """
        for selector in CAPTCHA_SELECTORS:
            try:
                element = await self.page.query(selector)
                if element is not None:
                    logger.info(f"Обнаружена капча: {selector}")
                    return selector
            except Exception:
                continue
        return None

    async def analyze_current_page(self) -> dict | None:
        """Выполняет полный анализ текущей страницы регистрации.

        Последовательно вызывает find_registration_form, identify_fields
        и detect_captcha, объединяя результаты.

        Returns:
            Объединённый словарь с данными формы и капчи или None,
            если форма регистрации не найдена.

        Example:
            {
                "form_selector": "form#register",
                "username": "input#username",
                "email": "input[name='email']",
                "password": "input[type='password']:nth-of-type(1)",
                "confirm_password": "input[type='password']:nth-of-type(2)",
                "agree_checkbox": "input#agree",
                "submit_button": "button[type='submit']",
                "captcha_indicator": "iframe[src*='recaptcha']",
                "custom_fields": []
            }
        """
        form_result = await self.find_registration_form()
        if form_result is None:
            logger.info("Форма регистрации не найдена — анализ прерван.")
            return None

        fields = await self.identify_fields(form_result["form_element"])
        captcha = await self.detect_captcha()

        return {
            "form_selector": form_result["form_selector"],
            **fields,
            "captcha_indicator": captcha,
        }


def _default_common_fields() -> dict:
    """Возвращает значения common_fields по умолчанию."""
    return {
        "agree_keywords": ["agree", "terms", "rules", "согласен", "правила"],
        "submit_keywords": ["register", "sign up", "create account", "зарегистрироваться"],
        "username_keywords": ["user", "login", "nick", "username", "логин"],
        "email_keywords": ["email", "mail", "e-mail"],
    }
