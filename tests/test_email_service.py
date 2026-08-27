import pytest

from app.core.config import settings
from app.services.email import send_password_reset_email


@pytest.fixture(autouse=True)
def _reset_smtp_settings():
    # settings is a module-level singleton - each test mutates it directly,
    # so every test must start from (and restore) a known-empty baseline.
    original = (settings.SMTP_USERNAME, settings.SMTP_PASSWORD, settings.SMTP_FROM_EMAIL)
    settings.SMTP_USERNAME = ""
    settings.SMTP_PASSWORD = ""
    settings.SMTP_FROM_EMAIL = ""
    yield
    settings.SMTP_USERNAME, settings.SMTP_PASSWORD, settings.SMTP_FROM_EMAIL = original


@pytest.mark.anyio
async def test_returns_false_without_crashing_when_smtp_not_configured():
    sent = await send_password_reset_email("user@example.com", "123456")
    assert sent is False


class _FakeSmtp:
    sent_messages = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        self.username = username
        self.password = password

    def sendmail(self, from_addr, to_addrs, message):
        _FakeSmtp.sent_messages.append({"from": from_addr, "to": to_addrs, "message": message})


@pytest.mark.anyio
async def test_sends_via_smtp_when_configured(monkeypatch):
    settings.SMTP_USERNAME = "bot@gmail.com"
    settings.SMTP_PASSWORD = "app-password"
    _FakeSmtp.sent_messages = []
    monkeypatch.setattr("app.services.email.smtplib.SMTP", _FakeSmtp)

    sent = await send_password_reset_email("user@example.com", "482913")

    assert sent is True
    assert len(_FakeSmtp.sent_messages) == 1
    message = _FakeSmtp.sent_messages[0]
    assert message["from"] == "bot@gmail.com"
    assert message["to"] == ["user@example.com"]
    assert "482913" in message["message"]


@pytest.mark.anyio
async def test_smtp_failure_is_caught_and_reported_as_not_sent(monkeypatch):
    import smtplib

    settings.SMTP_USERNAME = "bot@gmail.com"
    settings.SMTP_PASSWORD = "app-password"

    class _FailingSmtp(_FakeSmtp):
        def login(self, username, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr("app.services.email.smtplib.SMTP", _FailingSmtp)

    sent = await send_password_reset_email("user@example.com", "482913")
    assert sent is False
