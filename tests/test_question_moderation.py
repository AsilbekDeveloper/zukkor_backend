import json

import pytest

from app.services.gemini_client import GeminiCallError, GeminiResponse
from app.services.question_moderation import QuestionModerationError, moderate_question

_CATEGORIES = [(1, "Matematika"), (2, "Tarix")]


def _patch_gemini_response(monkeypatch, raw_text: str):
    async def _fake_call_gemini(prompt, *, response_schema, use_search=False):
        return GeminiResponse(text=raw_text, input_tokens=0, output_tokens=0)

    monkeypatch.setattr("app.services.question_moderation.call_gemini", _fake_call_gemini)


@pytest.mark.anyio
async def test_approved_response_returns_the_chosen_category(monkeypatch):
    _patch_gemini_response(monkeypatch, json.dumps({"is_approved": True, "rejection_reason": "", "category_id": 1}))

    result = await moderate_question(
        question_text="2x2?", options=["3", "4", "5", "6"], correct_option_index=1,
        requested_category_id=None, categories=_CATEGORIES,
    )

    assert result.is_approved is True
    assert result.category_id == 1
    assert result.rejection_reason is None


@pytest.mark.anyio
async def test_rejected_response_carries_the_reason_and_no_category(monkeypatch):
    _patch_gemini_response(
        monkeypatch,
        json.dumps({"is_approved": False, "rejection_reason": "Belgilangan javob noto'g'ri", "category_id": 2}),
    )

    result = await moderate_question(
        question_text="2x2?", options=["3", "4", "5", "6"], correct_option_index=0,
        requested_category_id=None, categories=_CATEGORIES,
    )

    assert result.is_approved is False
    assert result.rejection_reason == "Belgilangan javob noto'g'ri"
    assert result.category_id is None


@pytest.mark.anyio
async def test_rejects_when_ai_approves_but_hallucinates_an_unknown_category_id(monkeypatch):
    # Himoya qatlami - AI ro'yxatda yo'q ID qaytarsa, bazaga yaroqsiz FK
    # yozib qo'ymaslik uchun texnik xato ko'tarilishi kerak.
    _patch_gemini_response(monkeypatch, json.dumps({"is_approved": True, "rejection_reason": "", "category_id": 999}))

    with pytest.raises(QuestionModerationError):
        await moderate_question(
            question_text="2x2?", options=["3", "4", "5", "6"], correct_option_index=1,
            requested_category_id=None, categories=_CATEGORIES,
        )


@pytest.mark.anyio
async def test_raises_on_malformed_json_response(monkeypatch):
    _patch_gemini_response(monkeypatch, "bu JSON emas")

    with pytest.raises(QuestionModerationError):
        await moderate_question(
            question_text="2x2?", options=["3", "4", "5", "6"], correct_option_index=1,
            requested_category_id=None, categories=_CATEGORIES,
        )


@pytest.mark.anyio
async def test_raises_on_missing_required_field(monkeypatch):
    _patch_gemini_response(monkeypatch, json.dumps({"is_approved": True}))  # category_id yo'q

    with pytest.raises(QuestionModerationError):
        await moderate_question(
            question_text="2x2?", options=["3", "4", "5", "6"], correct_option_index=1,
            requested_category_id=None, categories=_CATEGORIES,
        )


@pytest.mark.anyio
async def test_wraps_gemini_call_error(monkeypatch):
    async def _fake_call_gemini(prompt, *, response_schema, use_search=False):
        raise GeminiCallError("AI xizmati hozircha sozlanmagan")

    monkeypatch.setattr("app.services.question_moderation.call_gemini", _fake_call_gemini)

    with pytest.raises(QuestionModerationError, match="AI xizmati hozircha sozlanmagan"):
        await moderate_question(
            question_text="2x2?", options=["3", "4", "5", "6"], correct_option_index=1,
            requested_category_id=None, categories=_CATEGORIES,
        )
