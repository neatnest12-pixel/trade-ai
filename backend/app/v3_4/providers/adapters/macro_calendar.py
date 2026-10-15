from __future__ import annotations

import os

from app.v3_4.providers.contracts import unavailable


class MacroCalendarAdapter:
    provider_id = "tradingeconomics_calendar"
    selected_provider = "Trading Economics Calendar API"
    provider_env = "V3_MACRO_PROVIDER"
    api_key_env = "V3_MACRO_API_KEY"

    def fetch_sample(self) -> dict[str, object]:
        provider = os.getenv(self.provider_env, "").strip().lower()
        api_key = os.getenv(self.api_key_env, "").strip()
        if provider != "tradingeconomics" or not api_key:
            return {
                **unavailable("MACRO_CREDENTIAL_REQUIRED"),
                "provider_id": self.provider_id,
                "selected_provider_type": self.selected_provider,
                "provider_env": self.provider_env,
                "api_key_env": self.api_key_env,
                "required_fields": self.required_fields(),
            }
        return {
            **unavailable("MACRO_CONNECTION_NOT_RUN_IN_THIS_CAMPAIGN"),
            "provider_id": self.provider_id,
            "selected_provider_type": self.selected_provider,
            "required_fields": self.required_fields(),
        }

    @staticmethod
    def required_fields() -> list[str]:
        return [
            "event_id",
            "event_timestamp_utc",
            "country",
            "currency",
            "event_type",
            "importance",
            "actual",
            "forecast",
            "previous",
            "revised_previous",
            "release_status",
            "source",
            "retrieved_at",
            "quality",
        ]
