from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    steam_api_key: str
    steam_id_64: str | None = None  # required for CLI; web users authenticate via Steam OpenID
    secret_key: str = "dev-secret-change-in-production"

    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    openai_model: str = "gpt-4o"

    cache_db_path: str = "./cache.sqlite"
    cache_ttl_days: int = 30

    top_n_recommendations: int = 10
    library_sample_for_rediscovery: int = 15
    steam_appdetails_rps: float = 0.5  # 150 req/5min — safely under Steam's ~200 limit


settings = Settings()
