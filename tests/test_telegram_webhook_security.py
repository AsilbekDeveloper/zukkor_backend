"""`POST /telegram/webhook` autentifikatsiyasi (2026-09-18, xavfsizlik
auditi): avval bu endpoint TO'LIQ autentifikatsiyasiz edi - istalgan
kishi Telegram'ni butunlay chetlab, to'g'ridan-to'g'ri soxta `Update`
yuborishi mumkin edi. Endi `X-Telegram-Bot-Api-Secret-Token` header
`settings.TELEGRAM_WEBHOOK_SECRET` bilan mos kelmasa (yoki bu sozlama
umuman bo'sh bo'lsa - "fail closed") 401 qaytaradi."""

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.routers.telegram import telegram_webhook


class _FakeWebhookRequest:
    """`telegram_webhook` faqat `.headers.get(...)` va `await .json()`
    ishlatadi - to'liq Starlette `Request` qurish shart emas."""

    def __init__(self, headers: dict[str, str], body: dict):
        self.headers = headers
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.anyio
async def test_webhook_rejects_a_request_with_no_secret_header(db_session, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "correct-secret")
    request = _FakeWebhookRequest(headers={}, body={})

    with pytest.raises(HTTPException) as exc_info:
        await telegram_webhook(request, db=db_session)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_webhook_rejects_a_request_with_the_wrong_secret(db_session, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "correct-secret")
    request = _FakeWebhookRequest(
        headers={"x-telegram-bot-api-secret-token": "wrong-secret"}, body={}
    )

    with pytest.raises(HTTPException) as exc_info:
        await telegram_webhook(request, db=db_session)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_webhook_accepts_a_request_with_the_correct_secret(db_session, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "correct-secret")
    request = _FakeWebhookRequest(
        headers={"x-telegram-bot-api-secret-token": "correct-secret"},
        body={"message": {"text": "/start", "from": {"id": 111}, "chat": {"id": 222}}},
    )

    result = await telegram_webhook(request, db=db_session)
    assert result == {"ok": True}


@pytest.mark.anyio
async def test_webhook_fails_closed_when_the_secret_is_not_configured(db_session, monkeypatch):
    # Sozlama umuman bo'sh bo'lsa (masalan deploy'da unutilgan) - HAMMA
    # so'rov, hatto to'g'ri ko'ringan header bilan ham, rad etiladi.
    # Webhook himoyasiz OCHIQ qolib ketmasligi kerak.
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
    request = _FakeWebhookRequest(headers={"x-telegram-bot-api-secret-token": ""}, body={})

    with pytest.raises(HTTPException) as exc_info:
        await telegram_webhook(request, db=db_session)
    assert exc_info.value.status_code == 401
