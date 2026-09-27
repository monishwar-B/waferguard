"""Runtime configuration.

Precedence (highest first): environment variables ``WG_*`` > the YAML profile
named by ``WG_CONFIG`` (deploy/configs/{edge,onprem,cloud,desktop}.yaml) > defaults.
Nested keys use ``__`` in env vars, e.g. ``WG_ALERTS__SPIKE_RATE=0.4``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AlertSettings(BaseModel):
    window: int = 50                 # inspections per equipment used for the rolling defect rate
    min_samples: int = 20
    spike_rate: float = 0.35         # absolute defect rate that raises an alert
    spike_factor: float = 2.0        # ... or this multiple of the long-run baseline
    consecutive_defects: int = 5
    alert_on_critical: bool = True
    cooldown_minutes: int = 15


class NotifierSettings(BaseModel):
    email_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_tls: bool = True
    email_from: str = "waferguard@localhost"
    email_to: list[str] = Field(default_factory=list)
    teams_webhook_url: str | None = None
    sms_enabled: bool = False
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from: str | None = None
    sms_to: list[str] = Field(default_factory=list)
    webhook_url: str | None = None   # generic JSON webhook (MES, Slack-compatible proxies ...)
    min_level: str = "warning"       # info | warning | critical


class ModelSettings(BaseModel):
    champion_dir: str = os.path.join(ROOT, "models", "wafer-ensemble")
    challenger_dir: str | None = None
    ab_mode: str = "off"             # off | shadow | split
    ab_split: float = 0.1            # share of traffic routed to the challenger in split mode
    providers: str | list[str] = "auto"  # auto | CPUExecutionProvider | TensorrtExecutionProvider | ...
    tta: bool | None = None          # None -> use the manifest setting
    threads: int | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WG_", env_nested_delimiter="__", extra="ignore")

    profile: str = "default"
    database_url: str = f"sqlite:///{os.path.join(ROOT, 'var', 'waferguard.db')}"
    redis_url: str | None = None
    storage_dir: str = os.path.join(ROOT, "var", "images")
    store_images: bool = True
    jwt_secret: str = "change-me-in-production"
    token_minutes: int = 12 * 60
    bootstrap_admin_user: str = "admin"
    bootstrap_admin_password: str = "admin123"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    max_upload_mb: int = 64
    log_json: bool = False
    log_level: str = "INFO"
    serve_frontend: bool = True
    camera_store_every: int = 1      # persist every Nth camera frame (1 = all)
    fail_ratio_usl: float = 0.15     # upper spec limit used for Cpk/Ppk of the fail-die ratio
    models: ModelSettings = Field(default_factory=ModelSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    notifiers: NotifierSettings = Field(default_factory=NotifierSettings)
    severity: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (init_settings, env_settings, _YamlSource(settings_cls), file_secret_settings)


class _YamlSource(PydanticBaseSettingsSource):
    def get_field_value(self, field, field_name):  # pragma: no cover - not used
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        path = os.environ.get("WG_CONFIG")
        if not path:
            return {}
        if not os.path.isabs(path) and not os.path.exists(path):
            path = os.path.join(ROOT, path)
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        return data


@lru_cache
def get_settings() -> Settings:
    return Settings()
