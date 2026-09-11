"""Direct unit tests for `app.services.streak.update_streak` - it's pure
logic on an in-memory `User` object (no DB access), so these run without
`db_session`/`anyio` at all. Written as part of a full audit of the streak
system (2026-09-11) - this function previously had NO dedicated test
coverage despite being the single source of truth for every user's streak.
"""

from datetime import datetime, timezone

from app.models.user import User
from app.services.streak import TASHKENT_OFFSET, update_streak


def _user(*, current_streak=0, longest_streak=0, last_played_at=None) -> User:
    return User(
        email="streak_test@example.com",
        current_streak=current_streak,
        longest_streak=longest_streak,
        last_played_at=last_played_at,
    )


def _at(local_date: str, hour: int = 12, minute: int = 0) -> datetime:
    """A tz-aware UTC datetime that lands on `local_date` (YYYY-MM-DD) at
    `hour:minute` TASHKENT local time - tests read in local terms, matching
    how a real player experiences "which day" they played on."""
    naive_local = datetime.fromisoformat(f"{local_date}T{hour:02d}:{minute:02d}:00")
    return naive_local.replace(tzinfo=timezone.utc) - TASHKENT_OFFSET


def test_first_ever_game_starts_streak_at_one():
    user = _user()
    update_streak(user, _at("2026-01-01"))
    assert user.current_streak == 1
    assert user.longest_streak == 1
    assert user.last_played_at is not None


def test_second_game_same_local_day_does_not_change_streak():
    user = _user(current_streak=1, longest_streak=1, last_played_at=_at("2026-01-01", hour=8))
    update_streak(user, _at("2026-01-01", hour=20))
    assert user.current_streak == 1


def test_consecutive_local_day_increments_streak():
    user = _user(current_streak=1, longest_streak=1, last_played_at=_at("2026-01-01"))
    update_streak(user, _at("2026-01-02"))
    assert user.current_streak == 2
    assert user.longest_streak == 2


def test_gap_of_more_than_one_day_resets_to_one():
    user = _user(current_streak=5, longest_streak=5, last_played_at=_at("2026-01-01"))
    update_streak(user, _at("2026-01-03"))
    assert user.current_streak == 1
    # Lifetime record must never drop, even though the current run reset.
    assert user.longest_streak == 5


def test_longest_streak_is_never_decreased_by_a_reset():
    user = _user(current_streak=10, longest_streak=10, last_played_at=_at("2026-01-01"))
    update_streak(user, _at("2026-01-05"))
    assert user.current_streak == 1
    assert user.longest_streak == 10


def test_day_boundary_uses_tashkent_time_not_utc():
    # 23:30 and the following 00:30, both TASHKENT-local, are only an hour
    # apart but cross a LOCAL midnight - in UTC they're both still
    # 2026-01-01 (Tashkent is UTC+5, so 00:30 local on the 2nd is only
    # 19:30 UTC on the 1st). A UTC-naive day comparison would call this
    # "the same day" and wrongly leave the streak unchanged; the correct,
    # Tashkent-aware comparison must treat it as the next day.
    user = _user(current_streak=1, longest_streak=1, last_played_at=_at("2026-01-01", hour=23, minute=30))
    update_streak(user, _at("2026-01-02", hour=0, minute=30))
    assert user.current_streak == 2


def test_naive_datetime_is_treated_as_utc():
    # `played_at` without tzinfo shouldn't normally happen (every real
    # caller uses `datetime.now(timezone.utc)`), but the function defends
    # against it rather than crashing or silently miscomputing.
    user = _user(current_streak=1, longest_streak=1, last_played_at=_at("2026-01-01"))
    naive_next_day = _at("2026-01-02").replace(tzinfo=None)
    update_streak(user, naive_next_day)
    assert user.current_streak == 2


def test_last_played_at_never_moves_backward():
    # Regression test: Solo/Duel/Lobby each call `update_streak` from
    # their own DB transaction, so two games for the same user can commit
    # out of chronological order (e.g. two devices on one account). A
    # later-committing call with an EARLIER `played_at` than what's
    # already stored must not regress `last_played_at` - that would
    # corrupt the next day's diff_days calculation and could silently
    # break a streak that was never actually missed.
    later = _at("2026-01-05", hour=12)
    earlier = _at("2026-01-04", hour=12)
    user = _user(current_streak=3, longest_streak=3, last_played_at=later)
    update_streak(user, earlier)
    assert user.last_played_at == later
