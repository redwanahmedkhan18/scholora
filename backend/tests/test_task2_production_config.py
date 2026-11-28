"""W4/H2 deployment-configuration validation tests.

These call the startup validator directly so no hosted environment is involved.
"""
from __future__ import annotations

import pytest
from app.core.config import settings


def _production(monkeypatch, **overrides):
    values = {
        "environment": "production",
        "app_env": "",
        "allowed_origins": "",
        "cors_origins": "https://scholara.com.bd",
        "public_site_url": "https://scholara.com.bd",
        "secret_key": "s" * 48,
        "assistant_session_secret": "a" * 48,
        # Explicit so the test does not depend on an ambient .env value.
        "internal_api_token": "i" * 40,
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value, raising=False)


def test_a_complete_production_configuration_passes(monkeypatch):
    _production(monkeypatch)
    settings.validate_deployment()


def test_production_requires_its_own_session_secret(monkeypatch):
    _production(monkeypatch, assistant_session_secret="")
    with pytest.raises(RuntimeError, match="ASSISTANT_SESSION_SECRET"):
        settings.validate_deployment()


@pytest.mark.parametrize("secret", ["dev-secret-change-me", "test-secret-key-not-for-production",
                                    "short"])
def test_published_or_short_secrets_are_refused(monkeypatch, secret):
    _production(monkeypatch, secret_key=secret)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        settings.validate_deployment()


def test_published_session_secret_is_refused_even_when_explicit(monkeypatch):
    _production(monkeypatch, assistant_session_secret="dev-secret-change-me")
    with pytest.raises(RuntimeError, match="ASSISTANT_SESSION_SECRET"):
        settings.validate_deployment()


def test_app_env_mismatch_fails_closed(monkeypatch):
    _production(monkeypatch, environment="development", app_env="production")
    with pytest.raises(RuntimeError, match="APP_ENV"):
        settings.validate_deployment()


def test_legacy_and_current_origin_names_must_not_disagree(monkeypatch):
    _production(monkeypatch, allowed_origins="https://old.example")
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        settings.validate_deployment()


def test_wildcard_production_cors_is_refused(monkeypatch):
    _production(monkeypatch, cors_origins="*")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        settings.validate_deployment()


def test_production_origins_must_be_https(monkeypatch):
    _production(monkeypatch, cors_origins="http://scholara.com.bd",
                public_site_url="http://scholara.com.bd")
    with pytest.raises(RuntimeError, match="https"):
        settings.validate_deployment()


def test_non_production_is_not_forced_into_production_requirements(monkeypatch):
    _production(monkeypatch, environment="test", app_env="", cors_origins="*",
                assistant_session_secret="")
    settings.validate_deployment()


def test_invalid_trusted_proxy_cidr_fails_validation(monkeypatch):
    _production(monkeypatch, trusted_proxies="not-a-cidr")
    with pytest.raises(RuntimeError, match="TRUSTED_PROXIES"):
        settings.validate_deployment()


def test_valid_trusted_proxy_cidrs_parse(monkeypatch):
    _production(monkeypatch, trusted_proxies="10.0.0.0/8, 127.0.0.1/32")
    settings.validate_deployment()
    assert len(settings.trusted_proxy_networks) == 2


def test_the_published_internal_token_is_refused_in_production(monkeypatch):
    """It is a header that exempts a request from every rate limit, including sign-in."""
    _production(monkeypatch, internal_api_token="dev-internal-render-token")
    with pytest.raises(RuntimeError, match="INTERNAL_API_TOKEN"):
        settings.validate_deployment()


def test_a_generated_internal_token_is_accepted(monkeypatch):
    _production(monkeypatch, internal_api_token="t" * 40)
    settings.validate_deployment()


def test_an_empty_internal_token_disables_the_bypass_instead_of_failing(monkeypatch):
    """No token means no exemption; that is safe, so startup must not refuse it."""
    _production(monkeypatch, internal_api_token="")
    settings.validate_deployment()
