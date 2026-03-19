from __future__ import annotations

import asyncio
import json
import re
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

import aiofiles
from loguru import logger
from pydoll.exceptions import PageLoadTimeout
from utils.account_manager import AccountManager

class MainOrchestrator:
    """Главный управляющий модуль системы массовой регистрации на форумах.

    Координирует работу всех компонентов:
    - BrowserController — управление браузером (per user)
    - RegistrationController — регистрация на форуме (per user)
    - ProxyManager — предоставление прокси
    - CaptchaExtensionHelper — решение капч (shared)
    - AccountManager — хранение аккаунтов

    Обеспечивает:
    - Параллельную обработку пользователей (asyncio.Semaphore)
    - Resume после сбоя (по последней строке results_ok_*.txt)
    - Retry с экспоненциальной задержкой
    - Graceful shutdown
    - Финальный отчёт
    """

    def __init__(self, config: dict | None = None) -> None:
        """Args:
            config: словарь конфигурации. Если не передан — загружается из settings.py.
        """
        self._config = config or self._load_settings()

        # Статистика сессии
        self._stats: dict = {
            "total_forums": 0,
            "total_users": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "captcha_auto": 0,
            "captcha_manual": 0,
            "captcha_failed": 0,
            "profiles_created": 0,
        }
        self._account_manager = AccountManager(
            accounts_file=self._config.get("ACCOUNTS_FILE", "data/accounts.json")
        )

        # Активные браузеры для graceful shutdown
        self._active_browsers: list = []
        self._shutdown_event = asyncio.Event()
        self._report_printed = False

    # =========================================================================
    # Публичные методы
    # =========================================================================

    async def run(self) -> dict:
        """Запускает полный цикл регистрации для всех пользователей на всех форумах.

        Returns:
            Словарь со статистикой сессии.
        """
        # 1. Загрузка данных
        all_forums = await self._load_forums()
        if not all_forums:
            logger.error("Список форумов пуст, завершение работы")
            return self._stats

        users = await self._load_accounts()
        if not users:
            logger.error("Список аккаунтов пуст, завершение работы")
            return self._stats

        self._stats["total_forums"] = len(all_forums)
        self._stats["total_users"] = len(users)
        logger.info(
            f"Загружено: {len(all_forums)} форумов, {len(users)} пользователей"
        )

        # 2. Resume logic — определяем стартовый индекс для каждого пользователя
        user_queues: dict[str, list[str]] = {}
        for user in users:
            username = user["username"]
            start_index = await self._get_resume_index(username, all_forums)
            queue = all_forums[start_index:]
            user_queues[username] = queue
            logger.info(
                f"User {username}: resuming from forum index {start_index} "
                f"({len(queue)} форумов осталось)"
            )

        # 3. Загружаем прокси
        proxy_manager = await self._init_proxy_manager()

        # 4. Параллельная обработка пользователей
        max_concurrent = self._config.get("MAX_CONCURRENT_USERS", 5)
        semaphore = asyncio.Semaphore(max_concurrent)

        tasks = []
        for user in users:
            username = user["username"]
            queue = user_queues.get(username, [])
            if not queue:
                logger.info(f"User {username}: все форумы уже обработаны, пропускаем")
                continue
            task = asyncio.create_task(
                self._process_user_with_semaphore(
                    semaphore, username, user, queue, proxy_manager
                )
            )
            tasks.append(task)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Ошибка в задаче пользователя: {r}")

        # 5. Финальный отчёт
        report = self._generate_final_report(all_forums, users)
        if self._config.get("SHOW_FINAL_REPORT", True):
            print(report)
        self._report_printed = True

        return self._stats

    async def shutdown(self) -> None:
        """Корректное завершение работы — закрывает браузеры, сохраняет прогресс."""
        logger.info("Initiating graceful shutdown...")
        self._shutdown_event.set()

        timeout = self._config.get("GRACEFUL_SHUTDOWN_TIMEOUT", 60)
        try:
            await asyncio.wait_for(
                asyncio.gather(*[b.stop() for b in self._active_browsers if b]),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Graceful shutdown timeout ({timeout}s), forcing close")

        if not self._report_printed:
            # Загружаем данные для отчёта из файлов
            all_forums = await self._load_forums()
            users = await self._load_accounts()
            report = self._generate_final_report(all_forums, users)
            print(report)

        logger.info("Shutdown complete")

    # =========================================================================
    # Вспомогательные методы — работа с файлами
    # =========================================================================

    def _sanitize_filename(self, username: str) -> str:
        """Создаёт безопасное имя файла из username.

        Args:
            username: имя пользователя.

        Returns:
            Строка без недопустимых символов файловой системы.
        """
        return re.sub(r'[<>:"/\\|?*]', "_", username).strip()

    def _parse_file_line(self, line: str) -> str | None:
        """Универсальный парсер строк файлов результатов.

        Args:
            line: строка из файла.

        Returns:
            URL (первое поле) или None для комментариев и пустых строк.
        """
        line = line.strip()
        comment_prefix = self._config.get("INPUT_COMMENT_PREFIX", "#")
        if not line or line.startswith(comment_prefix):
            return None
        return line.split(" ", 1)[0]

    def _write_result(
        self,
        filepath: str | Path,
        url: str,
        extra_data: list[str],
    ) -> None:
        """Записывает результат регистрации в файл (append + flush).

        Args:
            filepath: путь к файлу результатов.
            url: URL форума.
            extra_data: дополнительные поля (timestamp или error_code + timestamp).
        """
        line = f"{url} {' '.join(extra_data)}\n"
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line)
            if self._config.get("RESULTS_FLUSH_IMMEDIATELY", True):
                f.flush()

    async def _load_forums(self) -> list[str]:
        """Загружает список форумов из results_new.txt.

        Returns:
            Список URL форумов без комментариев и пустых строк.
        """
        source_file = Path(self._config.get("FORUMS_SOURCE_FILE", "data/results_new.txt"))
        if not source_file.exists():
            logger.error(f"Файл форумов не найден: {source_file}")
            return []

        forums = []
        async with aiofiles.open(source_file, encoding="utf-8") as f:
            async for line in f:
                url = self._parse_file_line(line)
                if url:
                    forums.append(url)

        logger.info(f"Загружено форумов: {len(forums)}")
        return forums

    async def _load_accounts(self) -> list[dict]:
        """Загружает список аккаунтов из accounts.json.

        Поддерживает хранение пароля в переменных окружения.
        Формат: "password": "name='ENV_VAR_NAME'"

        Returns:
            Список аккаунтов или пустой список при ошибке.
        """
        accounts_file = Path(self._config.get("ACCOUNTS_FILE", "data/accounts.json"))
        if not accounts_file.exists():
            logger.error(f"Файл аккаунтов не найден: {accounts_file}")
            return []

        try:
            async with aiofiles.open(accounts_file, encoding="utf-8") as f:
                content = await f.read()
            accounts = json.loads(content)

            # Подставляем пароли из переменных окружения
            for account in accounts:
                password = account.get("password", "")
                match = re.match(r"name='(.+)'", str(password))
                if match:
                    env_name = match.group(1)
                    env_value = os.environ.get(env_name, "")
                    if env_value:
                        account["password"] = env_value
                        logger.debug(f"Пароль загружен из переменной окружения: {env_name}")
                    else:
                        logger.error(f"Переменная окружения не найдена: {env_name}")

            # В конце _load_accounts перед return accounts
            accounts = [a for a in accounts if a.get("status", "pending") == "pending"]
            return accounts
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Ошибка загрузки аккаунтов: {e}")
            return []

    async def _get_resume_index(self, username: str, all_forums: list[str]) -> int:
        """Определяет индекс форума с которого нужно продолжить работу.

        Читает последнюю строку results_ok_{username}.txt и ищет URL в списке форумов.
        Затем пропускает форумы из results_bad_{username}.txt начиная с найденного
        индекса — до первого форума которого нет в bad-списке.

        Args:
            username: имя пользователя.
            all_forums: полный список форумов.

        Returns:
            Индекс следующего форума (0 если начинать сначала).
        """
        safe_name = self._sanitize_filename(username)
        results_dir = Path(self._config.get("RESULTS_DIR", "data"))
        ok_file = results_dir / f"results_ok_{safe_name}.txt"

        # Определяем start_index по results_ok
        start_index = 0
        if ok_file.exists() and ok_file.stat().st_size > 0:
            try:
                async with aiofiles.open(ok_file, encoding="utf-8") as f:
                    content = await f.read()
                urls = [
                    u for u in
                    (self._parse_file_line(l) for l in content.splitlines())
                    if u
                ]
                if urls:
                    last_url = urls[-1]
                    if last_url in all_forums:
                        start_index = all_forums.index(last_url) + 1
            except OSError as e:
                logger.warning(f"Не удалось прочитать файл ok для {username}: {e}")

        # Читаем results_bad — собираем множество URL
        bad_urls: set[str] = set()
        bad_file = results_dir / f"results_bad_{safe_name}.txt"
        if bad_file.exists() and bad_file.stat().st_size > 0:
            try:
                async with aiofiles.open(bad_file, encoding="utf-8") as f:
                    content = await f.read()
                bad_urls = {
                    u for u in
                    (self._parse_file_line(l) for l in content.splitlines())
                    if u
                }
            except OSError as e:
                logger.warning(f"Не удалось прочитать файл bad для {username}: {e}")

        # Пропускаем форумы из bad начиная с start_index
        if bad_urls:
            while start_index < len(all_forums):
                if all_forums[start_index] not in bad_urls:
                    break
                logger.debug(
                    f"User {username}: пропускаем форум из bad-списка: "
                    f"{all_forums[start_index]}"
                )
                start_index += 1

            if start_index >= len(all_forums):
                logger.warning(
                    f"User {username}: все форумы обработаны или находятся в bad-списке"
                )

        logger.debug(f"User {username}: начинаем с индекса {start_index}")
        return start_index

    async def _init_proxy_manager(self):
        """Инициализирует и загружает ProxyManager.

        Returns:
            Экземпляр ProxyManager с загруженными прокси.
        """
        from utils.proxy_manager import ProxyManager

        proxies_file = self._config.get("PROXIES_FILE", "data/proxies.txt")
        pm = ProxyManager(proxies_file)
        await pm.load_proxies()
        return pm

    def _get_proxy_for_user(self, user: dict, proxy_manager) -> str | None:
        """Возвращает прокси для пользователя по его proxy_id.

        Args:
            user: данные пользователя из accounts.json.
            proxy_manager: экземпляр ProxyManager.

        Returns:
            Строка прокси или None если не найден.
        """
        proxy_id = user.get("proxy_id")
        
        # proxy_id: null — явно указано работать без прокси
        if proxy_id is None:
            if self._config.get("REQUIRE_PROXY_PER_USER", True):
                logger.warning(
                    f"proxy_id не задан для {user['username']}, "
                    f"работаем без прокси"
                )
            return None  # ← раньше здесь была ошибка и пропуск пользователя
        
        # proxy_id задан — берём из списка
        if not proxy_manager.proxies:
            logger.error("Список прокси пуст")
            return None
    
        if proxy_id >= len(proxy_manager.proxies):
            logger.error(
                f"proxy_id={proxy_id} выходит за пределы списка "
                f"({len(proxy_manager.proxies)} прокси)"
            )
            return None
    
        return proxy_manager.proxies[proxy_id]

    # =========================================================================
    # Обработка пользователей
    # =========================================================================

    async def _process_user_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        username: str,
        user_data: dict,
        forum_queue: list[str],
        proxy_manager,
    ) -> dict:
        """Обёртка над _process_user с семафором для ограничения параллелизма."""
        async with semaphore:
            return await self._process_user(username, user_data, forum_queue, proxy_manager)

    async def _process_user(
        self,
        username: str,
        user_data: dict,
        forum_queue: list[str],
        proxy_manager,
    ) -> dict:
        """Обрабатывает все форумы для одного пользователя.

        Создаёт браузер с персистентным профилем, регистрируется на каждом
        форуме из очереди, записывает результаты, закрывает браузер.

        Args:
            username: имя пользователя.
            user_data: полные данные аккаунта из accounts.json.
            forum_queue: список форумов для обработки.
            proxy_manager: экземпляр ProxyManager.

        Returns:
            Статистика пользователя.
        """
        from controllers.browser_controller import BrowserController
        from controllers.registration_controller import RegistrationController
        from template_manager import TemplateManager
        from selector_finder import SelectorFinder
        from utils.captcha_helper import CaptchaExtensionHelper

        safe_username = self._sanitize_filename(username)
        results_dir = Path(self._config.get("RESULTS_DIR", "data"))
        results_dir.mkdir(parents=True, exist_ok=True)

        ok_file = results_dir / f"results_ok_{safe_username}.txt"
        bad_file = results_dir / f"results_bad_{safe_username}.txt"

        # Прокси пользователя
        require_proxy = self._config.get("REQUIRE_PROXY_PER_USER", True)
        if require_proxy:
            # Глобально включено — смотрим профиль пользователя
            proxy = self._get_proxy_for_user(user_data, proxy_manager)
        else:
            # Глобально выключено — игнорируем proxy_id в профиле
            proxy = None
            logger.debug(f"User {username}: прокси отключены глобально")


        # Профиль браузера
        profiles_dir = Path(self._config.get("PROFILES_DIR", "data/profiles"))
        profile_path = profiles_dir / safe_username
        if profile_path.exists():
            logger.info(f"User {username}: используется существующий профиль")
        else:
            logger.info(f"User {username}: создан новый профиль")

        user_stats = {
            "username": username,
            "success": 0,
            "failed": 0,
            "forums_registered": [],
        }

        browser = BrowserController(
            proxy=proxy,
            user_data_dir=profile_path,
            headless=not self._config.get("SHOW_BROWSER_WINDOWS", True),
        )

        self._active_browsers.append(browser)
        self._stats["profiles_created"] += 1

        try:
            await browser.start()
            page = await browser.get_current_tab()

            # Заголовок окна
            if self._config.get("SET_WINDOW_TITLE", True):
                title = self._config.get(
                    "WINDOW_TITLE_FORMAT", "{username} | {forum} | {status}"
                ).format(username=username, forum="init", status="running")
                try:
                    await page.execute_script(f"document.title = '{title}'")
                except Exception:
                    pass

            # Конфигурация для RegistrationController
            reg_config = {
                "manual_captcha_timeout": self._config.get("MANUAL_CAPTCHA_TIMEOUT", 300),
                "manual_field_fill_timeout": self._config.get("MANUAL_FIELD_FILL_TIMEOUT", 120),
                "max_retries": self._config.get("MAX_REGISTRATION_RETRIES", 3),
                "manual_fallback": True,
                "TEST_MODE": self._config.get("TEST_MODE", False),
            }

            # Captcha helper (stats callback обновляет общую статистику)
            captcha_helper = CaptchaExtensionHelper(
                page=page,
                stats_callback=self._on_captcha_stats,
            )

            template_manager = TemplateManager(
                accounts_file=self._config.get("ACCOUNTS_FILE", "data/accounts.json")
            )
            selector_finder = SelectorFinder(page=page, template_manager=template_manager)

            reg_controller = RegistrationController(
                browser_controller=browser,
                template_manager=template_manager,
                selector_finder=selector_finder,
                page=page,
                config=reg_config,
                captcha_helper=captcha_helper,
            )

            # Обработка каждого форума
            for forum_url in forum_queue:
                if self._shutdown_event.is_set():
                    logger.info(f"User {username}: shutdown signal, останавливаем")
                    break

                await self._process_forum(
                    browser=browser,
                    page=page,
                    reg_controller=reg_controller,
                    username=username,
                    forum_url=forum_url,
                    user_data=user_data,
                    ok_file=ok_file,
                    bad_file=bad_file,
                    user_stats=user_stats,
                )

            # Финализация captcha_helper (сохраняет learned costs)
            await captcha_helper.finalize()

        except Exception as e:
            logger.error(f"User {username}: критическая ошибка: {type(e).__name__}: {e}")
        finally:
            if self._config.get("CLOSE_BROWSER_AFTER_ALL_REGISTRATIONS", True):
                try:
                    await browser.stop()
                except Exception:
                    pass
            if browser in self._active_browsers:
                self._active_browsers.remove(browser)

        # Сохраняем meta.json профиля
        if self._config.get("SAVE_PROFILE_METADATA", True):
            await self._save_profile_meta(
                profile_path=profile_path,
                username=username,
                proxy_id=user_data.get("proxy_id", 0),
                proxy_value=proxy or "",
                forums_registered=user_stats["forums_registered"],
            )

        logger.info(
            f"User {username}: завершён. "
            f"Успешно: {user_stats['success']}, Неудач: {user_stats['failed']}"
        )
        return user_stats

    async def _process_forum(
        self,
        browser,
        page,
        reg_controller,
        username: str,
        forum_url: str,
        user_data: dict,
        ok_file: Path,
        bad_file: Path,
        user_stats: dict,
    ) -> None:
        """Выполняет регистрацию на одном форуме с retry-логикой.

        Args:
            browser: экземпляр BrowserController.
            page: текущая вкладка Pydoll.
            reg_controller: экземпляр RegistrationController.
            username: имя пользователя.
            forum_url: URL форума.
            user_data: данные аккаунта.
            ok_file: файл для записи успехов.
            bad_file: файл для записи неудач.
            user_stats: словарь статистики пользователя (изменяется in-place).
        """
        max_retries = self._config.get("MAX_REGISTRATION_RETRIES", 3)
        no_retry_reasons = self._config.get("NO_RETRY_REASONS", set())
        timestamp_fmt = self._config.get("OUTPUT_TIMESTAMP_FORMAT", "%Y-%m-%dT%H:%M:%S")

        # Обновляем заголовок окна
        if self._config.get("SET_WINDOW_TITLE", True):
            title_fmt = self._config.get(
                "WINDOW_TITLE_FORMAT", "{username} | {forum} | {status}"
            )
            try:
                title = title_fmt.format(
                    username=username, forum=forum_url, status="registering"
                )
                await page.execute_script(f"document.title = '{title}'")
            except Exception:
                pass

        attempt = 0
        result = None
        tab = None
        tab_count = 0  # счётчик открытых вкладок — защита от закрытия единственной

        while attempt < max_retries:
            try:
                if self._config.get("TAB_PER_REGISTRATION", True):
                    tab = await browser.new_tab()
                    tab_count += 1

                # Сначала обновляем page во всех контроллерах — потом навигация
                target_page = await browser.get_current_tab()
                if not target_page:
                    raise RuntimeError("Нет активной вкладки для регистрации")
                reg_controller.page = target_page
                reg_controller.selector_finder.page = target_page

                await browser.goto(
                    forum_url,
                    page_load_wait=self._config.get("PAGE_LOAD_WAIT", 5),
                    load_timeout=self._config.get("FIND_REGISTRATION_PAGE_TIMEOUT", 60),
                )

                # Определяем движок один раз — страница уже загружена
                try:
                    # Предпочитаем нативный метод, если он есть в твоей версии Pydoll
                    page_source = await target_page.get_content()
                except (AttributeError, NotImplementedError):
                    # Fallback через JS, если get_content() отсутствует
                    resp = await target_page.execute_script(
                        "return document.documentElement.outerHTML;"
                    )
                    page_source = resp.get("result", {}).get("result", {}).get("value", "")

                if not page_source:
                    logger.warning(f"Не удалось получить исходный код страницы {forum_url}")
                    page_source = ""  # или можно return / continue в зависимости от логики

                engine_name, template = await reg_controller.template_manager.detect_engine(
                    url=forum_url,
                    page_source=page_source,
                )

                logger.info(
                    f"Форум {forum_url}: определён движок '{engine_name}', "
                    f"шаблон {'найден' if template else 'будет создан или эвристика'}"
                )              

                # Запускаем регистрацию, передавая engine_name и template
                result = await asyncio.wait_for(
                    reg_controller.register(
                        user_data,
                        engine_name=engine_name,
                        template=template,
                    ),
                    timeout=self._config.get("TAB_TIMEOUT_SECONDS", 300),
                )

            except asyncio.TimeoutError:
                logger.warning(f"User {username} @ {forum_url}: таймаут регистрации")
                result = {
                    "success": False,
                    "reason": "timeout",
                    "template_used": None,
                    "screenshot": None,
                    "form_data": user_data,
                    "message": "Registration timeout",
                }

            except PageLoadTimeout:
                logger.warning(f"User {username} @ {forum_url}: таймаут загрузки страницы")
                result = {
                    "success": False,
                    "reason": "timeout",
                    "template_used": None,
                    "screenshot": None,
                    "form_data": user_data,
                    "message": "Page load timeout",
                }

            except RuntimeError as e:
                # page_unavailable — брошен из browser_controller.goto()
                if "page_unavailable" in str(e):
                    logger.warning(f"User {username} @ {forum_url}: {e}")
                    result = {
                        "success": False,
                        "reason": "page_unavailable",
                        "template_used": None,
                        "screenshot": None,
                        "form_data": user_data,
                        "message": str(e),
                    }
                else:
                    raise

            except Exception as e:
                logger.error(
                    f"User {username} @ {forum_url}: исключение: {type(e).__name__}: {e}"
                )
                result = {
                    "success": False,
                    "reason": "browser_crash",
                    "template_used": None,
                    "screenshot": None,
                    "form_data": user_data,
                    "message": str(e),
                }

                # При краше браузера — перезапускаем
                if "browser_crash" in str(e).lower():
                    try:
                        await browser.stop()
                        await browser.start()
                    except Exception:
                        pass

            finally:
                # Закрываем вкладку только если:
                # - конфиг разрешает CLOSE_TAB_AFTER_REGISTRATION
                # - TAB_PER_REGISTRATION включён
                # - вкладка была открыта в этой итерации
                # - это не единственная вкладка (tab_count > 1 защищает от закрытия последней)
                if (
                    self._config.get("CLOSE_TAB_AFTER_REGISTRATION", True)
                    and self._config.get("TAB_PER_REGISTRATION", True)
                    and tab is not None
                    and tab_count > 1
                ):
                    await browser.close_tab(tab)
                    tab_count -= 1
                    tab = None

            if result and result.get("success"):
                break

            # Проверяем нужен ли retry
            reason = result.get("reason") if result else "unknown"
            if reason in no_retry_reasons:
                logger.debug(f"User {username} @ {forum_url}: no-retry reason={reason}")
                break

            attempt += 1
            if attempt < max_retries:
                delay = 2 ** attempt
                logger.info(
                    f"User {username} @ {forum_url}: "
                    f"retry {attempt}/{max_retries} через {delay}s"
                )
                await asyncio.sleep(delay)

        # Записываем результат
        timestamp = datetime.now().strftime(timestamp_fmt)

        if result and result.get("success"):
            self._write_result(ok_file, forum_url, [timestamp])
            user_stats["success"] += 1
            user_stats["forums_registered"].append(forum_url)
            self._stats["success"] += 1
            logger.info(f"User {username} registered on {forum_url}")
            await self._account_manager.update_account_status(username=username)
        else:
            reason = (result.get("reason") or "unknown") if result else "unknown"
            self._write_result(bad_file, forum_url, [reason, timestamp])
            user_stats["failed"] += 1
            self._stats["failed"] += 1
            logger.warning(f"User {username} failed on {forum_url}: {reason}")
            await self._account_manager.update_account_status(
                username=username,
                reason=reason,
            )

    # =========================================================================
    # Отчёт и статистика
    # =========================================================================

    def _on_captcha_stats(self, stats: dict) -> None:
        """Callback для получения статистики капч от CaptchaExtensionHelper.

        Args:
            stats: словарь с полями provider, captcha_type, success, cost, solve_time.
        """
        if stats.get("success"):
            if stats.get("provider") == "manual":
                self._stats["captcha_manual"] += 1
            else:
                self._stats["captcha_auto"] += 1
        else:
            self._stats["captcha_failed"] += 1

    def _generate_final_report(self, all_forums: list[str], users: list[dict]) -> str:
        """Формирует итоговый отчёт сессии.

        Читает результирующие файлы и строит таблицы неудач по пользователям.

        Args:
            all_forums: полный список форумов.
            users: список аккаунтов.

        Returns:
            Отформатированная строка отчёта.
        """
        results_dir = Path(self._config.get("RESULTS_DIR", "data"))
        error_messages = self._config.get("ERROR_MESSAGES_RU", {})

        total_forums = len(all_forums)
        total_users = len(users)
        total_registrations = total_forums * total_users

        # Считаем успехи и неудачи из файлов
        total_ok = 0
        total_bad = 0
        failed_by_user: dict[str, list[tuple[str, str]]] = {}

        for user in users:
            username = user["username"]
            safe_name = self._sanitize_filename(username)

            ok_file = results_dir / f"results_ok_{safe_name}.txt"
            bad_file = results_dir / f"results_bad_{safe_name}.txt"

            if ok_file.exists():
                ok_lines = [
                    l.strip() for l in ok_file.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")
                ]
                total_ok += len(ok_lines)

            if bad_file.exists():
                bad_lines = [
                    l.strip() for l in bad_file.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")
                ]
                total_bad += len(bad_lines)
                if bad_lines and self._config.get("SHOW_FAILED_DETAILS", True):
                    failed_by_user[username] = []
                    for line in bad_lines:
                        parts = line.split(" ")
                        url = parts[0] if parts else "?"
                        reason = parts[1] if len(parts) > 1 else "unknown"
                        failed_by_user[username].append((url, reason))

        sep = "═" * 59
        thin_sep = "─" * 59

        lines = [
            sep,
            "                    REGISTRATION REPORT",
            sep,
            "",
            f"Всего форумов в очереди:     {total_forums}",
            f"Пользователей:               {total_users}",
            f"Всего регистраций:           {total_registrations} "
            f"({total_forums} форумов × {total_users} пользователя)",
            f"Обработано:                  {total_ok + total_bad}",
            f"Успешно:                     {total_ok}",
            f"Неудачи:                     {total_bad}",
            "",
            "Капчи:",
            f"  Решено автоматически:      {self._stats['captcha_auto']}",
            f"  Решено вручную:            {self._stats['captcha_manual']}",
            f"  Не решено:                 {self._stats['captcha_failed']}",
            "",
            f"Профили создано:             {self._stats['profiles_created']}",
        ]

        if failed_by_user and self._config.get("SHOW_FAILED_DETAILS", True):
            lines += [
                "",
                thin_sep,
                "                    FAILED REGISTRATIONS",
                thin_sep,
            ]
            for username, failures in failed_by_user.items():
                lines.append(f"\nПользователь: {username}")
                lines.append(f"| {'Форум':<24} | {'Причина':<32} |")
                lines.append(f"|{'-'*26}|{'-'*34}|")
                for url, reason in failures:
                    reason_ru = error_messages.get(reason, reason)
                    short_url = url.replace("https://", "").replace("http://", "")[:24]
                    lines.append(f"| {short_url:<24} | {reason_ru:<32} |")

        lines.append("")
        lines.append(sep)

        return "\n".join(lines)

    # =========================================================================
    # Вспомогательные методы
    # =========================================================================

    async def _save_profile_meta(
        self,
        profile_path: Path,
        username: str,
        proxy_id: int,
        proxy_value: str,
        forums_registered: list[str],
    ) -> None:
        """Сохраняет метаданные профиля браузера в meta.json.

        Args:
            profile_path: путь к директории профиля.
            username: имя пользователя.
            proxy_id: индекс прокси.
            proxy_value: строка прокси.
            forums_registered: список успешно зарегистрированных форумов.
        """
        meta = {
            "username": username,
            "proxy_id": proxy_id,
            "proxy_value": proxy_value,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "forums_registered": forums_registered,
        }
        try:
            profile_path.mkdir(parents=True, exist_ok=True)
            meta_file = profile_path / "meta.json"
            async with aiofiles.open(meta_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(meta, ensure_ascii=False, indent=2))
            logger.debug(f"Метаданные профиля сохранены: {meta_file}")
        except OSError as e:
            logger.warning(f"Не удалось сохранить meta.json для {username}: {e}")

    @staticmethod
    def _load_settings() -> dict:
        """Загружает конфигурацию из config/settings.py.

        Returns:
            Словарь с настройками.
        """
        try:
            import config.settings as s
            return {k: getattr(s, k) for k in dir(s) if not k.startswith("_")}
        except ImportError:
            logger.warning("config/settings.py не найден, используются дефолтные значения")
            return {}
