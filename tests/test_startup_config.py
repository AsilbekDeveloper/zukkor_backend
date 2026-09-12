"""`app.main._warn_about_missing_production_config` (2026-09-13
prod-tayyorlik auditi) - production muhitida ixtiyoriy (lekin muhim)
sozlamalar bo'sh qoldirilsa, operatorga ko'rinadigan log ogohlantirishi
chiqishini tekshiradi. Hech narsani to'xtatmaydi - faqat log."""

import logging

from app.core.config import settings
from app.main import _warn_about_missing_production_config


def _clear_all_optional_settings(monkeypatch) -> None:
    for name in ("SENTRY_DSN", "GEMINI_API_KEY", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_BUCKET", "SMTP_USERNAME", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setattr(settings, name, "")


def test_warns_when_production_and_everything_is_missing(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    _clear_all_optional_settings(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="zukkor.startup"):
        _warn_about_missing_production_config()

    assert len(caplog.records) == 1
    message = caplog.records[0].message
    for expected in ("SENTRY_DSN", "GEMINI_API_KEY", "R2_*", "SMTP_USERNAME", "TELEGRAM_BOT_TOKEN"):
        assert expected in message


def test_does_not_warn_in_non_production_environments(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    _clear_all_optional_settings(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="zukkor.startup"):
        _warn_about_missing_production_config()

    assert caplog.records == []


def test_does_not_warn_when_everything_is_configured(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://example.sentry.io/1")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "id")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(settings, "R2_BUCKET", "bucket")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "user@example.com")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "token")

    with caplog.at_level(logging.WARNING, logger="zukkor.startup"):
        _warn_about_missing_production_config()

    assert caplog.records == []


def test_warns_about_only_the_specific_missing_setting(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://example.sentry.io/1")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "id")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(settings, "R2_BUCKET", "bucket")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "token")

    with caplog.at_level(logging.WARNING, logger="zukkor.startup"):
        _warn_about_missing_production_config()

    assert len(caplog.records) == 1
    assert "SMTP_USERNAME" in caplog.records[0].message
    assert "SENTRY_DSN" not in caplog.records[0].message
