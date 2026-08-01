from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    port: int = 8000
    openrouter_api_key: str
    # Operator directive 2026-08-01: the SELF-HOSTED model, at its native 2560.
    # (Supersedes the 2026-07-31 T203 directive for `3-large` @3072, which held
    # for one day and whose measurements are preserved in research.md R-09 as the
    # comparison baseline — that is the whole point of keeping them.)
    #
    # The dimension choice is a SAFETY one, not a quality one, and the argument
    # is unchanged from the 3-large decision — only the numbers move. A model can
    # often be truncated to some other width, which would avoid rebuilding the
    # vector tables; but then vectors from two different models are
    # indistinguishable in SHAPE, so a half-finished re-embed scores nonsense
    # silently. 2560 collides with neither 1536 (3-small) nor 3072 (3-large), so
    # sqlite-vec REJECTS a mismatched insert and a partial re-embed CRASHES
    # instead of quietly returning garbage. Given this project's history, prefer
    # every time the option that cannot fail quietly.
    #
    # Qwen3 additionally refuses a `dimensions` request parameter outright (400,
    # "does not support Matryoshka embeddings"), so truncation is not even
    # available here — see embeddings.py.
    #
    # Changing either value invalidates every stored vector: cosine between
    # models is meaningless, not merely noisier. There is no incremental path —
    # the corpus is re-embedded wholesale or not at all.
    embedding_model: str = "qwen3-embedding-4b"
    embedding_dimensions: int = 2560

    # The embedding provider is now a LAN service, not OpenRouter. No auth: the
    # key is a placeholder because the OpenAI SDK requires a non-empty string.
    # Kept separate from `openrouter_api_key`, which the auto-store/classify LLM
    # calls still use — the two providers moved apart on 2026-08-01 and folding
    # them back into one setting would re-couple them.
    embedding_base_url: str = "http://192.168.1.5:31536/v1"
    embedding_api_key: str = "not-required-lan-service"
    default_db_path: str = "~/.memo/memo.db"

    # Which path `/search` serves. [002/FR-113]
    #
    # DEFAULT IS "document", AND CHANGING IT IS AN OPERATOR DECISION, NOT A
    # TUNING KNOB. As of 2026-07-31 the passage path is a large measured
    # improvement (research.md R-07: 2000+ rank-1 10/66 -> 38/66) and still
    # misses two of its three success criteria — SC-101 at 57.6% against 80%,
    # SC-103 at 67% against 75%. The flip waits on those, not on this flag
    # existing.
    #
    # Both paths stay addressable at their own endpoints regardless of this
    # setting (`/search-documents`, `/search-passages`) precisely so a config
    # change can never silently alter what a measurement is measuring — the
    # bench targets the explicit endpoints for that reason.
    memo_retrieval_path: str = "document"

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

    # Provider selection (Principle VIII / FR-045). `null` keeps memo working
    # standalone, with integration features WARN-logging what they would do.
    memo_conductor_provider: str = "atc"
    memo_agent_controller_provider: str = "agents_supervisor"

    # v2 generative LLM provider (research.md R-17). ALL generative calls go
    # through an INTERACTIVE Claude Code session (`memo-llm`) riding the Max
    # subscription — never a per-token API, and never `claude -p`, which bills
    # as API usage.
    #
    # DEFAULT STAYS `null` even though the claude_session adapter now exists
    # (T085a deviation, 2026-07-30). Flipping the default would make the v2
    # container immediately try to reach a `memo-llm` session that HAS NOT BEEN
    # CREATED on this fleet yet, and — working exactly as designed — DM the
    # `agents` supervisor about the outage. Shipping a default that pages the
    # supervisor about missing infrastructure is not a good default.
    #
    # To turn it on, once `memo-llm` exists:  MEMO_LLM_PROVIDER=claude_session
    memo_llm_provider: str = "null"
    memo_llm_timeout_seconds: float = 10.0
    # Fallback fires when more than this many candidates survive dedup +
    # bi-temporal filtering, or when top candidates conflict.
    memo_llm_fallback_candidate_threshold: int = 15

    @property
    def resolved_default_db_path(self) -> str:
        return str(Path(self.default_db_path).expanduser())


settings = Settings()
