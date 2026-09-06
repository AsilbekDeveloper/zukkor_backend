import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TelegramLinkCode(Base):
    """Telegram bot va Zukkor hisobini bog'lash uchun vaqtinchalik kod.

    Oqim: foydalanuvchi botga `/start` yozadi -> bot 6 xonali kod
    generatsiya qilib shu jadvalga yozadi va foydalanuvchiga yuboradi ->
    foydalanuvchi Zukkor ilovasida (Sozlamalar) shu kodni kiritadi ->
    `POST /telegram/link` kodni tekshirib, `User.telegram_user_id`ni
    to'ldiradi va kodni "ishlatilgan" deb belgilaydi.
    """

    __tablename__ = "telegram_link_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(6), unique=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
