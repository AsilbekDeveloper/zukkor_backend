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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_config import AppConfig

DAILY_LOGIN_BONUS = "daily_login_bonus"
FIRST_GAME_OF_DAY_BONUS = "first_game_of_day_bonus"
STREAK_BONUS_7D = "streak_bonus_7d"
REFERRAL_BONUS = "referral_bonus"
SIGNUP_COIN_BONUS = "signup_coin_bonus"
COIN_COST_PER_QUESTION = "coin_cost_per_question"
QUESTION_AUTHOR_SHARE_PERCENT = "question_author_share_percent"
DUEL_STAKE_COINS = "duel_stake_coins"
DUEL_TAX_PERCENT = "duel_tax_percent"

DEFAULTS: dict[str, int] = {
    DAILY_LOGIN_BONUS: 5,
    FIRST_GAME_OF_DAY_BONUS: 5,
    STREAK_BONUS_7D: 50,
    REFERRAL_BONUS: 50,
    SIGNUP_COIN_BONUS: 50,
    COIN_COST_PER_QUESTION: 1,
    QUESTION_AUTHOR_SHARE_PERCENT: 70,
    DUEL_STAKE_COINS: 10,
    DUEL_TAX_PERCENT: 10,
}

# Foizni ifodalaydigan kalitlar - 0 dan 100 gacha bo'lishi shart. Aks
# holda (masalan 150% yoki -20%) `wallet.charge_for_question_play`dagi
# `payout = (cost * share_percent) // 100` yechilgan narxdan KO'PROQ
# to'lab yuborishi (>100) yoki savol muallifidan pul YECHIB olishi
# (<0, chunki `credit_coin`ga manfiy son berilgan bo'lardi) mumkin edi.
# `DUEL_TAX_PERCENT` ham shu sababdan bu ro'yxatda - 100dan katta bo'lsa
# `wallet.award_duel_prize` g'olibdan pul yechib olardi.
_PERCENT_KEYS: frozenset[str] = frozenset({QUESTION_AUTHOR_SHARE_PERCENT, DUEL_TAX_PERCENT})

_DESCRIPTIONS: dict[str, str] = {
    DAILY_LOGIN_BONUS: "Har kuni ilovaga birinchi kirganda beriladigan Coin",
    FIRST_GAME_OF_DAY_BONUS: "Kunning birinchi o'yinini tugatgandagi bonus Coin",
    STREAK_BONUS_7D: "Har 7 kunlik streak uchun bonus Coin",
    REFERRAL_BONUS: "Taklif qilingan do'stning birinchi o'yinidan keyin taklif qilganga Coin",
    SIGNUP_COIN_BONUS: "Ro'yxatdan o'tganda beriladigan boshlang'ich Coin",
    COIN_COST_PER_QUESTION: "Bitta savolga javob berish o'yinchiga necha Coin turadi",
    QUESTION_AUTHOR_SHARE_PERCENT: "Savol muallifiga tegadigan ulush (foizda, 0-100)",
    DUEL_STAKE_COINS: "Duel boshlanganda har bir o'yinchidan yechiladigan stavka (Coin)",
    DUEL_TAX_PERCENT: "Duel yutuq fondidan ushlab qolinadigan (yo'q qilinadigan) soliq (foizda, 0-100)",
}


def validate_value(key: str, raw_value: str) -> int:
    """Admin panel (`app.admin.AppConfigAdmin.on_model_change`) yangi
    qiymatni saqlashdan OLDIN chaqiradi - 2026-09-13 prod-tayyorlik
    auditi: bu tekshiruv bo'lmaganda, admin panelidagi oddiy matn
    maydoniga noto'g'ri son (manfiy, yoki foiz uchun 100dan katta)
    kiritilsa hech qanday xatolik ko'rsatilmasdan saqlanardi va
    iqtisodiyot hisob-kitoblarini (`wallet.charge_for_question_play`)
    buzardi. Muvaffaqiyatli bo'lsa tekshirilgan butun sonni qaytaradi,
    aks holda adminga ko'rsatiladigan aniq xabar bilan `ValueError`
    ko'taradi (forma xatosi sifatida chiqadi, saqlanmaydi)."""
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"'{raw_value}' butun son emas")

    if value < 0:
        raise ValueError("Manfiy qiymat kiritib bo'lmaydi")

    if key in _PERCENT_KEYS and value > 100:
        raise ValueError("Foiz 100 dan katta bo'lishi mumkin emas")

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
    bo'ladi."""
    existing_result = await db.execute(select(AppConfig.key))
    existing_keys = {row[0] for row in existing_result.all()}
    for key, value in DEFAULTS.items():
        if key not in existing_keys:
            db.add(AppConfig(key=key, value=str(value), description=_DESCRIPTIONS.get(key)))
    await db.commit()
