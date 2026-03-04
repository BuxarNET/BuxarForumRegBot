#!/usr/bin/env python3
# src/controllers/browser_controller.py

import asyncio
from pathlib import Path
from typing import Optional, Union, Any

from loguru import logger
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.constants import Key
from pydoll.exceptions import ArgumentAlreadyExistsInOptions


class BrowserController:
    """
    Управление браузером Chrome через Pydoll.
    Поддерживает прокси, профили, расширения и человеческое поведение.
    """

    # Пути поиска браузера в дополнение к дефолтным путям pydoll
    EXTRA_BROWSER_PATHS = [
        '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/snap/bin/chromium',
        '/usr/lib/chromium-browser/chromium-browser',
    ]

    @staticmethod
    def _find_browser_binary() -> Optional[str]:
        """Ищет исполняемый файл браузера в нестандартных путях."""
        for path in BrowserController.EXTRA_BROWSER_PATHS:
            if Path(path).exists():
                logger.debug(f"Найден браузер: {path}")
                return path
        return None

    def __init__(
        self,
        proxy: Optional[str] = None,
        user_data_dir: Optional[Union[str, Path]] = None,
        headless: bool = False,
        extension_path: Optional[Union[str, Path]] = None,
    ):
        self.proxy = proxy
        self.user_data_dir = Path(user_data_dir) if user_data_dir else None
        self.headless = headless
        self.extension_path = Path(extension_path) if extension_path else None
        self.browser: Optional[Chrome] = None
        self._current_tab = None
        self._extension_loaded = (
            self.extension_path is not None and self.extension_path.exists()
        )
        if not self._extension_loaded and self.extension_path:
            logger.warning(f"Расширение не найдено по пути: {self.extension_path}")

    def _add_argument_safe(self, options: ChromiumOptions, arg: str):
        """Добавляет аргумент, игнорируя дубликаты."""
        try:
            options.add_argument(arg)
        except ArgumentAlreadyExistsInOptions:
            pass

    async def start(self):
        """Запускает браузер с заданными параметрами."""
        options = ChromiumOptions()
        options.headless = self.headless

        binary = self._find_browser_binary()
        if binary:
            options.binary_location = binary
            logger.info(f"Используется браузер: {binary}")
        else:
            logger.debug("Браузер не найден в EXTRA_BROWSER_PATHS, pydoll будет искать сам.")

        if self.proxy:
            self._add_argument_safe(options, f'--proxy-server={self.proxy}')
            logger.info(f"Используется прокси: {self.proxy}")

        if self.user_data_dir:
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            self._add_argument_safe(options, f'--user-data-dir={self.user_data_dir}')
            logger.info(f"Профиль сохранён в: {self.user_data_dir}")

        if self._extension_loaded:
            self._add_argument_safe(options, f'--disable-extensions-except={self.extension_path}')
            self._add_argument_safe(options, f'--load-extension={self.extension_path}')
            logger.info(f"Расширение загружено из {self.extension_path}")
        else:
            logger.info("Расширение для капчи не загружено (будет использован ручной режим при необходимости).")

        self.browser = Chrome(options=options)
        self._current_tab = await self.browser.start()
        logger.success("Браузер успешно запущен.")

    async def stop(self):
        """Останавливает браузер."""
        if self.browser:
            await self.browser.stop()
            self.browser = None
            self._current_tab = None
            logger.info("Браузер остановлен.")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    # ===== Управление вкладками =====

    async def get_current_tab(self):
        """Возвращает текущую активную вкладку."""
        return self._current_tab

    async def new_tab(self):
        """Создаёт новую вкладку и переключается на неё."""
        tab = await self.browser.new_tab()
        self._current_tab = tab
        logger.debug("Создана новая вкладка")
        return tab

    # ===== Навигация =====

    async def goto(self, url: str):
        """Переходит по URL в текущей вкладке."""
        await self._current_tab.go_to(url)
        logger.info(f"Перешли на {url}")

    async def back(self):
        """Возврат на предыдущую страницу."""
        await self._current_tab.go_back()
        logger.debug("Назад")

    async def refresh(self):
        """Обновляет страницу."""
        await self._current_tab.refresh()
        logger.debug("Страница обновлена")

    # ===== Поиск элементов =====

    async def find_element(
        self,
        selector: str,
        timeout: float = 10,
        raise_if_not_found: bool = True,
    ) -> Optional[Any]:
        """
        Ищет элемент по CSS-селектору через tab.query().

        В pydoll:
          - tab.find(**attrs)  — поиск по HTML-атрибутам (name=, type=, id=, ...)
          - tab.query(css)     — поиск по CSS-селектору  ← используем здесь
        """
        try:
            element = await self._current_tab.query(selector, timeout=timeout)
            if element is None:
                raise LookupError(f"Элемент не найден: {selector}")
            logger.debug(f"Элемент найден: {selector}")
            return element
        except Exception as e:
            if raise_if_not_found:
                raise
            logger.warning(f"Элемент не найден за {timeout}с: {selector}")
            return None

    async def wait_for_element(self, selector: str, timeout: float = 10) -> Any:
        """Ожидает появления элемента и возвращает его."""
        return await self.find_element(selector, timeout, raise_if_not_found=True)

    # ===== Human-like действия =====

    async def human_type(self, element_or_selector: Union[Any, str], text: str):
        """Вводит текст с имитацией человеческого набора."""
        if isinstance(element_or_selector, str):
            element = await self.wait_for_element(element_or_selector)
        else:
            element = element_or_selector

        await asyncio.sleep(0.2)
        await element.type_text(text)
        logger.debug(f"Введён текст: {text[:20]}{'...' if len(text) > 20 else ''}")

    async def human_click(self, element_or_selector: Union[Any, str]):
        """Кликает по элементу с реалистичным движением мыши."""
        if isinstance(element_or_selector, str):
            element = await self.wait_for_element(element_or_selector)
        else:
            element = element_or_selector

        await asyncio.sleep(0.3)
        await element.click()
        logger.debug("Клик выполнен")

    async def press_key(self, element_or_selector: Union[Any, str], key: Key):
        """
        Нажимает клавишу на элементе.

        Принимает константы из pydoll.constants.Key:
            Key.ENTER, Key.TAB, Key.ESCAPE, Key.ARROW_DOWN, ...

        Пример:
            await browser.press_key(search_box, Key.ENTER)
            await browser.press_key("#search", Key.TAB)
        """
        if isinstance(element_or_selector, str):
            element = await self.wait_for_element(element_or_selector)
        else:
            element = element_or_selector

        await element.press_keyboard_key(key)
        logger.debug(f"Нажата клавиша: {key}")

    async def press_tab_key(self):
        """Нажимает Tab через keyboard API вкладки (не привязан к элементу)."""
        await self._current_tab.keyboard.press(Key.TAB)
        logger.debug("Нажата клавиша Tab")

    async def scroll_to_element(self, element: Any):
        """Прокручивает страницу к элементу."""
        await element.scroll_into_view()
        await asyncio.sleep(0.3)
        logger.debug("Скролл к элементу выполнен")

    # ===== Обработка капчи =====

    async def wait_for_captcha_solved(
        self,
        timeout: Optional[float] = 300,
        manual_mode: bool = False,
    ) -> bool:
        """
        Ожидает решения капчи на текущей странице.
        """
        if manual_mode or not self._extension_loaded:
            logger.info("Решите капчу в открытом браузере вручную, затем нажмите Enter...")
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, input, "Нажмите Enter после решения капчи: ")
                logger.info("Ручное подтверждение получено.")
                return True
            except Exception as e:
                logger.error(f"Ошибка при ожидании ручного ввода: {e}")
                return False

        # Автоматический режим с расширением
        logger.info("Ожидание автоматического решения капчи...")
        captcha_selectors = [
            'iframe[src*="recaptcha"]',
            'iframe[src*="hcaptcha"]',
            'iframe[src*="turnstile"]',
            'iframe[src*="solvemedia"]',
            '.g-recaptcha',
            '.h-captcha',
            '#captcha',
        ]

        start_time = asyncio.get_running_loop().time()

        while True:
            captcha_found = False
            for selector in captcha_selectors:
                try:
                    element = await self._current_tab.query(selector, timeout=1)
                    if element is not None:
                        captcha_found = True
                        break
                except Exception:
                    pass

            if not captcha_found:
                logger.info("Капча не обнаружена (вероятно, решена).")
                return True

            elapsed = asyncio.get_running_loop().time() - start_time
            if timeout is not None and elapsed > timeout:
                logger.warning(f"Таймаут ожидания решения капчи ({timeout} с).")
                return False
    async def wait_for_manual_field_fill(
        self,
        timeout: float = 120,
    ) -> bool:
        """
        Ожидает ручного заполнения полей пользователем через консоль.
        
        Returns:
            True если пользователь подтвердил, False если таймаут.
        """
        logger.info(
            "Не удалось заполнить некоторые поля автоматически. "
            "Заполните их вручную в браузере, затем нажмите Enter..."
        )
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None, input, "Нажмите Enter после заполнения полей: "
                ),
                timeout=timeout,
            )
            logger.info("Ручное заполнение подтверждено.")
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут ручного заполнения полей ({timeout}с)")
            return False
    

                
                
                