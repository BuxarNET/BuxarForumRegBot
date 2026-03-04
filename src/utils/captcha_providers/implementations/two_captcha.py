from __future__ import annotations

import asyncio
import time

import aiohttp
from loguru import logger

from utils.captcha_providers.base import (
    CaptchaProvider,
    CaptchaResult,
    CaptchaUnsupportedError,
    CaptchaTimeoutError,
    CaptchaFailedError,
    APIKeyError,
    NoBalanceError,
    NetworkError,
)

_SUPPORTED_TYPES = [
    "recaptcha_v2", "recaptcha_v3", "hcaptcha",
    "turnstile", "funcaptcha", "geetest", "image",
]
_BASE_URL = "https://2captcha.com"
_DEFAULT_COST = 0.002
_POLL_INTERVAL = 5


class TwoCaptchaProvider(CaptchaProvider):
    """Провайдер 2Captcha — поддерживает 35+ типов капч.

    Документация: https://2captcha.com/api-docs
    """

    def __init__(self, api_key: str | None, learned_costs: dict | None = None) -> None:
        """Args:
            api_key: API-ключ 2Captcha.
            learned_costs: словарь выученных цен {provider: {type: cost}}.
        """
        self._api_key = api_key
        self._learned_costs = learned_costs or {}

    @property
    def name(self) -> str:
        return "2captcha"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supports_balance_check(self) -> bool:
        return True

    def supports_type(self, captcha_type: str) -> bool:
        return captcha_type in _SUPPORTED_TYPES

    def get_cost_estimate(self, captcha_type: str) -> float:
        return self._learned_costs.get("2captcha", {}).get(captcha_type, _DEFAULT_COST)

    async def get_balance(self) -> float | None:
        """Запрашивает баланс аккаунта 2Captcha."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{_BASE_URL}/res.php",
                    params={"key": self._api_key, "action": "getbalance", "json": 1},
                ) as resp:
                    data = await resp.json()
                    if data.get("status") == 1:
                        return float(data["request"])
                    if "ERROR_WRONG_USER_KEY" in str(data.get("request", "")):
                        raise APIKeyError("Неверный API-ключ 2Captcha")
                    logger.warning(f"2Captcha get_balance error: {data}")
                    return None
        except APIKeyError:
            raise
        except aiohttp.ClientError as e:
            raise NetworkError(f"2Captcha network error: {e}") from e

    async def solve(
        self,
        captcha_type: str,
        site_key: str | None,
        page_url: str,
        **kwargs,
    ) -> CaptchaResult:
        """Решает капчу через 2Captcha API.

        Args:
            captcha_type: тип капчи.
            site_key: sitekey с сайта.
            page_url: URL страницы с капчей.
            **kwargs: action (для v3), min_score и др.

        Returns:
            CaptchaResult с токеном.
        """
        if not self.supports_type(captcha_type):
            raise CaptchaUnsupportedError(f"2Captcha не поддерживает {captcha_type}")
        if not self._api_key:
            raise APIKeyError("API-ключ 2Captcha не задан")

        start_time = time.monotonic()
        task_id = await self._submit_task(captcha_type, site_key, page_url, **kwargs)
        token = await self._wait_for_result(task_id)
        solve_time = time.monotonic() - start_time

        return CaptchaResult(
            token=token,
            score=kwargs.get("min_score"),
            provider=self.name,
            cost=self.get_cost_estimate(captcha_type),
            solve_time=solve_time,
            captcha_type=captcha_type,
        )

    async def _submit_task(
        self,
        captcha_type: str,
        site_key: str | None,
        page_url: str,
        **kwargs,
    ) -> str:
        """Отправляет задачу на решение."""
        params: dict = {"key": self._api_key, "json": 1, "pageurl": page_url}

        if captcha_type == "recaptcha_v2":
            params.update({"method": "userrecaptcha", "googlekey": site_key})
        elif captcha_type == "recaptcha_v3":
            params.update({
                "method": "userrecaptcha",
                "googlekey": site_key,
                "version": "v3",
                "action": kwargs.get("action", "verify"),
                "min_score": kwargs.get("min_score", 0.5),
            })
        elif captcha_type == "hcaptcha":
            params.update({"method": "hcaptcha", "sitekey": site_key})
        elif captcha_type == "turnstile":
            params.update({"method": "turnstile", "sitekey": site_key})
        elif captcha_type == "image":
            params.update({"method": "base64", "body": kwargs.get("image_base64", "")})
        else:
            params.update({"method": captcha_type, "sitekey": site_key})

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{_BASE_URL}/in.php", data=params) as resp:
                    data = await resp.json()
                    if data.get("status") != 1:
                        error = str(data.get("request", ""))
                        if "ERROR_ZERO_BALANCE" in error:
                            raise NoBalanceError("Нет средств на балансе 2Captcha")
                        if "ERROR_WRONG_USER_KEY" in error:
                            raise APIKeyError("Неверный API-ключ 2Captcha")
                        raise CaptchaFailedError(f"2Captcha submit error: {error}")
                    return str(data["request"])
        except (APIKeyError, NoBalanceError, CaptchaFailedError):
            raise
        except aiohttp.ClientError as e:
            raise NetworkError(f"2Captcha network error при отправке: {e}") from e

    async def _wait_for_result(self, task_id: str, timeout: int = 120) -> str:
        """Ожидает результата решения."""
        params = {
            "key": self._api_key,
            "action": "get",
            "id": task_id,
            "json": 1,
        }
        deadline = time.monotonic() + timeout
        await asyncio.sleep(10)  # 2Captcha рекомендует подождать перед первым запросом

        while time.monotonic() < deadline:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{_BASE_URL}/res.php", params=params) as resp:
                        data = await resp.json()
                        if data.get("status") == 1:
                            return str(data["request"])
                        if str(data.get("request")) == "CAPCHA_NOT_READY":
                            await asyncio.sleep(_POLL_INTERVAL)
                            continue
                        raise CaptchaFailedError(f"2Captcha result error: {data.get('request')}")
            except (CaptchaFailedError, NetworkError):
                raise
            except aiohttp.ClientError as e:
                raise NetworkError(f"2Captcha network error при получении: {e}") from e

        raise CaptchaTimeoutError(f"2Captcha таймаут для задачи {task_id}")

    async def report_bad(self, task_id: str) -> bool:
        """Сообщает о неверном решении."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{_BASE_URL}/res.php",
                    params={
                        "key": self._api_key,
                        "action": "reportbad",
                        "id": task_id,
                        "json": 1,
                    },
                ) as resp:
                    data = await resp.json()
                    return data.get("status") == 1
        except aiohttp.ClientError as e:
            logger.warning(f"2Captcha report_bad error: {e}")
            return False
