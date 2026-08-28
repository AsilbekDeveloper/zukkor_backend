from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.friendship import Friendship
from app.models.quiz import Category, Question
from app.models.user import User
from app.schemas.ai_quiz import AiQuizOut, DiscoverQuizOut, ManualQuizCreate, VisibilityUpdate
from app.services.ai_quiz_generation import QuizGenerationError, _validate_questions, generate_questions, generate_questions_from_topic
from app.services.document_text import UnsupportedDocumentError, extract_text
from app.services.quiz_access import can_access_category, is_friend

router = APIRouter()

MAX_UPLOAD_SIZE_BYTES = 15 * 1024 * 1024  # 15MB - kitob uchun yetarli
_UPLOAD_READ_CHUNK_BYTES = 256 * 1024
DEFAULT_QUESTION_COUNT = 10
MAX_QUESTION_COUNT = 20
MAX_TOPIC_LENGTH = 300
VALID_VISIBILITIES = {"private", "friends", "public"}


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    # Hajm chegarasini butun faylni o'qib bo'lgandan KEYIN emas, o'qish
    # jarayonida tekshiramiz (users.py'dagi avatar yuklash bilan bir xil
    # naqsh) - ataylab yuborilgan juda katta fayl xotiraga to'liq tushib
    # ulgurmasin deb.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fayl hajmi 15MB dan oshmasligi kerak")
        chunks.append(chunk)
    return b"".join(chunks)


def _question_count_subquery():
    return (
        select(Question.category_id, func.count(Question.id).label("cnt"))
        .where(Question.is_active.is_(True))
        .group_by(Question.category_id)
        .subquery()
    )


@router.post(
    "/generate",
    response_model=AiQuizOut,
    status_code=status.HTTP_201_CREATED,
    summary="Hujjatdan AI orqali quiz yaratish",
)
async def generate_ai_quiz(
    file: UploadFile | None = File(None),
    instruction: str | None = Form(None),
    topic: str | None = Form(None),
    question_count: int = Form(DEFAULT_QUESTION_COUNT),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    question_count = max(1, min(question_count, MAX_QUESTION_COUNT))
    instruction_clean = (instruction or "").strip()
    topic_clean = (topic or "").strip()[:MAX_TOPIC_LENGTH]
    has_file = file is not None and bool(file.filename)

    if not has_file and not topic_clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hujjat yuklang yoki mavzu kiriting",
        )

    if has_file:
        assert file is not None
        contents = await _read_limited(file, MAX_UPLOAD_SIZE_BYTES)
        try:
            text = extract_text(file.filename or "", contents)
        except UnsupportedDocumentError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        if not text.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hujjatdan matn topilmadi")

        try:
            questions = await generate_questions(text, instruction_clean, question_count)
        except QuizGenerationError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

        title = (file.filename or "AI Quiz").rsplit(".", 1)[0][:50] or "AI Quiz"
        source = "ai_document"
    else:
        try:
            questions = await generate_questions_from_topic(topic_clean, instruction_clean, question_count)
        except QuizGenerationError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

        title = topic_clean[:50] or "AI Quiz"
        source = "ai_topic"

    category = Category(
        name=title,
        icon_name="sparkle",
        color_key="coral",
        is_active=True,
        owner_user_id=current_user.id,
        source=source,
        visibility="private",
    )
    db.add(category)
    await db.flush()

    for q in questions:
        db.add(
            Question(
                category_id=category.id,
                question_text=q["question_text"],
                options=q["options"],
                correct_option_index=q["correct_option_index"],
                is_active=True,
            )
        )
    await db.commit()
    await db.refresh(category)

    return AiQuizOut(
        id=category.id,
        name=category.name,
        question_count=len(questions),
        created_at=category.created_at.isoformat(),
        source=category.source,
        visibility=category.visibility,
    )


