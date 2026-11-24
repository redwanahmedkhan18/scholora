"""Application configuration (12-factor, env-driven)."""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_origin(raw: str) -> str:
    """Turn a CORS entry into ``scheme://host[:port]`` form, or "" when it is unusable.

    Dashboards and copied connection snippets routinely yield bare hostnames. A browser's
    ``Origin`` header always carries a scheme, so a bare hostname can never match; it is
    upgraded to https instead of being silently ignored.
    """
    entry = (raw or "").strip().strip('"').strip("'").rstrip("/").strip()
    if not entry:
        return ""
    if entry == "*":
        return "*"
    if entry.startswith(("http://", "https://")):
        return entry
    if "://" in entry:                      # e.g. ws:// - not a browser origin
        return ""
    return f"https://{entry}"


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
    # A regex for extra allowed origins (sandbox previews). Empty by default: a pattern
    # like https://.*\.e2b\.app lets *any* sandbox on the internet call the API, so it
    # must be an explicit, per-environment decision rather than a built-in.
    cors_origin_regex: str = ""
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
        """Allowed browser origins, normalised to scheme://host form.

        A browser sends ``Origin: https://scholara.example``; a CORS_ORIGINS entry of
        ``scholara.example`` therefore matches nothing and silently blocks every call
        from the site - which is exactly what a bare hostname in a dashboard produced.
        Bare entries are upgraded to https:// rather than ignored, because a value the
        operator clearly meant as an origin should work. The normalised list is logged
        at startup so the correction is visible.
        """
        if self.cors_origins.strip() == "*":
            return ["*"]
        origins: List[str] = []
        for raw in self.cors_origins.split(","):
            entry = normalize_origin(raw)
            if entry and entry not in origins:
                origins.append(entry)
        # The site's own origin is always allowed: it is already published in
        # PUBLIC_SITE_URL, and leaving it out of CORS_ORIGINS breaks every browser call
        # from the deployed frontend - which is exactly the failure this fixes.
        site = normalize_origin(self.public_site_url)
        if site and site not in origins:
            origins.append(site)
        return origins

    @property
    def cors_allows_any_origin(self) -> bool:
        return self.cors_origin_list == ["*"]

    @property
    def cors_allow_credentials(self) -> bool:
        """Never pair a wildcard origin with credentials.

        Starlette echoes the caller's Origin and sets Allow-Credentials when the list is
        ``["*"]``, which lets *any* website drive an authenticated session. This API
        authenticates with a bearer header, not cookies, so credentialed CORS is not
        needed and is disabled whenever every origin is allowed.
        """
        return not self.cors_allows_any_origin

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
