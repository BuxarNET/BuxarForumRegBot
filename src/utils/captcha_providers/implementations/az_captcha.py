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

_SUPPORTED_TYPES = ["recaptcha_v2", "hcaptcha", "image"]
_BASE_URL = "http://azcaptcha.com"
_DEFAULT_COST = 0.0005
_POLL_INTERVAL = 5


class AZCaptchaProvider(CaptchaProvider):
    """Провайдер AZCaptcha — бюджетная альтернатива 2Captcha.

    API совместим с 2Captcha.
    Документация: https://azcaptcha.com/api
    """

    def __init__(self, api_key: str | None, learned_costs: dict | None = None) -> None:
        self._api_key = api_key
        self._learned_costs = learned_costs or {}

    @property
    def name(self) -> str:
        return "azcaptcha"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supports_balance_check(self) -> bool:
        return True

    def supports_type(self, captcha_type: str) -> bool:
        return captcha_type in _SUPPORTED_TYPES

    def get_cost_estimate(self, captcha_type: str) -> float:
        return self._learned_costs.get("azcaptcha", {}).get(captcha_type, _DEFAULT_COST)

    async def get_balance(self) -> float | None:
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
                        raise APIKeyError("Неверный API-ключ AZCaptcha")
                    return None
        except APIKeyError:
            raise
        except aiohttp.ClientError as e:
            raise NetworkError(f"AZCaptcha network error: {e}") from e

    async def solve(
        self,
        captcha_type: str,
        site_key: str | None,
        page_url: str,
        **kwargs,
    ) -> CaptchaResult:
        if not self.supports_type(captcha_type):
            raise CaptchaUnsupportedError(f"AZCaptcha не поддерживает {captcha_type}")
        if not self._api_key:
            raise APIKeyError("API-ключ AZCaptcha не задан")

        start_time = time.monotonic()
        params: dict = {"key": self._api_key, "json": 1, "pageurl": page_url}

        if captcha_type == "recaptcha_v2":
            params.update({"method": "userrecaptcha", "googlekey": site_key})
        elif captcha_type == "hcaptcha":
            params.update({"method": "hcaptcha", "sitekey": site_key})
        elif captcha_type == "image":
            params.update({"method": "base64", "body": kwargs.get("image_base64", "")})

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{_BASE_URL}/in.php", data=params) as resp:
                    data = await resp.json()
                    if data.get("status") != 1:
                        error = str(data.get("request", ""))
                        if "ERROR_ZERO_BALANCE" in error:
                            raise NoBalanceError("Нет средств на балансе AZCaptcha")
                        raise CaptchaFailedError(f"AZCaptcha submit error: {error}")
                    task_id = str(data["request"])
        except (APIKeyError, NoBalanceError, CaptchaFailedError):
            raise
        except aiohttp.ClientError as e:
            raise NetworkError(f"AZCaptcha network error: {e}") from e

        token = await self._wait_for_result(task_id)
        return CaptchaResult(
            token=token,
            score=None,
            provider=self.name,
            cost=self.get_cost_estimate(captcha_type),
            solve_time=time.monotonic() - start_time,
            captcha_type=captcha_type,
        )

    async def _wait_for_result(self, task_id: str, timeout: int = 120) -> str:
        params = {"key": self._api_key, "action": "get", "id": task_id, "json": 1}
        deadline = time.monotonic() + timeout
        await asyncio.sleep(10)

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
                        raise CaptchaFailedError(f"AZCaptcha error: {data.get('request')}")
            except (CaptchaFailedError, NetworkError):
                raise
            except aiohttp.ClientError as e:
                raise NetworkError(f"AZCaptcha network error: {e}") from e

        raise CaptchaTimeoutError(f"AZCaptcha таймаут для задачи {task_id}")

    async def report_bad(self, task_id: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{_BASE_URL}/res.php",
                    params={"key": self._api_key, "action": "reportbad", "id": task_id, "json": 1},
                ) as resp:
                    data = await resp.json()
                    return data.get("status") == 1
        except aiohttp.ClientError:
            return False
