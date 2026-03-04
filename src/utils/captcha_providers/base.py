from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypedDict


class CaptchaResult(TypedDict):
    """Результат решения капчи."""

    token: str | None
    score: float | None
    provider: str
    cost: float
    solve_time: float
    captcha_type: str


class CaptchaUnsupportedError(Exception):
    """Провайдер не поддерживает данный тип капчи."""


class CaptchaTimeoutError(Exception):
    """Истекло время ожидания решения капчи."""


class CaptchaFailedError(Exception):
    """Провайдер не смог решить капчу."""


class APIKeyError(Exception):
    """Отсутствует или недействителен API-ключ."""


class NoBalanceError(Exception):
    """Недостаточно средств на балансе провайдера."""


class NetworkError(Exception):
    """Ошибка сети при обращении к провайдеру."""


class CaptchaProvider(ABC):
    """Абстрактный базовый класс для провайдеров решения капч.

    Каждый провайдер реализует этот интерфейс. Для добавления
    нового провайдера: создать файл в implementations/, унаследовать
    CaptchaProvider, добавить в registry.py.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Уникальный ID провайдера (как в реестре CAPTCHA_PROVIDERS)."""

    @abstractmethod
    def is_available(self) -> bool:
        """Проверяет наличие API-ключа и готовность провайдера.

        Returns:
            True если провайдер готов к работе.
        """

    @abstractmethod
    def supports_balance_check(self) -> bool:
        """Поддерживает ли провайдер проверку баланса.

        Returns:
            True если провайдер умеет возвращать баланс.
        """

    @abstractmethod
    def supports_type(self, captcha_type: str) -> bool:
        """Проверяет поддержку данного типа капчи.

        Args:
            captcha_type: тип капчи (recaptcha_v2, hcaptcha и т.д.).

        Returns:
            True если провайдер поддерживает этот тип.
        """

    @abstractmethod
    async def get_balance(self) -> float | None:
        """Запрашивает текущий баланс у провайдера.

        Returns:
            Баланс в USD или None если не поддерживается.

        Raises:
            APIKeyError: если ключ недействителен.
            NetworkError: при ошибке сети.
        """

    @abstractmethod
    async def solve(
        self,
        captcha_type: str,
        site_key: str | None,
        page_url: str,
        **kwargs,
    ) -> CaptchaResult:
        """Отправляет задачу и ожидает решения.

        Args:
            captcha_type: тип капчи.
            site_key: ключ сайта (для recaptcha/hcaptcha).
            page_url: URL страницы с капчей.
            **kwargs: дополнительные параметры (action для v3 и т.д.).

        Returns:
            CaptchaResult со токеном и метаданными.

        Raises:
            CaptchaUnsupportedError: тип не поддерживается.
            CaptchaTimeoutError: истекло время ожидания.
            CaptchaFailedError: не удалось решить.
            APIKeyError: проблема с ключом.
            NoBalanceError: недостаточно средств.
            NetworkError: ошибка сети.
        """

    @abstractmethod
    def get_cost_estimate(self, captcha_type: str) -> float:
        """Возвращает известную цену за решение.

        Args:
            captcha_type: тип капчи.

        Returns:
            Цена в USD (из learned_costs или дефолтная).
        """

    @abstractmethod
    async def report_bad(self, task_id: str) -> bool:
        """Сообщает о неверном решении для возврата средств.

        Args:
            task_id: ID задачи у провайдера.

        Returns:
            True если жалоба принята.
        """
