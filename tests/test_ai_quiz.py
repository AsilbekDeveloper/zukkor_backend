import io

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from app.core.security import hash_password
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.ai_quiz import (
    create_manual_quiz,
    delete_ai_quiz,
    generate_ai_quiz,
    list_my_ai_quizzes,
    list_user_quizzes,
    update_quiz_visibility,
)
from app.routers.categories import list_categories
from app.routers.quiz import start_quiz
from app.schemas.ai_quiz import ManualQuestionIn, ManualQuizCreate, VisibilityUpdate
from app.schemas.quiz import QuizStartRequest
from app.services.ai_quiz_generation import QuizGenerationError, _validate_questions
from app.services.document_text import UnsupportedDocumentError, extract_text

FAKE_QUESTIONS = [
    {"question_text": "1+1 nechaga teng?", "options": ["1", "2", "3", "4"], "correct_option_index": 1},
    {"question_text": "2+2 nechaga teng?", "options": ["3", "4", "5", "6"], "correct_option_index": 1},
]


async def _fake_generate_questions(text, instruction, question_count):
    return FAKE_QUESTIONS[:question_count]


async def _fake_generate_questions_from_topic(topic, question_count):
    return FAKE_QUESTIONS[:question_count]


def _upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


async def _create_user(db, email="user@example.com") -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"))
    db.add(user)
    await db.flush()
    return user


# --- document_text ---


def test_extract_text_from_txt():
    assert extract_text("notes.txt", "salom dunyo".encode("utf-8")) == "salom dunyo"


def test_extract_text_rejects_unsupported_extension():
    with pytest.raises(UnsupportedDocumentError):
        extract_text("file.exe", b"whatever")


def test_extract_text_rejects_corrupt_pdf():
    with pytest.raises(UnsupportedDocumentError):
        extract_text("file.pdf", b"not a real pdf")


# --- ai_quiz_generation validation ---


def test_validate_questions_accepts_well_formed_list():
    result = _validate_questions(FAKE_QUESTIONS)
    assert len(result) == 2


def test_validate_questions_drops_malformed_entries():
    raw = [
        {"question_text": "ok?", "options": ["a", "b", "c", "d"], "correct_option_index": 0},
        {"question_text": "", "options": ["a", "b", "c", "d"], "correct_option_index": 0},  # bo'sh savol
        {"question_text": "ok2?", "options": ["a", "b", "c"], "correct_option_index": 0},  # 3 ta variant
        {"question_text": "ok3?", "options": ["a", "b", "c", "d"], "correct_option_index": 5},  # chegaradan tashqari
        "not a dict",
    ]
    result = _validate_questions(raw)
    assert len(result) == 1
    assert result[0]["question_text"] == "ok?"


@pytest.mark.anyio
async def test_generate_questions_fails_fast_without_api_key():
    from app.services.ai_quiz_generation import generate_questions

    with pytest.raises(QuizGenerationError):
        await generate_questions("matn", "5 ta savol", 5)


@pytest.mark.anyio
async def test_generate_questions_from_topic_fails_fast_without_api_key():
    from app.services.ai_quiz_generation import generate_questions_from_topic

    with pytest.raises(QuizGenerationError):
        await generate_questions_from_topic("2-jahon tarixi", 5)


# --- router: generate/list/delete ---


