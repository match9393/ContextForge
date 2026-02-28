from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"

    allowed_google_domains: str = "netaxis.be"

    answer_provider: str = "openai"
    vision_provider: str = "openai"
    embeddings_provider: str = "openai"
    answer_model: str = "gpt-5.2"
    openai_api_key: str = ""
    openai_timeout_seconds: int = 60

    database_url: str = "postgresql://contextforge:contextforge@postgres:5432/contextforge"
    redis_url: str = "redis://redis:6379/0"

    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"

    ask_latency_p50_target_ms: int = 10000
    ask_latency_p95_target_ms: int = 25000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def is_allowed_google_domain(self, email: str) -> bool:
        configured = self.allowed_google_domains.strip()
        if configured == "*":
            return True

        domain = email.split("@")[-1].lower()
        allowed = [item.strip().lower() for item in configured.split(",") if item.strip()]
        return domain in allowed


settings = Settings()
