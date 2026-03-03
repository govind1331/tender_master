from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # PostgreSQL
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "tender_db"
    postgres_user: str = "tender_user"
    postgres_password: str = "tender_pass"

    # Redis / Celery
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    similarity_threshold: float = 0.75

    # Ollama — LLM inference
    ollama_base_url: str = "http://ollama:11434"
    llm_model: str = "llama3.2:3b"          # any model pulled in Ollama

    # HuggingFace — local sentence-transformer for embeddings (no API key needed)
    embedding_model: str = "all-MiniLM-L6-v2"   # 384-dim, fast, CPU-friendly
    embedding_dim: int = 384                      # must match chosen model

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
