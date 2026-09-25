"""App settings, read from environment variables or a .env file."""

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACKER_", env_file=".env", extra="ignore")

    db_url: str = "sqlite:///data/tracker.db"

    gmail_credentials: str = "credentials.json"
    gmail_token: str = "data/token.json"
    gmail_query: str = "from:bankmandiri.co.id"

    own_names: str = ""
    own_ewallets: str = "GOPAY,OVO,DANA,SHOPEEPAY,LINKAJA"
    count_topups_as_spending: bool = True

    monthly_budget: int = 0  # split into a daily allowance; wins over daily_budget
    daily_budget: int = 0  # fixed per-day budget, used only if monthly_budget is 0
    timezone: str = "Asia/Jakarta"
    # Day the budget period starts: 1 = calendar month, 25 = the 25th to the 24th of next month.
    period_start_day: int = 1

    # Telegram bot (phase 2)
    telegram_token: str = ""
    telegram_chat_id: int = 0  # only this chat can use the bot; send /start to get yours
    bot_source: str = "gmail"  # gmail | eml (eml = read a folder, for testing with synthetic data)
    bot_eml_path: str = "data/synthetic"
    sync_every_minutes: int = 5
    summary_time: str = "21:00"  # end-of-day summary, local time
    alert_max_age_hours: int = 24  # older transactions (e.g. first backfill) are stored without alerts

    @staticmethod
    def _split(value: str) -> list[str]:
        return [part.strip().upper() for part in value.split(",") if part.strip()]

    @property
    def own_name_list(self) -> list[str]:
        return self._split(self.own_names)

    @property
    def own_ewallet_list(self) -> list[str]:
        return self._split(self.own_ewallets)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
