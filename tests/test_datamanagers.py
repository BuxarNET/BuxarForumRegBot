from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# =============================================================================
# Тесты ProxyManager
# =============================================================================

@pytest.fixture
def proxy_file(tmp_path: Path) -> Path:
    """Временный файл с прокси для тестов."""
    content = """
# Комментарий — игнорируется

http://user:pass@127.0.0.1:8080
socks5://127.0.0.1:1080
192.168.1.1:3128

# Ещё один комментарий
https://proxy.example.com:8888
"""
    p = tmp_path / "proxies.txt"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def proxy_manager(proxy_file: Path):
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from utils.proxy_manager import ProxyManager
    return ProxyManager(proxy_file)


@pytest.mark.asyncio
async def test_load_proxies_reads_all(proxy_manager):
    """Загружает все прокси, игнорируя комментарии и пустые строки."""
    await proxy_manager.load_proxies()
    assert len(proxy_manager.proxies) == 4


@pytest.mark.asyncio
async def test_load_proxies_adds_http_prefix(proxy_manager):
    """host:port без протокола получает http:// префикс."""
    await proxy_manager.load_proxies()
    assert "http://192.168.1.1:3128" in proxy_manager.proxies


@pytest.mark.asyncio
async def test_load_proxies_preserves_protocols(proxy_manager):
    """Прокси с протоколом не изменяются."""
    await proxy_manager.load_proxies()
    assert "http://user:pass@127.0.0.1:8080" in proxy_manager.proxies
    assert "socks5://127.0.0.1:1080" in proxy_manager.proxies
    assert "https://proxy.example.com:8888" in proxy_manager.proxies


@pytest.mark.asyncio
async def test_load_proxies_file_not_found(tmp_path: Path):
    """При отсутствии файла proxies остаётся пустым."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from utils.proxy_manager import ProxyManager
    pm = ProxyManager(tmp_path / "nonexistent.txt")
    await pm.load_proxies()
    assert pm.proxies == []


@pytest.mark.asyncio
async def test_get_next_proxy_no_check(proxy_manager):
    """get_next_proxy(check=False) возвращает прокси без проверки."""
    await proxy_manager.load_proxies()
    proxy = await proxy_manager.get_next_proxy(check=False)
    assert proxy is not None
    assert "://" in proxy


@pytest.mark.asyncio
async def test_get_next_proxy_round_robin(proxy_manager):
    """Прокси выдаются по кругу."""
    await proxy_manager.load_proxies()
    total = len(proxy_manager.proxies)
    seen = set()
    for _ in range(total):
        p = await proxy_manager.get_next_proxy(check=False)
        seen.add(p)
    assert len(seen) == total


@pytest.mark.asyncio
async def test_get_next_proxy_skips_bad(proxy_manager):
    """Нерабочие прокси пропускаются при check=True."""
    await proxy_manager.load_proxies()
    # Первые три — плохие, последний — хороший
    results = [False, False, False, True]

    async def mock_check(proxy: str) -> bool:
        return results.pop(0)

    proxy_manager.check_proxy = mock_check
    proxy = await proxy_manager.get_next_proxy(check=True)
    assert proxy == proxy_manager.proxies[3]


@pytest.mark.asyncio
async def test_get_next_proxy_all_bad_returns_none(proxy_manager):
    """Если все прокси нерабочие — возвращается None."""
    await proxy_manager.load_proxies()
    proxy_manager.check_proxy = AsyncMock(return_value=False)
    result = await proxy_manager.get_next_proxy(check=True)
    assert result is None


@pytest.mark.asyncio
async def test_get_next_proxy_empty_list():
    """Пустой список прокси возвращает None."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from utils.proxy_manager import ProxyManager
    pm = ProxyManager("nonexistent.txt")
    result = await pm.get_next_proxy(check=False)
    assert result is None


