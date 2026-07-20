import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.database import Base, engine
from app.routers import auth, categories, duel_ws, friends, history, leaderboard, lobby_ws, notifications, quiz, users

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(categories.router, prefix="/categories", tags=["Categories"])
app.include_router(quiz.router, prefix="/quiz", tags=["Quiz"])
app.include_router(leaderboard.router, prefix="/leaderboard", tags=["Leaderboard"])
app.include_router(history.router, prefix="/history", tags=["History"])
app.include_router(friends.router, prefix="/friends", tags=["Friends"])
app.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
app.include_router(duel_ws.router, prefix="/ws", tags=["Duel WebSocket"])
app.include_router(lobby_ws.router, prefix="/ws", tags=["Lobby WebSocket"])

# Avatar rasmlari — autentifikatsiyasiz, ochiq (Flutter Image.network() to'g'ridan-to'g'ri shu manzildan yuklaydi)
app.mount("/media", StaticFiles(directory="media"), name="media")


@app.get("/", tags=["Health"], summary="API holati")
async def root():
    return {"status": "ok", "message": "Zukkor API ishlamoqda", "docs": "/docs"}
