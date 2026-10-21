from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env лежит в корне репозитория (на уровень выше backend/)
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql://kairos@localhost:5434/kairos"

    frontrun_api_key: SecretStr | None = None
    frontrun_base_url: str = "https://api.frontrun.pro/api/v1"
    # Лимит Frontrun по умолчанию: 60 запросов в минуту, 100 000 в сутки
    frontrun_rpm: int = 60

    pumpportal_ws_url: str = "wss://pumpportal.fun/api/data"

    helius_api_key: SecretStr | None = None
    helius_base_url: str = "https://mainnet.helius-rpc.com"
    helius_rps: float = 8.0
    # Бесплатный тариф Helius — 1 млн кредитов в месяц ≈ 33 000 в день; оставляем запас.
    helius_daily_credits: int = 20000
    helius_backfill_max_pages: int = 50         # 50 страниц × 100 транзакций ≈ 500 кредитов на один проход
    # Потолок на токен: 18.10.2026 один ботовый токен съел 2 130 кредитов (17,5 тыс. сделок) за час истории.
    helius_max_credits_per_token: int = 1500

    # Конвейер Frontrun. 100 000 кредитов в месяц ≈ 3 300 в день.
    frontrun_daily_credits: int = 3000
    # Гарантированные доли дневного бюджета Frontrun по видам задач, в процентах (CreditBudget.shares);
    # поиску CA достаётся остаток. Без них поиск (2 кредита, приоритет 10) съедал всё: smart followers,
    # история CA и кошельки шиллеров (400 кредитов) не выполнялись вообще. Доля вида без работы уходит остальным.
    frontrun_share_smart_followers_pct: int = 10    # 300 в сутки ≈ 100 проверок
    frontrun_share_kol_wallets_pct: int = 15        # 450 в сутки ≈ 1 поиск кошельков
    frontrun_share_ca_history_pct: int = 20         # 600 в сутки ≈ 12 историй CA
    search_on_migration: bool = True
    search_new_token_sample_pct: int = 5        # доля новых токенов, которые ищем во Frontrun
    search_new_token_delay_s: int = 300         # ждём, пока о токене начнут писать
    search_page_size: int = 100
    kol_min_followers: int = 1000               # с этого порога автор становится отслеживаемым шиллером
    kol_history_refresh_days: int = 7
    # Wallet discovery: top 10 KOLs x 400 credits, refreshed every 30 days.
    kol_wallets_top_n: int = 10
    kol_wallets_refresh_days: int = 30
    # Hourly finalized Solana history; up to 300 transactions per pass.
    kol_wallet_poll_minutes: int = 60
    kol_wallet_max_pages: int = 3
    # Separate UTC daily ceiling, also subject to the shared Helius budget.
    kol_wallet_daily_credits: int = 2500
    # Тратить дневные бюджеты Frontrun и Helius равномерно по суткам (см. CreditBudget), а не всё сразу.
    budget_pacing: bool = True

    # Скоринг и сигналы (docs/signals.md).
    signal_score_threshold: int = 60            # порог «сильного» сигнала и порог бота по умолчанию, 0–100
    signal_outcome_rule_version: str = "v2"     # по каким правилам исходов считается история шиллеров
    signal_max_age_hours: int = 6               # токены, у которых первый шилл старше, не скорим
    signal_interval_s: float = 60                # как часто пересчитывать сигналы в конвейере

    # Telegram-бот (kairos/bot). Без токена бот не стартует.
    telegram_bot_token: SecretStr | None = None
    bot_allowlist: str = ""                     # закрытая бета: chat_id через запятую; пусто — пускаем всех
    bot_poll_interval_s: int = 20               # как часто бот проверяет новые сигналы

    # Объяснения LLM (kairos/explain.py). Без ключа выключены.
    anthropic_api_key: SecretStr | None = None
    explain_model: str = "claude-sonnet-5-5"
    explain_daily_limit: int = 500              # не больше стольких вызовов LLM в сутки (UTC); 500 согласовано с заказчиком


@lru_cache
def get_settings() -> Settings:
    return Settings()
