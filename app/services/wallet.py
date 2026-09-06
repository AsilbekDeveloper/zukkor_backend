"""Coin/Diamond iqtisodiyotining markaziy xizmati (2026-09-06,
[[ai_cost_architecture]] qarori asosida).

Ikkita valyuta, hech qachon aralashmaydi:
- Coin (yumshoq) - faqat ilova ichi faollik orqali topiladi, hozircha
  sarflash mexanizmi yo'q (kosmetika/streak-himoya narxi hali
  belgilanmagan - keyinroq qo'shiladi).
- Diamond (qattiq) - ro'yxatdan o'tganda bepul boshlang'ich miqdor
  beriladi, keyin faqat Payme/Click (Telegram bot orqali, keyinroq)
  sotib olinadi. FAQAT AI-generatsiya narxini to'lashga sarflanadi,
  generatsiya TUGAGANDAN keyin, haqiqiy token sarfiga qarab (oldindan
  taxminiy summa emas - shuning uchun "muvaffaqiyatsiz urinishda qaytarish"
  degan alohida mantiq umuman kerak emas: hech qachon bo'lmagan narsa
  uchun pul olinmaydi).

Har bir balans o'zgarishi `CurrencyTransaction`ga yoziladi - bu ham audit,
ham foydalanuvchiga ko'rsatiladigan tarix uchun.
"""

import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.currency_transaction import CurrencyTransaction
from app.models.user import User
from app.services.streak import TASHKENT_OFFSET, update_streak

_REFERRAL_CODE_ALPHABET = string.ascii_uppercase + string.digits
_REFERRAL_CODE_LENGTH = 8

# Coin mukofotlari (2026-09-06 spec) - qat'iy: bular sotib olinmaydi, faqat
# shu harakatlar orqali beriladi.
DAILY_LOGIN_BONUS = 5
FIRST_GAME_OF_DAY_BONUS = 5
STREAK_BONUS_7D = 50
REFERRAL_BONUS = 50


class InsufficientBalanceError(Exception):
    """Foydalanuvchida so'ralgan harakat uchun yetarli Coin/Diamond yo'q."""


def _local_date(dt: datetime) -> object:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + TASHKENT_OFFSET).date()


async def _record(
    db: AsyncSession,
    user: User,
    *,
    currency: str,
    amount: int,
    reason: str,
    extra: dict | None = None,
) -> None:
    """Balansni o'zgartiradi va ledger yozuvini qo'shadi. Chaqiruvchi
    `db.commit()`ni o'zi qiladi (bir nechta `_record` chaqiruvi bitta
    tranzaksiyada birlashishi mumkin, masalan duel'da ikkala o'yinchi)."""
    if currency == "coin":
        user.coin_balance += amount
        balance_after = user.coin_balance
    elif currency == "diamond":
        user.diamond_balance += amount
        balance_after = user.diamond_balance
    else:
        raise ValueError(f"Noma'lum valyuta: {currency}")

    db.add(
        CurrencyTransaction(
            user_id=user.id,
            currency=currency,
            amount=amount,
            reason=reason,
            balance_after=balance_after,
            extra=extra,
        )
    )


async def credit_coin(db: AsyncSession, user: User, amount: int, reason: str, extra: dict | None = None) -> None:
    await _record(db, user, currency="coin", amount=amount, reason=reason, extra=extra)


async def credit_diamond(db: AsyncSession, user: User, amount: int, reason: str, extra: dict | None = None) -> None:
    await _record(db, user, currency="diamond", amount=amount, reason=reason, extra=extra)


async def debit_diamond(db: AsyncSession, user: User, amount: int, reason: str, extra: dict | None = None) -> None:
    """Diamond yechish - `credit_diamond(-amount, ...)` bilan bir xil,
    lekin chaqiruvchi tomonda ishorani unutmaslik uchun alohida nom."""
    await _record(db, user, currency="diamond", amount=-abs(amount), reason=reason, extra=extra)


def generate_referral_code() -> str:
    return "".join(secrets.choice(_REFERRAL_CODE_ALPHABET) for _ in range(_REFERRAL_CODE_LENGTH))


def apply_signup_defaults(user: User) -> None:
    """Yangi foydalanuvchi yaratilganda (register/google_auth) chaqiriladi -
    boshlang'ich Diamond balansini va o'z taklif kodini beradi. `db` kerak
    emas - faqat obyekt maydonlarini o'rnatadi, hech qanday ledger yozuvi
    yaratmaydi (buning uchun pastdagi `signup_bonus_transaction`)."""
    user.diamond_balance = settings.DEFAULT_STARTING_DIAMONDS
    user.referral_code = generate_referral_code()


def signup_bonus_transaction(user: User) -> CurrencyTransaction:
    """`apply_signup_defaults`dan KEYIN, VA `user.id` haqiqatan tayinlangandan
    (ya'ni chaqiruvchi kamida bitta `db.flush()` qilgandan) KEYIN
    chaqirilishi SHART - `mapped_column(default=...)` qiymati obyekt
    yaratilganda EMAS, balki flush paytida tayinlanadi, shuning uchun
    flush'dan oldin `user.id` hali `None`."""
    return CurrencyTransaction(
        user_id=user.id,
        currency="diamond",
        amount=settings.DEFAULT_STARTING_DIAMONDS,
        reason="signup_bonus",
        balance_after=user.diamond_balance,
    )


