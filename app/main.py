from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine
from app.routers import auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


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


@app.get("/", tags=["Health"], summary="API holati")
async def root():
    return {"status": "ok", "message": "Zukkor API ishlamoqda", "docs": "/docs"}
