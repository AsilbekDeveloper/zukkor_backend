import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqladmin import Admin

from app.admin import (
    AdminAuth,
    AnalyticsAdmin,
    AppConfigAdmin,
    CategoryAdmin,
    CurrencyTransactionAdmin,
    QuestionAdmin,
    QuestionSubmissionAdmin,
    ReportedQuestionAdmin,
    UserAdmin,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.limiter import limiter
from app.routers import (
    ai_quiz,
    auth,
    categories,
    duel_ws,
    friends,
    history,
    leaderboard,
    legal,
    lobby_ws,
    notifications,
    question_submissions,
    quiz,
    quiz_export,
    reports,
    telegram,
    users,
    wallet,
)
from app.services import economy_config, telegram_client
from app.services.streak_reminders import streak_reminder_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("zukkor.startup")

Path("media/avatars").mkdir(parents=True, exist_ok=True)

# SENTRY_DSN bo'sh bo'lsa (hali sozlanmagan/lokal dev) hech narsa
# yubormasdan jim o'tkazib yuboriladi - boshqa ixtiyoriy integratsiyalar
# (Gemini, R2, SMTP) kabi. `logger.exception(...)` chaqiruvlari kodning
# turli joylarida (masalan `ai_quiz_generation.py`) allaqachon mavjud -
# sentry-sdk'ning standart logging integratsiyasi ERROR darajadagi
# log yozuvlarini avtomatik Sentry hodisasiga aylantiradi, alohida
# `sentry_sdk.capture_exception(...)` chaqiruvlari qo'shishga hojat yo'q.
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )


def _warn_about_missing_production_config() -> None:
    """Yuqoridagi har bir ixtiyoriy sozlama (Sentry/Gemini/R2/SMTP/
    Telegram) bo'sh qoldirilsa ilova baribir ishga tushadi - ataylab
    shunday qilingan (dev/deploy hech qachon shu sabab bilan to'xtamasin
    deb). Lekin bu operatorga HECH QANDAY signal bermasdi: production'ga
    shu holatda chiqarib qo'yib, masalan Sentry butunlay o'chiq
    ekanligini payqamaslik mumkin edi (2026-09-13 prod-tayyorlik auditi
    topilmasi). Bu faqat LOG yozadi - hech narsani to'xtatmaydi,
    ADMIN_USERNAME/SECRET_KEY/DATABASE_URL kabi haqiqiy majburiy
    sozlamalar allaqachon standart qiymatsiz, ya'ni ular yo'q bo'lsa
    ilova bu yergacha yetib kelmaydi ham."""
    if settings.ENVIRONMENT != "production":
        return

    missing: list[str] = []
    if not settings.SENTRY_DSN:
        missing.append("SENTRY_DSN (xatolik kuzatuvi o'chiq)")
    if not settings.GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY (AI-quiz generatsiyasi ishlamaydi)")
    if not (settings.R2_ACCOUNT_ID and settings.R2_ACCESS_KEY_ID and settings.R2_BUCKET):
        missing.append("R2_* (avatar rasmlari doimiy saqlanmaydi, restart'da yo'qoladi)")
    if not settings.SMTP_USERNAME:
        missing.append("SMTP_USERNAME (parolni tiklash email'i yuborilmaydi)")
    if not settings.TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN (Diamond sotib olish kanali ishlamaydi)")
    elif not settings.TELEGRAM_WEBHOOK_SECRET:
        # Bot tokeni bor, lekin webhook maxfiy tokeni yo'q - bu holda
        # `/telegram/webhook` HAMMA so'rovni 401 bilan rad etadi (2026-09-18,
        # xavfsizlik auditi - ataylab "fail closed": webhook'ni himoyasiz
        # ochiq qoldirishdan ko'ra, butunlay ishlamay turgani xavfsizroq).
        missing.append("TELEGRAM_WEBHOOK_SECRET (webhook HAMMA so'rovni 401 bilan rad etadi)")

    if missing:
        logger.warning("Production muhitida quyidagi sozlamalar bo'sh: %s", "; ".join(missing))


_warn_about_missing_production_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await economy_config.seed_defaults(db)
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_WEBHOOK_URL and settings.TELEGRAM_WEBHOOK_SECRET:
        # Qayta-qayta chaqirish xavfsiz (Telegram buni idempotent qiladi) -
        # webhook manzili/maxfiy tokeni har doim shu deploy'dagi joriy
        # qiymatlar bilan sinxron turishi uchun har ishga tushishda
        # qayta tasdiqlanadi.
        await telegram_client.set_webhook(settings.TELEGRAM_WEBHOOK_URL, settings.TELEGRAM_WEBHOOK_SECRET)
    expiry_task = asyncio.create_task(duel_ws.expire_duel_invites_loop())
    duel_rate_limit_cleanup_task = asyncio.create_task(duel_ws.cleanup_stale_rate_limit_entries_loop())
    notification_cleanup_task = asyncio.create_task(notifications.cleanup_old_notifications_loop())
    streak_reminder_task = asyncio.create_task(streak_reminder_loop())
    yield
    expiry_task.cancel()
    duel_rate_limit_cleanup_task.cancel()
    notification_cleanup_task.cancel()
    streak_reminder_task.cancel()


