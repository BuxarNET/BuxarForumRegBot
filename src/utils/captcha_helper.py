from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from loguru import logger

from utils.captcha_providers.base import (
    CaptchaProvider,
    APIKeyError,
    NoBalanceError,
)
from utils.captcha_providers.registry import (
    get_provider_chain,
    CAPTCHA_COST_TRACKING,
)
from config.settings import CAPTCHA as CAPTCHA_SETTINGS


class CaptchaExtensionHelper:
    """Фасад для решения капч через цепочку провайдеров.

    Единая точка входа для всех типов капч. Автоматически выбирает
    провайдера по приоритету (или цене), переключается на следующего
    при неудаче (fallback). Считает стоимость решений на основе
    изменения баланса провайдеров.

    Использование:
        helper = CaptchaExtensionHelper(page=page)
        token = await helper.solve_captcha("recaptcha_v2", site_key, page_url)
        await helper.finalize()
    """

    def __init__(
        self,
        page=None,
        stats_callback: Callable[[dict], None] | None = None,
    ) -> None:
        """Args:
            page: объект страницы Pydoll для инъекции токена.
            stats_callback: колбэк для передачи статистики в AccountManager.
                           Сигнатура: callback(stats: dict) -> None
        """
        self._page = page
        self._stats_callback = stats_callback

        # Трекинг использования провайдеров
        self.used_providers: set[str] = set()
        self.balance_snapshots: dict[str, float] = {}
        self.solve_counts: dict[tuple[str, str], int] = {}
        self.last_captcha_type: dict[str, str] = {}

        # Загружаем сохранённые цены
        self._learned_costs: dict = self._load_learned_costs()

    # =========================================================================
    # Публичные методы
    # =========================================================================

    async def solve_captcha(
        self,
        captcha_type: str,
        site_key: str | None,
        page_url: str,
        **kwargs,
    ) -> str | None:
        """Решает капчу через цепочку провайдеров с fallback.

        Алгоритм:
            1. Получает цепочку провайдеров для данного типа
            2. Для каждого провайдера:
               - При первом использовании сохраняет баланс
               - Пытается решить капчу
               - При успехе — внедряет токен и возвращает его
               - При ошибке — переходит к следующему провайдеру
            3. Если все провайдеры исчерпаны — возвращает None

        Args:
            captcha_type: тип капчи (recaptcha_v2, hcaptcha и т.д.).
            site_key: sitekey с сайта (для recaptcha/hcaptcha).
            page_url: URL страницы с капчей.
            **kwargs: дополнительные параметры (action для v3 и т.д.).

        Returns:
            Токен капчи или None если решить не удалось.
        """
        chain = get_provider_chain(captcha_type)

        if not chain:
            logger.critical(f"Нет доступных провайдеров для {captcha_type}")
            return None

        for provider in chain:
            provider_name = provider.name

            # При первом использовании провайдера — сохраняем баланс
            if provider_name not in self.used_providers:
                await self._snapshot_balance(provider)
                self.used_providers.add(provider_name)

            # Проверяем смену типа капчи — пересчитываем стоимость
            prev_type = self.last_captcha_type.get(provider_name)
            if prev_type and prev_type != captcha_type:
                await self._recalculate_cost(provider, prev_type)

            # Устанавливаем страницу для manual провайдера
            if hasattr(provider, "set_page") and self._page is not None:
                provider.set_page(self._page)

            try:
                logger.info(f"Попытка решить {captcha_type} через {provider_name}")
                result = await provider.solve(captcha_type, site_key, page_url, **kwargs)

                if not result.get("token"):
                    logger.warning(f"{provider_name}: пустой токен, пробуем следующего")
                    continue

                # Обновляем счётчики
                key = (provider_name, captcha_type)
                self.solve_counts[key] = self.solve_counts.get(key, 0) + 1
                self.last_captcha_type[provider_name] = captcha_type

                token = result["token"]

                # Внедряем токен в страницу
                if self._page is not None:
                    await self._inject_token(token, captcha_type)

                # Передаём статистику в AccountManager
                if self._stats_callback:
                    self._stats_callback({
                        "provider": provider_name,
                        "captcha_type": captcha_type,
                        "success": True,
                        "cost": result.get("cost", 0),
                        "solve_time": result.get("solve_time", 0),
                    })

                logger.info(
                    f"Капча решена: {provider_name} / {captcha_type} "
                    f"за {result.get('solve_time', 0):.1f}s"
                )
                return token

            except (APIKeyError, NoBalanceError) as e:
                logger.error(f"{provider_name} исключён из цепочки: {e}")
                continue
            except Exception as e:
                logger.warning(f"{provider_name} не смог решить {captcha_type}: {type(e).__name__}: {e}")
                if self._stats_callback:
                    self._stats_callback({
                        "provider": provider_name,
                        "captcha_type": captcha_type,
                        "success": False,
                        "cost": 0,
                        "solve_time": 0,
                    })
                continue

        logger.critical(f"Все провайдеры исчерпаны для {captcha_type}")
        return None

    async def finalize(self) -> None:
        """Завершает работу — пересчитывает стоимость и сохраняет цены.

        Вызывается MainOrchestrator при завершении всех регистраций.
        """
        if not CAPTCHA_COST_TRACKING.get("ENABLED", True):
            return

        chain = get_provider_chain("recaptcha_v2")  # для получения экземпляров
        provider_map = {p.name: p for p in chain}

        for provider_name in self.used_providers:
            provider = provider_map.get(provider_name)
            if provider is None or not provider.supports_balance_check():
                continue

            last_type = self.last_captcha_type.get(provider_name)
            if last_type:
                await self._recalculate_cost(provider, last_type)

        logger.info("CaptchaExtensionHelper finalized, цены сохранены")

    # =========================================================================
    # Внутренние методы
    # =========================================================================

    async def _snapshot_balance(self, provider: CaptchaProvider) -> None:
        """Запрашивает и сохраняет баланс провайдера."""
        if not provider.supports_balance_check():
            return
        try:
            balance = await provider.get_balance()
            if balance is not None:
                self.balance_snapshots[provider.name] = balance
                logger.debug(f"Баланс {provider.name}: ${balance:.4f}")
        except Exception as e:
            logger.warning(f"Не удалось получить баланс {provider.name}: {e}")

    async def _recalculate_cost(
        self,
        provider: CaptchaProvider,
        captcha_type: str,
    ) -> None:
        """Пересчитывает и сохраняет стоимость для пары (провайдер, тип).

        Args:
            provider: провайдер для которого пересчитывается цена.
            captcha_type: тип капчи.
        """
        if not CAPTCHA_COST_TRACKING.get("ENABLED", True):
            return
        if not provider.supports_balance_check():
            return

        provider_name = provider.name
        key = (provider_name, captcha_type)
        count = self.solve_counts.get(key, 0)
        min_solves = CAPTCHA_COST_TRACKING.get("MIN_SOLVES_FOR_CALCULATION", 3)

        if count < min_solves:
            logger.debug(
                f"Недостаточно решений для расчёта цены "
                f"{provider_name}/{captcha_type}: {count}/{min_solves}"
            )
            return

        prev_balance = self.balance_snapshots.get(provider_name)
        if prev_balance is None:
            return

        try:
            current_balance = await provider.get_balance()
        except Exception as e:
            logger.warning(f"Не удалось получить баланс для расчёта: {e}")
            return

        if current_balance is None:
            return

        spent = prev_balance - current_balance
        if spent <= 0:
            logger.debug(f"Потрачено $0 для {provider_name}/{captcha_type}, пропускаем")
            return

        cost_per_solve = spent / count
        default_cost = CAPTCHA_COST_TRACKING.get("DEFAULT_COST", 0.002)
        outlier_threshold = CAPTCHA_COST_TRACKING.get("OUTLIER_THRESHOLD", 5.0)

        # Проверка на выброс
        if cost_per_solve > default_cost * outlier_threshold:
            logger.warning(
                f"Выброс в цене {provider_name}/{captcha_type}: "
                f"${cost_per_solve:.4f} > threshold, игнорируем"
            )
            return

        # Сохраняем в learned_costs
        if provider_name not in self._learned_costs:
            self._learned_costs[provider_name] = {}
        self._learned_costs[provider_name][captcha_type] = cost_per_solve

        self._save_learned_costs()

        if CAPTCHA_COST_TRACKING.get("LOG_LEARNED_PRICES", True):
            logger.info(
                f"💡 Learned: {provider_name}/{captcha_type} = ${cost_per_solve:.4f} "
                f"(на основе {count} решений)"
            )

        # Обновляем snapshot для следующего расчёта
        self.balance_snapshots[provider_name] = current_balance
        self.solve_counts[key] = 0

    async def _inject_token(self, token: str, captcha_type: str) -> None:
        """Внедряет токен капчи в DOM страницы через JavaScript.

        Args:
            token: токен полученный от провайдера.
            captcha_type: тип капчи для выбора нужного JS-сниппета.
        """
        js_snippets = {
            "recaptcha_v2": f"""
                (function() {{
                    const el = document.getElementById('g-recaptcha-response');
                    if (el) {{
                        el.value = '{token}';
                        el.style.display = 'block';
                    }}
                    if (typeof ___grecaptcha_cfg !== 'undefined') {{
                        Object.entries(___grecaptcha_cfg.clients).forEach(([k, v]) => {{
                            const cb = v?.callback || v?.['']?.callback;
                            if (typeof cb === 'function') cb('{token}');
                        }});
                    }}
                }})();
            """,
            "recaptcha_v3": f"""
                (function() {{
                    const el = document.getElementById('g-recaptcha-response');
                    if (el) el.value = '{token}';
                }})();
            """,
            "hcaptcha": f"""
                (function() {{
                    const el = document.getElementById('h-captcha-response');
                    if (el) el.value = '{token}';
                    if (typeof hcaptcha !== 'undefined') {{
                        hcaptcha.execute();
                    }}
                }})();
            """,
            "turnstile": f"""
                (function() {{
                    const el = document.querySelector('[name="cf-turnstile-response"]');
                    if (el) el.value = '{token}';
                    if (typeof turnstile !== 'undefined') {{
                        turnstile.reset();
                    }}
                }})();
            """,
        }

        js = js_snippets.get(captcha_type)
        if js is None:
            logger.debug(f"Нет JS-сниппета для инъекции токена типа {captcha_type}")
            return

        try:
            await self._page.evaluate(js)
            logger.debug(f"Токен внедрён в страницу для {captcha_type}")
        except Exception as e:
            logger.warning(f"Не удалось внедрить токен в страницу: {e}")

    def _load_learned_costs(self) -> dict:
        """Загружает сохранённые цены из файла."""
        costs_file = Path(CAPTCHA_COST_TRACKING.get(
            "LEARNED_COSTS_FILE", "data/captcha_learned_costs.json"
        ))
        if not costs_file.exists():
            return {}
        try:
            return json.loads(costs_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Не удалось загрузить learned costs: {e}")
            return {}

    def _save_learned_costs(self) -> None:
        """Сохраняет выученные цены в файл."""
        costs_file = Path(CAPTCHA_COST_TRACKING.get(
            "LEARNED_COSTS_FILE", "data/captcha_learned_costs.json"
        ))
        try:
            costs_file.parent.mkdir(parents=True, exist_ok=True)
            costs_file.write_text(
                json.dumps(self._learned_costs, indent=4, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error(f"Не удалось сохранить learned costs: {e}")
