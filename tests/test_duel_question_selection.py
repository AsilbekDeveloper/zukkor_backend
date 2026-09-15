"""`duel_engine._pick_questions` - 2026-09-16, unumdorlik auditi:
`ORDER BY RANDOM()` o'rniga ID'larni olib, Python'da `random.sample`
bilan tanlab, so'ng `IN (...)` bilan to'liq qatorlarni olish."""

import pytest
from sqlalchemy import select

from app.models.quiz import Category, Question
from app.services.duel_engine import _pick_questions


async def _create_category(db, name="Test") -> Category:
    category = Category(name=name, icon_name="star", color_key="coral", is_active=True)
    db.add(category)
    await db.flush()
    return category


async def _create_questions(db, category_id: int, count: int, *, active: bool = True) -> list[Question]:
    questions = [
        Question(
            category_id=category_id,
            question_text=f"Savol {i}?",
            options=["a", "b", "c", "d"],
            correct_option_index=0,
            is_active=active,
        )
        for i in range(count)
    ]
    db.add_all(questions)
    await db.flush()
    return questions


@pytest.mark.anyio
async def test_returns_exactly_the_requested_count_when_enough_are_available(db_session):
    category = await _create_category(db_session)
    await _create_questions(db_session, category.id, 10)
    await db_session.commit()

    picked = await _pick_questions(db_session, category.id, 5)

    assert len(picked) == 5


@pytest.mark.anyio
async def test_never_picks_the_same_question_twice(db_session):
    category = await _create_category(db_session)
    await _create_questions(db_session, category.id, 10)
    await db_session.commit()

    picked = await _pick_questions(db_session, category.id, 10)

    assert len({q.id for q in picked}) == 10


@pytest.mark.anyio
async def test_caps_at_the_available_count_when_fewer_questions_exist(db_session):
    category = await _create_category(db_session)
    await _create_questions(db_session, category.id, 3)
    await db_session.commit()

    picked = await _pick_questions(db_session, category.id, 10)

    assert len(picked) == 3


@pytest.mark.anyio
async def test_returns_empty_list_for_a_category_with_no_questions(db_session):
    category = await _create_category(db_session)
    await db_session.commit()

    picked = await _pick_questions(db_session, category.id, 5)

    assert picked == []


@pytest.mark.anyio
async def test_ignores_inactive_questions(db_session):
    category = await _create_category(db_session)
    await _create_questions(db_session, category.id, 3, active=True)
    await _create_questions(db_session, category.id, 5, active=False)
    await db_session.commit()

    picked = await _pick_questions(db_session, category.id, 10)

    assert len(picked) == 3
    assert all(q.is_active for q in picked)


@pytest.mark.anyio
async def test_ignores_questions_from_a_different_category(db_session):
    category_a = await _create_category(db_session, "A")
    category_b = await _create_category(db_session, "B")
    await _create_questions(db_session, category_a.id, 3)
    await _create_questions(db_session, category_b.id, 3)
    await db_session.commit()

    picked = await _pick_questions(db_session, category_a.id, 10)

    assert len(picked) == 3
    assert {q.category_id for q in picked} == {category_a.id}
