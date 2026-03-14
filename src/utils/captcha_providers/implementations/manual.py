from __future__ import annotations

import asyncio
import time

from loguru import logger

from utils.captcha_providers.base import (
    CaptchaProvider,
    CaptchaResult,
    CaptchaTimeoutError,
)


class ManualProvider(CaptchaProvider):
    """Ручной режим решения капч — всегда последний в цепочке.

    Ожидает пока пользователь решит капчу в браузере.
    Не требует API-ключа и не проверяет баланс.
    """

    def __init__(self, api_key: str | None = None, learned_costs: dict | None = None) -> None:
        self._page = None  # устанавливается через set_page()
        self._timeout = 300

    def set_page(self, page) -> None:
        """Устанавливает объект страницы Pydoll для polling DOM."""
        self._page = page

    @property
    def name(self) -> str:
        return "manual"

    def is_available(self) -> bool:
        return True  # всегда доступен

    def supports_balance_check(self) -> bool:
        return False  # нет баланса

    def supports_type(self, captcha_type: str) -> bool:
        return True  # поддерживает все типы

    def get_cost_estimate(self, captcha_type: str) -> float:
        return 0.0  # бесплатно

    async def get_balance(self) -> float | None:
        return None

    async def solve(
        self,
        captcha_type: str,
        site_key: str | None,
        page_url: str,
        **kwargs,
    ) -> CaptchaResult:
        """Ожидает ручного решения капчи пользователем.

        Алгоритм:
            1. Логирует запрос на ручное решение
            2. Подсвечивает капчу через JS (если есть страница)
            3. Polling DOM каждые 2 сек на наличие токена
            4. При таймауте — CaptchaTimeoutError

        Args:
            captcha_type: тип капчи.
            site_key: не используется в ручном режиме.
            page_url: URL страницы (для логирования).
            **kwargs: timeout — таймаут ожидания в секундах.
        """
        timeout = kwargs.get("timeout", self._timeout)
        logger.info(
            f"Manual captcha solving required on {page_url}. "
            f"Waiting for user... (timeout: {timeout}s)"
        )

        # Подсвечиваем капчу если есть доступ к странице
        if self._page is not None:
            await self._highlight_captcha(captcha_type)

        start_time = time.monotonic()
        token = await self._poll_for_token(captcha_type, timeout)

        if token:
            solve_time = time.monotonic() - start_time
            logger.info(f"Manual captcha solved in {solve_time:.1f}s")
            return CaptchaResult(
                token=token,
                score=None,
                provider=self.name,
                cost=0.0,
                solve_time=solve_time,
                captcha_type=captcha_type,
            )

        raise CaptchaTimeoutError(
            f"Ручная капча не решена за {timeout} секунд"
        )

    async def _highlight_captcha(self, captcha_type: str) -> None:
        """Подсвечивает элемент капчи на странице через JS."""
        selector_map = {
            "recaptcha_v2": ".g-recaptcha, iframe[src*='recaptcha']",
            "hcaptcha": ".h-captcha, iframe[src*='hcaptcha']",
            "turnstile": ".cf-turnstile, iframe[src*='challenges.cloudflare']",
        }
        selector = selector_map.get(captcha_type, "#captcha, .captcha")

        js = f"""
        (function() {{
            const el = document.querySelector('{selector}');
            if (el) {{
                el.style.outline = '3px solid red';
                el.style.boxShadow = '0 0 10px red';
                el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
            }}
        }})();
        """
        try:
            await self._page.execute_script(js)
        except Exception as e:
            logger.debug(f"Не удалось подсветить капчу: {e}")

    async def _poll_for_token(self, captcha_type: str, timeout: int) -> str | None:
        """Опрашивает DOM каждые 2 секунды в поисках токена капчи.

        Args:
            captcha_type: тип капчи для выбора нужного селектора.
            timeout: максимальное время ожидания в секундах.

        Returns:
            Токен капчи или None при таймауте.
        """
        token_selectors = {
            "recaptcha_v2": "document.getElementById('g-recaptcha-response')",
            "recaptcha_v3": "document.getElementById('g-recaptcha-response')",
            "hcaptcha": "document.getElementById('h-captcha-response')",
            "turnstile": "document.querySelector('[name=\"cf-turnstile-response\"]')",
        }

        js_getter = token_selectors.get(
            captcha_type,
            "document.getElementById('g-recaptcha-response')"
        )
        js = f"""
        (function() {{
            const el = {js_getter};
            return el ? el.value : null;
        }})();
        """

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Если нет страницы — просто ждём (fallback без polling)
            if self._page is None:
                await asyncio.sleep(2)
                continue

            try:
                token = await self._page.execute_script(js)
                if token and len(token) > 10:
                    return token
            except Exception as e:
                logger.debug(f"Manual polling error: {e}")

            await asyncio.sleep(2)

        return None

    async def report_bad(self, task_id: str) -> bool:
        return False  # ручной режим не поддерживает жалобы
