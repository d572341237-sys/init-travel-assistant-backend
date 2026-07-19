from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    deepseek_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "OPENAI_API_KEY"),
    )
    deepseek_base_url: str | None = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("DEEPSEEK_BASE_URL", "OPENAI_BASE_URL"),
    )
    deepseek_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("DEEPSEEK_MODEL", "OPENAI_MODEL"),
    )
    amap_api_key: str = Field(default="", alias="AMAP_API_KEY")
    amap_private_key: str = Field(default="", alias="AMAP_PRIVATE_KEY")
    amap_js_api_key: str = Field(default="", alias="AMAP_JS_API_KEY")
    amap_js_security_code: str = Field(default="", alias="AMAP_JS_SECURITY_CODE")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(default="sqlite:///./travel_assistant.db", alias="DATABASE_URL")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    cookie_samesite: str = Field(default="lax", alias="COOKIE_SAMESITE")
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def has_amap_credentials(self) -> bool:
        return bool(self.amap_api_key)

    @property
    def has_amap_js_credentials(self) -> bool:
        return bool(self.amap_js_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
