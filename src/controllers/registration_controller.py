from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict, NotRequired

from loguru import logger


class RegistrationResult(TypedDict):
    success: bool
    message: str
    reason: str | None
    template_used: str | None
    screenshot: str | None
    form_data: dict


class AccountData(TypedDict):
    username: str
    email: str
    password: str
    proxy_id: int
    custom_fields: dict[str, str]
    status: str
    attempts: int
    last_attempt: str | None


class RegistrationController:
    """
    Контроллер для регистрации аккаунтов на форумах.
    
    Использует BrowserController для управления браузером,
    TemplateManager для работы с шаблонами,
    SelectorFinder для эвристического поиска полей.
    """
    
    def __init__(
        self,
        browser_controller,
        template_manager,
        selector_finder,
        page,
        config: dict | None = None,
        captcha_helper=None
    ):
        """
        Args:
            browser_controller: экземпляр BrowserController для управления браузером.
            template_manager: экземпляр TemplateManager для работы с шаблонами.
            selector_finder: экземпляр SelectorFinder для эвристического поиска полей.
            page: объект текущей страницы Pydoll для выполнения запросов к DOM.
            config: словарь с настройками (таймауты, индикаторы успеха/ошибки и др.).
        """
        self.browser = browser_controller
        self.template_manager = template_manager
        self.selector_finder = selector_finder
        self.page = page
        self.config = config or {}
        self.captcha_helper = captcha_helper
        
        # Индикаторы для проверки результата (из config или дефолтные)
        self.success_indicators = self.config.get("success_indicators", [
            "thank you", "activate", "успешно", "created", "account has been"
        ])
        self.error_indicators = self.config.get("error_indicators", [
            "error", "failed", "invalid", "ошибка", "already taken"
        ])
        
        self.screenshot_dir = Path(__file__).parent.parent / "data" / "screenshots"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
    
    async def register(self, account_data: AccountData) -> RegistrationResult:
        """
        Выполняет регистрацию на текущем форуме.
        
        Args:
            account_data: данные учётной записи (username, email, password, custom_fields).
        
        Returns:
            RegistrationResult: словарь с результатом регистрации.
        """
        logger.info(f"Starting registration for user: {account_data['username']}")
        
        # Проверка обязательных полей
        required = ("username", "email", "password", "proxy_id")
        missing = [f for f in required if not account_data.get(f)]
        if missing:
            logger.warning(f"Пропуск аккаунта — отсутствуют поля: {missing}")
            return {
                "success": False,
                "message": f"Missing required fields: {missing}",
                "reason": "missing_fields",
                "template_used": None,
                "screenshot": None,
                "form_data": account_data
            }

        try:
            # 1. Получение селекторов
            selectors, template, form_selector = await self._get_selectors()
            if selectors is None:
                return {
                    "success": False,
                    "message": "Registration form not found",
                    "reason": "no_form_detected",
                    "template_used": None,
                    "screenshot": None,
                    "form_data": account_data
                }
            
            # 2. Переход на страницу регистрации
            await self._navigate_to_registration_page(template)
            
            # 3. Заполнение полей
            await self._fill_fields(selectors, account_data)
            
            # 4. Ожидание решения капчи
            captcha_result = await self._handle_captcha(selectors)
            if not captcha_result:
                screenshot_path = await self._take_screenshot(
                    prefix="captcha_error",
                    username=account_data.get("username")
                )
                return {
                    "success": False,
                    "message": "Captcha not solved",
                    "reason": "captcha_timeout",
                    "template_used": template["name"] if template else "heuristic",
                    "screenshot": screenshot_path,
                    "form_data": account_data
                }
            
            # 5. Отправка формы
            await self._submit_form(selectors, form_selector)
            
            # 6. Проверка результата
            success, error_reason = await self._check_result(template)
            
            if success:
                logger.info(f"Registration successful for user: {account_data['username']}")
                return {
                    "success": True,
                    "message": "Registration completed successfully",
                    "reason": None,
                    "template_used": template["name"] if template else "heuristic",
                    "screenshot": None,
                    "form_data": account_data
                }
            else:
                screenshot_path = await self._take_screenshot(
                    prefix="error",
                    username=account_data.get("username")
                )
                logger.warning(f"Registration failed for user {account_data['username']}: {error_reason}")
                return {
                    "success": False,
                    "message": f"Registration failed: {error_reason}",
                    "reason": error_reason,
                    "template_used": template["name"] if template else "heuristic",
                    "screenshot": screenshot_path,
                    "form_data": account_data
                }
                
        except Exception as e:
            logger.error(f"Registration exception for user {account_data['username']}: {type(e).__name__}: {e}")
            screenshot_path = await self._take_screenshot(
                prefix="exception",
                username=account_data.get("username")
            )
            return {
                "success": False,
                "message": f"Exception: {type(e).__name__}",
                "reason": f"exception: {type(e).__name__}",
                "template_used": None,
                "screenshot": screenshot_path,
                "form_data": account_data
            }
            filled = await self._fill_fields(selectors, account_data)
            if not filled:
                return {
                    "success": False,
                    "message": "Manual field fill timeout",
                    "reason": "manual_fill_timeout",
                    "template_used": template["name"] if template else "heuristic",
                    "screenshot": await self._take_screenshot("manual_fill_timeout", account_data.get("username")),
                    "form_data": account_data
                }
            
    async def _get_selectors(self) -> tuple[dict | None, dict | None, str | None]:
        """
        Получает селекторы для формы регистрации (шаблон или эвристика).
        
        Returns:
            Кортеж (selectors, template, form_selector) или (None, None, None) при ошибке.
        """
        # Получаем текущий URL и HTML
        try:
            url = await self.page.get_url()
            page_source = await self.page.evaluate("document.documentElement.outerHTML")
        except Exception as e:
            logger.error(f"Failed to get page source: {e}")
            return None, None, None
        
        # Пытаемся найти шаблон
        template = await self.template_manager.detect_template(url, page_source)
        
        if template:
            logger.info(f"Template found: {template.get('name', 'unknown')}")
            selectors = template.get("fields", {})
            form_selector = template.get("registration_page", {}).get("form_selector")
            return selectors, template, form_selector
        else:
            # Эвристика
            logger.info("No template found, using heuristic analysis")
            selectors = await self.selector_finder.analyze_current_page()
            
            if selectors is None:
                logger.warning("Heuristic analysis failed to find form")
                return None, None, None
            
            form_selector = selectors.get("form_selector")
            return selectors, None, form_selector
    
    async def _navigate_to_registration_page(self, template: dict | None) -> None:
        """
        Переходит на страницу регистрации (если указано в шаблоне).
        """
        if template:
            reg_page = template.get("registration_page", {})
            reg_url = reg_page.get("url")
            
            if reg_url:
                current_url = await self.page.get_url()
                full_url = current_url.rstrip("/") + reg_url
                logger.info(f"Navigating to registration page: {full_url}")
                await self.browser.goto(full_url)
            else:
                logger.info("Already on registration page (no URL in template)")
        else:
            logger.info("No template, assuming already on registration page")
    
    async def _fill_fields(self, selectors: dict, account_data: AccountData) -> bool:
        """
        Заполняет поля формы регистрации.
        """
        # Стандартные поля
        field_mappings = {
            "username": account_data.get("username"),
            "email": account_data.get("email"),
            "password": account_data.get("password"),
        }
        
        for field_name, value in field_mappings.items():
            selector = selectors.get(field_name)
            if selector and value:
                logger.debug(f"Filling field {field_name} with selector {selector}")
                await self.browser.human_type(selector, value)
            elif not selector:
                logger.debug(f"No selector for field {field_name}")
        
        # Confirm password (только если есть селектор)
        confirm_selector = selectors.get("confirm_password")
        if confirm_selector:
            logger.debug(f"Filling confirm_password with selector {confirm_selector}")
            await self.browser.human_type(confirm_selector, account_data.get("password"))
        else:
            logger.debug("No confirm_password selector, skipping")
        
        # Checkbox (клик, не ввод)
        checkbox_selector = selectors.get("agree_checkbox")
        if checkbox_selector:
            logger.debug(f"Clicking agree_checkbox with selector {checkbox_selector}")
            await self.browser.human_click(checkbox_selector)
        else:
            logger.debug("No agree_checkbox selector, skipping")
        
        # Custom fields
        custom_fields = account_data.get("custom_fields", {})
        template_custom = selectors.get("custom_fields", [])
        
        for custom_field in template_custom:
            field_name = custom_field.get("name")
            selector = custom_field.get("selector")
            if field_name and selector and field_name in custom_fields:
                logger.debug(f"Filling custom field {field_name}")
                await self.browser.human_type(selector, custom_fields[field_name])
                
        # Если ключевые поля не найдены — запрашиваем ручное заполнение
        key_fields = ["username", "email", "password"]
        missing = [f for f in key_fields if not selectors.get(f)]
        if missing:
            logger.warning(f"Поля не найдены автоматически: {missing}, ожидание ручного заполнения")
            timeout = self.config.get("manual_field_fill_timeout", 120)
            filled = await self.browser.wait_for_manual_field_fill(timeout=timeout)
            if not filled:
                return False

        return True
    # Заменить весь метод _handle_captcha целиком:
    async def _handle_captcha(self, selectors: dict) -> bool:
        """
        Обрабатывает капчу (ожидание решения).
    
        Если передан captcha_helper — решает через API (auto режим).
        При неудаче API — fallback на ручной режим если разрешено в конфиге.
        Если captcha_helper не передан — только ручной режим.
    
        Returns:
            True если капча решена, False если таймаут или ошибка.
        """
        captcha_indicator = selectors.get("captcha_indicator")
        if not captcha_indicator:
            logger.info("No captcha detected")
            return True
    
        logger.info("Captcha detected, waiting for solution")
        timeout = self.config.get("manual_captcha_timeout", 300)
    
        try:
            if self.captcha_helper:
                # Автоматический режим через CaptchaExtensionHelper
                page_url = await self.page.get_url()
                captcha_type = selectors.get("captcha_type", "recaptcha_v2")
                site_key = selectors.get("captcha_site_key")
                token = await self.captcha_helper.solve_captcha(
                    captcha_type=captcha_type,
                    site_key=site_key,
                    page_url=page_url,
                )
                if token:
                    logger.info("Captcha solved automatically via API")
                    return True
                # API не справился — fallback на ручной если разрешено
                if not self.config.get("manual_fallback", True):
                    logger.error("Captcha API failed, manual fallback disabled")
                    return False
                logger.warning("Captcha API failed, falling back to manual mode")
    
            # Ручной режим (или fallback после неудачи API)
            await self.browser.wait_for_captcha_solved(
                timeout=timeout,
                manual_mode=True,
            )
            logger.info("Captcha solved manually")
            return True

        except Exception as e:
            logger.error(f"Captcha solving failed: {e}")
            return False
            
    async def _submit_form(self, selectors: dict, form_selector: str | None) -> None:
        """
        Отправляет форму регистрации.
        """
        submit_selector = selectors.get("submit_button")
        
        if not submit_selector and form_selector:
            # Пытаемся найти кнопку внутри формы
            logger.debug("No submit_button selector, searching within form")
            try:
                form_element = await self.page.query(form_selector)
                if form_element:
                    buttons = await form_element.query_all('button[type="submit"], input[type="submit"]')
                    if buttons:
                        # Генерируем селектор для первой кнопки
                        submit_selector = await self._generate_css_selector(buttons[0])
                        logger.debug(f"Found submit button: {submit_selector}")
            except Exception as e:
                logger.warning(f"Failed to find submit button in form: {e}")
        
        if submit_selector:
            logger.info(f"Submitting form with selector: {submit_selector}")
            await self.browser.human_click(submit_selector)
        else:
            logger.error("No submit button found")
            raise RuntimeError("Submit button not found")
    
    async def _check_result(self, template: dict | None) -> tuple[bool, str | None]:
        """
        Проверяет результат регистрации.
        
        Returns:
            Кортеж (success, error_reason).
        """
        # Ждём загрузки страницы
        import asyncio
        await asyncio.sleep(3)
        
        # Получаем HTML
        try:
            page_source = await self.page.evaluate("document.documentElement.outerHTML")
        except Exception as e:
            logger.error(f"Failed to get page source for result check: {e}")
            return False, "page_source_error"
        
        # Получаем индикаторы
        if template:
            success_indicators = template.get("success_indicators", self.success_indicators)
            error_indicators = template.get("error_indicators", self.error_indicators)
        else:
            success_indicators = self.success_indicators
            error_indicators = self.error_indicators
        
        # Проверяем успех
        if self._check_success(page_source, success_indicators):
            return True, None
        
        # Проверяем ошибку
        error_reason = self._check_error(page_source, error_indicators)
        if error_reason:
            return False, error_reason
        
        # Если нет явных индикаторов, проверяем URL
        try:
            current_url = await self.page.get_url()
            if "register" not in current_url.lower():
                logger.info("URL changed, assuming success")
                return True, None
        except Exception:
            pass
        
        # По умолчанию считаем успехом
        logger.warning("No explicit success/error indicators, assuming success")
        return True, None
    
    def _check_success(self, page_source: str, indicators: list[str]) -> bool:
        """
        Проверяет наличие индикаторов успеха в HTML.
        """
        page_source_lower = page_source.lower()
        for indicator in indicators:
            if indicator.lower() in page_source_lower:
                logger.debug(f"Success indicator found: {indicator}")
                return True
        return False
    
    def _check_error(self, page_source: str, indicators: list[str]) -> str | None:
        """
        Возвращает первый найденный индикатор ошибки.
        """
        page_source_lower = page_source.lower()
        for indicator in indicators:
            if indicator.lower() in page_source_lower:
                logger.debug(f"Error indicator found: {indicator}")
                return indicator
        return None
    
    async def _take_screenshot(self, prefix: str = "error", username: str | None = None) -> str:
        """
        Делает скриншот текущей страницы.
        
        Args:
            prefix: префикс для имени файла.
            username: имя пользователя (опционально).
        
        Returns:
            Путь к сохранённому файлу.
        """
        try:
            # Получаем скриншот через page
            image_data = await self.page.screenshot()
            
            # Формируем имя файла
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if username:
                filename = f"{prefix}_{username}_{timestamp}.png"
            else:
                filename = f"{prefix}_{timestamp}.png"
            
            filepath = self.screenshot_dir / filename
            
            # Сохраняем файл
            import aiofiles
            async with aiofiles.open(filepath, "wb") as f:
                await f.write(image_data)
            
            logger.info(f"Screenshot saved: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return None
    
    async def _generate_css_selector(self, element) -> str:
        """
        Генерирует CSS-селектор для элемента.
        
        Args:
            element: элемент Pydoll.
        
        Returns:
            CSS-селектор в виде строки.
        """
        try:
            # Пытаемся получить id
            elem_id = await element.get_attribute("id")
            if elem_id:
                return f"#{elem_id}"
            
            # Пытаемся получить name
            elem_name = await element.get_attribute("name")
            if elem_name:
                tag = await element.get_attribute("tagName")
                return f"{tag.lower()}[name='{elem_name}']"
            
            # Fallback: используем tag
            tag = await element.get_attribute("tagName")
            return f"{tag.lower()}"
            
        except Exception as e:
            logger.warning(f"Failed to generate CSS selector: {e}")
            return "body"