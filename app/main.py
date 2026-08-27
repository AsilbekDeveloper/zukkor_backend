import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqladmin import Admin

from app.admin import AdminAuth, CategoryAdmin, QuestionAdmin, ReportedQuestionAdmin
from app.core.config import settings
from app.core.database import Base, engine
from app.core.limiter import limiter
from app.routers import (
    ai_quiz,
    auth,
    categories,
    duel_ws,
    friends,
    history,
    leaderboard,
    lobby_ws,
    notifications,
    quiz,
    reports,
    users,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

Path("media/avatars").mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    expiry_task = asyncio.create_task(duel_ws.expire_duel_invites_loop())
    yield
    expiry_task.cancel()


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
app.include_router(reports.router, prefix="/questions", tags=["Reports"])
app.include_router(ai_quiz.router, prefix="/ai-quiz", tags=["AI Quiz"])
app.include_router(leaderboard.router, prefix="/leaderboard", tags=["Leaderboard"])
app.include_router(history.router, prefix="/history", tags=["History"])
app.include_router(friends.router, prefix="/friends", tags=["Friends"])
app.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
app.include_router(duel_ws.router, prefix="/ws", tags=["Duel WebSocket"])
app.include_router(lobby_ws.router, prefix="/ws", tags=["Lobby WebSocket"])

# Avatar rasmlari — autentifikatsiyasiz, ochiq (Flutter Image.network() to'g'ridan-to'g'ri shu manzildan yuklaydi)
app.mount("/media", StaticFiles(directory="media"), name="media")

admin = Admin(
    app, engine, authentication_backend=AdminAuth(secret_key=settings.ADMIN_SESSION_SECRET), title="Zukkor Admin"
)
admin.add_view(CategoryAdmin)
admin.add_view(QuestionAdmin)
admin.add_view(ReportedQuestionAdmin)


@app.get("/", tags=["Health"], summary="API holati")
async def root():
    return {"status": "ok", "message": "Zukkor API ishlamoqda", "docs": "/docs"}
