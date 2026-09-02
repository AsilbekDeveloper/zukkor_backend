import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import hash_password
from app.models.ai_quiz_job import AiQuizGenerationJob
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers import ai_quiz
from app.routers.ai_quiz import generate_ai_quiz_async, get_generation_job
from app.services.ai_quiz_generation import QuizGenerationError

FAKE_QUESTIONS = [
    {"question_text": "1+1 nechaga teng?", "options": ["1", "2", "3", "4"], "correct_option_index": 1},
]


class _FakePushRecorder:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, db, user_id, title, body):
        self.calls.append((user_id, title, body))


@pytest.fixture
async def _isolated_session_maker(monkeypatch):
    # _run_generation_job writes via the module-level AsyncSessionLocal -
    # point that at an isolated in-memory SQLite engine (same pattern as
    # test_duel_forfeit.py / test_notification_cleanup.py).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ai_quiz, "AsyncSessionLocal", session_maker)
    yield session_maker
    await engine.dispose()


@pytest.fixture
def _fake_push(monkeypatch):
    recorder = _FakePushRecorder()
    monkeypatch.setattr(ai_quiz, "send_push_to_user", recorder)
    return recorder


async def _create_user(db, email="user@example.com") -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"))
    db.add(user)
    await db.flush()
    return user


async def _run_scheduled_tasks(background_tasks: BackgroundTasks) -> None:
    for task in background_tasks.tasks:
        await task()


@pytest.mark.anyio
async def test_topic_job_starts_pending_then_completes(_isolated_session_maker, _fake_push, monkeypatch):
    async def _fake_generate(topic, instruction, count):
        return FAKE_QUESTIONS

    monkeypatch.setattr(ai_quiz, "generate_questions_from_topic", _fake_generate)
    background_tasks = BackgroundTasks()

    # Endpoint's own `db` and the background task's AsyncSessionLocal must
    # point at the SAME database, or the job the endpoint writes is
    # invisible to the background task that's supposed to finish it.
    async with _isolated_session_maker() as db:
        user = await _create_user(db)
        await db.commit()

        started = await generate_ai_quiz_async(
            background_tasks,
            file=None,
            instruction=None,
            topic="Matematika",
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db,
        )

        job = await db.get(AiQuizGenerationJob, started.job_id)
        assert job is not None
        assert job.status == "pending"

    await _run_scheduled_tasks(background_tasks)

    async with _isolated_session_maker() as bg_db:
        finished_job = await bg_db.get(AiQuizGenerationJob, started.job_id)
        assert finished_job.status == "completed"
        assert finished_job.category_id is not None
        category = await bg_db.get(Category, finished_job.category_id)
        assert category.owner_user_id == user.id
        questions = (
            await bg_db.execute(select(Question).where(Question.category_id == category.id))
        ).scalars().all()
        assert len(questions) == 1

    assert _fake_push.calls
    push_user_id, push_title, _ = _fake_push.calls[0]
    assert push_user_id == user.id
    assert push_title == "Quiz tayyor!"


@pytest.mark.anyio
async def test_job_failure_is_recorded_and_pushed(_isolated_session_maker, _fake_push, monkeypatch):
    async def _boom(topic, instruction, count):
        raise QuizGenerationError("AI xizmati hozircha sozlanmagan")

    monkeypatch.setattr(ai_quiz, "generate_questions_from_topic", _boom)
    background_tasks = BackgroundTasks()

    async with _isolated_session_maker() as db:
        user = await _create_user(db)
        await db.commit()

        started = await generate_ai_quiz_async(
            background_tasks,
            file=None,
            instruction=None,
            topic="Tarix",
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db,
        )
    await _run_scheduled_tasks(background_tasks)

    async with _isolated_session_maker() as bg_db:
        job = await bg_db.get(AiQuizGenerationJob, started.job_id)
        assert job.status == "failed"
        assert job.error_message == "AI xizmati hozircha sozlanmagan"

    assert _fake_push.calls
    _, push_title, _ = _fake_push.calls[0]
    assert push_title == "Quiz yaratib bo'lmadi"


@pytest.mark.anyio
async def test_generate_async_rejects_when_neither_file_nor_topic(db_session):
    user = await _create_user(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await generate_ai_quiz_async(
            BackgroundTasks(),
            file=None,
            instruction=None,
            topic=None,
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_get_generation_job_hides_other_users_jobs(db_session):
    owner = await _create_user(db_session, "owner@example.com")
    stranger = await _create_user(db_session, "stranger@example.com")
    job = AiQuizGenerationJob(user_id=owner.id, status="pending", question_count=5)
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with pytest.raises(HTTPException) as exc_info:
        await get_generation_job(job.id, current_user=stranger, db=db_session)
    assert exc_info.value.status_code == 404

    # Owner can see it.
    result = await get_generation_job(job.id, current_user=owner, db=db_session)
    assert result.status == "pending"
    assert result.quiz is None
