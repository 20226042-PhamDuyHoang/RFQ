from pydantic_settings import BaseSettings
from pydantic import field_validator, ConfigDict
from typing import Optional


class Settings(BaseSettings):
    """
    Cau hinh ung dung, doc tu file .env hoac environment variables.
    """

    app_name: str = "RFQ Automation System"
    debug: bool = False
    secret_key: str = "change-me"

    # Database
    database_url: str = "sqlite:///./rfq_automation.db"

    # LLM (OpenAI-compatible, e.g. NVIDIA NIM)
    openai_api_key: str = ""
    openai_base_url: str = "https://integrate.api.nvidia.com/v1"
    openai_model: str = "openai/gpt-oss-120b"

    # SMTP
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "RFQ Automation System"
    smtp_use_tls: bool = True

    # IMAP
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_use_ssl: bool = True
    imap_mailbox: str = "INBOX"
    imap_poll_interval_seconds: int = 60

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    # Currency
    exchange_rate_api_key: str = ""
    exchange_rate_base_url: str = "https://v6.exchangerate-api.com/v6"

    # Sender profile (hien thi trong email signature)
    sender_name: str = "Duy"
    sender_position: str = "Procurement Manager"
    sender_company: str = "Sotatek"
    sender_phone: str = ""

    @field_validator("smtp_port")
    @classmethod
    def validate_smtp_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("smtp_port must be 1-65535")
        return v

    @field_validator("imap_port")
    @classmethod
    def validate_imap_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("imap_port must be 1-65535")
        return v

    @field_validator("imap_poll_interval_seconds")
    @classmethod
    def validate_poll_interval(cls, v: int) -> int:
        if v < 10:
            raise ValueError("imap_poll_interval_seconds must be >= 10")
        return v

    def safe_repr(self) -> dict:
        """Return settings dict with secrets masked — safe for logging."""
        data = self.model_dump()
        secret_keys = {"secret_key", "openai_api_key", "smtp_password", "imap_password", "exchange_rate_api_key"}
        for k in secret_keys:
            if data.get(k):
                data[k] = data[k][:4] + "****"
        return data

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
