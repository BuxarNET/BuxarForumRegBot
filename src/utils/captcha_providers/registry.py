from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger

from utils.captcha_providers.base import CaptchaProvider


# Конфигурация провайдеров (импортируется из config/settings.py)
from config.settings import CAPTCHA as CAPTCHA
from config.settings import CAPTCHA_PROVIDERS_CONFIG
from config.settings import CAPTCHA_COST_TRACKING


def _load_learned_costs() -> dict:
    """Загружает сохранённые цены из файла.

    Returns:
        Словарь {provider: {captcha_type: cost}} или пустой словарь.
    """
    costs_file = Path(CAPTCHA_COST_TRACKING["LEARNED_COSTS_FILE"])
    if not costs_file.exists():
        return {}
    try:
        return json.loads(costs_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Не удалось загрузить learned costs: {e}")
        return {}


def _get_cost_for_provider(
    provider_id: str,
    captcha_type: str,
    learned_costs: dict,
) -> float:
    """Возвращает цену для пары (провайдер, тип капчи).

    Args:
        provider_id: ID провайдера.
        captcha_type: тип капчи.
        learned_costs: загруженные цены.

    Returns:
        Цена в USD.
    """
    return (
        learned_costs.get(provider_id, {}).get(captcha_type)
        or CAPTCHA_COST_TRACKING["DEFAULT_COST"]
    )


def _create_provider(provider_id: str, api_key: str | None) -> CaptchaProvider | None:
    """Создаёт экземпляр провайдера по его ID.

    Args:
        provider_id: ID провайдера из реестра.
        api_key: API-ключ провайдера.

    Returns:
        Экземпляр CaptchaProvider или None если провайдер неизвестен.
    """
    from utils.captcha_providers.implementations.two_captcha import TwoCaptchaProvider
    from utils.captcha_providers.implementations.az_captcha import AZCaptchaProvider
    from utils.captcha_providers.implementations.cap_solver import CapSolverProvider
    from utils.captcha_providers.implementations.manual import ManualProvider
    from utils.captcha_providers.implementations._stubs import (
        TrueCaptchaProvider,
        DeathByCaptchaProvider,
        SolveCaptchaProvider,
        EndCaptchaProvider,
    )

    mapping = {
        "2captcha": TwoCaptchaProvider,
        "azcaptcha": AZCaptchaProvider,
        "capsolver": CapSolverProvider,
        "truecaptcha": TrueCaptchaProvider,
        "deathbycaptcha": DeathByCaptchaProvider,
        "solvecaptcha": SolveCaptchaProvider,
        "endcaptcha": EndCaptchaProvider,
        "manual": ManualProvider,
    }

    cls = mapping.get(provider_id)
    if cls is None:
        logger.warning(f"Неизвестный провайдер: {provider_id}")
        return None

    learned_costs = _load_learned_costs()
    return cls(api_key=api_key, learned_costs=learned_costs)


def get_provider_chain(captcha_type: str) -> list[CaptchaProvider]:
    """Формирует цепочку провайдеров для данного типа капчи.

    Алгоритм:
        1. Берёт включённых провайдеров из CAPTCHA_PROVIDERS_CONFIG
        2. Проверяет наличие API-ключа
        3. Фильтрует по поддерживаемым типам
        4. Сортирует по приоритету (или цене если AUTO_SORT_BY_COST)
        5. Добавляет manual в конец (если ALLOW_MANUAL_FALLBACK)

    Args:
        captcha_type: тип капчи для фильтрации провайдеров.

    Returns:
        Список готовых к использованию провайдеров.
    """
    learned_costs = _load_learned_costs()
    chain: list[CaptchaProvider] = []
    manual_provider: CaptchaProvider | None = None

    for provider_id, config in CAPTCHA_PROVIDERS_CONFIG.items():
        if not config.get("enabled", False):
            continue

        # Пропускаем manual — добавим в конец отдельно
        if provider_id == "manual":
            if CAPTCHA.get("ALLOW_MANUAL_FALLBACK", True):
                manual_provider = _create_provider("manual", None)
            continue

        # Проверяем наличие API-ключа
        env_key = config.get("env_key")
        api_key = os.getenv(env_key) if env_key else None
        if not api_key:
            logger.debug(f"Провайдер {provider_id} пропущен: нет API-ключа ({env_key})")
            continue

        # Фильтруем по типу капчи
        supported = config.get("supported_types", [])
        if "*" not in supported and captcha_type not in supported:
            logger.debug(f"Провайдер {provider_id} не поддерживает тип {captcha_type}")
            continue

        provider = _create_provider(provider_id, api_key)
        if provider is None:
            continue

        chain.append(provider)

    # Сортировка
    if CAPTCHA.get("AUTO_SORT_BY_COST", False):
        chain.sort(
            key=lambda p: _get_cost_for_provider(p.name, captcha_type, learned_costs)
        )
        logger.debug("Провайдеры отсортированы по цене")
    else:
        chain.sort(
            key=lambda p: CAPTCHA_PROVIDERS_CONFIG.get(p.name, {}).get("priority", 999)
        )
        logger.debug("Провайдеры отсортированы по приоритету")

    # Добавляем manual в конец
    if manual_provider is not None:
        chain.append(manual_provider)

    logger.info(
        f"Цепочка провайдеров для {captcha_type}: "
        f"{[p.name for p in chain]}"
    )
    return chain
