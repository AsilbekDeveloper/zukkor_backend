import pytest

from app.core.security import hash_password
from app.models.friendship import Friendship
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.ai_quiz import discover_quizzes, search_discover_quizzes


async def _create_user(db, email: str, username: str | None = None) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"), username=username)
    db.add(user)
    await db.flush()
    return user


async def _create_owned_category(
    db, owner: User, visibility: str, name: str = "Quiz", source: str = "manual", topic_category_id: int | None = None
) -> Category:
    category = Category(
        name=name, icon_name="sparkle", color_key="coral", is_active=True,
        owner_user_id=owner.id, source=source, visibility=visibility, topic_category_id=topic_category_id,
    )
    db.add(category)
    await db.flush()
    db.add(
        Question(
            category_id=category.id, question_text="Savol?", options=["a", "b", "c", "d"],
            correct_option_index=0, is_active=True,
        )
    )
    await db.commit()
    await db.refresh(category)
    return category


async def _make_friends(db, user_a: User, user_b: User) -> None:
    db.add(Friendship(user_id=user_a.id, friend_id=user_b.id))
    db.add(Friendship(user_id=user_b.id, friend_id=user_a.id))
    await db.commit()


@pytest.mark.anyio
async def test_public_quiz_from_a_stranger_appears_in_discover(db_session):
    owner = await _create_user(db_session, "owner1@example.com", username="ali")
    viewer = await _create_user(db_session, "viewer1@example.com")
    await _create_owned_category(db_session, owner, visibility="public", name="Ali's Quiz")

    results = await discover_quizzes(current_user=viewer, db=db_session)

    assert len(results) == 1
    assert results[0].name == "Ali's Quiz"
    assert results[0].owner_user_id == owner.id
    assert results[0].owner_username == "ali"


@pytest.mark.anyio
async def test_private_quiz_never_appears_in_discover(db_session):
    owner = await _create_user(db_session, "owner2@example.com")
    viewer = await _create_user(db_session, "viewer2@example.com")
    await _create_owned_category(db_session, owner, visibility="private")

    results = await discover_quizzes(current_user=viewer, db=db_session)
    assert results == []


@pytest.mark.anyio
async def test_friends_only_quiz_appears_only_for_actual_friends(db_session):
    owner = await _create_user(db_session, "owner3@example.com")
    friend = await _create_user(db_session, "friend3@example.com")
    stranger = await _create_user(db_session, "stranger3@example.com")
    await _make_friends(db_session, owner, friend)
    await _create_owned_category(db_session, owner, visibility="friends")

    friend_results = await discover_quizzes(current_user=friend, db=db_session)
    stranger_results = await discover_quizzes(current_user=stranger, db=db_session)

    assert len(friend_results) == 1
    assert stranger_results == []


@pytest.mark.anyio
async def test_own_quizzes_never_appear_in_discover(db_session):
    owner = await _create_user(db_session, "owner4@example.com")
    await _create_owned_category(db_session, owner, visibility="public")

    results = await discover_quizzes(current_user=owner, db=db_session)
    assert results == []


@pytest.mark.anyio
async def test_global_official_categories_never_appear_in_discover(db_session):
    viewer = await _create_user(db_session, "viewer5@example.com")
    db_session.add(Category(name="Math", icon_name="calculator", color_key="coral", is_active=True))
    await db_session.commit()

    results = await discover_quizzes(current_user=viewer, db=db_session)
    assert results == []


@pytest.mark.anyio
async def test_search_matches_by_name_within_visible_quizzes_only(db_session):
    owner = await _create_user(db_session, "owner6@example.com")
    viewer = await _create_user(db_session, "viewer6@example.com")
    await _create_owned_category(db_session, owner, visibility="public", name="History Trivia")
    await _create_owned_category(db_session, owner, visibility="private", name="History Secrets")
    await _create_owned_category(db_session, owner, visibility="public", name="Sports Quiz")

    results = await search_discover_quizzes(q="history", current_user=viewer, db=db_session)

    assert len(results) == 1
    assert results[0].name == "History Trivia"


@pytest.mark.anyio
async def test_search_with_blank_query_returns_nothing(db_session):
    owner = await _create_user(db_session, "owner7@example.com")
    viewer = await _create_user(db_session, "viewer7@example.com")
    await _create_owned_category(db_session, owner, visibility="public")

    results = await search_discover_quizzes(q="   ", current_user=viewer, db=db_session)
    assert results == []


# --- category_id filter (2026-08-28) ---


@pytest.mark.anyio
async def test_discover_category_filter_only_returns_matching_topic(db_session):
    owner = await _create_user(db_session, "owner8@example.com")
    viewer = await _create_user(db_session, "viewer8@example.com")
    sport = Category(name="Sport", icon_name="ball", color_key="teal", is_active=True)
    history = Category(name="Tarix", icon_name="book", color_key="terra", is_active=True)
    db_session.add_all([sport, history])
    await db_session.commit()
    await db_session.refresh(sport)
    await db_session.refresh(history)

    await _create_owned_category(db_session, owner, visibility="public", name="Futbol", topic_category_id=sport.id)
    await _create_owned_category(db_session, owner, visibility="public", name="Urush", topic_category_id=history.id)
    await _create_owned_category(db_session, owner, visibility="public", name="Tegsiz")

    results = await discover_quizzes(category_id=sport.id, current_user=viewer, db=db_session)

    assert len(results) == 1
    assert results[0].name == "Futbol"
    assert results[0].topic_category_id == sport.id
    assert results[0].topic_category_name == "Sport"


@pytest.mark.anyio
async def test_discover_search_can_be_combined_with_category_filter(db_session):
    owner = await _create_user(db_session, "owner9@example.com")
    viewer = await _create_user(db_session, "viewer9@example.com")
    sport = Category(name="Sport", icon_name="ball", color_key="teal", is_active=True)
    db_session.add(sport)
    await db_session.commit()
    await db_session.refresh(sport)

    await _create_owned_category(db_session, owner, visibility="public", name="Futbol tarixi", topic_category_id=sport.id)
    await _create_owned_category(db_session, owner, visibility="public", name="Futbol qoidalari")

    results = await search_discover_quizzes(q="futbol", category_id=sport.id, current_user=viewer, db=db_session)

    assert len(results) == 1
    assert results[0].name == "Futbol tarixi"
