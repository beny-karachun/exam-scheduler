"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration – reads from .env or OS env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────
    # Default: async SQLite for local development.
    # Production: postgresql+asyncpg://user:pass@host/dbname
    DATABASE_URL: str = "sqlite+aiosqlite:///./exam_scheduler_dev.db"

    # ── Scheduling ────────────────────────────────────────────
    TRANSIT_BUFFER_MINUTES: int = 45
    FATIGUE_WINDOW_HOURS: int = 24
    FATIGUE_MAX_EXAMS: int = 2
    FATIGUE_PENALTY_WEIGHT: int = 1000

    # ── AI / Gemini (wired in Step 3) ─────────────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-pro"

    # ── Celery / Redis (wired in Step 3) ──────────────────────
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"


settings = Settings()
