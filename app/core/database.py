from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    # pool_pre_ping: har bir ulanishni pooldan olishdan oldin "tirikligini"
    # tekshiradi - buning yo'qligi sabab, Railway/tarmoq jimgina uzib
    # qo'ygan eski ulanish qayta ishlatilib, so'rov xatosiz "osilib
    # qolardi" (client-side timeoutgacha hech narsa qaytmasdi).
    pool_pre_ping=True,
    # pool_recycle: 5 daqiqadan uzoq bo'sh turgan ulanishni faol
    # ishlatilishidan oldin yangilaydi - ba'zi proksi/firewall'lar shundan
    # uzunroq bo'sh ulanishlarni sezmasdan uzib qo'yadi.
    pool_recycle=300,
    pool_size=10,
    max_overflow=20,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
