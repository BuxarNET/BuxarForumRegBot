from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# =============================================================================
# Фикстуры
# =============================================================================

@pytest.fixture
def mock_page():
    """Мок объекта страницы Pydoll."""
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    page.get_url = AsyncMock(return_value="https://example.com/register")
    return page


@pytest.fixture
def captcha_helper(mock_page):
    from utils.captcha_helper import CaptchaExtensionHelper
    return CaptchaExtensionHelper(page=mock_page)


@pytest.fixture
def mock_provider():
    """Мок провайдера капч."""
    provider = MagicMock()
    provider.name = "mock_provider"
    provider.is_available.return_value = True
    provider.supports_balance_check.return_value = True
    provider.supports_type.return_value = True
    provider.get_cost_estimate.return_value = 0.002
    provider.get_balance = AsyncMock(return_value=10.0)
    provider.solve = AsyncMock(return_value={
        "token": "mock_token_abc123",
        "score": None,
        "provider": "mock_provider",
        "cost": 0.002,
        "solve_time": 5.0,
        "captcha_type": "recaptcha_v2",
    })
    provider.report_bad = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def mock_provider_chain(mock_provider):
    """Патч get_provider_chain возвращающий одного мок-провайдера."""
    return [mock_provider]


# =============================================================================
# Тесты CaptchaExtensionHelper.solve_captcha
# =============================================================================

@pytest.mark.asyncio
async def test_solve_captcha_success(captcha_helper, mock_provider):
    """solve_captcha возвращает токен при успешном решении."""
    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[mock_provider],
    ):
        token = await captcha_helper.solve_captcha(
            "recaptcha_v2", "site_key_123", "https://example.com"
        )

    assert token == "mock_token_abc123"


@pytest.mark.asyncio
async def test_solve_captcha_injects_token(captcha_helper, mock_page, mock_provider):
    """solve_captcha внедряет токен в DOM после успешного решения."""
    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[mock_provider],
    ):
        await captcha_helper.solve_captcha(
            "recaptcha_v2", "site_key_123", "https://example.com"
        )

    mock_page.evaluate.assert_called()
    js_call = mock_page.evaluate.call_args[0][0]
    assert "mock_token_abc123" in js_call


@pytest.mark.asyncio
async def test_solve_captcha_empty_chain_returns_none(captcha_helper):
    """Пустая цепочка провайдеров → None."""
    with patch("utils.captcha_helper.get_provider_chain", return_value=[]):
        token = await captcha_helper.solve_captcha(
            "recaptcha_v2", "key", "https://example.com"
        )
    assert token is None


@pytest.mark.asyncio
async def test_solve_captcha_fallback_to_second_provider(captcha_helper):
    """При неудаче первого провайдера — переходит ко второму."""
    bad_provider = MagicMock()
    bad_provider.name = "bad_provider"
    bad_provider.supports_balance_check.return_value = False
    bad_provider.get_balance = AsyncMock(return_value=None)
    bad_provider.solve = AsyncMock(side_effect=Exception("solve failed"))

    good_provider = MagicMock()
    good_provider.name = "good_provider"
    good_provider.supports_balance_check.return_value = False
    good_provider.get_balance = AsyncMock(return_value=None)
    good_provider.solve = AsyncMock(return_value={
        "token": "fallback_token",
        "score": None,
        "provider": "good_provider",
        "cost": 0.002,
        "solve_time": 3.0,
        "captcha_type": "recaptcha_v2",
    })

    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[bad_provider, good_provider],
    ):
        token = await captcha_helper.solve_captcha(
            "recaptcha_v2", "key", "https://example.com"
        )

    assert token == "fallback_token"
    bad_provider.solve.assert_called_once()
    good_provider.solve.assert_called_once()


@pytest.mark.asyncio
async def test_solve_captcha_all_fail_returns_none(captcha_helper):
    """Все провайдеры провалились → None."""
    bad1 = MagicMock()
    bad1.name = "bad1"
    bad1.supports_balance_check.return_value = False
    bad1.get_balance = AsyncMock(return_value=None)
    bad1.solve = AsyncMock(side_effect=Exception("failed"))

    bad2 = MagicMock()
    bad2.name = "bad2"
    bad2.supports_balance_check.return_value = False
    bad2.get_balance = AsyncMock(return_value=None)
    bad2.solve = AsyncMock(side_effect=Exception("failed"))

    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[bad1, bad2],
    ):
        token = await captcha_helper.solve_captcha(
            "recaptcha_v2", "key", "https://example.com"
        )

    assert token is None


