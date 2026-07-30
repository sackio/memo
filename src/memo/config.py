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
    auto_store_similarity_threshold: float = 0.82
    # `auto_store_model` (was "openai/gpt-4o-mini") REMOVED 2026-07-29 per R-17.
    # auto_store's generative calls now go through the shared LLMProvider — an
    # interactive Claude Code session, not a per-token API. The setting is gone
    # rather than left unused so nobody re-wires a direct model call to it;
    # R-17 requires exactly one generative path. Use MEMO_LLM_PROVIDER instead.
    # (Embeddings are unaffected and still use embedding_model via OpenRouter.)

    # Single-global refactor (2026-06-29): db_path is ignored server-side
    # but kept in the schema for backward compat. Toggle to suppress the
    # sampled warning if it becomes noisy.
    ignored_db_path_warning: bool = True

    # v2 TTL reaper (FR-007). 300s == the 5-minute sweep the spec calls for.
    # memo_reaper_enabled exists so a migration/soak run can hold the sweep
    # off without editing code.
    memo_reaper_enabled: bool = True
    memo_reaper_interval_seconds: int = 300

    # v2 mediator LLM provider (research.md R-17). Served by an INTERACTIVE
    # Claude Code session (`memo-llm`) riding the Max subscription — never a
    # per-token API, and never `claude -p`, which bills as API usage.
    # Defaults to `null` (always-unavailable -> mediators degrade) until the
    # claude_session adapter lands in Phase 5 / T085a.
    memo_llm_provider: str = "null"
    memo_llm_timeout_seconds: float = 10.0
    # Fallback fires when more than this many candidates survive dedup +
    # bi-temporal filtering, or when top candidates conflict.
    memo_llm_fallback_candidate_threshold: int = 15

    @property
    def resolved_default_db_path(self) -> str:
        return str(Path(self.default_db_path).expanduser())


settings = Settings()
