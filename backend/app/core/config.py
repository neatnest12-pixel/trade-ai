from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Navora Quant AI"
    app_env: str = "development"
    database_url: str = "sqlite:///./navora.db"
    cors_origins: str = "http://localhost:3000"
    market_data_source: str = "mock"
    dataset_path: str | None = None
    auto_create_schema: bool = True
    log_level: str = "INFO"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