@pytest.mark.asyncio
async def test_solve_captcha_api_key_error_skips_provider(captcha_helper):
    """APIKeyError — провайдер пропускается, переходим к следующему."""
    from utils.captcha_providers.base import APIKeyError

    bad = MagicMock()
    bad.name = "bad_key"
    bad.supports_balance_check.return_value = False
    bad.get_balance = AsyncMock(return_value=None)
    bad.solve = AsyncMock(side_effect=APIKeyError("bad key"))

    good = MagicMock()
    good.name = "good"
    good.supports_balance_check.return_value = False
    good.get_balance = AsyncMock(return_value=None)
    good.solve = AsyncMock(return_value={
        "token": "good_token",
        "score": None,
        "provider": "good",
        "cost": 0.001,
        "solve_time": 2.0,
        "captcha_type": "recaptcha_v2",
    })

    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[bad, good],
    ):
        token = await captcha_helper.solve_captcha(
            "recaptcha_v2", "key", "https://example.com"
        )

    assert token == "good_token"


@pytest.mark.asyncio
async def test_solve_captcha_calls_stats_callback(mock_page, mock_provider):
    """stats_callback вызывается при успешном решении."""
    from utils.captcha_helper import CaptchaExtensionHelper

    stats = []
    helper = CaptchaExtensionHelper(page=mock_page, stats_callback=stats.append)

    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[mock_provider],
    ):
        await helper.solve_captcha("recaptcha_v2", "key", "https://example.com")

    assert len(stats) == 1
    assert stats[0]["success"] is True
    assert stats[0]["provider"] == "mock_provider"


@pytest.mark.asyncio
async def test_solve_captcha_tracks_used_providers(captcha_helper, mock_provider):
    """После решения провайдер добавляется в used_providers."""
    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[mock_provider],
    ):
        await captcha_helper.solve_captcha("recaptcha_v2", "key", "https://example.com")

    assert "mock_provider" in captcha_helper.used_providers


@pytest.mark.asyncio
async def test_solve_captcha_snapshots_balance_on_first_use(captcha_helper, mock_provider):
    """При первом использовании провайдера сохраняется его баланс."""
    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[mock_provider],
    ):
        await captcha_helper.solve_captcha("recaptcha_v2", "key", "https://example.com")

    assert "mock_provider" in captcha_helper.balance_snapshots
    assert captcha_helper.balance_snapshots["mock_provider"] == 10.0


@pytest.mark.asyncio
async def test_solve_captcha_increments_solve_count(captcha_helper, mock_provider):
    """Счётчик solve_counts увеличивается с каждым решением."""
    with patch(
        "utils.captcha_helper.get_provider_chain",
        return_value=[mock_provider],
    ):
        await captcha_helper.solve_captcha("recaptcha_v2", "key", "https://example.com")
        await captcha_helper.solve_captcha("recaptcha_v2", "key", "https://example.com")

    assert captcha_helper.solve_counts[("mock_provider", "recaptcha_v2")] == 2


# =============================================================================
# Тесты _inject_token
# =============================================================================

@pytest.mark.asyncio
async def test_inject_token_recaptcha_v2(captcha_helper, mock_page):
    """Токен reCAPTCHA v2 внедряется в g-recaptcha-response."""
    await captcha_helper._inject_token("test_token_123", "recaptcha_v2")

    mock_page.evaluate.assert_called_once()
    js = mock_page.evaluate.call_args[0][0]
    assert "test_token_123" in js
    assert "g-recaptcha-response" in js


@pytest.mark.asyncio
async def test_inject_token_hcaptcha(captcha_helper, mock_page):
    """Токен hCaptcha внедряется в h-captcha-response."""
    await captcha_helper._inject_token("hcaptcha_token", "hcaptcha")

    js = mock_page.evaluate.call_args[0][0]
    assert "hcaptcha_token" in js
    assert "h-captcha-response" in js


@pytest.mark.asyncio
async def test_inject_token_unknown_type(captcha_helper, mock_page):
    """Неизвестный тип капчи — evaluate не вызывается."""
    await captcha_helper._inject_token("token", "unknown_type")
    mock_page.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_inject_token_page_error_doesnt_raise(captcha_helper, mock_page):
    """Ошибка evaluate не пробрасывается наружу."""
    mock_page.evaluate.side_effect = Exception("page error")
    # Не должно выбросить исключение
    await captcha_helper._inject_token("token", "recaptcha_v2")


