import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.limiter import limiter
from app.dependencies.auth import get_current_user
from app.models.ai_quiz_job import AiQuizGenerationJob
from app.models.friendship import Friendship
from app.models.quiz import Category, Question
from app.models.user import User
from app.schemas.ai_quiz import (
    AiQuizOut,
    DiscoverQuizOut,
    GenerationJobOut,
    GenerationJobStartedOut,
    ManualQuestionIn,
    ManualQuizCreate,
    QuizQuestionOut,
    TopicUpdate,
    VisibilityUpdate,
)
from app.services.ai_quiz_generation import QuizGenerationError, _validate_questions, generate_questions, generate_questions_from_topic
from app.services.document_text import UnsupportedDocumentError, extract_text
from app.services.push import send_push_to_user
from app.services.quiz_access import can_access_category, is_friend
from app.services import wallet

router = APIRouter()

logger = logging.getLogger("zukkor.ai_quiz_jobs")

MAX_UPLOAD_SIZE_BYTES = 15 * 1024 * 1024  # 15MB - kitob uchun yetarli
_UPLOAD_READ_CHUNK_BYTES = 256 * 1024
DEFAULT_QUESTION_COUNT = 10
MAX_QUESTION_COUNT = 20
MAX_TOPIC_LENGTH = 300
VALID_VISIBILITIES = {"private", "friends", "public"}

