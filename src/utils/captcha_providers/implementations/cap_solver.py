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

_SUPPORTED_TYPES = ["recaptcha_v2", "recaptcha_v3", "hcaptcha", "turnstile", "image"]
_BASE_URL = "https://api.capsolver.com"
_DEFAULT_COST = 0.0008
_POLL_INTERVAL = 3


class CapSolverProvider(CaptchaProvider):
    """Провайдер CapSolver — сервис с бесплатным тестовым периодом.

    Документация: https://docs.capsolver.com
    """

    def __init__(self, api_key: str | None, learned_costs: dict | None = None) -> None:
        self._api_key = api_key
        self._learned_costs = learned_costs or {}

    @property
    def name(self) -> str:
        return "capsolver"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supports_balance_check(self) -> bool:
        return True

    def supports_type(self, captcha_type: str) -> bool:
        return captcha_type in _SUPPORTED_TYPES

    def get_cost_estimate(self, captcha_type: str) -> float:
        return self._learned_costs.get("capsolver", {}).get(captcha_type, _DEFAULT_COST)

    async def get_balance(self) -> float | None:
        """Запрашивает баланс аккаунта CapSolver."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_BASE_URL}/getBalance",
                    json={"clientKey": self._api_key},
                ) as resp:
                    data = await resp.json()
                    if data.get("errorId") == 0:
                        return float(data.get("balance", 0))
                    error = data.get("errorCode", "")
                    if error == "ERROR_KEY_DENIED_ACCESS":
                        raise APIKeyError("Неверный API-ключ CapSolver")
                    logger.warning(f"CapSolver get_balance error: {data}")
                    return None
        except APIKeyError:
            raise
        except aiohttp.ClientError as e:
            raise NetworkError(f"CapSolver network error: {e}") from e

    async def solve(
        self,
        captcha_type: str,
        site_key: str | None,
        page_url: str,
        **kwargs,
    ) -> CaptchaResult:
        """Решает капчу через CapSolver API."""
        if not self.supports_type(captcha_type):
            raise CaptchaUnsupportedError(f"CapSolver не поддерживает {captcha_type}")
        if not self._api_key:
            raise APIKeyError("API-ключ CapSolver не задан")

        start_time = time.monotonic()
        task_id = await self._create_task(captcha_type, site_key, page_url, **kwargs)
        token = await self._get_task_result(task_id)
        solve_time = time.monotonic() - start_time

        return CaptchaResult(
            token=token,
            score=None,
            provider=self.name,
            cost=self.get_cost_estimate(captcha_type),
            solve_time=solve_time,
            captcha_type=captcha_type,
        )

    async def _create_task(
        self,
        captcha_type: str,
        site_key: str | None,
        page_url: str,
        **kwargs,
    ) -> str:
        """Создаёт задачу в CapSolver."""
        task_map = {
            "recaptcha_v2": "ReCaptchaV2Task",
            "recaptcha_v3": "ReCaptchaV3Task",
            "hcaptcha": "HCaptchaTask",
            "turnstile": "AntiTurnstileTask",
            "image": "ImageToTextTask",
        }
        task_type = task_map.get(captcha_type, captcha_type)

        task: dict = {"type": task_type, "websiteURL": page_url}
        if site_key:
            task["websiteKey"] = site_key
        if captcha_type == "recaptcha_v3":
            task["pageAction"] = kwargs.get("action", "verify")
        if captcha_type == "image":
            task["body"] = kwargs.get("image_base64", "")

        payload = {"clientKey": self._api_key, "task": task}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{_BASE_URL}/createTask", json=payload) as resp:
                    data = await resp.json()
                    if data.get("errorId") == 0:
                        return str(data["taskId"])
                    error = data.get("errorCode", "")
                    if "BALANCE" in error:
                        raise NoBalanceError("Нет средств на балансе CapSolver")
                    if "KEY" in error:
                        raise APIKeyError("Неверный API-ключ CapSolver")
                    raise CaptchaFailedError(f"CapSolver createTask error: {error}")
        except (APIKeyError, NoBalanceError, CaptchaFailedError):
            raise
        except aiohttp.ClientError as e:
            raise NetworkError(f"CapSolver network error при создании задачи: {e}") from e

    async def _get_task_result(self, task_id: str, timeout: int = 120) -> str:
        """Ожидает результата задачи."""
        payload = {"clientKey": self._api_key, "taskId": task_id}
        deadline = time.monotonic() + timeout
        await asyncio.sleep(5)

        while time.monotonic() < deadline:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{_BASE_URL}/getTaskResult", json=payload
                    ) as resp:
                        data = await resp.json()
                        if data.get("errorId") != 0:
                            raise CaptchaFailedError(
                                f"CapSolver error: {data.get('errorCode')}"
                            )
                        status = data.get("status")
                        if status == "ready":
                            solution = data.get("solution", {})
                            return str(
                                solution.get("gRecaptchaResponse")
                                or solution.get("token")
                                or solution.get("text", "")
                            )
                        await asyncio.sleep(_POLL_INTERVAL)
            except (CaptchaFailedError, NetworkError):
                raise
            except aiohttp.ClientError as e:
                raise NetworkError(f"CapSolver network error при получении: {e}") from e

        raise CaptchaTimeoutError(f"CapSolver таймаут для задачи {task_id}")

    async def report_bad(self, task_id: str) -> bool:
        """CapSolver не поддерживает reportBad — возвращает False."""
        logger.debug("CapSolver не поддерживает report_bad")
        return False
