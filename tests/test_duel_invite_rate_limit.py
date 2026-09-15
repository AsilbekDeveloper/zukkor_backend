"""Duel taklifi spam-himoyasining sof (WebSocket/DB'siz) mantiqi -
umumiy chastota limiti va rad etilgandan keyingi cooldown."""

import time

import pytest

from app.routers import duel_ws


@pytest.fixture(autouse=True)
def _clear_state():
    # Har bir test boshqa testning holatidan ta'sirlanmasligi uchun -
    # modul darajasidagi xotira jadvallarini tozalaymiz.
    duel_ws._invite_timestamps.clear()
    duel_ws._decline_cooldown_until.clear()
    yield
    duel_ws._invite_timestamps.clear()
    duel_ws._decline_cooldown_until.clear()


def test_allows_up_to_the_limit_then_blocks(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    assert duel_ws._is_invite_rate_limited("u1") is False
    duel_ws._record_invite_sent("u1")
    assert duel_ws._is_invite_rate_limited("u1") is False
    duel_ws._record_invite_sent("u1")
    # 2/2 - endi limitga yetdi
    assert duel_ws._is_invite_rate_limited("u1") is True


def test_rate_limit_is_per_sender(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._record_invite_sent("u1")
    duel_ws._record_invite_sent("u1")
    assert duel_ws._is_invite_rate_limited("u1") is True
    # Boshqa yuboruvchi hali ta'sirlanmagan
    assert duel_ws._is_invite_rate_limited("u2") is False


def test_rate_limit_window_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._record_invite_sent("u1")
    duel_ws._record_invite_sent("u1")
    assert duel_ws._is_invite_rate_limited("u1") is True

    now[0] += duel_ws._INVITE_WINDOW_SECONDS + 1
    assert duel_ws._is_invite_rate_limited("u1") is False


def test_decline_cooldown_blocks_same_pair_only(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._start_decline_cooldown("u1", "u2")
    assert duel_ws._is_in_decline_cooldown("u1", "u2") is True
    # Boshqa juftlik ta'sirlanmagan
    assert duel_ws._is_in_decline_cooldown("u1", "u3") is False
    assert duel_ws._is_in_decline_cooldown("u2", "u1") is False


def test_decline_cooldown_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._start_decline_cooldown("u1", "u2")
    now[0] += duel_ws._DECLINE_COOLDOWN_SECONDS + 1
    assert duel_ws._is_in_decline_cooldown("u1", "u2") is False


# --- _cleanup_stale_rate_limit_entries (2026-09-16, unumdorlik auditi:
# bu ikkita dict avval hech qachon o'zi kichraymasdi - kalitlar
# muddati o'tgandan keyin ham abadiy qolib ketardi) ---


def test_cleanup_removes_an_invite_key_whose_entire_window_has_expired(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._record_invite_sent("u1")
    now[0] += duel_ws._INVITE_WINDOW_SECONDS + 1  # oynadagi yagona yozuv ham eskirdi

    duel_ws._cleanup_stale_rate_limit_entries()

    assert "u1" not in duel_ws._invite_timestamps


def test_cleanup_keeps_an_invite_key_with_a_still_recent_timestamp(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._record_invite_sent("u1")
    now[0] += 5  # oyna hali tugamagan

    duel_ws._cleanup_stale_rate_limit_entries()

    assert "u1" in duel_ws._invite_timestamps


def test_cleanup_removes_an_expired_decline_cooldown_pair(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._start_decline_cooldown("u1", "u2")
    now[0] += duel_ws._DECLINE_COOLDOWN_SECONDS + 1

    duel_ws._cleanup_stale_rate_limit_entries()

    assert ("u1", "u2") not in duel_ws._decline_cooldown_until


def test_cleanup_keeps_a_decline_cooldown_pair_still_in_effect(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._start_decline_cooldown("u1", "u2")
    now[0] += 5

    duel_ws._cleanup_stale_rate_limit_entries()

    assert ("u1", "u2") in duel_ws._decline_cooldown_until


def test_cleanup_does_not_disturb_rate_limiting_still_in_progress(monkeypatch):
    # Ikkala yozuv ham hali oynada - tozalash kalitni ham, ichidagi
    # yozuvlarni ham buzmasligi kerak.
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    duel_ws._record_invite_sent("u1")
    now[0] += duel_ws._INVITE_WINDOW_SECONDS - 5
    duel_ws._record_invite_sent("u1")  # hali eskisi ham oynada

    duel_ws._cleanup_stale_rate_limit_entries()

    assert "u1" in duel_ws._invite_timestamps
    assert len(duel_ws._invite_timestamps["u1"]) == 2
    # Ikkalasi ham hisobga kiradi - limit (2) allaqachon yetgan.
    assert duel_ws._is_invite_rate_limited("u1") is True
