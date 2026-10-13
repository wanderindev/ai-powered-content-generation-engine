from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_name: str = "Panama In Context API"
    debug: bool = False
    environment: str = "production"

    # Database
    database_url: str = "postgresql://user:pass@localhost:5432/dbname"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Supported languages
    default_language: str = "es"
    supported_languages: list[str] = ["es", "en"]

    # DeepL API for translations
    deepl_api: str = ""

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
    instagram_access_token: str = ""
    instagram_account_id: str = ""
    instagram_graph_api_url: str = "https://graph.facebook.com/v23.0"
    instagram_scheduler_enabled: bool = True
    instagram_scheduler_interval_seconds: int = 60

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
