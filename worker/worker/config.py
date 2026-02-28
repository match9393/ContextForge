from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql://contextforge:contextforge@postgres:5432/contextforge"
    redis_url: str = "redis://redis:6379/0"
    worker_poll_seconds: int = 5

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
