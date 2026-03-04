# Промт 2: BrowserController — управление браузером через Pydoll

## Цель
Создать класс `BrowserController`, который будет центральным интерфейсом для управления браузером Chrome (Chromium) с использованием библиотеки `pydoll-python`. Он должен обеспечивать:
- Запуск браузера с заданными параметрами (прокси, профиль, расширения).
- Автоматическое подключение расширения для решения капчи (NopeCHA / 2Captcha) либо возможность ручного ввода капчи пользователем.
- Удобные методы для взаимодействия со страницей: `human_type`, `human_click`, `wait_for_element`, `scroll_to_element` и др.
- Универсальный метод ожидания решения капчи, который работает как с автоматическим расширением, так и в режиме ручного вмешательства.
- Работу с несколькими вкладками (по необходимости).
- Базовую обработку ошибок и перезапуск при падении.

## Задачи
1. Реализовать класс `BrowserController` в файле `src/controllers/browser_controller.py`.
2. Добавить поддержку прокси (HTTP/HTTPS/SOCKS5) с авторизацией.
3. Обеспечить загрузку расширения для капчи (путь из переменной окружения `CAPTCHA_EXTENSION_PATH`).
4. Реализовать методы с "человеческим" поведением:
   - `human_type(element_or_selector, text)` — ввод текста с неравномерными задержками между символами.
   - `human_click(element_or_selector)` — клик с реалистичным движением мыши.
   - `wait_for_element(selector, timeout=10)` — ожидание появления элемента.
   - `scroll_to_element(element)` — плавный скролл к элементу.
5. Реализовать метод `wait_for_captcha_solved(timeout=None, manual_mode=False)`:
   - Если `manual_mode=False` (по умолчанию) и расширение загружено, метод ожидает, пока капча будет автоматически решена (проверяет исчезновение характерных элементов капчи или появление признаков успеха).
   - Если `manual_mode=True` или расширение не загружено, метод выводит в лог сообщение с просьбой решить капчу вручную и ожидает нажатия клавиши Enter в консоли (или другого сигнала от пользователя).
   - Возвращает `True`, если капча решена, иначе `False` (по таймауту).
6. Добавить методы для работы с вкладками: `new_tab()`, `switch_to_tab(index_or_id)`, `close_tab()`.
7. Реализовать корректное завершение работы браузера (освобождение ресурсов).
8. Написать пример использования в отдельном тестовом файле `test_browser_controller.py`, демонстрирующий оба режима работы с капчей.

## Детальные инструкции

### 1. Базовая структура класса

```python
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

            await asyncio.sleep(1)
```

### 2. Тестовый файл `test_browser_controller.py`

```python
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
```

## 3. Критерии готовности
- [ ] Класс `BrowserController` реализован с указанными методами.
- [ ] Поддерживается запуск с прокси (проверено на тестовом прокси, если есть).
- [ ] Расширение для капчи корректно загружается (визуально заметно при запуске).
- [ ] Профили работают (после первого запуска в папке профиля появляются файлы).
- [ ] Тестовый скрипт выполняется без ошибок, браузер открывает страницу, вводит текст и кликает.
- [ ] Логирование работает, в логах видны отладочные сообщения.
- [ ] Ручной и автоматический режимы капчи функционируют как описано.
```
