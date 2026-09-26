"""Coin/Diamond iqtisodiyotining markaziy xizmati (2026-09-06,
[[ai_cost_architecture]] qarori asosida).

Ikkita valyuta, hech qachon aralashmaydi:
- Coin (yumshoq) - faqat ilova ichi faollik orqali topiladi (sotib
  olinmaydi). 2026-09-12'dan boshlab sarflash tomoni ham bor: har bir
  o'ynalgan savol o'yinchidan bir necha Coin oladi, shundan bir qismi
  savol muallifiga (foydalanuvchi taklif qilgan bo'lsa) to'lanadi -
  `charge_for_question_play`ga qarang. Barcha miqdorlar (bonuslar,
  savol narxi, muallif ulushi) `economy_config` orqali admin panelidan
  o'zgartiriladi, qayta deploy shart emas.
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

from sqlalchemy import case, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.currency_transaction import CurrencyTransaction
from app.models.quiz import Question
from app.models.user import User
from app.services import economy_config
from app.services.streak import TASHKENT_OFFSET, update_streak

_REFERRAL_CODE_ALPHABET = string.ascii_uppercase + string.digits
_REFERRAL_CODE_LENGTH = 8

# 2026-09-26, foydalanuvchi qarori: hech kimning diamond_balance'i HECH
# QACHON shu chegaradan oshmasin - "Free Get" kunlik miqdori (200) bilan
# ATAYLAB teng, ya'ni bitta foydalanuvchi eng ko'pi bilan bir kunlik
# to'liq claim'ga teng miqdorni "zaxira" sifatida ushlab turishi mumkin,
# undan ortig'ini yig'a olmaydi (sarflamay davomiy yig'ib borish orqali
# cheksiz zaxiralash imkoni yopiladi). `apply_atomic_balance_delta`
# darajasida (pastda) qo'llanadi - bu YAGONA choke-point, shuning uchun
# diamond qo'shiladigan HAR QANDAY yo'l (bugungi bepul claim, kelajakdagi
# xarid, reservatsiya qaytarilishi) avtomatik shu chegaraga bo'ysunadi,
# har bir chaqiruvchi joyida alohida tekshirish yozish shart emas.
DIAMOND_BALANCE_CAP = 200


class InsufficientBalanceError(Exception):
    """Foydalanuvchida so'ralgan harakat uchun yetarli Coin/Diamond yo'q."""


def _local_date(dt: datetime) -> object:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + TASHKENT_OFFSET).date()


async def apply_atomic_balance_delta(
    db: AsyncSession,
    user: User,
    *,
    currency: str,
    amount: int,
    require_sufficient: bool = False,
    cap: int | None = None,
) -> int:
    """Balansni ATOMIK ravishda o'zgartiradi (bitta SQL
    `UPDATE ... SET balance = balance + :amount ... RETURNING balance`,
    Python darajasida "avval o'qib, keyin yozish" EMAS) va yangi
    qiymatni qaytaradi - HECH QANDAY `CurrencyTransaction` yozmaydi (buni
    chaqiruvchi o'zi qiladi - pastdagi `_record`, yoki `app.admin`dagi
    kabi ledger qatorini boshqa yo'l bilan yaratadigan chaqiruvchi).

    Atomiklik muhim: eski (`user.coin_balance += amount`) yondashuvda
    bir xil foydalanuvchi uchun ikkita parallel so'rov (masalan bir
    vaqtda 2 ta qurilmadan o'ynash, yoki concurrent AI-generatsiya
    so'rovlari) balansni "yo'qolgan yangilanish" (lost update) bilan
    buzardi - ikkinchisi birinchisining natijasini bekor qilib qo'yardi,
    sabab: ikkalasi ham eskirgan qiymatni o'qib, o'shanga qo'shib
    yozardi. Bitta SQL UPDATE esa DB darajasida qatorni avtomatik
    qulflaydi - bu poyga fizik jihatdan mumkin emas (2026-09-13,
    prod-tayyorlik auditi topilmasi - `app/admin.py`dagi
    `CurrencyTransactionAdmin` ham xuddi shu eski usulda edi, endi shu
    funksiyaga o'tkazildi).

    `require_sufficient=True` bo'lsa, yechish (`amount` manfiy) natijasi
    balansni manfiyga tushirsa, HECH NARSA yozilmaydi va
    `InsufficientBalanceError` ko'tariladi - bu tekshiruv ham xuddi shu
    bitta SQL so'rovda (`WHERE balance + amount >= 0`) amalga oshadi,
    shuning uchun oldindan Python'da `if user.balance < cost` tekshirish
    bilan solishtirganda poyga holati yo'q.

    `cap` berilsa (faqat `credit_diamond` shunday chaqiradi, pastga
    qarang) va `amount` musbat bo'lsa, natija shu qiymatdan oshmaydi -
    2026-09-26, foydalanuvchi qarori: hech kimning diamond balansi HECH
    QACHON `DIAMOND_BALANCE_CAP`dan oshmasin. `reserve_diamond`/
    `release_diamond_reservation`/`finalize_diamond_reservation` ATAYLAB
    `cap` bermaydi - ular allaqachon foydalanuvchining O'ZINING puli
    (vaqtincha band qilingan, keyin qaytariladigan), shu chegara ularga
    tegsa, "band qilib qaytarish" operatsiyasi noto'g'ri ravishda pulni
    yo'q qilib qo'yardi (bu YANGI daromad emas)."""
    if currency == "coin":
        column = User.coin_balance
    elif currency == "diamond":
        column = User.diamond_balance
    else:
        raise ValueError(f"Noma'lum valyuta: {currency}")

    new_value = column + amount
    if cap is not None and amount > 0:
        # `func.least()` o'rniga portable `CASE WHEN` ishlatiladi -
        # SQLite (testlar)da `least` funksiyasi yo'q, faqat Postgres
        # (production)da bor.
        new_value = case((new_value > cap, cap), else_=new_value)

    stmt = sql_update(User).where(User.id == user.id).values(**{f"{currency}_balance": new_value})
    if require_sufficient:
        stmt = stmt.where(column + amount >= 0)
    stmt = stmt.returning(column)

    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        raise InsufficientBalanceError(f"user={user.id} currency={currency} amount={amount}")

    balance_after = row[0]
    # ORM obyektini yangi qiymat bilan sinxronlaymiz - shu funksiyadan
    # KEYIN kod (masalan `charge_for_question_play`ning o'zi) darhol
    # `user.coin_balance`ni o'qisa, eskirgan emas, aynan hozir yozilgan
    # qiymatni ko'rsin.
    setattr(user, f"{currency}_balance", balance_after)
    return balance_after


async def _record(
    db: AsyncSession,
    user: User,
    *,
    currency: str,
    amount: int,
    reason: str,
    extra: dict | None = None,
    require_sufficient: bool = False,
    cap: int | None = None,
) -> None:
    """`apply_atomic_balance_delta` + shu o'zgarish uchun ledger yozuvi.
    Chaqiruvchi `db.commit()`ni o'zi qiladi (bir nechta `_record`
    chaqiruvi bitta tranzaksiyada birlashishi mumkin, masalan duel'da
    ikkala o'yinchi).

    Ledger'ga `amount` (so'ralgan miqdor) EMAS, HAQIQIY o'zgargan miqdor
    (`balance_after - balance_before`) yoziladi - `cap` tufayli kesib
    tashlangan bo'lsa (masalan balans 150 edi, +200 so'raldi, lekin
    `DIAMOND_BALANCE_CAP=200`gacha faqat +50 qo'shildi), foydalanuvchi
    tarixida haqiqatan nechchi Diamond kelgani ko'rinsin, so'ralgan
    (lekin qisman rad etilgan) miqdor emas."""
    balance_before = getattr(user, f"{currency}_balance")
    balance_after = await apply_atomic_balance_delta(
        db, user, currency=currency, amount=amount, require_sufficient=require_sufficient, cap=cap
    )
    db.add(
        CurrencyTransaction(
            user_id=user.id,
            currency=currency,
            amount=balance_after - balance_before,
            reason=reason,
            balance_after=balance_after,
            extra=extra,
        )
    )


async def credit_coin(db: AsyncSession, user: User, amount: int, reason: str, extra: dict | None = None) -> None:
    await _record(db, user, currency="coin", amount=amount, reason=reason, extra=extra)


async def credit_diamond(db: AsyncSession, user: User, amount: int, reason: str, extra: dict | None = None) -> None:
    """Foydalanuvchiga YANGI Diamond beriladigan HAR BIR joy shu orqali
    o'tishi kerak (hozir: Telegram botning kunlik "Free Get"i, admin
    panelidan qo'lda kredit) - `DIAMOND_BALANCE_CAP` shu yerda
    qo'llaniladi (`reserve_diamond`/`release_diamond_reservation`/
    `finalize_diamond_reservation`da EMAS - ular yangi daromad emas,
    foydalanuvchining o'zining vaqtincha band qilingan pulini qaytaradi)."""
    await _record(db, user, currency="diamond", amount=amount, reason=reason, extra=extra, cap=DIAMOND_BALANCE_CAP)


async def reserve_diamond(db: AsyncSession, user: User, amount: int) -> None:
    """AI-generatsiya BOSHLANISHIDAN OLDIN (Gemini chaqirilishidan oldin),
    taxminiy narxni ATOMIK ravishda "band qiladi" (balansdan darhol
    yechib qo'yadi, lekin hali CurrencyTransaction YOZMAYDI - ledger'da
    foydalanuvchiga faqat HAQIQIY yakuniy narx bitta yozuv sifatida
    ko'rinadi, `finalize_diamond_reservation`ga qarang).

    2026-09-16, xavfsizlik auditi: avvalgi kod balansni Python darajasida
    o'qib solishtirar (`if user.diamond_balance < estimated_cost`), keyin
    Gemini MUVAFFAQIYATLI chaqirilgandan SO'NG `require_sufficient=False`
    bilan yechardi. Bu ikki bosqich orasida poyga (race condition) bor
    edi: bitta foydalanuvchidan bir vaqtning o'zida kelgan bir nechta
    parallel so'rov hammasi BIR XIL (eskirgan) boshlang'ich balansni
    o'qib, hammasi "yetarli" degan noto'g'ri xulosaga kelib, hammasi
    Gemini'ni chaqirar edi - 1 ta Diamondi bor odam ham bir nechta
    parallel so'rov bilan bir nechta Gemini chaqiruvini "to'lovsiz"
    qildira olardi.

    Bu funksiya o'rniga DB darajasidagi bitta atomik
    `UPDATE ... WHERE balance + amount >= 0` ishlatadi
    (`apply_atomic_balance_delta(require_sufficient=True)`) - parallel
    so'rovlar endi PostgreSQL'ning qator darajasidagi qulfi orqali
    navbat bilan ishlaydi, ikkinchisi birinchisi band qilib ulgurgan
    miqdorni ALLAQACHON kamaytirilgan balansdan ko'radi. `SELECT ... FOR
    UPDATE` bilan qo'lda qulflashdan farqli - bu yerda qulf FAQAT shu
    tezkor UPDATE davomida ushlab turiladi, keyin darhol COMMIT qilinadi
    (pastda) - sekin (soniyalar davom etadigan) Gemini so'rovi davomida
    DB tranzaksiyasi/qulfi OCHIQ qolib ketmaydi, aks holda bir xil
    foydalanuvchining boshqa (masalan Profil balansini o'qiydigan)
    so'rovlari ham shu vaqt osilib qolardi.

    Yetarli bo'lmasa `InsufficientBalanceError` ko'taradi (hech narsa
    yozilmagan - muvaffaqiyatsiz `UPDATE` shunchaki 0 qatorga tegadi,
    ROLLBACK qilish shart emas: buni qasddan qilmaymiz, chunki
    `db.rollback()` sessiyadagi barcha ORM obyektlarini "eskirgan" deb
    belgilaydi va chaqiruvchi keyin ularning maydonini o'qisa, kutilmagan
    qo'shimcha SELECT'ga olib keladi)."""
    await apply_atomic_balance_delta(db, user, currency="diamond", amount=-abs(amount), require_sufficient=True)
    await db.commit()


async def release_diamond_reservation(db: AsyncSession, user: User, amount: int) -> None:
    """`reserve_diamond` band qilgan miqdorni TO'LIQ qaytaradi -
    generatsiya butunlay muvaffaqiyatsiz bo'lganda (Gemini xatosi yoki
    kunlik chegara) chaqiriladi. Hech qanday CurrencyTransaction
    yaratmaydi - xuddi hech narsa bo'lmagandek (foydalanuvchi hech qachon
    bo'lmagan narsa uchun to'lamaydi, avvalgi xulq-atvor bilan bir xil).
    Darhol COMMIT qiladi."""
    await apply_atomic_balance_delta(db, user, currency="diamond", amount=abs(amount), require_sufficient=False)
    await db.commit()


async def finalize_diamond_reservation(
    db: AsyncSession,
    user: User,
    *,
    reserved_amount: int,
    actual_amount: int,
    reason: str,
    extra: dict | None = None,
) -> None:
    """Generatsiya MUVAFFAQIYATLI tugagach chaqiriladi - `reserve_diamond`
    band qilgan (`reserved_amount`) va haqiqiy (`actual_amount`, token
    sarfidan hisoblangan) narx orasidagi farqni to'g'rilaydi (band
    qilingan miqdor haqiqiysidan qimmat chiqsa - qaytaradi, arzon chiqsa -
    qo'shimcha yechadi, bu bosqichda ham balans manfiyga tushishi mumkin,
    chunki Gemini xarajati ALLAQACHON qilingan) va ANIQ BITTA
    CurrencyTransaction yozuvini yaratadi - Wallet ekranida foydalanuvchi
    bitta aniq "AI generatsiya" qatorini ko'radi, taxminiy/tuzatish
    yozuvlariga bo'linib ketmaydi. Chaqiruvchi COMMIT qilishi kerak
    (odatda quiz/savollar yaratish bilan bir tranzaksiyada)."""
    delta = actual_amount - reserved_amount
    if delta != 0:
        await apply_atomic_balance_delta(db, user, currency="diamond", amount=-delta, require_sufficient=False)
    db.add(
        CurrencyTransaction(
            user_id=user.id,
            currency="diamond",
            amount=-actual_amount,
            reason=reason,
            balance_after=user.diamond_balance,
            extra=extra,
        )
    )


# --- Duel stavkasi (2026-09-17, anti-farming/inflyatsiya himoyasi) ---
#
# UCHALASI HAM FAQAT "coin" valyutasi bilan ishlaydi - `currency`
# parametri yo'q, ataylab qattiq kodlangan. Duel yutug'i hech qachon
# Diamond bermasligi shu bilan STRUKTURAVIY ravishda kafolatlanadi (bu
# funksiyalarni chaqirib Diamond berish shunchaki MUMKIN EMAS), izohga
# tayanadigan konvensiya emas. Diamond FAQAT `app/routers/ai_quiz.py`
# orqali (AI-generatsiya narxi sifatida) harakatlanadi.


async def charge_duel_stake(db: AsyncSession, user: User, amount: int) -> None:
    """Anti-Rage-Quit: duel ACTIVE bo'lishi BILANOQ (`duel_engine.start_duel`,
    savollar tanlanishidan HAM oldin) ikkala o'yinchidan ham stavkani
    ATOMIK ravishda yechadi (`_record(require_sufficient=True)` - xuddi
    `charge_for_question_play`dagi kabi, DB darajasidagi qulf orqali
    poyga holatisiz). Shu bosqichda pul allaqachon "band" - o'yin
    o'rtasida kimdir chiqib ketsa/aloqasi uzilsa, qaytarish degan alohida
    mantiq SHART EMAS: chiquvchi shunchaki hech narsa qaytarib olmaydi
    (`duel_engine.forfeit_duel`ga qarang).

    Yetarli bo'lmasa `InsufficientBalanceError` ko'taradi - chaqiruvchi
    buni duel UMUMAN boshlanmasligiga aylantiradi. Chaqiruvchi COMMIT
    qilishi kerak."""
    await _record(db, user, currency="coin", amount=-abs(amount), reason="duel_stake", require_sufficient=True)


async def refund_duel_stake(db: AsyncSession, user: User, amount: int, reason: str = "duel_stake_refund") -> None:
    """Stavkani TO'LIQ qaytaradi - duel DURANG bilan tugaganda (ikkala
    tomon ham o'zining tikkan puli bilan qoladi, soliqsiz) yoki duel
    umuman boshlanmay qolganda (masalan kategoriyada savol topilmadi).
    Chaqiruvchi COMMIT qilishi kerak."""
    await credit_coin(db, user, amount, reason)


async def award_duel_prize(db: AsyncSession, winner: User, stake_per_player: int, tax_percent: int) -> int:
    """Anti-Farming/Deflyatsiya: g'olibga yutuq fondini (ikkala
    o'yinchining stavkasi yig'indisi) TO'LIQ EMAS - `tax_percent` ulush
    ushlab qolingandan (BURN qilingandan) keyin beradi. Ushlab qolingan
    qism HECH KIMGA yozilmaydi va hech qanday CurrencyTransaction'da
    ko'rinmaydi - tizimdagi umumiy Coin miqdoridan butunlay yo'q bo'ladi
    (aks holda ikkita hamkorlashgan akkaunt bitta doim ATAYLAB yutqazib,
    bir-biriga cheksiz va bepul Coin ko'chirishi mumkin bo'lardi - bu
    soliq shu yo'lni yopadi).

    Xuddi shu soliq FORFEIT (raqib chiqib ketgan) holatida ham qo'llanadi
    (`duel_engine.forfeit_duel`) - aks holda ikkita hamkorlashgan akkaunt
    "duel boshlab, birortasi ataylab chiqib ketish" orqali soliqni
    butunlay aylanib o'tishi mumkin bo'lardi.

    G'olibga yozilgan yakuniy Coin miqdorini qaytaradi (Duel natijasi
    xabarida ko'rsatish uchun). Chaqiruvchi COMMIT qilishi kerak."""
    pool = stake_per_player * 2
    tax = (pool * tax_percent) // 100
    payout = pool - tax
    await credit_coin(
        db, winner, payout, "duel_prize",
        extra={"pool": pool, "tax_percent": tax_percent, "tax": tax},
    )
    return payout


async def debit_coin(db: AsyncSession, user: User, amount: int, reason: str, extra: dict | None = None) -> None:
    """Coin yechish - balans yetarli bo'lmasa ham manfiyga tushiradi.
    Balans HECH QACHON manfiyga tushmasligi kerak bo'lgan chaqiruvchilar
    (masalan `charge_for_question_play`) buning o'rniga `_record`ni
    `require_sufficient=True` bilan to'g'ridan-to'g'ri chaqiradi."""
    await _record(db, user, currency="coin", amount=-abs(amount), reason=reason, extra=extra)


def generate_referral_code() -> str:
    return "".join(secrets.choice(_REFERRAL_CODE_ALPHABET) for _ in range(_REFERRAL_CODE_LENGTH))


async def apply_signup_defaults(db: AsyncSession, user: User) -> None:
    """Yangi foydalanuvchi yaratilganda (register/google_auth) chaqiriladi -
    boshlang'ich Diamond va Coin balansini va o'z taklif kodini beradi.
    Faqat obyekt maydonlarini o'rnatadi, hech qanday ledger yozuvi
    yaratmaydi (buning uchun pastdagi `signup_bonus_transaction`/
    `signup_coin_bonus_transaction`) - `db` faqat Coin miqdorini
    `economy_config`dan o'qish uchun kerak."""
    user.diamond_balance = min(settings.DEFAULT_STARTING_DIAMONDS, DIAMOND_BALANCE_CAP)
    user.coin_balance = await economy_config.get_int(db, economy_config.SIGNUP_COIN_BONUS)
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
        amount=user.diamond_balance,
        reason="signup_bonus",
        balance_after=user.diamond_balance,
    )