def _estimate_input_tokens(text_or_bytes: str | bytes) -> int:
    # Fayl hali matnga ajratilmagan bo'lsa (masalan `/generate-async`ning
    # oldindan-tekshiruvi) xom bayt uzunligi ishlatiladi - PDF/DOCX kabi
    # formatlar odatda o'z matnidan KO'PROQ bayt egallaydi, shuning uchun
    # bu haqiqiy token sonini OSHIRIB (xavfsiz tomonga) taxmin qiladi.
    # `CHARS_PER_TOKEN_ESTIMATE` - `settings`da (Flutter bilan bir xil
    # formula, `GET /wallet/pricing` orqali) - lokal doim emas.
    return max(1, len(text_or_bytes) // settings.CHARS_PER_TOKEN_ESTIMATE)

# Ro'yxat/Discover so'rovlarida quiz'ning mavzu-kategoriyasi nomini bitta
# JOIN bilan olish uchun - har bir qatorga alohida so'rov yubormaslik uchun.
TopicCategory = aliased(Category)


async def _resolve_topic_category(db: AsyncSession, topic_category_id: int | None) -> str | None:
    """`topic_category_id` haqiqatan ham faol, global (owner_user_id=NULL)
    kategoriyaga ishora qilishini tekshiradi va nomini qaytaradi - yaratish/
    yangilashda validatsiya UCHUN HAM, javobda ko'rsatiladigan nomni olish
    UCHUN HAM shu bitta funksiya ishlatiladi."""
    if topic_category_id is None:
        return None
    topic = await db.get(Category, topic_category_id)
    if topic is None or topic.owner_user_id is not None or not topic.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Noto'g'ri mavzu-kategoriya")
    return topic.name


async def _has_duplicate_quiz_name(db: AsyncSession, owner_user_id: str, name: str) -> bool:
    """Bitta foydalanuvchida bir xil nomdagi (katta-kichik harf va bosh-
    oxiridagi bo'shliqlarga sezgir bo'lmagan) 2 ta quiz bo'lmasligi kerak -
    boshqa foydalanuvchilarda xohlagancha bo'lishi mumkin, shuning uchun
    tekshiruv faqat shu bitta `owner_user_id` doirasida."""
    result = await db.execute(
        select(Category.id).where(
            Category.owner_user_id == owner_user_id,
            Category.is_active.is_(True),
            func.lower(Category.name) == name.strip().lower(),
        )
    )
    return result.first() is not None


async def _unique_quiz_name(db: AsyncSession, owner_user_id: str, base_name: str) -> str:
    """AI-generatsiya uchun - narx ALLAQACHON (Diamond) to'langan bo'lgani
    sababli, nom to'qnashuvi hech qachon generatsiyani rad etmaydi (foydalanuvchi
    ataylab tanlagan nom emas, fayl nomi/mavzudan avtomatik olingan) - buning
    o'rniga bo'sh nom topilguncha " (2)", " (3)" ... qo'shiladi."""
    candidate = base_name[:50]
    suffix = 2
    while await _has_duplicate_quiz_name(db, owner_user_id, candidate) and suffix < 100:
        candidate = f"{base_name[:44]} ({suffix})"
        suffix += 1
    return candidate


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
@limiter.limit("5/minute")
async def generate_ai_quiz(
    request: Request,
    file: UploadFile | None = File(None),
    instruction: str | None = Form(None),
    topic: str | None = Form(None),
    question_count: int = Form(DEFAULT_QUESTION_COUNT),
    topic_category_id: int | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    question_count = max(1, min(question_count, MAX_QUESTION_COUNT))
    instruction_clean = (instruction or "").strip()
    topic_clean = (topic or "").strip()[:MAX_TOPIC_LENGTH]
    has_file = file is not None and bool(file.filename)
    # Generatsiyadan OLDIN tekshiramiz - noto'g'ri mavzu-kategoriya bilan
    # Gemini'ga bekorga pul/vaqt sarflanmasin.
    topic_category_name = await _resolve_topic_category(db, topic_category_id)

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

        estimated_cost = wallet.estimate_diamond_cost(
            estimated_input_tokens=_estimate_input_tokens(text), question_count=question_count
        )
        if current_user.diamond_balance < estimated_cost:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Diamond balansi yetarli emas")

        try:
            result = await generate_questions(text, instruction_clean, question_count)
        except QuizGenerationError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

        questions = result.questions
        title = (file.filename or "AI Quiz").rsplit(".", 1)[0][:50] or "AI Quiz"
        source = "ai_document"
    else:
        estimated_cost = wallet.estimate_diamond_cost(
            estimated_input_tokens=_estimate_input_tokens(topic_clean), question_count=question_count
        )
        if current_user.diamond_balance < estimated_cost:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Diamond balansi yetarli emas")

        try:
            result = await generate_questions_from_topic(topic_clean, instruction_clean, question_count)
        except QuizGenerationError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

        questions = result.questions
        title = topic_clean[:50] or "AI Quiz"
        source = "ai_topic"

    # Diamond FAQAT shu yerda, generatsiya haqiqatan MUVAFFAQIYATLI
    # tugagandan keyin, haqiqiy token sarfidan yechiladi - shuning uchun
    # muvaffaqiyatsiz urinishda "qaytarish" degan alohida mantiq kerak
    # emas (hech qachon bo'lmagan narsa uchun pul olinmaydi).
    diamond_cost = wallet.diamond_cost_from_tokens(result.input_tokens, result.output_tokens)
    await wallet.debit_diamond(
        db,
        current_user,
        diamond_cost,
        "ai_generation",
        extra={
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "question_count": question_count,
            "mode": source,
        },
    )

    unique_title = await _unique_quiz_name(db, current_user.id, title)
    category = Category(
        name=unique_title,
        icon_name="sparkle",
        color_key="coral",
        is_active=True,
        owner_user_id=current_user.id,
        source=source,
        visibility="private",
        topic_category_id=topic_category_id,
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
        topic_category_id=category.topic_category_id,
        topic_category_name=topic_category_name,
        diamond_cost=diamond_cost,
    )


async def _run_generation_job(
    job_id: str,
    user_id: str,
    *,
    file_bytes: bytes | None,
    filename: str | None,
    instruction: str,
    topic: str,
    question_count: int,
    topic_category_id: int | None,
) -> None:
    """Fon vazifasi (BackgroundTasks) - so'rov allaqachon 202 bilan
    qaytgandan KEYIN ishga tushadi, shuning uchun request-scoped `db`dan
    foydalana olmaydi, o'zining AsyncSessionLocal'ini ochadi (xuddi
    `duel_engine.forfeit_duel` va boshqa fon vazifalari kabi)."""
    async with AsyncSessionLocal() as db:
        job = await db.get(AiQuizGenerationJob, job_id)
        if job is None:
            return

        try:
            user = await db.get(User, user_id)
            if file_bytes is not None:
                try:
                    text = extract_text(filename or "", file_bytes)
                except UnsupportedDocumentError as exc:
                    raise QuizGenerationError(str(exc)) from exc
                if not text.strip():
                    raise QuizGenerationError("Hujjatdan matn topilmadi")

                result = await generate_questions(text, instruction, question_count)
                title = (filename or "AI Quiz").rsplit(".", 1)[0][:50] or "AI Quiz"
                source = "ai_document"
            else:
                result = await generate_questions_from_topic(topic, instruction, question_count)
                title = topic[:50] or "AI Quiz"
                source = "ai_topic"

            questions = result.questions

            # Diamond FAQAT muvaffaqiyatli generatsiyadan keyin, haqiqiy
            # token sarfidan yechiladi - `generate_ai_quiz` (sinxron yo'l)
            # bilan bir xil mantiq, [[ai_cost_architecture]].
            diamond_cost = None
            if user is not None:
                diamond_cost = wallet.diamond_cost_from_tokens(result.input_tokens, result.output_tokens)
                await wallet.debit_diamond(
                    db,
                    user,
                    diamond_cost,
                    "ai_generation",
                    extra={
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "question_count": question_count,
                        "mode": source,
                    },
                )

            unique_title = await _unique_quiz_name(db, user_id, title)
            category = Category(
                name=unique_title,
                icon_name="sparkle",
                color_key="coral",
                is_active=True,
                owner_user_id=user_id,
                source=source,
                visibility="private",
                topic_category_id=topic_category_id,
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

            job.status = "completed"
            job.category_id = category.id
            job.diamond_cost = diamond_cost
            job.finished_at = datetime.now(timezone.utc)
            await db.commit()

            await send_push_to_user(
                db,
                user_id,
                "Quiz tayyor!",
                f"“{unique_title}” testi tayyor bo'ldi - o'ynash uchun bosing",
                data={"type": "ai_quiz_ready", "quiz_id": str(category.id)},
            )
        except QuizGenerationError as exc:
            job.status = "failed"
            job.error_message = str(exc)[:300]
            job.finished_at = datetime.now(timezone.utc)
            await db.commit()
            await send_push_to_user(
                db, user_id, "Quiz yaratib bo'lmadi", str(exc)[:150], data={"type": "ai_quiz_failed"}
            )
        except Exception:
            # Kutilmagan xato (masalan tarmoq uzilishi) - job'ni "failed"
            # deb belgilaymiz, aks holda foydalanuvchi "pending" holatida
            # abadiy kutib qoladi.
            logger.exception("AI quiz generation job muvaffaqiyatsiz bo'ldi (job_id=%s)", job_id)
            job.status = "failed"
            job.error_message = "Kutilmagan xatolik yuz berdi"
            job.finished_at = datetime.now(timezone.utc)
            await db.commit()
            await send_push_to_user(
                db, user_id, "Quiz yaratib bo'lmadi", "Kutilmagan xatolik yuz berdi", data={"type": "ai_quiz_failed"}
            )


@router.post(
    "/generate-async",
    response_model=GenerationJobStartedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Hujjatdan/mavzudan AI quiz generatsiyasini FON REJIMIDA boshlash",
    description="Darhol job_id bilan qaytadi - haqiqiy generatsiya fon vazifasida ketadi, "
    "tayyor bo'lgach push (`type: pdf_ready`) yuboriladi. Katta hujjatlar uchun (1-2 daqiqa "
    "davom etishi mumkin) ochiq HTTP ulanishini kutishning oldini olish uchun.",
)
@limiter.limit("5/minute")
async def generate_ai_quiz_async(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(None),
    instruction: str | None = Form(None),
    topic: str | None = Form(None),
    question_count: int = Form(DEFAULT_QUESTION_COUNT),
    topic_category_id: int | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    question_count = max(1, min(question_count, MAX_QUESTION_COUNT))
    instruction_clean = (instruction or "").strip()
    topic_clean = (topic or "").strip()[:MAX_TOPIC_LENGTH]
    has_file = file is not None and bool(file.filename)
    # Generatsiya boshlanishidan OLDIN tekshiramiz - noto'g'ri mavzu-kategoriya
    # bilan fon vazifasi behuda ishga tushmasin.
    await _resolve_topic_category(db, topic_category_id)

    if not has_file and not topic_clean:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hujjat yuklang yoki mavzu kiriting")

    file_bytes: bytes | None = None
    filename: str | None = None
    if has_file:
        assert file is not None
        file_bytes = await _read_limited(file, MAX_UPLOAD_SIZE_BYTES)
        filename = file.filename

    # Fon vazifasi (`_run_generation_job`) behuda ishga tushmasin - haqiqiy
    # narx generatsiya tugagach qayta hisoblanadi, bu faqat tахminiy tekshiruv.
    estimated_cost = wallet.estimate_diamond_cost(
        estimated_input_tokens=_estimate_input_tokens(file_bytes if file_bytes is not None else topic_clean),
        question_count=question_count,
    )
    if current_user.diamond_balance < estimated_cost:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Diamond balansi yetarli emas")

    job = AiQuizGenerationJob(user_id=current_user.id, status="pending", question_count=question_count)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(
        _run_generation_job,
        job.id,
        current_user.id,
        file_bytes=file_bytes,
        filename=filename,
        instruction=instruction_clean,
        topic=topic_clean,
        question_count=question_count,
        topic_category_id=topic_category_id,
    )

    return GenerationJobStartedOut(job_id=job.id)


@router.get(
    "/generate-async/{job_id}",
    response_model=GenerationJobOut,
    summary="Fon rejimidagi AI quiz generatsiyasi holatini so'rash",
    description="Asosiy yo'l - push xabarini kutish; bu endpoint faqat push kelmasa "
    "(masalan ruxsat berilmagan bo'lsa) yoki ekranda holatni ko'rsatib turish uchun zaxira.",
)
async def get_generation_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(AiQuizGenerationJob, job_id)
    if job is None or job.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")

    quiz_out: AiQuizOut | None = None
    if job.status == "completed" and job.category_id is not None:
        category = await db.get(Category, job.category_id)
        if category is not None:
            question_count_result = await db.execute(
                select(func.count()).select_from(Question).where(
                    Question.category_id == category.id, Question.is_active.is_(True)
                )
            )
            topic_category_name = await _resolve_topic_category(db, category.topic_category_id)
            quiz_out = AiQuizOut(
                id=category.id,
                name=category.name,
                question_count=question_count_result.scalar_one(),
                created_at=category.created_at.isoformat(),
                source=category.source,
                visibility=category.visibility,
                topic_category_id=category.topic_category_id,
                topic_category_name=topic_category_name,
                diamond_cost=job.diamond_cost,
            )

    return GenerationJobOut(job_id=job.id, status=job.status, quiz=quiz_out, error=job.error_message)


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
    # Foydalanuvchi bu nomni ATAYLAB o'zi yozgan (AI'dan farqli, fayl
    # nomi/mavzudan avtomatik olinmagan) - shuning uchun avtomatik
    # o'zgartirish o'rniga aniq xato qaytariladi, boshqa nom tanlashni
    # so'rab. Bitta foydalanuvchi doirasida - boshqa birov xohlagan
    # nomdagi quiz yaratishi mumkin.
    if await _has_duplicate_quiz_name(db, current_user.id, name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Bu nomda quizingiz allaqachon bor - boshqa nom tanlang"
        )

    raw_questions = [q.model_dump() for q in payload.questions[:MAX_QUESTION_COUNT]]
    validated = _validate_questions(raw_questions)
    if not validated:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kamida bitta to'g'ri to'ldirilgan savol kerak")

    topic_category_name = await _resolve_topic_category(db, payload.topic_category_id)

    category = Category(
        name=name,
        icon_name="sparkle",
        color_key="coral",
        is_active=True,
        owner_user_id=current_user.id,
        source="manual",
        visibility="private",
        topic_category_id=payload.topic_category_id,
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
        topic_category_id=category.topic_category_id,
        topic_category_name=topic_category_name,
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
    topic_category_name = await _resolve_topic_category(db, category.topic_category_id)
    return AiQuizOut(
        id=category.id,
        name=category.name,
        question_count=question_count_result.scalar_one(),
        created_at=category.created_at.isoformat(),
        source=category.source,
        visibility=category.visibility,
        topic_category_id=category.topic_category_id,
        topic_category_name=topic_category_name,
    )


@router.patch("/{quiz_id}/topic", response_model=AiQuizOut, summary="Quiz mavzu-kategoriyasini o'zgartirish")
async def update_quiz_topic(
    quiz_id: int,
    payload: TopicUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    category = await db.get(Category, quiz_id)
    if category is None or category.owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")

    topic_category_name = await _resolve_topic_category(db, payload.topic_category_id)
    category.topic_category_id = payload.topic_category_id
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
        topic_category_id=category.topic_category_id,
        topic_category_name=topic_category_name,
    )


async def _get_owned_manual_quiz(db: AsyncSession, quiz_id: int, current_user: User) -> Category:
    """Savol qo'shish/tahrirlash/o'chirish endpointlari uchun umumiy
    ruxsat tekshiruvi - boshqa foydalanuvchining (yoki umuman mavjud
    bo'lmagan) quizi ekanligini oshkor qilmaslik uchun 404, lekin
    egasining O'ZI AI orqali yaratgan quizga urinsa buni aniq 400 bilan
    aytamiz - bu holatda mavjudlikni yashirishning hojati yo'q, chunki
    so'rovchi allaqachon shu quizning egasi."""
    category = await db.get(Category, quiz_id)
    if category is None or category.owner_user_id != current_user.id or not category.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")
    if category.source != "manual":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Faqat qo'lda yaratilgan quizlarning savollarini tahrirlash mumkin",
        )
    return category


def _question_out(question: Question) -> QuizQuestionOut:
    return QuizQuestionOut(
        id=question.id,
        question_text=question.question_text,
        options=question.options,
        correct_option_index=question.correct_option_index,
    )


@router.get(
    "/{quiz_id}/questions",
    response_model=list[QuizQuestionOut],
    summary="Qo'lda yaratilgan quizning savollari ro'yxati",
)
async def list_quiz_questions(
    quiz_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_manual_quiz(db, quiz_id, current_user)
    result = await db.execute(
        select(Question)
        .where(Question.category_id == quiz_id, Question.is_active.is_(True))
        .order_by(Question.id)
    )
    return [_question_out(q) for q in result.scalars().all()]


@router.post(
    "/{quiz_id}/questions",
    response_model=QuizQuestionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Qo'lda yaratilgan quizga yangi savol qo'shish",
)
async def add_quiz_question(
    quiz_id: int,
    payload: ManualQuestionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_manual_quiz(db, quiz_id, current_user)

    count_result = await db.execute(
        select(func.count()).select_from(Question).where(
            Question.category_id == quiz_id, Question.is_active.is_(True)
        )
    )
    if count_result.scalar_one() >= MAX_QUESTION_COUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bitta quizda ko'pi bilan {MAX_QUESTION_COUNT} ta savol bo'lishi mumkin",
        )

    validated = _validate_questions([payload.model_dump()])
    if not validated:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Savol to'g'ri to'ldirilmagan")

    question = Question(category_id=quiz_id, is_active=True, **validated[0])
    db.add(question)
    await db.commit()
    await db.refresh(question)
    return _question_out(question)


@router.patch(
    "/{quiz_id}/questions/{question_id}",
    response_model=QuizQuestionOut,
    summary="Qo'lda yaratilgan quizdagi savolni tahrirlash",
)
async def update_quiz_question(
    quiz_id: int,
    question_id: int,
    payload: ManualQuestionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_manual_quiz(db, quiz_id, current_user)

    question = await db.get(Question, question_id)
    if question is None or question.category_id != quiz_id or not question.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")

    validated = _validate_questions([payload.model_dump()])
    if not validated:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Savol to'g'ri to'ldirilmagan")

    question.question_text = validated[0]["question_text"]
    question.options = validated[0]["options"]
    question.correct_option_index = validated[0]["correct_option_index"]
    await db.commit()
    await db.refresh(question)
    return _question_out(question)


@router.delete(
    "/{quiz_id}/questions/{question_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Qo'lda yaratilgan quizdagi savolni o'chirish",
)
async def delete_quiz_question(
    quiz_id: int,
    question_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_manual_quiz(db, quiz_id, current_user)

    question = await db.get(Question, question_id)
    if question is None or question.category_id != quiz_id or not question.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")

    count_result = await db.execute(
        select(func.count()).select_from(Question).where(
            Question.category_id == quiz_id, Question.is_active.is_(True)
        )
    )
    if count_result.scalar_one() <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quizda kamida bitta savol qolishi kerak")

    question.is_active = False
    await db.commit()


@router.get("", response_model=list[AiQuizOut], summary="Mening AI quizlarim")
async def list_my_ai_quizzes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    question_count_subq = _question_count_subquery()
    stmt = (
        select(Category, func.coalesce(question_count_subq.c.cnt, 0), TopicCategory.name)
        .outerjoin(question_count_subq, question_count_subq.c.category_id == Category.id)
        .outerjoin(TopicCategory, TopicCategory.id == Category.topic_category_id)
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
            topic_category_id=category.topic_category_id,
            topic_category_name=topic_name,
        )
        for category, question_count, topic_name in result.all()
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
        select(Category, func.coalesce(question_count_subq.c.cnt, 0), User, TopicCategory.name)
        .join(User, User.id == Category.owner_user_id)
        .outerjoin(question_count_subq, question_count_subq.c.category_id == Category.id)
        .outerjoin(TopicCategory, TopicCategory.id == Category.topic_category_id)
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
            topic_category_id=category.topic_category_id,
            topic_category_name=topic_name,
            owner_user_id=owner.id,
            owner_username=owner.username,
        )
        for category, question_count, owner, topic_name in result.all()
    ]


@router.get(
    "/discover",
    response_model=list[DiscoverQuizOut],
    summary="Boshqalarning ochiq/do'stlar quizlari (feed)",
)
async def discover_quizzes(
    category_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    quiz_filter = _discover_visibility_filter(current_user.id)
    if category_id is not None:
        quiz_filter = and_(quiz_filter, Category.topic_category_id == category_id)
    return await _run_discover_query(db, quiz_filter)


@router.get(
    "/discover/search",
    response_model=list[DiscoverQuizOut],
    summary="Discover ichida quiz nomi bo'yicha qidirish",
)
async def search_discover_quizzes(
    q: str,
    category_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = q.strip()
    if not query:
        return []
    quiz_filter = and_(_discover_visibility_filter(current_user.id), Category.name.ilike(f"%{query}%"))
    if category_id is not None:
        quiz_filter = and_(quiz_filter, Category.topic_category_id == category_id)
    return await _run_discover_query(db, quiz_filter)


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
        select(Category, func.coalesce(question_count_subq.c.cnt, 0), TopicCategory.name)
        .outerjoin(question_count_subq, question_count_subq.c.category_id == Category.id)
        .outerjoin(TopicCategory, TopicCategory.id == Category.topic_category_id)
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
            topic_category_id=category.topic_category_id,
            topic_category_name=topic_name,
        )
        for category, question_count, topic_name in result.all()
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
