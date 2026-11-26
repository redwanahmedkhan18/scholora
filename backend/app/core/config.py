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
    # How long a half-authenticated login may be finished with a second factor. Short by
    # design: the challenge token proves only "this password was correct".
    mfa_challenge_ttl_seconds: int = 300
    mfa_recovery_code_count: int = 8
    # Demo/staff seed data creates well-known accounts with a published password and a TOTP
    # secret that is checked into this repository. Production must never do that, so seeding
    # users is opt-in and additionally refused when ENVIRONMENT=production.
    dev_seed_enabled: bool = True

    # --- retrieval / embeddings -------------------------------------------
    embedding_model: str = "text-embedding-3-small"

    admin_email: str = "admin@scholara.com.bd"
    admin_password: str = "Admin@12345"
    # Passwords that appear verbatim in this repository (seed data, README, the old login
    # screen). They are refused at the production login endpoint: a credential that ships with
    # the source is public, and treating it as a password is how a back office gets owned.
    # This must list *every* password the demo seed writes (app/seed.py) - one omission is a
    # usable production credential for the account that still holds it.
    published_passwords: str = "Admin@12345,Senior@12345,Counselor@123,Data@12345"

    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = "scholara-verify-token"
    whatsapp_app_secret: str = ""

    # --- outbound email (Gmail SMTP or any STARTTLS relay) ------------------
    # Left empty on purpose: no host means "not configured", and the email service then
    # reports that honestly instead of pretending a message was delivered. The sender
    # address defaults to the authenticated account, because Gmail rejects a From that
    # does not match (or is not an alias of) the account that logged in.
    smtp_host: str = ""
    smtp_port: int = 587
    # SMTP_USER is the name used in this project's docs; SMTP_USERNAME is accepted too, since
    # hosting dashboards and other frameworks use it. `smtp_login` picks whichever is set.
    smtp_user: str = ""
    smtp_username: str = ""
    smtp_password: str = ""
    # true => STARTTLS on the plaintext port (587, the Gmail recommendation).
    # false with smtp_ssl_port below => implicit TLS (465). One of the two must be on;
    # certificate verification is never disabled.
    smtp_use_tls: bool = True
    smtp_ssl: bool = False
    smtp_from_email: str = ""            # defaults to the authenticated account
    smtp_from_name: str = ""             # defaults to APP_NAME ("Scholara")
    smtp_timeout_seconds: float = 10.0   # connect + per-command socket timeout
    smtp_max_retries: int = 2            # additional attempts for transient failures only
    smtp_enabled: bool = True            # kill switch, separate from credentials

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
    def published_password_list(self) -> List[str]:
        return [p for p in (x.strip() for x in self.published_passwords.split(",")) if p]

    # ---- outbound email ---------------------------------------------------
    def _effective(self, name: str):
        """A field value with the database overlay in front of it.

        The admin screen stores overrides in the database and `settings` applies them by
        intercepting attribute access on the dynamic wrapper. These SMTP helpers are
        *properties*, and a property runs on the base `Settings` object, so `self.smtp_host`
        always returns the environment value: a mailbox saved in Admin -> Configuration was
        silently ignored and email stayed "not configured" no matter what was entered. Every
        derived check therefore reads its inputs through here.
        """
        try:
            from app.core import runtime
            value = runtime.override(name)
            if value is not None:
                return value
        except Exception:
            pass                      # overlay unavailable: fall back to the environment
        return getattr(self, name)

    @property
    def smtp_login(self) -> str:
        """The authenticated mailbox: SMTP_USERNAME wins when both are present."""
        username = self._effective("smtp_username") or self._effective("smtp_user")
        return str(username or "").strip()

    @property
    def smtp_sender_email(self) -> str:
        """From address. Gmail requires it to be the account that authenticated."""
        return str(self._effective("smtp_from_email") or self.smtp_login or "").strip()

    @property
    def smtp_sender_name(self) -> str:
        return str(self._effective("smtp_from_name") or self.app_name or "Scholara").strip()

    @property
    def smtp_port_is_valid(self) -> bool:
        """A port outside 1-65535 means the configuration is wrong, so nothing is attempted.

        Deliberately not a pydantic validator: a typo in a hosting dashboard should degrade
        email (reported clearly) rather than take the whole API down at import time.
        """
        try:
            return 1 <= int(self._effective("smtp_port") or 0) <= 65535
        except (TypeError, ValueError):
            return False

    @property
    def smtp_missing(self) -> List[str]:
        """The keys that still need a value. Names only - never values."""
        missing: List[str] = []
        if not str(self._effective("smtp_host") or "").strip():
            missing.append("SMTP_HOST")
        if not self.smtp_login:
            missing.append("SMTP_USER")
        if not self._effective("smtp_password"):
            missing.append("SMTP_PASSWORD")
        if not self.smtp_port_is_valid:
            missing.append("SMTP_PORT")
        if not (self._effective("smtp_use_tls") or self._effective("smtp_ssl")):
            missing.append("SMTP_USE_TLS")
        return missing

    @property
    def smtp_configured(self) -> bool:
        return bool(self._effective("smtp_enabled")) and not self.smtp_missing

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
