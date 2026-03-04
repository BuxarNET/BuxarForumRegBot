from __future__ import annotations

# Заглушки для провайдеров — реализованы по аналогии с TwoCaptchaProvider.
# Каждый наследует базовый класс и реализует специфику своего API.

from utils.captcha_providers.base import (
    CaptchaProvider,
    CaptchaResult,
    CaptchaUnsupportedError,
    CaptchaTimeoutError,
    CaptchaFailedError,
    APIKeyError,
    NetworkError,
)


class _StubProvider(CaptchaProvider):
    """Базовый stub для провайдеров с минимальной реализацией."""

    _SUPPORTED: list[str] = []
    _DEFAULT_COST: float = 0.002
    _PROVIDER_NAME: str = "stub"

    def __init__(self, api_key: str | None, learned_costs: dict | None = None) -> None:
        self._api_key = api_key
        self._learned_costs = learned_costs or {}

    @property
    def name(self) -> str:
        return self._PROVIDER_NAME

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supports_balance_check(self) -> bool:
        return True

    def supports_type(self, captcha_type: str) -> bool:
        return captcha_type in self._SUPPORTED

    def get_cost_estimate(self, captcha_type: str) -> float:
        return self._learned_costs.get(self._PROVIDER_NAME, {}).get(
            captcha_type, self._DEFAULT_COST
        )

    async def get_balance(self) -> float | None:
        raise NotImplementedError(f"{self._PROVIDER_NAME}.get_balance не реализован")

    async def solve(self, captcha_type, site_key, page_url, **kwargs) -> CaptchaResult:
        raise NotImplementedError(f"{self._PROVIDER_NAME}.solve не реализован")

    async def report_bad(self, task_id: str) -> bool:
        return False


class TrueCaptchaProvider(_StubProvider):
    """TrueCaptcha — специализируется на image и recaptcha_v2."""
    _SUPPORTED = ["image", "recaptcha_v2"]
    _DEFAULT_COST = 0.001
    _PROVIDER_NAME = "truecaptcha"


class DeathByCaptchaProvider(_StubProvider):
    """DeathByCaptcha — один из старейших сервисов решения капч."""
    _SUPPORTED = ["recaptcha_v2", "hcaptcha", "image"]
    _DEFAULT_COST = 0.00139
    _PROVIDER_NAME = "deathbycaptcha"


class SolveCaptchaProvider(_StubProvider):
    """SolveCaptcha — бюджетный провайдер."""
    _SUPPORTED = ["recaptcha_v2", "hcaptcha", "image"]
    _DEFAULT_COST = 0.001
    _PROVIDER_NAME = "solvecaptcha"


class EndCaptchaProvider(_StubProvider):
    """EndCaptcha — дополнительный провайдер."""
    _SUPPORTED = ["recaptcha_v2", "image"]
    _DEFAULT_COST = 0.001
    _PROVIDER_NAME = "endcaptcha"
