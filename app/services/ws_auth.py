from jose import JWTError

from app.core.database import AsyncSessionLocal
from app.core.security import decode_token
from app.models.user import User


async def authenticate_ws(token: str) -> User | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
    except JWTError:
        return None

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return user
