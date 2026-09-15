"""Gemini API'ning global kunlik "circuit breaker"i (2026-09-16, xavfsizlik
auditi) - `app/services/ai_usage_limiter.py`."""

import pytest

from app.services import ai_usage_limiter


@pytest.mark.anyio
async def test_reserve_daily_gemini_call_increments_and_returns_the_running_count(db_session):
    assert await ai_usage_limiter.reserve_daily_gemini_call(db_session) == 1
    assert await ai_usage_limiter.reserve_daily_gemini_call(db_session) == 2
    assert await ai_usage_limiter.reserve_daily_gemini_call(db_session) == 3


@pytest.mark.anyio
async def test_reserve_daily_gemini_call_raises_once_the_limit_is_reached(db_session, monkeypatch):
    monkeypatch.setattr(ai_usage_limiter.settings, "MAX_DAILY_GEMINI_CALLS", 3)

    for _ in range(3):
        await ai_usage_limiter.reserve_daily_gemini_call(db_session)

    with pytest.raises(ai_usage_limiter.DailyLimitExceeded):
        await ai_usage_limiter.reserve_daily_gemini_call(db_session)


@pytest.mark.anyio
async def test_reserve_daily_gemini_call_stays_blocked_on_repeated_calls_past_the_limit(db_session, monkeypatch):
    # Bloklangandan keyin yana urinish HAM rad etilishi kerak - bir marta
    # oshib ketgach hisoblagich "tiklanib" ketmasligini tekshiradi.
    monkeypatch.setattr(ai_usage_limiter.settings, "MAX_DAILY_GEMINI_CALLS", 1)

    await ai_usage_limiter.reserve_daily_gemini_call(db_session)
    with pytest.raises(ai_usage_limiter.DailyLimitExceeded):
        await ai_usage_limiter.reserve_daily_gemini_call(db_session)
    with pytest.raises(ai_usage_limiter.DailyLimitExceeded):
        await ai_usage_limiter.reserve_daily_gemini_call(db_session)


@pytest.mark.anyio
async def test_reserve_daily_gemini_call_zero_limit_blocks_the_very_first_call(db_session, monkeypatch):
    monkeypatch.setattr(ai_usage_limiter.settings, "MAX_DAILY_GEMINI_CALLS", 0)

    with pytest.raises(ai_usage_limiter.DailyLimitExceeded):
        await ai_usage_limiter.reserve_daily_gemini_call(db_session)


def test_tashkent_today_returns_an_iso_date_string():
    today = ai_usage_limiter._tashkent_today()
    assert len(today) == 10
    assert today.count("-") == 2