async def check_and_grant_daily_login_bonus(db: AsyncSession, user: User) -> None:
    """Har safar joriy foydalanuvchi profili o'qilganda (`GET /auth/me`)
    chaqiriladi - shu Toshkent-kunida hali berilmagan bo'lsa, kunlik
    kirish bonusini beradi. Alohida endpoint/tugma kerak emas - foydalanuvchi
    ilovani ochgani (Home har safar `/auth/me`ni yuklaydi) o'zi "kirdi"
    degani."""
    today_local = _local_date(datetime.now(timezone.utc))
    if user.last_daily_bonus_at is not None and _local_date(user.last_daily_bonus_at) == today_local:
        return

    user.last_daily_bonus_at = datetime.now(timezone.utc)
    await credit_coin(db, user, DAILY_LOGIN_BONUS, "daily_login")


async def on_game_finished(
    db: AsyncSession,
    user: User,
    played_at: datetime,
    *,
    is_first_game_ever: bool,
) -> None:
    """Solo/Duel/Lobby - HAR BIR o'yin tugash joyida `update_streak(user,
    played_at)` o'rniga shu chaqiriladi (streak+coin bonuslarini birgalikda
    hisoblash uchun, ular bir xil "kun" mantig'iga bog'liq). Chaqiruvchi
    o'zi `user.games_played += 1`ni ALLAQACHON bajargan bo'lishi kerak -
    `is_first_game_ever` shu bilan hisoblanadi (`games_played == 1`)."""
    old_streak = user.current_streak
    update_streak(user, played_at)

    # Kunning birinchi o'yini bonusi - kuniga faqat 1 marta.
    today_local = _local_date(played_at)
    if user.last_first_game_bonus_at is None or _local_date(user.last_first_game_bonus_at) != today_local:
        user.last_first_game_bonus_at = played_at
        await credit_coin(db, user, FIRST_GAME_OF_DAY_BONUS, "first_game")

    # 7 kunlik streak bonusi - faqat streak ENDI ko'paygan va aynan 7ga
    # bo'linadigan qiymatga yetganda (bir kunda bir necha marta o'ynash
    # qayta-qayta bonus bermaydi, chunki `update_streak` bir kunda
    # current_streak'ni faqat bir marta o'zgartiradi).
    if user.current_streak != old_streak and user.current_streak > 0 and user.current_streak % 7 == 0:
        await credit_coin(db, user, STREAK_BONUS_7D, "streak_bonus_7d", extra={"streak": user.current_streak})

    # Referral bonusi - taklif qilingan do'stning ENG BIRINCHI o'yini
    # tugagach, taklif qilganga beriladi. Cheklovsiz (foydalanuvchining
    # ochiq qarori).
    if is_first_game_ever and user.referred_by_user_id:
        referrer = await db.get(User, user.referred_by_user_id)
        if referrer is not None:
            await credit_coin(db, referrer, REFERRAL_BONUS, "referral", extra={"referred_user_id": user.id})


def diamond_cost_from_tokens(input_tokens: int, output_tokens: int) -> int:
    """AI-generatsiya TUGAGANDAN keyin, Gemini javobidagi haqiqiy token
    sonidan Diamond narxini hisoblaydi: sotish narxi = 4 x (2027-yil
    standart tarifdagi API xarajati). Har doim kamida 1 Diamond olinadi
    (0 chiqishi mumkin emas - eng kichik so'rov ham biror xarajat qiladi)."""
    input_cost_usd = (input_tokens / 1_000_000) * settings.GEMINI_2027_INPUT_USD_PER_1M_TOKENS
    output_cost_usd = (output_tokens / 1_000_000) * settings.GEMINI_2027_OUTPUT_USD_PER_1M_TOKENS
    sale_price_usd = (input_cost_usd + output_cost_usd) * settings.DIAMOND_MARKUP_MULTIPLIER
    diamonds = round(sale_price_usd / settings.USD_PER_DIAMOND)
    return max(1, diamonds)


def estimate_diamond_cost(*, estimated_input_tokens: int, question_count: int) -> int:
    """Generatsiya BOSHLANISHIDAN oldin, taxminiy balans-yetarlilik
    tekshiruvi uchun - hali chiqish (output) tokenlari noma'lum, shuning
    uchun har bir savol uchun ~120 token deb taxmin qilinadi (4 ta variant +
    savol matni uchun JSON chiqish, odatiy savol uzunligida yetarlicha
    zaxira bilan). Haqiqiy yechish har doim `diamond_cost_from_tokens` bilan,
    generatsiya tugagach hisoblanadi - bu faqat "so'rovni boshlashga arziydimi"
    degan arzon oldindan tekshiruv."""
    estimated_output_tokens = question_count * 120
    return diamond_cost_from_tokens(estimated_input_tokens, estimated_output_tokens)
