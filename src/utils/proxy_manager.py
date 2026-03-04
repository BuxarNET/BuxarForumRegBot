from __future__ import annotations

from pathlib import Path

import aiofiles
import aiohttp
from loguru import logger


class ProxyManager:
    """Менеджер прокси-серверов с поддержкой ротации и проверки.

    Загружает список прокси из файла, проверяет их работоспособность
    и выдаёт по кругу (round-robin).
    """

    def __init__(self, proxy_file: str | Path, check_timeout: int = 5) -> None:
        """Инициализация менеджера прокси.

        Args:
            proxy_file: Путь к файлу со списком прокси.
            check_timeout: Таймаут проверки прокси в секундах.
        """
        self.proxy_file = Path(proxy_file)
        self.check_timeout = check_timeout
        self.proxies: list[str] = []
        self.current_index: int = 0

    async def load_proxies(self) -> None:
        """Асинхронно загружает список прокси из файла.

        Поддерживаемые форматы:
            - protocol://user:pass@host:port
            - protocol://host:port
            - host:port (автоматически добавляется http://)

        Пустые строки и комментарии (#) игнорируются.
        """
        try:
            async with aiofiles.open(self.proxy_file, encoding="utf-8") as f:
                content = await f.read()
        except FileNotFoundError:
            logger.warning(f"Файл прокси не найден: {self.proxy_file}")
            self.proxies = []
            return

        proxies = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Если нет протокола — добавляем http://
            if "://" not in line:
                logger.warning(f"Протокол не указан, используется http://: {line}")
                line = f"http://{line}"
            proxies.append(line)

        self.proxies = proxies
        logger.info(f"Загружено прокси: {len(self.proxies)} из {self.proxy_file}")

    async def check_proxy(self, proxy: str) -> bool:
        """Проверяет работоспособность прокси.

        Подключается к http://httpbin.org/ip через указанный прокси.

        Args:
            proxy: Строка прокси в формате protocol://host:port.

        Returns:
            True если прокси работает, False в противном случае.
        """
        timeout = aiohttp.ClientTimeout(total=self.check_timeout)

        # Определяем connector для SOCKS-прокси
        connector = None
        if proxy.startswith(("socks4://", "socks5://")):
            try:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
                proxy_param = None  # connector сам обрабатывает прокси
            except ImportError:
                logger.warning("aiohttp-socks не установлен, SOCKS-прокси не поддерживается")
                return False
        else:
            proxy_param = proxy

        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout
            ) as session:
                kwargs: dict = {"url": "http://httpbin.org/ip"}
                if connector is None:
                    kwargs["proxy"] = proxy_param

                async with session.get(**kwargs) as response:
                    if response.status != 200:
                        logger.warning(f"Прокси {proxy}: статус {response.status}")
                        return False
                    data = await response.json()
                    if "origin" not in data:
                        logger.warning(f"Прокси {proxy}: некорректный ответ")
                        return False
                    return True
        except Exception as e:
            logger.warning(f"Прокси {proxy} недоступен: {type(e).__name__}: {e}")
            return False

    async def get_next_proxy(self, check: bool = True) -> str | None:
        """Возвращает следующий прокси по кругу (round-robin).

        Args:
            check: Если True — проверяет каждый прокси перед возвратом,
                   пропуская нерабочие.

        Returns:
            Строка прокси или None если рабочих прокси нет.
        """
        if not self.proxies:
            logger.warning("Список прокси пуст")
            return None

        total = len(self.proxies)

        for _ in range(total):
            proxy = self.proxies[self.current_index]
            self.current_index = (self.current_index + 1) % total

            if not check:
                logger.info(f"Используется прокси: {proxy}")
                return proxy

            if await self.check_proxy(proxy):
                logger.info(f"Используется прокси: {proxy}")
                return proxy
            else:
                logger.debug(f"Прокси пропущен (нерабочий): {proxy}")

        logger.warning("Не найдено ни одного рабочего прокси")
        return None

    async def refresh_proxies(self) -> None:
        """Перезагружает список прокси из файла и сбрасывает счётчик.

        Вызывает load_proxies() и сбрасывает current_index в 0.
        """
        self.current_index = 0
        await self.load_proxies()
        logger.info("Список прокси обновлён")