# =============================================================================
# Тесты расчёта стоимости
# =============================================================================

@pytest.mark.asyncio
async def test_recalculate_cost_saves_learned_price(mock_page, tmp_path):
    """_recalculate_cost сохраняет цену при достаточном числе решений."""
    from utils.captcha_helper import CaptchaExtensionHelper
    import utils.captcha_helper as ch_module

    costs_file = tmp_path / "learned_costs.json"

    with patch.dict("utils.captcha_providers.registry.CAPTCHA_COST_TRACKING", {
        "ENABLED": True,
        "LEARNED_COSTS_FILE": str(costs_file),
        "MIN_SOLVES_FOR_CALCULATION": 3,
        "DEFAULT_COST": 0.002,
        "OUTLIER_THRESHOLD": 5.0,
        "LOG_LEARNED_PRICES": False,
    }):
        helper = CaptchaExtensionHelper(page=mock_page)
        helper.balance_snapshots["test_provider"] = 10.0
        helper.solve_counts[("test_provider", "recaptcha_v2")] = 5

        provider = MagicMock()
        provider.name = "test_provider"
        provider.supports_balance_check.return_value = True
        provider.get_balance = AsyncMock(return_value=9.99)  # потрачено $0.01

        await helper._recalculate_cost(provider, "recaptcha_v2")

    assert costs_file.exists()
    data = json.loads(costs_file.read_text())
    assert "test_provider" in data
    assert abs(data["test_provider"]["recaptcha_v2"] - 0.002) < 0.001  # $0.01 / 5


@pytest.mark.asyncio
async def test_recalculate_cost_ignores_outlier(mock_page, tmp_path):
    """Выброс в цене игнорируется и не сохраняется."""
    from utils.captcha_helper import CaptchaExtensionHelper
    import utils.captcha_helper as ch_module

    costs_file = tmp_path / "learned_costs.json"

    with patch.dict("utils.captcha_providers.registry.CAPTCHA_COST_TRACKING", {
        "ENABLED": True,
        "LEARNED_COSTS_FILE": str(costs_file),
        "MIN_SOLVES_FOR_CALCULATION": 3,
        "DEFAULT_COST": 0.002,
        "OUTLIER_THRESHOLD": 5.0,
        "LOG_LEARNED_PRICES": False,
    }):
        helper = CaptchaExtensionHelper(page=mock_page)
        helper.balance_snapshots["test_provider"] = 10.0
        helper.solve_counts[("test_provider", "recaptcha_v2")] = 3

        provider = MagicMock()
        provider.name = "test_provider"
        provider.supports_balance_check.return_value = True
        # $9 потрачено за 3 решения = $3 за штуку (выброс при threshold 5.0 * 0.002 = 0.01)
        provider.get_balance = AsyncMock(return_value=1.0)

        await helper._recalculate_cost(provider, "recaptcha_v2")

    # Файл не создан или пустой — выброс проигнорирован
    if costs_file.exists():
        data = json.loads(costs_file.read_text())
        assert "test_provider" not in data


@pytest.mark.asyncio
async def test_recalculate_cost_skips_if_not_enough_solves(mock_page, tmp_path):
    """Пересчёт не происходит если решений меньше минимума."""
    from utils.captcha_helper import CaptchaExtensionHelper
    import utils.captcha_helper as ch_module

    costs_file = tmp_path / "learned_costs.json"

    with patch.dict("utils.captcha_providers.registry.CAPTCHA_COST_TRACKING", {
        "ENABLED": True,
        "LEARNED_COSTS_FILE": str(costs_file),
        "MIN_SOLVES_FOR_CALCULATION": 3,
        "DEFAULT_COST": 0.002,
        "OUTLIER_THRESHOLD": 5.0,
        "LOG_LEARNED_PRICES": False,
    }):
        helper = CaptchaExtensionHelper(page=mock_page)
        helper.balance_snapshots["test_provider"] = 10.0
        helper.solve_counts[("test_provider", "recaptcha_v2")] = 2  # меньше 3

        provider = MagicMock()
        provider.name = "test_provider"
        provider.supports_balance_check.return_value = True
        provider.get_balance = AsyncMock(return_value=9.99)

        await helper._recalculate_cost(provider, "recaptcha_v2")

    assert not costs_file.exists()


# =============================================================================
# Тесты ManualProvider
# =============================================================================

