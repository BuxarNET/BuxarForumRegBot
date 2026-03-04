# BuxarForumRegBot

<div align="center">

[🇷🇺 Русский](README.md) | [🇬🇧 English](README_ENG.md)

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-139%20passed-brightgreen.svg)]()
[![Pydoll](https://img.shields.io/badge/Browser-Pydoll-orange.svg)](https://github.com/autoscrape-labs/pydoll)

**Automated mass account registration system for forums**

*Support for multiple forum engines · Automatic captcha solving · Parallel processing · Fault tolerance*

</div>

---

## 📋 Table of Contents

- [Description](#-description)
- [Features](#-features)
- [Requirements](#-requirements)
- [Installation](#-installation)
- [Quick Start](#-quick-start)
- [Project Structure](#-project-structure)
- [Configuration](#-configuration)
- [File Formats](#-file-formats)
- [Testing](#-testing)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [Donations](#-donations)
- [License](#-license)

---

## 📖 Description

**BuxarForumRegBot** is an asynchronous system for mass automatic account registration on forums. It supports popular engines (phpBB, XenForo, vBulletin, SMF) through a template system, and for unknown forums it automatically analyzes the HTML and heuristically finds registration fields.

The system works in conjunction with a real Chrome browser via the Pydoll library, ensuring maximum compatibility with forum protections and realistic behavior.

---

## ✨ Features

### Registration
- 🌐 Support for popular forum engines: **phpBB, XenForo, vBulletin, SMF**
- 🔍 Heuristic analysis of unknown forums — automatic form field detection
- 👥 Parallel operation of multiple users simultaneously (configurable)
- 🔄 Resume after failure — continues from where it stopped without reprocessing
- 🔁 Retry with exponential backoff on temporary errors

### Captcha
- 🤖 Automatic solving via a chain of providers with fallback
- 🖐 Manual mode — wait for user solving in the open browser
- 💰 Smart provider selection by priority or lowest cost
- 📊 Automatic real cost calculation based on balance change
- 🔌 Support for 7 providers: AZCaptcha, CapSolver, 2Captcha, TrueCaptcha, DeathByCaptcha, SolveCaptcha, EndCaptcha

### Browser and Profiles
- 🖥 Separate browser with persistent profile for each user
- 🍪 Cookie retention between tabs (realistic behavior)
- 🔒 User isolation — each works in its own profile and proxy
- 📌 Informative window titles for visual monitoring

### Results and Reports
- 📝 Immediate result recording after each registration (with flush)
- 📊 Detailed final report with failure tables in English
- 📸 Automatic screenshots on errors
- 🛑 Graceful shutdown — properly terminates with progress saved

---

## 💻 Requirements

- **Python** 3.12+
- **Chromium** or **Google Chrome** (installed separately)
- **uv** (recommended) or **pip**
- Operating system: Linux, macOS, Windows

---

## 🚀 Installation

### 1. Install uv (recommended)

`uv` is a modern fast Python package manager, a replacement for pip + venv.

**Linux / macOS:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

After installation, restart your terminal or run:
```bash
source $HOME/.local/bin/env   # Linux/macOS
```

### 2. Clone the repository

```bash
git clone https://github.com/buxar/BuxarForumRegBot.git
cd BuxarForumRegBot
```

### 3. Create a virtual environment and install dependencies

**With uv (recommended):**
```bash
uv venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

uv pip install -r requirements.txt
```

**With pip (alternative):**
```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

### 4. Configure settings

```bash
cp .env.example .env
```

Open `.env` and add API keys for the captcha providers you need:

```env
# Add keys only for the providers you use
AZCAPTCHA_API_KEY=your_key
CAPSOLVER_API_KEY=your_key
TWOCAPTCHA_API_KEY=your_key
TRUECAPTCHA_API_KEY=your_key
DEATHBYCAPTCHA_API_KEY=your_key
SOLVECAPTCHA_API_KEY=your_key
ENDCAPTCHA_API_KEY=your_key
```

> If API keys are not set, the system will automatically switch to manual captcha solving mode.

---

## ⚡ Quick Start

### Data Preparation

**`src/data/accounts.json`** — list of accounts:
```json
[
    {
        "username": "myuser1",
        "email": "user1@example.com",
        "password": "StrongPass123",
        "proxy_id": 0,
        "custom_fields": {},
        "status": "pending",
        "attempts": 0,
        "last_attempt": null
    }
]
```

**`src/data/proxies.txt`** — list of proxies (one per line):
```
http://user:pass@1.2.3.4:8080
http://5.6.7.8:3128
socks5://9.10.11.12:1080
```

**`src/data/results_new.txt`** — list of forums:
```
# List of forums to register on
https://forum1.com/
https://forum2.com/register
https://forum3.com/
```

### Running

```bash
cd src

# Check the environment
python main.py --check

# Preview the plan without launching browsers
python main.py --dry-run

# Start registration
python main.py

# Generate a report from existing results
python main.py --report
```

---

## 📁 Project Structure

```
BuxarForumRegBot/
├── src/
│   ├── config/
│   │   ├── settings.py              # All system settings
│   │   └── logging.conf             # Logging configuration
│   ├── controllers/
│   │   ├── browser_controller.py    # Chrome browser management (Pydoll)
│   │   └── registration_controller.py # Forum registration logic
│   ├── data/
│   │   ├── profiles/                # Persistent browser profiles
│   │   ├── screenshots/             # Registration error screenshots
│   │   ├── accounts.json            # List of accounts
│   │   ├── proxies.txt              # List of proxies
│   │   └── results_new.txt          # Input list of forums
│   ├── extensions/
│   │   └── nopecha_solver/          # NopeCHA extension for auto-captcha
│   ├── logs/                        # System logs
│   ├── orchestrator/
│   │   └── main_orchestrator.py     # Main coordinating module
│   ├── templates/
│   │   ├── known_forums/            # Templates for known forum engines
│   │   │   ├── phpbb.json           # phpBB template
│   │   │   ├── smf.json             # SMF template
│   │   │   ├── vbulletin.json       # vBulletin template
│   │   │   └── xenforo.json         # XenForo template
│   │   ├── common_fields.json       # Common form field rules
│   │   └── heuristic_rules.json     # Heuristic field search rules
│   ├── utils/
│   │   ├── captcha_providers/       # Captcha solving providers
│   │   │   ├── implementations/
│   │   │   │   ├── az_captcha.py    # AZCaptcha
│   │   │   │   ├── cap_solver.py    # CapSolver
│   │   │   │   ├── two_captcha.py   # 2Captcha
│   │   │   │   ├── manual.py        # Manual mode
│   │   │   │   └── _stubs.py        # Stubs for other providers
│   │   │   ├── base.py              # Abstract provider class
│   │   │   └── registry.py          # Provider registry and chain
│   │   ├── account_manager.py       # Account management
│   │   ├── captcha_helper.py        # Facade for captcha solving
│   │   └── proxy_manager.py         # Proxy management
│   ├── main.py                      # Entry point
│   ├── selector_finder.py           # Heuristic form field search
│   └── template_manager.py          # Forum template management
├── tests/
│   ├── integration/
│   │   └── test_registration_flow.py # Integration tests (manual run)
│   ├── check_browser_controller.py   # Manual browser check
│   ├── check_setup.py                # Environment check
│   ├── conftest.py                   # Common pytest fixtures
│   ├── test_captcha_helper.py        # Captcha provider tests
│   ├── test_datamanagers.py          # Data manager tests
│   ├── test_integration.py           # Module integration tests
│   ├── test_main_orchestrator.py     # Main orchestrator tests
│   ├── test_registration_controller.py # Registration controller tests
│   ├── test_selector_finder.py       # Field search tests
│   └── test_template_manager.py      # Template manager tests
├── pytest.ini                        # Pytest configuration
├── README.md                         # Documentation (Russian)
├── README_ENG.md                     # Documentation (English)
└── requirements.txt                  # Python dependencies
```

---

## ⚙️ Configuration

All settings are located in `src/config/settings.py`.

### 📂 File Paths

| Parameter | Default | Description |
|----------|-------------|----------|
| `ACCOUNTS_FILE` | `data/accounts.json` | File with account list |
| `PROXIES_FILE` | `data/proxies.txt` | File with proxy list |
| `FORUMS_SOURCE_FILE` | `data/results_new.txt` | Input list of forums |
| `PROFILES_DIR` | `data/profiles` | Browser profiles directory |
| `RESULTS_DIR` | `data` | Directory for result files |
| `RESULTS_FLUSH_IMMEDIATELY` | `True` | Write results immediately after each operation |

### ⚡ Concurrency

| Parameter | Default | Description |
|----------|-------------|----------|
| `MAX_CONCURRENT_USERS` | `5` | Maximum number of simultaneous browsers |
| `EACH_USER_ALL_FORUMS` | `True` | `True` — each user processes all forums; `False` — forums are distributed among users |

### ⏱ Timeouts and Retries

| Parameter | Default | Description |
|----------|-------------|----------|
| `MAX_REGISTRATION_RETRIES` | `3` | Attempts per forum before marking as failure |
| `MANUAL_CAPTCHA_TIMEOUT` | `300` | Seconds for manual captcha solving (5 min) |
| `MANUAL_FIELD_FILL_TIMEOUT` | `120` | Seconds for manual field filling (2 min) |
| `TAB_TIMEOUT_SECONDS` | `300` | Total timeout for one registration (5 min) |
| `FIND_REGISTRATION_PAGE_TIMEOUT` | `60` | Seconds to find registration page |
| `GRACEFUL_SHUTDOWN_TIMEOUT` | `60` | Seconds for graceful shutdown on stop |

### 🌐 Proxy

| Parameter | Default | Description |
|----------|-------------|----------|
| `REQUIRE_PROXY_PER_USER` | `True` | Require a proxy for each user |
| `ALLOW_GLOBAL_FALLBACK_PROXY` | `False` | Use a global fallback proxy if personal is missing |
| `PROXY_RETRY_ON_FAILURE` | `True` | Retry on proxy error |

### 🖥 Browser and Profiles

| Parameter | Default | Description |
|----------|-------------|----------|
| `BROWSER_MODE` | `one_per_user` | Mode: `one_per_user` — separate browser per user |
| `PROFILE_PER_USER` | `True` | Create separate profile for each user |
| `PROFILE_PERSISTENCE` | `True` | Save profile after completion (cookies, history) |
| `SAVE_PROFILE_METADATA` | `True` | Save `meta.json` with profile data |
| `CLOSE_BROWSER_AFTER_ALL_REGISTRATIONS` | `True` | Close browser after all registrations are done |
| `SET_WINDOW_TITLE` | `True` | Set informative browser window title |
| `SHOW_BROWSER_WINDOWS` | `True` | Show browser windows (False = headless) |
| `WINDOW_TITLE_FORMAT` | `{username} \| {forum} \| {status}` | Window title template |

### 🗂 Tabs

| Parameter | Default | Description |
|----------|-------------|----------|
| `TAB_PER_REGISTRATION` | `True` | Open a new tab for each registration |
| `CLOSE_TAB_AFTER_REGISTRATION` | `True` | Close tab after processing |
| `CLEAN_TAB_BEFORE_OPEN` | `False` | Clear cookies before opening (False = keep realism) |

### 🔐 Captcha

| Parameter | Default | Description |
|----------|-------------|----------|
| `CAPTCHA["AUTO_SORT_BY_COST"]` | `False` | `True` — sort providers by cost; `False` — by priority |
| `CAPTCHA["DEFAULT_TIMEOUT"]` | `120` | Timeout for captcha solving (sec) |
| `CAPTCHA["V3_MIN_SCORE"]` | `0.5` | Minimum score for reCAPTCHA v3 (0.0–1.0) |
| `CAPTCHA["ALLOW_MANUAL_FALLBACK"]` | `True` | Fallback to manual mode if all providers fail |

#### Captcha Providers (`CAPTCHA_PROVIDERS_CONFIG`)

| Provider | Priority | Supported Types | .env variable |
|-----------|-----------|--------------------|--------------------|
| AZCaptcha | 1 | recaptcha_v2, hcaptcha, image | `AZCAPTCHA_API_KEY` |
| CapSolver | 5 | recaptcha_v2, v3, hcaptcha, turnstile, image | `CAPSOLVER_API_KEY` |
| 2Captcha | 10 | recaptcha_v2, v3, hcaptcha, turnstile, funcaptcha, geetest, image | `TWOCAPTCHA_API_KEY` |
| TrueCaptcha | 15 | image, recaptcha_v2 | `TRUECAPTCHA_API_KEY` |
| DeathByCaptcha | 20 | recaptcha_v2, hcaptcha, image | `DEATHBYCAPTCHA_API_KEY` |
| SolveCaptcha | 25 | recaptcha_v2, hcaptcha, image | `SOLVECAPTCHA_API_KEY` |
| EndCaptcha | 30 | recaptcha_v2, image | `ENDCAPTCHA_API_KEY` |
| Manual | 999 | all types | not required |

#### Captcha Cost Tracking (`CAPTCHA_COST_TRACKING`)

| Parameter | Default | Description |
|----------|-------------|----------|
| `ENABLED` | `True` | Enable automatic cost calculation |
| `LEARNED_COSTS_FILE` | `data/captcha_learned_costs.json` | File to store learned prices |
| `MIN_SOLVES_FOR_CALCULATION` | `3` | Minimum solves to calculate average price |
| `DEFAULT_COST` | `0.002` | Default price ($) if no data |
| `OUTLIER_THRESHOLD` | `5.0` | Outlier cutoff threshold (multiplier) |
| `LOG_LEARNED_PRICES` | `True` | Log learned prices to console |

---

## 📄 File Formats

### accounts.json
```json
[
    {
        "username": "myuser1",
        "email": "user1@example.com",
        "password": "StrongPass123",
        "proxy_id": 0,
        "custom_fields": {
            "referral": "REF123",
            "city": "Moscow"
        },
        "status": "pending",
        "attempts": 0,
        "last_attempt": null
    }
]
```

### proxies.txt
```
# Format: protocol://[user:pass@]host:port
http://user:pass@1.2.3.4:8080
http://5.6.7.8:3128
socks5://9.10.11.12:1080
```

### results_new.txt (input forum list)
```
# Comments start with #
https://forum1.com/
https://forum2.com/register
# https://forum3.com/  <- commented out, skipped
https://forum4.com/
```

### results_ok_username.txt (successful registrations)
```
https://forum1.com/ 2026-02-24T15:30:22
https://forum2.com/ 2026-02-24T15:35:10
```

### results_bad_username.txt (failures)
```
https://forum3.com/ captcha_timeout 2026-02-24T15:40:12
https://forum4.com/ fields_not_found 2026-02-24T15:45:33
```

---

## 🧪 Testing

### Automatic Tests (unit)

Run all automatic tests:

```bash
pytest tests/ -v
```

Short output:
```bash
pytest tests/ -v --tb=short
```

Run a specific module:
```bash
pytest tests/test_registration_controller.py -v
pytest tests/test_captcha_helper.py -v
pytest tests/test_main_orchestrator.py -v
```

Current coverage: **139 tests** (all passing), 3 skipped (integration).

### Integration Tests (manual run)

Integration tests require a real browser, proxies, and API keys. Run manually with the flag:

```bash
pytest tests/ -v --integration
```

### Manual Checks

**Environment check** (dependencies, files, keys):
```bash
python tests/check_setup.py
```

**Browser check** (real Chrome launch with test scenarios):
```bash
# Test navigation and text input
python tests/check_browser_controller.py

# Inside the file, three scenarios are available:
# - test_basic_navigation()     — basic navigation
# - test_manual_captcha_mode()  — manual captcha solving
# - test_auto_captcha_mode_with_extension() — auto-captcha with extension
```

> ⚠️ `check_*.py` files are not run automatically by `pytest` — only manually via `python`.

### Pre-run Verification

```bash
# Step 1: check environment
python src/main.py --check

# Step 2: preview plan
python src/main.py --dry-run
```

---

## 🔧 Troubleshooting

**Browser not found**
```
Check for Chromium: which chromium || which chromium-browser
Or specify the path manually in browser_controller.py → EXTRA_BROWSER_PATHS
```

**ModuleNotFoundError**
```bash
# Make sure you're running from the src/ directory
cd src && python main.py
# Or that the virtual environment is activated
source .venv/bin/activate
```

**Captcha not solving automatically**
```
Check API key in .env
Ensure provider balance is not zero
The system will automatically fallback to manual mode
```

**Proxy not connecting**
```
Check format: http://user:pass@host:port
Ensure proxy is reachable: curl --proxy http://host:port https://api.ipify.org
```

---

## 🤝 Contributing

We welcome any contributions to the project!

### How to Contribute

1. **Fork** the repository
2. Create a branch for your feature:
   ```bash
   git checkout -b feature/new-feature
   ```
3. Make changes and add tests
4. Ensure all tests pass:
   ```bash
   pytest tests/ -v
   ```
5. Create a **Pull Request** with a description of changes

### Adding a New Forum Template

Create a file `src/templates/known_forums/engine_name.json` following the examples of existing templates. The structure is described in `src/templates/common_fields.json`.

### Adding a New Captcha Provider

1. Create a file `src/utils/captcha_providers/implementations/new_provider.py`
2. Inherit from `CaptchaProvider` in `base.py`
3. Implement all abstract methods
4. Register the provider in `registry.py` and `config/settings.py`

### Code Style

- Python 3.12+, `from __future__ import annotations`
- Type hints with `| None`, `TypedDict`
- Docstrings in Google style
- Logging with `loguru`
- Formatting: `black`, `isort`

### Report an Issue

Open an [Issue](https://github.com/buxar/BuxarForumRegBot/issues) with:
- Python version and OS
- Steps to reproduce
- Expected and actual behavior
- Logs from `src/logs/`

---

## 💖 Donations

If BuxarForumRegBot is useful to you, please support its development:

### Cryptocurrency
- **Bitcoin**: `bc1q5nq5046gxcng03el66vxswunfz7a7m0k658wkj`
- **Ethereum**: `0xbdAbB06907A69f8814DcF2Cc3a84Ef23ba3f9b00`
- **BNB**: `0xbdAbB06907A69f8814DcF2Cc3a84Ef23ba3f9b00`
- **USDT ERC20**: `0xbdAbB06907A69f8814DcF2Cc3a84Ef23ba3f9b00`
- **USDT TRC20**: `TYXzhFkVMovxXa8CRPyrGV78S7aC79WUFd`
- **TRON**: `TYXzhFkVMovxXa8CRPyrGV78S7aC79WUFd`

### Russia
- **YooMoney**: [https://yoomoney.ru/to/4100173831748](https://yoomoney.ru/to/4100173831748)

### Transfers from anywhere in the world
- **Paysera**: Account number: `EVP8610001034598`

Your support helps maintain and improve the project!

---

## 📜 License

This project is distributed under the [MIT](LICENSE) license.

---

<div align="center">

Made with ❤️ by the BuxarNET team

[⬆ Back to top](#buxarforumregbot)

</div>