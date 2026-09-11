import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(50), unique=True, index=True, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    first_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    avatar_image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_color: Mapped[str | None] = mapped_column(String(20), nullable=True, default="a-coral")
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    auth_provider: Mapped[str] = mapped_column(String(10), default="email")
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)

    total_xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Yutuqlar (achievements) tizimi uchun - Home'da ko'rsatiladigan doimiy
    # (bir marta erishilsa hech qachon pasaymaydigan) nishonlar. `total_xp`
    # va `longest_streak` allaqachon shu xususiyatga ega (hech qachon
    # kamaymaydi) - bu ikkitasi esa xuddi shunday "eng yaxshi natija"
    # bo'lishi uchun alohida saqlanadi.
    #
    # Faqat Duel g'alabalari hisoblanadi (`total_wins`) - Solo'da raqib
    # yo'q (g'alaba tushunchasi yo'q), Lobby'da "g'alaba" nima ekanligi
    # (faqat 1-o'rinmi?) noaniq - Duel esa 1v1, "yutish" tushunchasi
    # allaqachon `Duel.user_a_result`/`user_b_result` orqali aniq
    # belgilangan.
    total_wins: Mapped[int] = mapped_column(Integer, default=0)

    # Reyting (`rank`) hech qachon saqlanmaydi - har doim so'rov paytida
    # jonli hisoblanadi (`ORDER BY total_xp DESC`) - shuning uchun "eng
    # yaxshi qachondir erishilgan o'rin" ni bilish uchun alohida maydon
    # kerak. Past qiymat = yaxshiroq o'rin, shuning uchun faqat KAMAYSA
    # yangilanadi (`app/routers/leaderboard.py`dagi `get_player_stats`
    # ichida, statistikani har safar hisoblashda - alohida fon-jarayon
    # kerak emas).
    best_rank_achieved: Mapped[int | None] = mapped_column(Integer, nullable=True)

    interests: Mapped[list | None] = mapped_column(JSON, nullable=True)
    study_place: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quiz_liking: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- Coin/Diamond iqtisodiyoti (2026-09-06) - [[ai_cost_architecture]] ---
    # Coin - yumshoq valyuta, faqat ilova ichi faollik orqali topiladi
    # (sotib olinmaydi), kosmetika/streak-himoya uchun sarflanadi. Diamond -
    # qattiq valyuta, Payme/Click orqali (Telegram bot orqali) sotib
    # olinadi, FAQAT AI-generatsiya uchun sarflanadi. Ikkalasi hech qachon
    # bir-biriga aylantirilmaydi - alohida ustunlar, alohida mantiq.
    coin_balance: Mapped[int] = mapped_column(Integer, default=0)
    diamond_balance: Mapped[int] = mapped_column(Integer, default=0)

    # Kunlik bonuslarning oxirgi berilgan sanasi (Toshkent mahalliy kuni,
    # `app.services.streak.TASHKENT_OFFSET` bilan bir xil kun chegarasi) -
    # bir kunda faqat bir marta berilishini nazorat qilish uchun.
    last_daily_bonus_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_first_game_bonus_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Oxirgi marta "seriyangiz uzilishi mumkin" bildirishnomasi yuborilgan
    # payt (Toshkent mahalliy kuni) - `app.services.streak_reminders`ning
    # fon-vazifasi kuniga bir necha marta tekshiradi, shu ustun bir kunda
    # bittadan ortiq yubormaslikni ta'minlaydi.
    last_streak_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Har bir foydalanuvchining o'z taklif kodi (do'stlarni taklif qilish
    # uchun ulashadi) - ro'yxatdan o'tishda generatsiya qilinadi.
    referral_code: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True, index=True)
    # Kim taklif qilgani - faqat ro'yxatdan o'tishda to'g'ri kod kiritilsa
    # to'ldiriladi, keyinchalik o'zgarmaydi. Taklif qilgan userga bonus
    # ushbu do'st birinchi o'yinini tugatganda beriladi (cheklovsiz -
    # foydalanuvchining o'z qarori bilan).
    referred_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Telegram bot orqali Diamond sotib olish uchun - [[ai_cost_architecture]].
    # Bot'da hisob bog'langandan keyin to'ldiriladi (`app/routers/telegram.py`
    # ning `/telegram/link`i orqali). Telegram user ID'lari 32-bit'dan
    # oshib ketishi mumkin (masalan 5989898989), shuning uchun BigInteger.
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True, index=True)

    duel_invites: Mapped[bool] = mapped_column(Boolean, default=True)
    streak_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    leaderboard_updates: Mapped[bool] = mapped_column(Boolean, default=True)
    friend_requests: Mapped[bool] = mapped_column(Boolean, default=True)
    product_updates: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Xom JWT emas - app.core.security.hash_token() natijasi (SHA-256 hex
    # digest) saqlanadi, DB sizib chiqsa ham to'g'ridan-to'g'ri ishlaydigan
    # token qo'lga tushmasin deb.
    token: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    # Xom 6 xonali kod emas - app.core.security.hash_token() natijasi
    # (SHA-256 hex digest) saqlanadi.
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 6 xonali kod atigi 1 million variantga ega - shu maydon orqali
    # noto'g'ri urinishlar cheklanadi (routers/auth.py'dagi MAX_RESET_ATTEMPTS),
    # aks holda umumiy rate-limiting yo'q hozircha kodni taxmin qilib topish
    # mumkin bo'lardi.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
