from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_name: str = "Panama In Context API"
    debug: bool = False
    environment: str = "production"

    # Logging (issue #424). Only the ``app`` namespace is raised to this level;
    # the root logger stays at WARNING so third-party libraries (httpx, the
    # anthropic SDK, botocore, deepl) don't flood the 1GB droplet's logs.
    # Override with the LOG_LEVEL env var.
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql://user:pass@localhost:5432/dbname"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Supported languages
    default_language: str = "es"
    supported_languages: list[str] = ["es", "en"]

    # DeepL API for translations
    deepl_api: str = ""
    # EN→ES glossary (#633): off by default. The id is printed by
    # `python -m scripts.sync_deepl_glossary`; enabled with an empty id degrades
    # to no glossary (one warning), it never crashes.
    deepl_glossary_enabled: bool = False
    deepl_glossary_id: str = ""

    # Tour Booking System
    frontend_url: str = "https://panamaincontext.com"
    admin_notification_email: str = "diego@panamaincontext.com"
    sender_email: str = "diego@panamaincontext.com"

    # SMTP relay (Google Workspace smtp-relay.gmail.com — IP-allowlisted, no auth)
    smtp_relay_host: str = "smtp-relay.gmail.com"
    smtp_relay_port: int = 587
    smtp_relay_timeout: int = 10

    # PayPal Integration (webhook verification only - invoicing is manual)
    paypal_api_url: str = "https://api-m.sandbox.paypal.com"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_business_email: str = ""
    paypal_webhook_id: str = ""

    # OpenAI
    openai_api_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""
    # Generous in-process runaway backstop: cumulative input tokens spent by one
    # worker process before further LLM calls raise BudgetExceededError. Sized so
    # normal operation never trips it within a process lifetime; a runaway loop
    # does. Set to 0 (or negative) to disable the cap. See app/services/llm.py.
    anthropic_budget_input_tokens: int = 20_000_000
    # Anthropic call timeouts / streaming (issue #68). Long generations stream
    # via messages.stream() so they never block a worker on a single blocking
    # read; short calls get a tight cap instead of the SDK's 600s default.
    anthropic_timeout_seconds: float = 120.0  # non-streamed (short) calls
    anthropic_stream_timeout_seconds: float = 600.0  # streamed safety-net
    anthropic_stream_min_tokens: int = 8000  # max_tokens >= this → stream

    # Media-acquisition outbound HTTP (epic #275). The shared hardened client in
    # app/services/http_client.py reads these. Schemes / blocked hosts / allowed
    # content-types are closed security vocab and live as module constants there,
    # not here — they are not meant to be loosened by environment.
    media_http_timeout: float = 60.0  # per-request timeout for image/crawler fetches
    media_http_max_retries: int = 3  # total attempts on transient 5xx/429 (incl. first)
    loc_rate_limit_seconds: float = 3.0  # LOC allows 20 req/min — 3s stays safe
    wikimedia_rate_limit_seconds: float = 1.0  # Wikimedia Commons min interval

    # Admin emails (comma-separated)
    admin_emails: str = ""

    # DigitalOcean Spaces (S3-compatible object storage)
    do_spaces_key: str = ""
    do_spaces_secret: str = ""
    do_spaces_bucket: str = "panamaincontext"
    do_spaces_region: str = "nyc3"
    do_spaces_endpoint: str = "https://nyc3.digitaloceanspaces.com"
    do_spaces_cdn_url: str = "https://panamaincontext.nyc3.digitaloceanspaces.com"

    # ElevenLabs (voice narration for slide presentations — issue #311)
    elevenlabs_api_key: str = ""
    elevenlabs_voice_diego: str = ""

    # Instagram Graph API publishing (social pipeline). Empty credentials =
    # publisher disabled: publish endpoints answer 503 and the scheduler
    # no-ops, so everything upstream (generate/render/schedule) still works.
    # The scheduler loop itself stays on by default: with publishing off it
    # only sweeps stale claims and runs the daily token-expiry check.
    instagram_access_token: str = ""
    instagram_account_id: str = ""
    instagram_graph_api_url: str = "https://graph.facebook.com/v23.0"
    instagram_scheduler_enabled: bool = True
    # Publishing kill switch (#576). Off by default and independent of the
    # credentials: with a token configured but this off, publish-now answers
    # 409 and the scheduler leaves due rows SCHEDULED (never FAILED), while
    # generate / render / approve / schedule and the never-posting
    # ``POST .../instagram/verify`` keep working. An agency runs the account's
    # posting strategy; turning this on is an operator decision.
    instagram_publishing_enabled: bool = False
    # Sentry-warn when the long-lived token (~60 days) has fewer days left.
    instagram_token_warn_days: int = 7
    instagram_scheduler_interval_seconds: int = 60

    # Gemini image generation (#419). Empty = feature-image generation disabled:
    # the generate-image endpoint answers 503.
    gemini_api_key: str = ""

    # Voyage AI embeddings + rerank (#622, epic #617). Read only by
    # app/services/embeddings.py. Empty key = embeddings disabled: callers get
    # EmbeddingsDisabledError and degrade or answer 503; nothing else breaks.
    voyage_api_key: str = ""
    embedding_model: str = "voyage-4"
    # A stored vector column's size is fixed, so this must match the
    # knowledge_chunks vector width. Valid for voyage-4*: 256/512/1024/2048.
    embedding_dimensions: int = 1024
    rerank_model: str = "rerank-2.5"

    # Verified-claims KB matching via vector candidates (#628, epic #618).
    # Off = the word-overlap candidate path (#378), unchanged. On = each
    # claim's Haiku candidates are its nearest VERIFIED_CLAIM chunks
    # (cosine similarity >= the floor, at most top_k), with the exact
    # claim_key match still first. Falls back to word overlap when embeddings
    # are disabled or the query embedding fails. Promoting it is an operator
    # decision: run `python -m scripts.eval_kb_matching` first.
    kb_vector_matching_enabled: bool = False
    kb_vector_top_k: int = 5
    kb_vector_min_similarity: float = 0.6

    # Semantic duplicate-topic detection for article suggestions (#630, epic
    # #618). Off = suggestion generation is byte-for-byte unchanged. On = the
    # prompt also lists the nearest existing articles as "already covered"
    # (at most steer_titles), and each new suggestion is flagged (advisory
    # only, never blocked) as a possible duplicate of the closest article
    # centroid or existing suggestion at cosine similarity >= the floor. Any
    # embedding / retrieval failure degrades to no steer / no flag. The 0.725
    # floor is #632's calibrated value (prod read-only eval 2026-10-10: P 93%,
    # R 93%; the old provisional 0.85 flagged nothing). Switching the flag on
    # is an operator decision.
    suggestion_dedup_enabled: bool = False
    suggestion_dedup_min_similarity: float = 0.725
    suggestion_dedup_steer_titles: int = 10

    # Cross-article consistency check, shadow mode (#631, epic #618). Off = the
    # fact-check job runner is unchanged. On = after an ARTICLE fact-check job
    # has committed SUCCEEDED (and its claims reindex), each of its claims (at
    # most max_claims) is compared with its nearest facts from OTHER articles
    # and the verified-claims KB (at most top_k per claim, cosine similarity >=
    # the floor, at most max_pairs per article) by a Sonnet judge in batches of
    # batch_pairs; CONTRADICTS pairs land in consistency_findings. Records only:
    # never changes a verdict, never gates, not driver-visible. The 0.5 floor
    # was kept by #632 (prod read-only eval 2026-10-10: P 95%, R 95%): floors
    # 0.3-0.6 tied on a seed whose items sit close to their own source, so the
    # tool's 0.6 pick was a tie-break artifact; 0.5 costs ~0.5 extra pairs per
    # claim. Switching it on, and any promotion to a gate, are operator decisions.
    cross_article_consistency_enabled: bool = False
    consistency_max_claims: int = 40
    consistency_top_k: int = 3
    consistency_min_similarity: float = 0.5
    consistency_max_pairs: int = 120
    consistency_batch_pairs: int = 20

    # Ask Panama in Context abuse / cost controls (#634, epic #619), read only
    # by app/services/ask_pic_controls.py. The public Q&A is the first
    # unauthenticated endpoint that spends LLM money and nothing else
    # rate-limits inbound requests, so every limit below is DB-backed (counts /
    # sums over ask_pic_queries) and holds across both uvicorn workers.
    # Off = the endpoint answers 503. An empty ip_hash_secret also answers 503
    # (one warning), never crashes: IPs are stored only as
    # HMAC-SHA256(secret, UTC date | ip), so no secret means no safe storage.
    ask_pic_enabled: bool = False
    ask_pic_ip_hash_secret: str = ""
    ask_pic_daily_budget_usd: float = 2.00  # global, per UTC day, worst-case reserved
    ask_pic_per_ip_per_hour: int = 10
    ask_pic_global_per_day: int = 300  # per UTC day
    ask_pic_max_question_chars: int = 500
    # Question text is stored only when this is on; rows (with or without
    # text) are deleted after retention_days by scripts.prune_ask_pic_queries.
    ask_pic_store_questions: bool = False
    ask_pic_retention_days: int = 30
    # The answer endpoint itself (#635), read by app/services/ask_pic.py.
    # Answer model: Haiku 5.5 to start; #637's eval decides Haiku vs Sonnet.
    # It must be in llm.PRICE_PER_MILLION_TOKENS (the budget fails closed).
    ask_pic_model: str = "claude-haiku-5-5"
    # Abstain floors, measured by #653 (2026-10-10, voyage-4 + hybrid +
    # rerank): no LLM call when the top-1 rerank score is under the first, or,
    # when the rerank was unavailable, the top-1 vector similarity
    # (1 - cosine distance) is under the second. Both gaps were thin (0.039 /
    # 0.018); recalibrate on live traffic (the scores are logged per query).
    ask_pic_min_rerank_score: float = 0.74
    ask_pic_min_vector_similarity: float = 0.575
    # Query rewriting (#696): one Haiku call (effort low) before retrieval
    # adds an other-language and a period-terms query; the three rankings
    # are fused (RRF) and reranked against the original question. Its cost
    # counts toward the daily budget and the ledger. Off until the eval says
    # otherwise (docs/portfolio/rag/results/*-696*.md).
    ask_pic_query_rewrite_enabled: bool = False

    # Weekly planner (#417): the Gemini feature-image endpoint (#419) now exists,
    # so this flag is the output-quality gate — while it is False the planner
    # plans GENERATE_FEATURE_IMAGE SKIPPED and the manual upload path stays the
    # pipeline default. Flip it once generated images are judged good enough.
    weekly_plan_feature_images_enabled: bool = False

    # Weekly planner: Instagram post generation (#552). The IG account is not
    # connected yet, so every generated draft just piles up in the content
    # calendar. While False the planner plans GENERATE_SABIAS_QUE,
    # GENERATE_ARTICLE_PROMO and any IG post chain (approve / render /
    # schedule) SKIPPED. The generation endpoints stay live for manual runs;
    # flip this once the account is connected.
    weekly_plan_ig_posts_enabled: bool = False

    # Grammarly meaning check (#518): before grammar-apply writes a proofread
    # body, Sonnet reviews every changed paragraph and reverts edits that
    # changed meaning. Off = write whatever the operator accepted, unreviewed.
    # An empty anthropic_api_key also skips the check (never crashes it).
    grammar_meaning_check_enabled: bool = True

    # Series cadence. False = one series per ISO week (goal key
    # weekly_series:YYYY-Www, IG posts spread over the week). True = one goal
    # key per DAY: a plan-week call that starts a goal anchors it at
    # daily_series:YYYY-MM-DD and compresses the IG schedule (sabías-qué
    # day+1/day+2). Either way the article publish date is picked when the
    # series is finished (SET_PUBLISH_DATE, #558), not planned here, and the
    # promo is scheduled the day after it (#576).
    # Turned on 2026-09-03 to work through the research backlog (target: reach
    # 1903 by November); how many series actually start is paced by
    # weekly_plan_max_in_flight below (#559).
    weekly_plan_daily_cadence: bool = False

    # Series in flight (#559). plan-week starts a NEW goal only while fewer
    # than this many goals are unfinished (any task not DONE / SKIPPED —
    # FAILED and WAITING count); at the cap it answers 200 with
    # at_capacity=true. Expanding an existing goal (PLAN_ARTICLES) and an
    # explicit research_id are never capped. 0 or negative disables the cap.
    weekly_plan_max_in_flight: int = 2

    # Weekly-series driver (#418). agent_driver_token empty = no service token
    # accepted (feature disabled); set as an env var on the droplet.
    agent_driver_token: str = ""
    # 30 minutes covers the longest single driver step with margin (the droplet
    # proxy allows 1200s for generation endpoints).
    agent_task_lease_minutes: int = 30
    agent_task_max_attempts: int = 3
    # A wait task (#553) whose condition does not hold goes WAITING and is not
    # handed out again for this long — i.e. not before the next daily drive.
    agent_task_wait_recheck_hours: int = 20

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Unknown env vars must NOT crash the app. The pydantic default
        # (extra="forbid") turned a single new ELEVENLABS_* key in the prod .env
        # into a full backend crash-loop on startup. Ignoring unknown vars makes
        # it safe to add env keys ahead of the code that reads them.
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