@pytest.mark.asyncio
async def test_manual_provider_polls_dom(mock_page):
    """ManualProvider обнаруживает токен через DOM polling."""
    from utils.captcha_providers.implementations.manual import ManualProvider

    # Сначала пусто, потом появляется токен
    mock_page.evaluate = AsyncMock(side_effect=[None, None, "manual_token_xyz"])

    provider = ManualProvider()
    provider.set_page(mock_page)

    result = await provider.solve("recaptcha_v2", None, "https://example.com", timeout=30)

    assert result["token"] == "manual_token_xyz"
    assert result["provider"] == "manual"
    assert result["cost"] == 0.0


@pytest.mark.asyncio
async def test_manual_provider_timeout():
    """ManualProvider выбрасывает CaptchaTimeoutError при таймауте."""
    from utils.captcha_providers.implementations.manual import ManualProvider
    from utils.captcha_providers.base import CaptchaTimeoutError

    provider = ManualProvider()
    # Без страницы — polling недоступен, быстрый таймаут

    with pytest.raises(CaptchaTimeoutError):
        await provider.solve("recaptcha_v2", None, "https://example.com", timeout=3)


@pytest.mark.asyncio
async def test_manual_provider_is_always_available():
    """ManualProvider всегда доступен без API-ключа."""
    from utils.captcha_providers.implementations.manual import ManualProvider

    provider = ManualProvider()
    assert provider.is_available() is True
    assert provider.supports_balance_check() is False
    assert provider.get_cost_estimate("recaptcha_v2") == 0.0
    assert provider.supports_type("any_type") is True


# =============================================================================
# Тесты провайдеров (unit с моком aiohttp)
# =============================================================================

@pytest.mark.asyncio
async def test_two_captcha_get_balance_success():
    """TwoCaptchaProvider.get_balance возвращает баланс."""
    from utils.captcha_providers.implementations.two_captcha import TwoCaptchaProvider

    provider = TwoCaptchaProvider(api_key="test_key")

    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"status": 1, "request": "5.50"})

    with patch("utils.captcha_providers.implementations.two_captcha.aiohttp.ClientSession") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.get.return_value = mock_cm
    
        balance = await provider.get_balance()

    assert balance == 5.50


@pytest.mark.asyncio
async def test_two_captcha_get_balance_wrong_key():
    """TwoCaptchaProvider.get_balance выбрасывает APIKeyError при неверном ключе."""
    from utils.captcha_providers.implementations.two_captcha import TwoCaptchaProvider
    from utils.captcha_providers.base import APIKeyError

    provider = TwoCaptchaProvider(api_key="wrong_key")

    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={
        "status": 0,
        "request": "ERROR_WRONG_USER_KEY"
    })

    with patch("utils.captcha_providers.implementations.two_captcha.aiohttp.ClientSession") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.get.return_value = mock_cm

        with pytest.raises(APIKeyError):
            await provider.get_balance()


@pytest.mark.asyncio
async def test_capsolver_get_balance_success():
    """CapSolverProvider.get_balance возвращает баланс."""
    from utils.captcha_providers.implementations.cap_solver import CapSolverProvider

    provider = CapSolverProvider(api_key="test_key")

    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"errorId": 0, "balance": 3.75})

    with patch("utils.captcha_providers.implementations.cap_solver.aiohttp.ClientSession") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.post.return_value = mock_cm

        balance = await provider.get_balance()

    assert balance == 3.75


def test_provider_supports_type():
    """Провайдеры корректно фильтруют поддерживаемые типы."""
    from utils.captcha_providers.implementations.two_captcha import TwoCaptchaProvider
    from utils.captcha_providers.implementations.az_captcha import AZCaptchaProvider

    two = TwoCaptchaProvider(api_key="key")
    az = AZCaptchaProvider(api_key="key")

    assert two.supports_type("recaptcha_v2") is True
    assert two.supports_type("geetest") is True
    assert two.supports_type("unknown") is False

    assert az.supports_type("recaptcha_v2") is True
    assert az.supports_type("geetest") is False  # AZCaptcha не поддерживает


def test_provider_cost_estimate_uses_learned():
    """get_cost_estimate использует выученную цену если есть."""
    from utils.captcha_providers.implementations.two_captcha import TwoCaptchaProvider

    learned = {"2captcha": {"recaptcha_v2": 0.0012}}
    provider = TwoCaptchaProvider(api_key="key", learned_costs=learned)

    assert provider.get_cost_estimate("recaptcha_v2") == 0.0012
    assert provider.get_cost_estimate("hcaptcha") == 0.002  # дефолт