def signup_coin_bonus_transaction(user: User) -> CurrencyTransaction:
    """`signup_bonus_transaction`ning Coin varianti - xuddi shu chaqiruv
    tartibi shartlariga bo'ysunadi (flush'dan keyin)."""
    return CurrencyTransaction(
        user_id=user.id,
        currency="coin",
        amount=user.coin_balance,
        reason="signup_bonus",
        balance_after=user.coin_balance,
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
    bonus = await economy_config.get_int(db, economy_config.DAILY_LOGIN_BONUS)
    await credit_coin(db, user, bonus, "daily_login")


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
        bonus = await economy_config.get_int(db, economy_config.FIRST_GAME_OF_DAY_BONUS)
        await credit_coin(db, user, bonus, "first_game")

    # 7 kunlik streak bonusi - faqat streak ENDI ko'paygan va aynan 7ga
    # bo'linadigan qiymatga yetganda (bir kunda bir necha marta o'ynash
    # qayta-qayta bonus bermaydi, chunki `update_streak` bir kunda
    # current_streak'ni faqat bir marta o'zgartiradi).
    if user.current_streak != old_streak and user.current_streak > 0 and user.current_streak % 7 == 0:
        streak_bonus = await economy_config.get_int(db, economy_config.STREAK_BONUS_7D)
        await credit_coin(db, user, streak_bonus, "streak_bonus_7d", extra={"streak": user.current_streak})

    # Referral bonusi - taklif qilingan do'stning ENG BIRINCHI o'yini
    # tugagach, taklif qilganga beriladi. Cheklovsiz (foydalanuvchining
    # ochiq qarori).
    if is_first_game_ever and user.referred_by_user_id:
        referrer = await db.get(User, user.referred_by_user_id)
        if referrer is not None:
            referral_bonus = await economy_config.get_int(db, economy_config.REFERRAL_BONUS)
            await credit_coin(db, referrer, referral_bonus, "referral", extra={"referred_user_id": user.id})


async def charge_for_question_play(db: AsyncSession, player: User, question_id: int) -> None:
    """Solo/Duel/Lobby'ning har UCHALASIDA ham, o'yinchi bitta savolga
    javob bergan har safar chaqiriladi (2026-09-12 qaror - "gold coin
    faqat ilova ichida ishlab topiladi", sarflash tomoni).

    O'yinchidan bitta savol narxini yechadi; agar shu savol biror
    foydalanuvchi tomonidan qo'shilgan bo'lsa (`Question.created_by_user_id`,
    taklif qilib tasdiqlangan savollar), narxning bir qismini o'sha
    muallifga to'laydi - o'zining o'z savoliga javob berishi hisobga
    olinmaydi (aks holda cheksiz coin fermalash imkoni bo'lardi).

    Balans yetarli bo'lmasa HECH NARSA qilinmaydi (na o'yinchidan
    yechiladi, na muallifga to'lanadi) - o'yin hech qachon
    bloklanmaydi va balans manfiyga tushmaydi, savol shunchaki
    "bepul" o'tadi. Bu tekshiruv ATOMIK (`_record`ning
    `require_sufficient=True`si) - ikkita parallel savol-javob (masalan
    Lobby'da bir necha ishtirokchi, yoki bitta user 2 ta qurilmadan)
    balansni bir vaqtda yechishga urinsa ham, DB darajasidagi qulf
    tufayli ikkalasi ham "yetarli" deb noto'g'ri o'tib keta olmaydi
    (avvalgi Python darajasidagi `player.coin_balance < cost` tekshiruvi
    poyga holatiga ochiq edi - 2026-09-13 audit topilmasi)."""
    cost = await economy_config.get_int(db, economy_config.COIN_COST_PER_QUESTION)
    if cost <= 0:
        return

    try:
        await _record(
            db, player, currency="coin", amount=-cost, reason="question_play",
            extra={"question_id": question_id}, require_sufficient=True,
        )
    except InsufficientBalanceError:
        return

    question = await db.get(Question, question_id)
    author_id = question.created_by_user_id if question is not None else None
    if author_id is None or author_id == player.id:
        return

    author = await db.get(User, author_id)
    if author is None:
        return

    share_percent = await economy_config.get_int(db, economy_config.QUESTION_AUTHOR_SHARE_PERCENT)
    payout = (cost * share_percent) // 100
    if payout > 0:
        await credit_coin(
            db, author, payout, "question_royalty", extra={"question_id": question_id, "payer_user_id": player.id}
        )


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