@pytest.mark.asyncio
async def test_refresh_proxies_reloads(tmp_path: Path):
    """refresh_proxies перезагружает список и сбрасывает индекс."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from utils.proxy_manager import ProxyManager

    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("http://1.1.1.1:8080\n", encoding="utf-8")

    pm = ProxyManager(proxy_file)
    await pm.load_proxies()
    await pm.get_next_proxy(check=False)  # сдвигаем индекс
    assert pm.current_index == 0

    # Добавляем новый прокси в файл
    proxy_file.write_text("http://1.1.1.1:8080\nhttp://2.2.2.2:8080\n", encoding="utf-8")
    await pm.refresh_proxies()

    assert pm.current_index == 0
    assert len(pm.proxies) == 2


# =============================================================================
# Тесты AccountManager
# =============================================================================

@pytest.fixture
def account_manager(tmp_path: Path):
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from utils.account_manager import AccountManager
    return AccountManager(
        accounts_file=tmp_path / "accounts.json",
        log_file=tmp_path / "registration_log.json",
    )


@pytest.fixture
def sample_accounts() -> list[dict]:
    return [
        {
            "username": "user1",
            "email": "user1@example.com",
            "password": "Pass123",
            "proxy_id": 0,
            "custom_fields": {},
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        },
        {
            "username": "user2",
            "email": "user2@example.com",
            "password": "Pass456",
            "proxy_id": 1,
            "custom_fields": {"city": "Moscow"},
            "status": "registered",
            "attempts": 1,
            "last_attempt": "2026-02-24T10:00:00",
        },
        {
            "username": "user3",
            "email": "user3@example.com",
            "password": "Pass789",
            "proxy_id": 2,
            "custom_fields": {},
            "status": "failed",
            "attempts": 2,
            "last_attempt": "2026-02-24T11:00:00",
        },
    ]


@pytest.fixture
def sample_result() -> dict:
    return {
        "success": True,
        "message": "Registration completed",
        "reason": None,
        "template_used": "XenForo",
        "screenshot": None,
        "form_data": {
            "username": "user1",
            "email": "user1@example.com",
            "password": "Pass123",
            "proxy_id": 0,
            "custom_fields": {},
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        },
    }


@pytest.mark.asyncio
async def test_log_registration_saves_entry(account_manager, sample_result):
    """log_registration сохраняет запись в лог-файл."""
    await account_manager.log_registration(sample_result)

    log = await account_manager.get_registration_log()
    assert len(log) == 1
    assert log[0]["username"] == "user1"
    assert log[0]["success"] is True
    assert log[0]["template_used"] == "XenForo"


@pytest.mark.asyncio
async def test_log_registration_appends(account_manager, sample_result):
    """log_registration добавляет записи, не перезаписывает."""
    await account_manager.log_registration(sample_result)
    await account_manager.log_registration(sample_result)

    log = await account_manager.get_registration_log()
    assert len(log) == 2


@pytest.mark.asyncio
async def test_log_registration_uses_account_data(account_manager, sample_result):
    """log_registration использует account_data если передан."""
    account_data = {
        "username": "explicit_user",
        "email": "explicit@example.com",
        "password": "pass",
        "proxy_id": 0,
        "custom_fields": {},
        "status": "pending",
        "attempts": 0,
        "last_attempt": None,
    }
    await account_manager.log_registration(sample_result, account_data)

    log = await account_manager.get_registration_log()
    assert log[0]["username"] == "explicit_user"
    assert log[0]["email"] == "explicit@example.com"


@pytest.mark.asyncio
async def test_get_registration_log_filter_username(account_manager, sample_result):
    """Фильтрация лога по username."""
    await account_manager.log_registration(sample_result)

    result2 = dict(sample_result)
    result2["form_data"] = dict(sample_result["form_data"])
    result2["form_data"]["username"] = "other_user"
    await account_manager.log_registration(result2)

    log = await account_manager.get_registration_log(username="user1")
    assert len(log) == 1
    assert log[0]["username"] == "user1"


@pytest.mark.asyncio
async def test_get_registration_log_filter_success(account_manager, sample_result):
    """Фильтрация лога по success."""
    await account_manager.log_registration(sample_result)

    failed_result = dict(sample_result)
    failed_result["success"] = False
    await account_manager.log_registration(failed_result)

    successful = await account_manager.get_registration_log(success=True)
    assert all(e["success"] is True for e in successful)

    failed = await account_manager.get_registration_log(success=False)
    assert all(e["success"] is False for e in failed)


@pytest.mark.asyncio
async def test_get_pending_accounts_filters_status(account_manager, sample_accounts, tmp_path):
    """get_pending_accounts возвращает только pending аккаунты."""
    await account_manager._save_json(account_manager.accounts_file, sample_accounts)

    pending = await account_manager.get_pending_accounts()
    assert len(pending) == 1
    assert pending[0]["username"] == "user1"


@pytest.mark.asyncio
async def test_get_pending_accounts_skips_invalid(account_manager, tmp_path):
    """Аккаунты без обязательных полей пропускаются."""
    accounts = [
        {
            "username": "valid_user",
            "email": "valid@example.com",
            "password": "pass",
            "proxy_id": 0,
            "custom_fields": {},
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        },
        {
            "username": "no_email",
            "password": "pass",
            "proxy_id": 0,
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        },
        {
            "username": "no_proxy",
            "email": "no_proxy@example.com",
            "password": "pass",
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        },
    ]
    await account_manager._save_json(account_manager.accounts_file, accounts)

    pending = await account_manager.get_pending_accounts()
    assert len(pending) == 1
    assert pending[0]["username"] == "valid_user"


@pytest.mark.asyncio
async def test_get_pending_accounts_sorting(account_manager, tmp_path):
    """Сортировка: сначала attempts==0, затем по last_attempt."""
    accounts = [
        {
            "username": "user_b",
            "email": "b@example.com",
            "password": "pass",
            "proxy_id": 0,
            "custom_fields": {},
            "status": "pending",
            "attempts": 1,
            "last_attempt": "2026-02-24T09:00:00",
        },
        {
            "username": "user_a",
            "email": "a@example.com",
            "password": "pass",
            "proxy_id": 1,
            "custom_fields": {},
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        },
        {
            "username": "user_c",
            "email": "c@example.com",
            "password": "pass",
            "proxy_id": 2,
            "custom_fields": {},
            "status": "pending",
            "attempts": 1,
            "last_attempt": "2026-02-24T10:00:00",
        },
    ]
    await account_manager._save_json(account_manager.accounts_file, accounts)

    pending = await account_manager.get_pending_accounts()
    assert pending[0]["username"] == "user_a"   # attempts==0 первый
    assert pending[1]["username"] == "user_b"   # раньше last_attempt
    assert pending[2]["username"] == "user_c"


@pytest.mark.asyncio
async def test_get_pending_accounts_limit(account_manager, tmp_path):
    """Параметр limit ограничивает количество аккаунтов."""
    accounts = [
        {
            "username": f"user{i}",
            "email": f"user{i}@example.com",
            "password": "pass",
            "proxy_id": i,
            "custom_fields": {},
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        }
        for i in range(10)
    ]
    await account_manager._save_json(account_manager.accounts_file, accounts)

    pending = await account_manager.get_pending_accounts(limit=3)
    assert len(pending) == 3


@pytest.mark.asyncio
async def test_update_account_status(account_manager, sample_accounts):
    """update_account_status обновляет статус, увеличивает attempts."""
    await account_manager._save_json(account_manager.accounts_file, sample_accounts)

    await account_manager.update_account_status(
        username="user1",
        status="registered",
    )

    accounts = await account_manager._load_json(account_manager.accounts_file)
    user1 = next(a for a in accounts if a["username"] == "user1")

    assert user1["status"] == "registered"
    assert user1["attempts"] == 1
    assert user1["last_attempt"] is not None


@pytest.mark.asyncio
async def test_update_account_status_with_reason(account_manager, sample_accounts):
    """update_account_status сохраняет last_error при наличии reason."""
    await account_manager._save_json(account_manager.accounts_file, sample_accounts)

    await account_manager.update_account_status(
        username="user1",
        status="failed",
        reason="captcha_timeout",
        proxy="http://1.1.1.1:8080",
    )

    accounts = await account_manager._load_json(account_manager.accounts_file)
    user1 = next(a for a in accounts if a["username"] == "user1")

    assert user1["last_error"]["reason"] == "captcha_timeout"
    assert user1["last_error"]["proxy_used"] == "http://1.1.1.1:8080"


@pytest.mark.asyncio
async def test_update_account_status_invalidates_cache(account_manager, sample_accounts):
    """update_account_status инвалидирует кэш."""
    await account_manager._save_json(account_manager.accounts_file, sample_accounts)
    account_manager._accounts_cache = sample_accounts  # type: ignore

    await account_manager.update_account_status("user1", "registered")
    assert account_manager._accounts_cache is None


@pytest.mark.asyncio
async def test_update_account_status_not_found(account_manager, sample_accounts):
    """update_account_status не падает если аккаунт не найден."""
    await account_manager._save_json(account_manager.accounts_file, sample_accounts)
    # Не должно выбросить исключение
    await account_manager.update_account_status("nonexistent", "registered")


@pytest.mark.asyncio
async def test_export_failed_accounts(account_manager, sample_accounts, tmp_path):
    """export_failed_accounts экспортирует только failed и banned."""
    # Добавим banned
    sample_accounts.append({
        "username": "user4",
        "email": "user4@example.com",
        "password": "Pass000",
        "proxy_id": 3,
        "custom_fields": {},
        "status": "banned",
        "attempts": 5,
        "last_attempt": "2026-02-24T12:00:00",
    })
    await account_manager._save_json(account_manager.accounts_file, sample_accounts)

    output = tmp_path / "failed_export.json"
    count = await account_manager.export_failed_accounts(output)

    assert count == 2  # user3 (failed) + user4 (banned)
    exported = json.loads(output.read_text(encoding="utf-8"))
    statuses = {a["status"] for a in exported}
    assert statuses == {"failed", "banned"}


@pytest.mark.asyncio
async def test_load_json_missing_file(account_manager, tmp_path):
    """_load_json возвращает [] если файл не существует."""
    result = await account_manager._load_json(tmp_path / "nonexistent.json")
    assert result == []


@pytest.mark.asyncio
async def test_load_json_invalid_json(account_manager, tmp_path):
    """_load_json возвращает [] при невалидном JSON."""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{ not valid json }", encoding="utf-8")
    result = await account_manager._load_json(bad_file)
    assert result == []
