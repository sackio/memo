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
    # "document" | "passages" | "size-routed"
    memo_retrieval_path: str = "document"
    # Tokens kept either side of the matched passage when packing /context.
    # 0 disables windowing entirely (pack whole documents). [002/FR-120]
    memo_context_span_window: int = 0

    # Token count at or above which `size-routed` prefers the PASSAGE index for a
    # given result. [002/FR-113 T273]
    #
    # 1000 comes from the R-09 census, not from taste. Below it the document path
    # measures better on every band (78.5/76.9/76.6 vs 77.1/72.0/74.5); at and
    # above it the passage path wins decisively (51.8→68.5, then 18.8→47.6). The
    # crossover sits inside 500-1000, so 1000 is the first band boundary where
    # passages are unambiguously ahead.
    #
    # Re-derive it from a fresh census before trusting it on a different model —
    # it is a property of THIS corpus measured on 3-large, and R-10 may move it.
    memo_size_route_threshold: int = 1000

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
    # ⚠️ CORRECTED 2026-08-10. This comment previously said "the claude_session
    # adapter now exists" and told you to set `MEMO_LLM_PROVIDER=claude_session`.
    # BOTH WERE FALSE: that adapter was REMOVED on 2026-07-30 (see the note in
    # providers/llm/__init__.py, which was accurate the whole time), so following
    # this comment set an unknown provider name — which falls back to `null` with
    # a warning and looks exactly like leaving it alone. ⭐ A stale comment that
    # names a specific symbol is worse than no comment: the specificity reads as
    # evidence somebody checked. Third time this file's comments were trusted over
    # the code in one week.
    #
    # ⭐ AND THERE IS NO REPLACEMENT ADAPTER, BY DESIGN. Do not write one.
    #
    # RECALL SYNTHESIS IS THE CALLER'S JOB. Every caller of `/recall` is itself a
    # Claude Code session with subagents on the Max subscription: it is already
    # running, already holds the question, and can spawn a background subagent that
    # reconciles the candidates and returns only the conclusion. A server-side LLM
    # would add a network hop, a queue, and a dependency that can be down — to do
    # worse what the caller does locally. The `/recall` skill implements this.
    #
    # ⚠️ 2026-08-10: an `atc_session` adapter (memo → `memo-llm` seat → subagent →
    # ATC reply zone) was written and then REMOVED UNSHIPPED on the same day, once
    # Ben pointed out the caller already is the LLM. ⭐ It was a re-implementation
    # of exactly what had been deleted on 07-30 — written by someone who had read
    # the note recording that deletion an hour earlier and filed it as stale trivia.
    # **A comment saying "we deliberately removed X" is a design decision, not
    # archaeology.** If a third adapter ever seems necessary, the question to answer
    # first is why the caller cannot do it.
    #
    # ⇒ `null` is not a placeholder awaiting a real provider. It is the setting that
    # makes the mediators degrade and report it, which is what lets the caller know
    # to synthesise. The `degraded:` anomaly is the interface.
    memo_llm_provider: str = "null"
    memo_llm_timeout_seconds: float = 10.0
    # Fallback fires when more than this many candidates survive dedup +
    # bi-temporal filtering, or when top candidates conflict.
    memo_llm_fallback_candidate_threshold: int = 15

    @property
    def resolved_default_db_path(self) -> str:
        return str(Path(self.default_db_path).expanduser())


settings = Settings()
