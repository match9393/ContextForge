from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"

    allowed_google_domains: str = "netaxis.be"
    admin_emails: str = ""

    answer_provider: str = "openai"
    vision_provider: str = "openai"
    embeddings_provider: str = "openai"
    answer_model: str = "gpt-5.2"
    vision_model: str = "gpt-5.2"
    embeddings_model: str = "text-embedding-3-large"
    answer_grounding_mode: str = "balanced"
    openai_api_key: str = ""
    openai_timeout_seconds: int = 60
    generated_images_enabled: bool = True
    generated_image_model: str = "gpt-image-1"
    generated_image_size: str = "1024x1024"
    generated_image_quality: str = "medium"
    generated_image_max_per_answer: int = 1
    ask_top_k: int = 6
    web_fetch_timeout_seconds: int = 20
    web_ingest_max_chars: int = 120000
    web_ingest_max_chunks: int = 120
    web_ingest_max_images: int = 60
    web_ingest_user_agent: str = "ContextForgeBot/1.0"
    google_delegated_bearer_token: str = ""

    database_url: str = "postgresql://contextforge:contextforge@postgres:5432/contextforge"
    redis_url: str = "redis://redis:6379/0"

    s3_endpoint: str = "http://minio:9000"
    s3_public_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_documents: str = "documents"
    s3_bucket_assets: str = "assets"

    ask_latency_p50_target_ms: int = 10000
    ask_latency_p95_target_ms: int = 25000
    ingest_chunk_size_chars: int = 1200
    ingest_chunk_overlap_chars: int = 180
    ingest_max_chunks: int = 200
    image_min_width: int = 320
    image_min_height: int = 320
    image_min_area: int = 200000
    image_min_bytes: int = 15000
    image_max_aspect_ratio: float = 8.0
    image_max_per_page: int = 5
    caption_max_chars: int = 1200
    ingest_max_vision_images: int = 0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def is_allowed_google_domain(self, email: str) -> bool:
        configured = self.allowed_google_domains.strip()
        if configured == "*":
            return True

        domain = email.split("@")[-1].lower()
        allowed = [item.strip().lower() for item in configured.split(",") if item.strip()]
        return domain in allowed

    def is_admin_email(self, email: str) -> bool:
        configured = self.admin_emails.strip()
        if configured == "*":
            return True
        if not configured:
            return False

        normalized_email = email.strip().lower()
        allowed = [item.strip().lower() for item in configured.split(",") if item.strip()]
        return normalized_email in allowed


settings = Settings()