app = FastAPI(
    title="Zukkor API",
    description="""
## O'zbekiston bozori uchun real-time multiplayer bilim musobaqasi 🎯

### Auth endpointlari:
- **POST /auth/register** — Ro'yxatdan o'tish
- **POST /auth/login** — Tizimga kirish
- **POST /auth/refresh** — Tokenni yangilash (rotation)
- **POST /auth/logout** — Tizimdan chiqish
- **GET /auth/me** — Joriy foydalanuvchi (🔒 Bearer token kerak)

### Token ishlash tartibi:
1. Register yoki Login → `access_token` (30 min) + `refresh_token` (7 kun)
2. Har so'rovda: `Authorization: Bearer <access_token>`
3. Access token tugasa: `/auth/refresh` → yangi tokenlar
4. Chiqishda: `/auth/logout` → refresh token bekor qilinadi
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Hech qanday brauzerda ochiladigan web-frontend yo'q (faqat Flutter mobil
# ilova + /admin) - mobil ilova brauzer CORS siyosatiga umuman bog'liq
# emas, shuning uchun `allow_origins=["*"]` mobil ilova uchun xavfsiz.
# `allow_credentials=False` esa /admin sessiya cookie'sini cross-origin
# so'rovlardan himoya qiladi (`*` + credentials=True kombinatsiyasi
# Starlette'da so'ragan domenni echo qilib, cookie asosidagi so'rovlarga
# yo'l ochib qo'yardi).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(categories.router, prefix="/categories", tags=["Categories"])
app.include_router(quiz.router, prefix="/quiz", tags=["Quiz"])
app.include_router(quiz_export.router, prefix="/quiz", tags=["Quiz Export"])
app.include_router(reports.router, prefix="/questions", tags=["Reports"])
app.include_router(question_submissions.router, prefix="/questions", tags=["Question Submissions"])
app.include_router(ai_quiz.router, prefix="/ai-quiz", tags=["AI Quiz"])
app.include_router(leaderboard.router, prefix="/leaderboard", tags=["Leaderboard"])
app.include_router(history.router, prefix="/history", tags=["History"])
app.include_router(friends.router, prefix="/friends", tags=["Friends"])
app.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
app.include_router(wallet.router, prefix="/wallet", tags=["Wallet"])
app.include_router(telegram.router, prefix="/telegram", tags=["Telegram"])
app.include_router(legal.router, tags=["Legal"])
app.include_router(duel_ws.router, prefix="/ws", tags=["Duel WebSocket"])
app.include_router(lobby_ws.router, prefix="/ws", tags=["Lobby WebSocket"])

# Avatar rasmlari — autentifikatsiyasiz, ochiq (Flutter Image.network() to'g'ridan-to'g'ri shu manzildan yuklaydi)
app.mount("/media", StaticFiles(directory="media"), name="media")

admin = Admin(
    app, engine, authentication_backend=AdminAuth(secret_key=settings.ADMIN_SESSION_SECRET), title="Zukkor Admin"
)
admin.add_view(AnalyticsAdmin)
admin.add_view(UserAdmin)
admin.add_view(CategoryAdmin)
admin.add_view(QuestionAdmin)
admin.add_view(ReportedQuestionAdmin)
admin.add_view(QuestionSubmissionAdmin)
admin.add_view(CurrencyTransactionAdmin)
admin.add_view(AppConfigAdmin)


@app.get("/", tags=["Health"], summary="API holati")
async def root():
    return {"status": "ok", "message": "Zukkor API ishlamoqda", "docs": "/docs"}


# 2026-09-29, VAQTINCHALIK - Sentry integratsiyasini haqiqiy xato bilan
# qo'lda tekshirish uchun (foydalanuvchi so'rovi). Tekshiruv tugagach
# OLIB TASHLANADI - bu hech qanday himoyasiz, istalgan kishi chaqira
# oladigan 500 xato yo'li, production'da qoldirilmasligi kerak.
@app.get("/sentry-debug", tags=["Health"], include_in_schema=False)
async def trigger_sentry_test_error():
    1 / 0
