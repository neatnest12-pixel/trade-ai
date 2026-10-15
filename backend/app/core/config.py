from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LEGACY_DATABASE = (_PROJECT_ROOT / "backend" / "navora.db").resolve()


class Settings(BaseSettings):
    app_name: str = "Navora Quant AI"
    app_env: str = "development"
    database_url: str | None = None
    navora_database_path: Path | None = None
    cors_origins: str = "http://localhost:3000"
    market_data_source: str = "mock"
    dataset_path: str | None = None
    auto_create_schema: bool = True
    mt5_read_only: bool = True
    trading_execution_enabled: bool = False
    v1_snapshot_id: str | None = None
    v1_config_hash: str | None = None
    twelve_data_api_key: str | None = None
    log_level: str = "INFO"
    api_auth_required: bool = False
    navora_api_secret: str | None = None
    api_rate_limit_per_minute: int = 120
    runtime_state_path: str = "runtime/worker_state.json"
    worker_lease_seconds: int = 30
    mt5_broker: str = "Raw Trading Ltd"
    mt5_server: str = "ICMarketsSC-Demo"
    v3_mt5_server_utc_offset_seconds: int | None = None
    model_config = SettingsConfigDict(env_file=_PROJECT_ROOT / ".env", extra="ignore")

    @field_validator("v3_mt5_server_utc_offset_seconds")
    @classmethod
    def validate_mt5_server_utc_offset(cls, value: int | None) -> int | None:
        if value is not None and abs(value) > 14 * 60 * 60:
            raise ValueError("V3_MT5_SERVER_UTC_OFFSET_SECONDS_IMPLAUSIBLE")
        return value

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def resolved_database_path(self) -> Path | None:
        if self.navora_database_path:
            value = self.navora_database_path
            return (value if value.is_absolute() else _PROJECT_ROOT / value).resolve()
        if self.database_url and self.database_url.startswith("sqlite:///"):
            value = Path(self.database_url.removeprefix("sqlite:///"))
            return (value if value.is_absolute() else _PROJECT_ROOT / value).resolve()
        return None

    @property
    def resolved_database_url(self) -> str:
        if self.app_env == "test" and self.database_url:
            return self.database_url
        path = self.resolved_database_path
        if not path:
            raise RuntimeError("NAVORA_DATABASE_PATH or an absolute SQLite DATABASE_URL is required")
        return f"sqlite:///{path.as_posix()}"

    def validate_production(self) -> None:
        path = self.resolved_database_path
        if self.app_env in {"production", "paper"}:
            if not path or not path.is_absolute():
                raise RuntimeError("Production/paper mode requires deterministic NAVORA_DATABASE_PATH")
            if path == _LEGACY_DATABASE:
                raise RuntimeError("Legacy backend/navora.db is forbidden in production/paper mode")
            if not self.api_auth_required or not self.navora_api_secret:
                raise RuntimeError("Production API authentication requires NAVORA_API_SECRET")
            if "*" in self.origins:
                raise RuntimeError("Wildcard CORS is forbidden in production")
        if not self.mt5_read_only or self.trading_execution_enabled:
            raise RuntimeError("MT5 must remain read-only and trading execution must remain disabled")


@lru_cache
def get_settings() -> Settings:
    return Settings()
