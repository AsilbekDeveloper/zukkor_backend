import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def hash_token(token: str) -> str:
    # Refresh tokenlar bazada shu hash orqali saqlanadi, xom JWT emas -
    # DB agar qandaydir yo'l bilan sizib chiqsa, tajovuzkor to'g'ridan-to'g'ri
    # ishlaydigan tokenlarni olmaydi. Bcrypt emas SHA-256: bu yerda parol
    # emas, allaqachon yuqori entropiyali (256+ bitlik imzolangan JWT)
    # qiymat - maqsad tezkor va deterministik qidiruv, sekinlashtirish shart
    # emas.
    return hashlib.sha256(token.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# Login'da "email topilmadi" holatini "parol noto'g'ri" holatidan ajratib
# bo'lmasligi kerak - aks holda ikkalasi bir xil xabar qaytarsa ham, birinchi
# holatda bcrypt umuman chaqirilmagani uchun javob sezilarli tezroq keladi va
# bu javob VAQTI orqali qaysi emaillar ro'yxatdan o'tganini bilib olish
# mumkin bo'lib qoladi. Foydalanuvchi/parol topilmasa ham shu qo'g'irchoq
# hash bilan solishtirib, bcrypt har doim ishlaydi.
DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"zukkor-timing-attack-decoy", bcrypt.gensalt()).decode()


def create_access_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {**data, "exp": expire, "type": "access", "jti": str(uuid.uuid4())},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def create_refresh_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {**data, "exp": expire, "type": "refresh", "jti": str(uuid.uuid4())},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