@router.post(
    "/manual",
    response_model=AiQuizOut,
    status_code=status.HTTP_201_CREATED,
    summary="Qo'lda quiz yaratish",
)
async def create_manual_quiz(
    payload: ManualQuizCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    name = payload.name.strip()[:50]
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quiz nomini kiriting")

    raw_questions = [q.model_dump() for q in payload.questions[:MAX_QUESTION_COUNT]]
    validated = _validate_questions(raw_questions)
    if not validated:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kamida bitta to'g'ri to'ldirilgan savol kerak")

    category = Category(
        name=name,
        icon_name="sparkle",
        color_key="coral",
        is_active=True,
        owner_user_id=current_user.id,
        source="manual",
        visibility="private",
    )
    db.add(category)
    await db.flush()

    for q in validated:
        db.add(
            Question(
                category_id=category.id,
                question_text=q["question_text"],
                options=q["options"],
                correct_option_index=q["correct_option_index"],
                is_active=True,
            )
        )
    await db.commit()
    await db.refresh(category)

    return AiQuizOut(
        id=category.id,
        name=category.name,
        question_count=len(validated),
        created_at=category.created_at.isoformat(),
        source=category.source,
        visibility=category.visibility,
    )


@router.patch("/{quiz_id}/visibility", response_model=AiQuizOut, summary="Quiz ko'rinishini o'zgartirish")
async def update_quiz_visibility(
    quiz_id: int,
    payload: VisibilityUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.visibility not in VALID_VISIBILITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Noto'g'ri ko'rinish qiymati")

    category = await db.get(Category, quiz_id)
    if category is None or category.owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")

    category.visibility = payload.visibility
    await db.commit()
    await db.refresh(category)

    question_count_result = await db.execute(
        select(func.count()).select_from(Question).where(Question.category_id == category.id, Question.is_active.is_(True))
    )
    return AiQuizOut(
        id=category.id,
        name=category.name,
        question_count=question_count_result.scalar_one(),
        created_at=category.created_at.isoformat(),
        source=category.source,
        visibility=category.visibility,
    )


@router.get("", response_model=list[AiQuizOut], summary="Mening AI quizlarim")
async def list_my_ai_quizzes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    question_count_subq = _question_count_subquery()
    stmt = (
        select(Category, func.coalesce(question_count_subq.c.cnt, 0))
        .outerjoin(question_count_subq, question_count_subq.c.category_id == Category.id)
        .where(Category.owner_user_id == current_user.id, Category.is_active.is_(True))
        .order_by(Category.created_at.desc())
    )
    result = await db.execute(stmt)
    return [
        AiQuizOut(
            id=category.id,
            name=category.name,
            question_count=question_count,
            created_at=category.created_at.isoformat(),
            source=category.source,
            visibility=category.visibility,
        )
        for category, question_count in result.all()
    ]


DISCOVER_LIMIT = 100


def _discover_visibility_filter(current_user_id: str):
    """A quiz shows up in Discover if it's public, or if it's
    friends-only and its owner is actually a friend of the viewer - never
    the viewer's own quizzes (those live in "Mening quizlarim" already),
    never a private one, and never a global (owner_user_id is None)
    category (Discover is specifically for OTHER USERS' quizzes)."""
    friend_ids_subq = select(Friendship.friend_id).where(Friendship.user_id == current_user_id)
    return and_(
        Category.owner_user_id.is_not(None),
        Category.owner_user_id != current_user_id,
        Category.is_active.is_(True),
        or_(
            Category.visibility == "public",
            and_(Category.visibility == "friends", Category.owner_user_id.in_(friend_ids_subq)),
        ),
    )


async def _run_discover_query(db: AsyncSession, extra_filter=None):
    question_count_subq = _question_count_subquery()
    stmt = (
        select(Category, func.coalesce(question_count_subq.c.cnt, 0), User)
        .join(User, User.id == Category.owner_user_id)
        .outerjoin(question_count_subq, question_count_subq.c.category_id == Category.id)
        .order_by(Category.created_at.desc())
        .limit(DISCOVER_LIMIT)
    )
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)
    result = await db.execute(stmt)
    return [
        DiscoverQuizOut(
            id=category.id,
            name=category.name,
            question_count=question_count,
            created_at=category.created_at.isoformat(),
            source=category.source,
            visibility=category.visibility,
            owner_user_id=owner.id,
            owner_username=owner.username,
        )
        for category, question_count, owner in result.all()
    ]


@router.get(
    "/discover",
    response_model=list[DiscoverQuizOut],
    summary="Boshqalarning ochiq/do'stlar quizlari (feed)",
)
async def discover_quizzes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _run_discover_query(db, _discover_visibility_filter(current_user.id))


@router.get(
    "/discover/search",
    response_model=list[DiscoverQuizOut],
    summary="Discover ichida quiz nomi bo'yicha qidirish",
)
async def search_discover_quizzes(
    q: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = q.strip()
    if not query:
        return []
    return await _run_discover_query(
        db, and_(_discover_visibility_filter(current_user.id), Category.name.ilike(f"%{query}%"))
    )


@router.get("/users/{user_id}", response_model=list[AiQuizOut], summary="Boshqa foydalanuvchining ko'rinadigan quizlari")
async def list_user_quizzes(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foydalanuvchi topilmadi")

    if user_id == current_user.id:
        # O'zining sahifasi - visibility'dan qat'iy nazar hammasini ko'radi
        # (list_my_ai_quizzes bilan bir xil natija, boshqa profil ekranida
        # ham ishlatilaveradi deb).
        allowed_visibilities: list[str] | None = None
    elif await is_friend(db, current_user.id, user_id):
        allowed_visibilities = ["public", "friends"]
    else:
        allowed_visibilities = ["public"]

    question_count_subq = _question_count_subquery()
    stmt = (
        select(Category, func.coalesce(question_count_subq.c.cnt, 0))
        .outerjoin(question_count_subq, question_count_subq.c.category_id == Category.id)
        .where(Category.owner_user_id == user_id, Category.is_active.is_(True))
        .order_by(Category.created_at.desc())
    )
    if allowed_visibilities is not None:
        stmt = stmt.where(Category.visibility.in_(allowed_visibilities))

    result = await db.execute(stmt)
    return [
        AiQuizOut(
            id=category.id,
            name=category.name,
            question_count=question_count,
            created_at=category.created_at.isoformat(),
            source=category.source,
            visibility=category.visibility,
        )
        for category, question_count in result.all()
    ]


@router.delete("/{quiz_id}", status_code=status.HTTP_204_NO_CONTENT, summary="AI quizni o'chirish")
async def delete_ai_quiz(
    quiz_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    category = await db.get(Category, quiz_id)
    # Boshqa foydalanuvchining (yoki umuman shaxsiy bo'lmagan) kategoriyasi
    # ekanligini oshkor qilmaslik uchun ikkala holatda ham bir xil 404.
    if category is None or category.owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")
    # Haqiqiy o'chirish emas - is_active=False (global kategoriyalarni admin
    # ham xuddi shunday "o'chiradi") - o'ynalgan tarix (QuizSession) bilan
    # bog'liq FK ziddiyatlarisiz.
    category.is_active = False
    await db.commit()
