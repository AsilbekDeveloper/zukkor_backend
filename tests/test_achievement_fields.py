"""New PlayerStats fields for the achievements system (2026-09-06):
`total_wins` (Duel-win counter) and `best_rank_achieved` (permanent -
only ever improves, unlike the live-computed `rank`)."""

import pytest

from app.core.security import hash_password
from app.models.user import User
from app.routers.leaderboard import get_player_stats


async def _create_user(db, email: str, total_xp: int = 0, **kwargs) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"), total_xp=total_xp, **kwargs)
    db.add(user)
    await db.flush()
    return user


@pytest.mark.anyio
async def test_total_wins_defaults_to_zero_and_is_returned(db_session):
    user = await _create_user(db_session, "a@example.com")
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)

    assert result.total_wins == 0


@pytest.mark.anyio
async def test_best_rank_achieved_is_set_on_first_stats_fetch(db_session):
    # Alone in the DB - rank is always #1.
    user = await _create_user(db_session, "a@example.com")
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)

    assert result.rank == 1
    assert result.best_rank_achieved == 1


@pytest.mark.anyio
async def test_best_rank_achieved_only_improves_never_regresses(db_session):
    user = await _create_user(db_session, "a@example.com", total_xp=100)
    rival = await _create_user(db_session, "b@example.com", total_xp=50)
    await db_session.commit()

    # First fetch: user is #1 (100 > 50) - best_rank_achieved becomes 1.
    result = await get_player_stats(user.id, db=db_session, current_user=user)
    assert result.rank == 1
    assert result.best_rank_achieved == 1

    # Rival overtakes - user is now #2, but their best-ever stays #1.
    rival.total_xp = 500
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)
    assert result.rank == 2
    assert result.best_rank_achieved == 1  # unchanged - still their all-time best


@pytest.mark.anyio
async def test_best_rank_achieved_updates_when_a_new_best_is_reached(db_session):
    leader = await _create_user(db_session, "leader@example.com", total_xp=1000)
    user = await _create_user(db_session, "a@example.com", total_xp=100)
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)
    assert result.rank == 2
    assert result.best_rank_achieved == 2

    # User overtakes the leader - a new, better best-ever rank.
    user.total_xp = 2000
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)
    assert result.rank == 1
    assert result.best_rank_achieved == 1
