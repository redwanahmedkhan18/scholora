"""Application configuration (12-factor, env-driven)."""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Scholara"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 720
    algorithm: str = "HS256"

    database_url: str = "sqlite:///./data/scholara.db"
    # Supabase Session Pooler is the production target. These values keep a single
    # Render web instance, its Celery worker and one-off migration jobs comfortably
    # inside a small Supabase pool. Override per service if concurrency is raised.
    db_connect_timeout: int = 10
    db_pool_size: int = 3
    db_max_overflow: int = 2
    db_pool_timeout: int = 30
    db_pool_recycle: int = 180
    db_sslmode: str = ""                     # empty => Supabase URLs get "require"
    cors_origins: str = "*"
    public_site_url: str = "http://localhost:3000"

    # --- infrastructure ---------------------------------------------------
    redis_url: str = ""                      # empty => in-process fallback (dev only)
    redis_socket_connect_timeout: float = 3.0
    redis_socket_timeout: float = 3.0
    redis_health_check_interval: int = 30
    cache_key_prefix: str = "scholara:"
    rate_limit_enabled: bool = True
    # Shared secret the Next.js server sends on server-side rendering calls so a build is
    # not rate-limited by its own backend. Never exposed to the browser.
    internal_api_token: str = "dev-internal-render-token"
    scheduler_enabled: bool = True           # in-process jobs when Celery is absent
    allow_in_process_scheduler_in_production: bool = False
    celery_broker_url: str = ""              # set => run workers instead
    celery_result_backend: str = ""          # optional; unset => Celery ignores results

    # --- private document storage ----------------------------------------
    storage_backend: str = "local"           # "local" | "s3"
    storage_dir: str = "./data/private"
    signed_url_ttl_seconds: int = 300
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_region: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # --- auth hardening ---------------------------------------------------
    mfa_required_roles: str = "ADMIN,SENIOR_COUNSELOR"
    mfa_issuer: str = "Scholara"

    # --- retrieval / embeddings -------------------------------------------
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 256

    admin_email: str = "admin@scholara.com.bd"
    admin_password: str = "Admin@12345"

    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = "scholara-verify-token"
    whatsapp_app_secret: str = ""

    facebook_page_id: str = ""
    facebook_page_access_token: str = ""
    meta_pixel_id: str = ""
    meta_capi_access_token: str = ""

    ga4_measurement_id: str = ""
    google_ads_conversion_id: str = ""
    google_site_verification: str = ""

    # ---- LLM providers for the grounded assistant (no OpenAI dependency) ----
    # All three speak the OpenAI-compatible /chat/completions wire format, so they are
    # interchangeable. Tried in `llm_provider_order`; the first configured one wins.
    llm_enabled: bool = True
    llm_provider_order: str = "grok,mistral,gemini"
    llm_timeout_seconds: float = 20.0
    llm_max_tokens: int = 700
    llm_temperature: float = 0.2

    xai_api_key: str = ""
    xai_model: str = "grok-4-fast-non-reasoning"
    # Meta deprecates Graph API versions on a rolling basis, so the version must not be
    # buried in five call sites. Overridable in the environment, which also lets the
    # conformance harness point the real client code at a local server.
    meta_graph_base_url: str = "https://graph.facebook.com/v21.0"
    xai_base_url: str = "https://api.x.ai/v1"

    mistral_api_key: str = ""
    mistral_model: str = "mistral-large-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"

    # ---- Embeddings for pgvector retrieval ----
    # Falls back to a deterministic local hashing vectoriser when no key is set, so the
    # vector path works everywhere. Width must match the column the migration created.
    embeddings_enabled: bool = True
    embedding_dim: int = 1024
    retrieval_dense_weight: float = 0.65   # dense vs lexical in the hybrid fusion
    retrieval_min_score: float = 0.10      # dense floor for a match with no shared vocabulary
    # Which embedding vendor to try first. Admin-editable at runtime.
    embedding_provider_order: str = "mistral,gemini"
    mistral_embed_model: str = "mistral-embed"
    gemini_embed_model: str = "gemini-embedding-001"

    @property
    def cors_origin_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def mfa_required_role_list(self) -> List[str]:
        return [r.strip().upper() for r in self.mfa_required_roles.split(",") if r.strip()]

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgres")

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    """The pure environment/.env view. Use this when you deliberately want to bypass the
    database overlay (the admin screen does, to show what each key would fall back to)."""
    return Settings()


class _DynamicSettings:
    """`Settings` with a database overlay in front of it.

    Credentials, the embedding provider and the retrieval weights are editable at runtime
    from the admin UI. Rather than rewrite every `settings.x` read - there are dozens, and
    missing one would silently keep using a stale value - attribute access is intercepted
    here. Keys on the `runtime.OVERRIDABLE` allowlist come from the database when set;
    everything else, including properties like `is_postgres`, falls straight through.
    """
    __slots__ = ()

    def __getattr__(self, name: str):
        if not name.startswith("_"):
            try:
                from app.core import runtime
                value = runtime.override(name)
                if value is not None:
                    return value
            except Exception:
                pass          # overlay unavailable (fresh install, migration running, ...)
        return getattr(get_settings(), name)

    def __setattr__(self, name: str, value) -> None:
        # Tests monkeypatch settings directly; keep that working by writing to the base.
        setattr(get_settings(), name, value)

    def __repr__(self) -> str:
        return f"<DynamicSettings {get_settings().app_name!r}>"


settings = _DynamicSettings()
