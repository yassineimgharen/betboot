from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///betboot.db"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = Field(default="", pattern=r"^[A-Za-z0-9._-]*$")
    football_data_token: SecretStr = SecretStr("")
    provider_request_interval: float = Field(default=6.1, ge=6.1)
    odds_api_key: SecretStr = SecretStr("")
    api_football_key: SecretStr = SecretStr("")
    isports_api_key: SecretStr = SecretStr("")
    odds_max_age_seconds: int = Field(default=600, ge=30, le=1800)
    research_timezone: str = "America/New_York"
    world_history_leagues: int = Field(default=3, ge=0, le=30)
    history_seasons: int = Field(default=2, ge=0, le=3)
    slip_min_probability: float = Field(default=0.65, ge=0.5, le=0.99)
    odds_extra_markets: bool = False
    official_feed_urls: list[str] = Field(default_factory=list)
