"""call_gemini() vaqtinchalik xatolarda (tarmoq uzilishi, 429/5xx)
eksponensial kutish bilan qayta urinadi - bu shu xatti-harakatni sinaydi.
Bu mantiq oldin app/services/ai_quiz_generation.py ichida edi, endi barcha
AI-xususiyatlar (quiz generatsiya, savol moderatsiyasi) baham ko'radigan
app/services/gemini_client.py'ga chiqarilgan."""

import httpx
import pytest

from app.core.config import settings
from app.services import gemini_client
from app.services.gemini_client import GeminiCallError, call_gemini

_TRIVIAL_SCHEMA = {"type": "object", "properties": {}}

FAKE_SUCCESS_BODY = {
    "steps": [
        {
            "type": "model_output",
            "content": [{"type": "text", "text": "[]"}],
        }
    ]
}


def _make_response(status_code: int, json_body: dict) -> httpx.Response:
    return httpx.Response(status_code, json=json_body, request=httpx.Request("POST", "https://example.com"))


@pytest.fixture(autouse=True)
def _configure_api_key(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key-for-tests")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Testlar eksponensial kutishni haqiqatan ham kutmasin - faqat qayta
    # urinish sodir bo'lganini tekshiramiz, vaqtni emas.
    async def _instant_sleep(_seconds):
        return None

    monkeypatch.setattr(gemini_client.asyncio, "sleep", _instant_sleep)


class _FakeAsyncClient:
    """httpx.AsyncClient(...) o'rniga ishlatiladigan soxta klient - har bir
    chaqiruvda navbatdagi oldindan tayyorlangan javobni (yoki xatoni)
    qaytaradi/ko'taradi."""

    _responses: list

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _install_fake_client(monkeypatch, responses: list):
    fake_cls = type("_FakeAsyncClient", (_FakeAsyncClient,), {"_responses": responses})
    monkeypatch.setattr(gemini_client.httpx, "AsyncClient", fake_cls)


@pytest.mark.anyio
async def test_succeeds_immediately_when_first_attempt_is_ok(monkeypatch):
    _install_fake_client(monkeypatch, [_make_response(200, FAKE_SUCCESS_BODY)])
    result = await call_gemini("prompt", response_schema=_TRIVIAL_SCHEMA)
    assert result == "[]"


@pytest.mark.anyio
async def test_retries_after_a_network_error_then_succeeds(monkeypatch):
    _install_fake_client(
        monkeypatch,
        [httpx.ConnectError("bo'ldi", request=httpx.Request("POST", "https://example.com")), _make_response(200, FAKE_SUCCESS_BODY)],
    )
    result = await call_gemini("prompt", response_schema=_TRIVIAL_SCHEMA)
    assert result == "[]"


@pytest.mark.anyio
async def test_retries_after_a_503_then_succeeds(monkeypatch):
    _install_fake_client(
        monkeypatch,
        [_make_response(503, {"error": {"message": "overloaded"}}), _make_response(200, FAKE_SUCCESS_BODY)],
    )
    result = await call_gemini("prompt", response_schema=_TRIVIAL_SCHEMA)
    assert result == "[]"


@pytest.mark.anyio
async def test_retries_after_a_429_then_succeeds(monkeypatch):
    _install_fake_client(
        monkeypatch,
        [_make_response(429, {"error": {"message": "quota"}}), _make_response(200, FAKE_SUCCESS_BODY)],
    )
    result = await call_gemini("prompt", response_schema=_TRIVIAL_SCHEMA)
    assert result == "[]"


@pytest.mark.anyio
async def test_gives_up_after_max_attempts_of_persistent_5xx(monkeypatch):
    # _MAX_ATTEMPTS ta marta ketma-ket 500 qaytarilsa, oxir-oqibat xato
    # ko'tarilishi kerak - abadiy qayta urinmasligi kerak.
    responses = [_make_response(500, {"error": {"message": "down"}}) for _ in range(gemini_client._MAX_ATTEMPTS)]
    _install_fake_client(monkeypatch, responses)
    with pytest.raises(GeminiCallError):
        await call_gemini("prompt", response_schema=_TRIVIAL_SCHEMA)


@pytest.mark.anyio
async def test_does_not_retry_a_non_retryable_client_error(monkeypatch):
    # 400 kabi mijoz xatosi qayta urinishda ham o'zgarmaydi - faqat bitta
    # urinish qilinishi kerak (ro'yxatda faqat bitta javob bor).
    _install_fake_client(monkeypatch, [_make_response(400, {"error": {"message": "bad request"}})])
    with pytest.raises(GeminiCallError):
        await call_gemini("prompt", response_schema=_TRIVIAL_SCHEMA)
