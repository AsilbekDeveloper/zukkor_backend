"""Coin iqtisodiyotining admin (SQLAdmin) orqali ish vaqtida
o'zgartiriladigan parametrlari - `app.services.wallet`dagi ilgarigi
qattiq Python konstantalaridan (`DAILY_LOGIN_BONUS = 5` kabi) farqli,
bularni o'zgartirish uchun qayta deploy shart emas: admin `AppConfig`
jadvalidagi qatorni tahrirlaydi, keyingi so'rovdan boshlab yangi qiymat
ishlatiladi.

`app_config` jadvalida hali qator yo'q kalit uchun (masalan birinchi
marta ishga tushirilganda, `seed_defaults` chaqirilmasdan oldin yoki
undan keyin admin qatorni o'chirib qo'ysa) `DEFAULTS`dagi qiymat
ishlatiladi - shu bilan jadval bo'sh bo'lsa ham ilova hech qachon
qulamaydi.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_config import AppConfig

DAILY_LOGIN_BONUS = "daily_login_bonus"
FIRST_GAME_OF_DAY_BONUS = "first_game_of_day_bonus"
STREAK_BONUS_7D = "streak_bonus_7d"
REFERRAL_REWARD_COINS = "referral_reward_coins"
REFERRAL_REWARD_DIAMONDS = "referral_reward_diamonds"
REFERRAL_MIN_QUESTIONS = "referral_min_questions"
REFERRAL_DAILY_LIMIT = "referral_daily_limit"
SIGNUP_COIN_BONUS = "signup_coin_bonus"
CORRECT_ANSWER_COINS = "correct_answer_coins"
WRONG_ANSWER_PENALTY_COINS = "wrong_answer_penalty_coins"
DUEL_WIN_BONUS = "duel_win_bonus"
DUEL_WIN_BONUS_DAILY_LIMIT = "duel_win_bonus_daily_limit"
QUESTION_APPROVED_BONUS = "question_approved_bonus"
FREE_GET_DIAMOND_AMOUNT = "free_get_diamond_amount"
EXPORT_DIAMOND_COST = "export_diamond_cost"

# 2026-10-14 iqtisodiyot qayta qurilishida olib tashlangan kalitlar
# (savol narxi, muallif ulushi, duel stavkasi va solig'i). `seed_defaults`
# ularning eski `app_config` qatorlarini o'chiradi - admin panelida endi
# hech narsaga ta'sir qilmaydigan sozlama ko'rinib turmasin.
_OBSOLETE_KEYS: frozenset[str] = frozenset(
    {"coin_cost_per_question", "question_author_share_percent", "duel_stake_coins", "duel_tax_percent", "referral_bonus"}
)

DEFAULTS: dict[str, int] = {
    DAILY_LOGIN_BONUS: 5,
    FIRST_GAME_OF_DAY_BONUS: 5,
    STREAK_BONUS_7D: 50,
    # Taklif mukofoti - ikkala tomonga ham (taklif qilgan va qilingan), taklif
    # qilingan foydalanuvchi birinchi SIFATLI o'yinini tugatganda.
    REFERRAL_REWARD_COINS: 100,
    REFERRAL_REWARD_DIAMONDS: 100,
    # Sifatli o'yin: rasmiy kategoriya va kamida shuncha savol.
    REFERRAL_MIN_QUESTIONS: 5,
    # Bir foydalanuvchi bir kunda (Toshkent vaqti) ko'pi bilan nechta
    # mukofot olishi mumkin; 0 - cheklovsiz (hozirgi qaror).
    REFERRAL_DAILY_LIMIT: 0,
    SIGNUP_COIN_BONUS: 50,
    CORRECT_ANSWER_COINS: 1,
    WRONG_ANSWER_PENALTY_COINS: 1,
    DUEL_WIN_BONUS: 5,
    # Ikkita hamkorlashgan akkaunt bittasi ataylab yutqazib, cheksiz
    # bonus yig'masligi uchun.
    DUEL_WIN_BONUS_DAILY_LIMIT: 10,
    QUESTION_APPROVED_BONUS: 5,
    # ~39 Diamond - joriy narxlash formulasi bo'yicha o'rtacha bitta
    # mavzudan AI-test generatsiyasining taxminiy narxi (10 savol,
    # ~500 kirish tokeni) - 5ga ko'paytirilgan va yaxlitlangan. Haqiqiy
    # narx generatsiyadan-generatsiyaga farq qiladi (hujjat hajmi,
    # savol soni), shuning uchun bu ANIQ emas, TAXMINIY kalibrlash -
    # kerak bo'lsa admin panelidan osongina o'zgartiriladi.
    FREE_GET_DIAMOND_AMOUNT: 200,
    # 2026-09-29, foydalanuvchi qarori: quizni PDF/Word sifatida eksport
    # qilish QATIY BELGILANGAN 40 Diamond - savollar soniga QARAMAYDI
    # (avval savol boshiga hisoblanardi, endi doimiy narx). 1 Diamond =
    # taxminan 18 so'm (foydalanuvchi bergan baho) bo'yicha bu ~720 so'mga
    # to'g'ri keladi.
    EXPORT_DIAMOND_COST: 40,
}

_DESCRIPTIONS: dict[str, str] = {
    DAILY_LOGIN_BONUS: "Har kuni ilovaga birinchi kirganda beriladigan Coin",
    FIRST_GAME_OF_DAY_BONUS: "Kunning birinchi o'yinini tugatgandagi bonus Coin",
    STREAK_BONUS_7D: "Har 7 kunlik streak uchun bonus Coin",
    REFERRAL_REWARD_COINS: "Taklif mukofoti: har ikki tomonga Coin (taklif qilingan do'st birinchi sifatli o'yinini tugatganda)",
    REFERRAL_REWARD_DIAMONDS: "Taklif mukofoti: har ikki tomonga Diamond (balans chegarasi 200 qo'llanadi)",
    REFERRAL_MIN_QUESTIONS: "Taklif mukofoti uchun sifatli o'yin: rasmiy kategoriyada kamida shuncha savol",
    REFERRAL_DAILY_LIMIT: "Taklif qilgan foydalanuvchi kuniga necha marta mukofot olishi mumkin (0 - cheklovsiz)",
    SIGNUP_COIN_BONUS: "Ro'yxatdan o'tganda beriladigan boshlang'ich Coin",
    CORRECT_ANSWER_COINS: "Yakka o'yinda rasmiy savolga BIRINCHI marta to'g'ri javob uchun Coin",
    WRONG_ANSWER_PENALTY_COINS: "Yakka o'yinda rasmiy savolga xato javob uchun ayiriladigan Coin (balans 0 dan tushmaydi)",
    DUEL_WIN_BONUS: "Duel g'olibiga tizimdan beriladigan bonus Coin (raqibdan olinmaydi)",
    DUEL_WIN_BONUS_DAILY_LIMIT: "Bir foydalanuvchi kuniga necha marta duel g'olibi bonusini olishi mumkin",
    QUESTION_APPROVED_BONUS: "Taklif qilingan savol tasdiqlanganda muallifga bir martalik Coin",
    FREE_GET_DIAMOND_AMOUNT: "Telegram botda kuniga bir marta beriladigan bepul Diamond miqdori",
    EXPORT_DIAMOND_COST: "Quizni PDF/Word qilib eksport qilish narxi (Diamond, savollar soniga qaramaydi)",
}


def validate_value(key: str, raw_value: str) -> int:
    """Admin panel (`app.admin.AppConfigAdmin.on_model_change`) yangi
    qiymatni saqlashdan OLDIN chaqiradi - 2026-09-13 prod-tayyorlik
    auditi: bu tekshiruv bo'lmaganda, admin panelidagi oddiy matn
    maydoniga noto'g'ri son (manfiy, yoki foiz uchun 100dan katta)
    kiritilsa hech qanday xatolik ko'rsatilmasdan saqlanardi va
    iqtisodiyot hisob-kitoblarini buzardi. Muvaffaqiyatli bo'lsa
    tekshirilgan butun sonni qaytaradi, aks holda adminga ko'rsatiladigan
    aniq xabar bilan `ValueError` ko'taradi (forma xatosi sifatida chiqadi,
    saqlanmaydi)."""
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"'{raw_value}' butun son emas")

    if value < 0:
        raise ValueError("Manfiy qiymat kiritib bo'lmaydi")

    return value


async def get_int(db: AsyncSession, key: str) -> int:
    row = await db.get(AppConfig, key)
    if row is None:
        return DEFAULTS[key]
    try:
        return int(row.value)
    except (TypeError, ValueError):
        return DEFAULTS[key]


async def seed_defaults(db: AsyncSession) -> None:
    """Ilova ishga tushganda (`app/main.py` lifespan) chaqiriladi -
    `app_config`da hali YO'Q kalitlar uchun standart qiymatni yozadi.
    Mavjud qatorlarga tegmaydi (admin allaqachon o'zgartirgan bo'lishi
    mumkin) - shuning uchun har bir deploy'da xavfsiz qayta chaqirsa
    bo'ladi. Endi ishlatilmaydigan (`_OBSOLETE_KEYS`) qatorlar o'chiriladi."""
    await db.execute(delete(AppConfig).where(AppConfig.key.in_(_OBSOLETE_KEYS)))
    existing_result = await db.execute(select(AppConfig.key))
    existing_keys = {row[0] for row in existing_result.all()}
    for key, value in DEFAULTS.items():
        if key not in existing_keys:
            db.add(AppConfig(key=key, value=str(value), description=_DESCRIPTIONS.get(key)))
    await db.commit()
