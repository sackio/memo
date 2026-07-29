from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    port: int = 8000
    openrouter_api_key: str
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536
    default_db_path: str = "~/.memo/memo.db"

    # Hook settings (written to ~/.memo/hooks.env during memo-hooks install)
    memo_auto_recall: bool = True
    memo_prework_recall: bool = True
    memo_recall_min_score: float = 0.5
    memo_recall_token_budget: int = 2000

    # Auto-store settings
    memo_auto_store: bool = True
    auto_store_model: str = "openai/gpt-4o-mini"
    auto_store_similarity_threshold: float = 0.82

    # Single-global refactor (2026-06-29): db_path is ignored server-side
    # but kept in the schema for backward compat. Toggle to suppress the
    # sampled warning if it becomes noisy.
    ignored_db_path_warning: bool = True

    # v2 TTL reaper (FR-007). 300s == the 5-minute sweep the spec calls for.
    # memo_reaper_enabled exists so a migration/soak run can hold the sweep
    # off without editing code.
    memo_reaper_enabled: bool = True
    memo_reaper_interval_seconds: int = 300

    @property
    def resolved_default_db_path(self) -> str:
        return str(Path(self.default_db_path).expanduser())


settings = Settings()