@pytest.mark.anyio
async def test_generate_ai_quiz_creates_private_category(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    user = await _create_user(db_session)

    result = await generate_ai_quiz(
        file=_upload("kitob.txt", b"kitobning matni"),
        instruction="hammasidan 2 ta savol",
        topic=None,
        question_count=2,
        current_user=user,
        db=db_session,
    )

    assert result.question_count == 2
    assert result.name == "kitob"

    category = await db_session.get(Category, result.id)
    assert category.owner_user_id == user.id

    questions = (
        await db_session.execute(select(Question).where(Question.category_id == result.id))
    ).scalars().all()
    assert len(questions) == 2


@pytest.mark.anyio
async def test_generate_ai_quiz_instruction_is_optional_with_file(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    user = await _create_user(db_session)

    result = await generate_ai_quiz(
        file=_upload("kitob.txt", b"kitobning matni"),
        instruction=None,
        topic=None,
        question_count=2,
        current_user=user,
        db=db_session,
    )
    assert result.question_count == 2


@pytest.mark.anyio
async def test_generate_ai_quiz_topic_only_uses_web_search(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions_from_topic", _fake_generate_questions_from_topic)
    user = await _create_user(db_session)

    result = await generate_ai_quiz(
        file=None,
        instruction=None,
        topic="2-jahon tarixidan savollar, o'rtacha qiyinchilik",
        question_count=2,
        current_user=user,
        db=db_session,
    )

    assert result.question_count == 2
    assert result.name == "2-jahon tarixidan savollar, o'rtacha qiyinchilik"

    category = await db_session.get(Category, result.id)
    assert category.owner_user_id == user.id


@pytest.mark.anyio
async def test_generate_ai_quiz_rejects_when_neither_file_nor_topic_given(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    user = await _create_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await generate_ai_quiz(
            file=None,
            instruction=None,
            topic=None,
            question_count=2,
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_generate_ai_quiz_rejects_unsupported_file(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    user = await _create_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await generate_ai_quiz(
            file=_upload("virus.exe", b"whatever"),
            instruction="savollar",
            topic=None,
            question_count=2,
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 400




@pytest.mark.anyio
async def test_list_my_ai_quizzes_only_returns_own_active_quizzes(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    user_a = await _create_user(db_session, "a@example.com")
    user_b = await _create_user(db_session, "b@example.com")

    await generate_ai_quiz(
        file=_upload("a-kitob.txt", b"matn"),
        instruction="x",
        topic=None,
        question_count=2,
        current_user=user_a,
        db=db_session,
    )
    await generate_ai_quiz(
        file=_upload("b-kitob.txt", b"matn"),
        instruction="x",
        topic=None,
        question_count=2,
        current_user=user_b,
        db=db_session,
    )

    result = await list_my_ai_quizzes(current_user=user_a, db=db_session)
    assert len(result) == 1
    assert result[0].name == "a-kitob"


@pytest.mark.anyio
async def test_delete_ai_quiz_soft_deletes_and_is_owner_only(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    owner = await _create_user(db_session, "owner@example.com")
    other = await _create_user(db_session, "other@example.com")

    created = await generate_ai_quiz(
        file=_upload("kitob.txt", b"matn"),
        instruction="x",
        topic=None,
        question_count=2,
        current_user=owner,
        db=db_session,
    )

    with pytest.raises(HTTPException) as exc_info:
        await delete_ai_quiz(quiz_id=created.id, current_user=other, db=db_session)
    assert exc_info.value.status_code == 404

    await delete_ai_quiz(quiz_id=created.id, current_user=owner, db=db_session)

    category = await db_session.get(Category, created.id)
    assert category.is_active is False

    remaining = await list_my_ai_quizzes(current_user=owner, db=db_session)
    assert remaining == []


@pytest.mark.anyio
async def test_other_user_cannot_start_quiz_on_someone_elses_private_category(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    owner = await _create_user(db_session, "owner2@example.com")
    other = await _create_user(db_session, "other2@example.com")

    created = await generate_ai_quiz(
        file=_upload("kitob.txt", b"matn"),
        instruction="x",
        topic=None,
        question_count=2,
        current_user=owner,
        db=db_session,
    )

    with pytest.raises(HTTPException) as exc_info:
        await start_quiz(QuizStartRequest(category_id=created.id, question_count=2), current_user=other, db=db_session)
    assert exc_info.value.status_code == 404

    # Egasi esa muammosiz boshlay oladi.
    started = await start_quiz(
        QuizStartRequest(category_id=created.id, question_count=2), current_user=owner, db=db_session
    )
    assert started.question is not None


@pytest.mark.anyio
async def test_private_ai_categories_never_appear_in_public_categories_list(db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ai_quiz.generate_questions", _fake_generate_questions)
    user = await _create_user(db_session)

    db_session.add(Category(name="Math", icon_name="calculator", color_key="coral", is_active=True))
    await generate_ai_quiz(
        file=_upload("kitob.txt", b"matn"),
        instruction="x",
        topic=None,
        question_count=2,
        current_user=user,
        db=db_session,
    )

    public_categories = await list_categories(db=db_session)
    assert len(public_categories) == 1
    assert public_categories[0].name == "Math"


# --- manual quiz creation ---


def _manual_payload(name="Qo'lda quiz", count=2):
    return ManualQuizCreate(
        name=name,
        questions=[
            ManualQuestionIn(question_text=f"Savol {i}?", options=["a", "b", "c", "d"], correct_option_index=0)
            for i in range(count)
        ],
    )


@pytest.mark.anyio
async def test_create_manual_quiz_creates_private_category(db_session):
    user = await _create_user(db_session)

    result = await create_manual_quiz(payload=_manual_payload(), current_user=user, db=db_session)

    assert result.question_count == 2
    assert result.source == "manual"
    assert result.visibility == "private"

    category = await db_session.get(Category, result.id)
    assert category.owner_user_id == user.id


@pytest.mark.anyio
async def test_create_manual_quiz_rejects_malformed_questions(db_session):
    user = await _create_user(db_session)
    payload = ManualQuizCreate(
        name="Yaroqsiz",
        questions=[ManualQuestionIn(question_text="", options=["a", "b", "c"], correct_option_index=0)],
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_manual_quiz(payload=payload, current_user=user, db=db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_create_manual_quiz_rejects_blank_name(db_session):
    user = await _create_user(db_session)
    payload = _manual_payload(name="   ")

    with pytest.raises(HTTPException) as exc_info:
        await create_manual_quiz(payload=payload, current_user=user, db=db_session)
    assert exc_info.value.status_code == 400


# --- visibility updates ---


@pytest.mark.anyio
async def test_update_quiz_visibility_changes_it(db_session):
    user = await _create_user(db_session)
    created = await create_manual_quiz(payload=_manual_payload(), current_user=user, db=db_session)

    updated = await update_quiz_visibility(
        quiz_id=created.id, payload=VisibilityUpdate(visibility="public"), current_user=user, db=db_session
    )
    assert updated.visibility == "public"


@pytest.mark.anyio
async def test_update_quiz_visibility_rejects_non_owner(db_session):
    owner = await _create_user(db_session, "owner_v@example.com")
    other = await _create_user(db_session, "other_v@example.com")
    created = await create_manual_quiz(payload=_manual_payload(), current_user=owner, db=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await update_quiz_visibility(
            quiz_id=created.id, payload=VisibilityUpdate(visibility="public"), current_user=other, db=db_session
        )
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_update_quiz_visibility_rejects_invalid_value(db_session):
    user = await _create_user(db_session)
    created = await create_manual_quiz(payload=_manual_payload(), current_user=user, db=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await update_quiz_visibility(
            quiz_id=created.id, payload=VisibilityUpdate(visibility="everyone"), current_user=user, db=db_session
        )
    assert exc_info.value.status_code == 400


# --- GET /ai-quiz/users/{user_id} ---


@pytest.mark.anyio
async def test_list_user_quizzes_shows_only_public_to_strangers(db_session):
    owner = await _create_user(db_session, "owner_u1@example.com")
    stranger = await _create_user(db_session, "stranger_u1@example.com")

    public_quiz = await create_manual_quiz(payload=_manual_payload("Ommaviy"), current_user=owner, db=db_session)
    await update_quiz_visibility(
        quiz_id=public_quiz.id, payload=VisibilityUpdate(visibility="public"), current_user=owner, db=db_session
    )
    await create_manual_quiz(payload=_manual_payload("Shaxsiy"), current_user=owner, db=db_session)

    result = await list_user_quizzes(user_id=owner.id, current_user=stranger, db=db_session)
    assert len(result) == 1
    assert result[0].name == "Ommaviy"


@pytest.mark.anyio
async def test_list_user_quizzes_owner_sees_everything_on_own_page(db_session):
    owner = await _create_user(db_session, "owner_u2@example.com")
    await create_manual_quiz(payload=_manual_payload("A"), current_user=owner, db=db_session)
    await create_manual_quiz(payload=_manual_payload("B"), current_user=owner, db=db_session)

    result = await list_user_quizzes(user_id=owner.id, current_user=owner, db=db_session)
    assert len(result) == 2
