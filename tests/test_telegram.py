"""Telegram bot integratsiyasi (2026-09-06) - hisobni bog'lash
(`/telegram/link`) va bot orqali Diamond "Free Get" (haqiqiy to'lov hali
ulanmagan - [[ai_cost_architecture]]). `TELEGRAM_BOT_TOKEN` testlarda
bo'sh bo'lgani uchun `telegram_client`ning haqiqiy HTTP chaqiruvlari
jimgina hech narsa qilmaydi (Gemini/SMTP kabi) - shu yerda faqat DB
tomonidagi natija (balans, ledger, kod holati) tekshiriladi."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.models.currency_transaction import CurrencyTransaction
from app.models.telegram_link_code import TelegramLinkCode
from app.models.user import User
from app.routers.telegram import link_telegram_account
from app.schemas.telegram import TelegramLinkRequest
from app.services import economy_config, telegram_bot, wallet


async def _create_user(db, email: str, **kwargs) -> User:
    user = User(email=email, hashed_password=await hash_password("Parol1234"), **kwargs)
    db.add(user)
    await db.flush()
    return user


async def _create_code(db, *, code: str = "123456", telegram_user_id: int = 555, expires_in_minutes: int = 10) -> TelegramLinkCode:
    link_code = TelegramLinkCode(
        code=code,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes),
    )
    db.add(link_code)
    await db.flush()
    return link_code


# --- /start -> link-code generation ---


@pytest.mark.anyio
async def test_start_command_never_creates_a_link_code(db_session):
    # /start endi faqat tanishtiruv xabari - kod generatsiyasi /link'ga
    # ko'chirildi (2026-09-06, foydalanuvchi ko'rsatmasi bilan).
    await telegram_bot.handle_update(
        db_session,
        {"message": {"text": "/start", "from": {"id": 111}, "chat": {"id": 222}}},
    )

    codes = (await db_session.execute(select(TelegramLinkCode))).scalars().all()
    assert codes == []


@pytest.mark.anyio
async def test_link_command_creates_a_link_code_for_a_new_telegram_user(db_session):
    await telegram_bot.handle_update(
        db_session,
        {"message": {"text": "/link", "from": {"id": 111}, "chat": {"id": 222}}},
    )

    code = (await db_session.execute(select(TelegramLinkCode))).scalar_one()
    assert code.telegram_user_id == 111
    assert code.telegram_chat_id == 222
    assert code.is_used is False
    assert len(code.code) == 6


@pytest.mark.anyio
async def test_link_command_for_an_already_linked_user_does_not_create_a_new_code(db_session):
    await _create_user(db_session, "a@example.com", telegram_user_id=111)
    await db_session.commit()

    await telegram_bot.handle_update(
        db_session,
        {"message": {"text": "/link", "from": {"id": 111}, "chat": {"id": 222}}},
    )

    codes = (await db_session.execute(select(TelegramLinkCode))).scalars().all()
    assert codes == []


# --- POST /telegram/link ---


@pytest.mark.anyio
async def test_link_with_valid_code_sets_telegram_user_id_and_marks_code_used(db_session):
    user = await _create_user(db_session, "a@example.com")
    link_code = await _create_code(db_session, telegram_user_id=999)
    await db_session.commit()

    result = await link_telegram_account(TelegramLinkRequest(code="123456"), current_user=user, db=db_session)

    assert user.telegram_user_id == 999
    assert result.diamond_balance == user.diamond_balance
    await db_session.refresh(link_code)
    assert link_code.is_used is True


@pytest.mark.anyio
async def test_link_with_unknown_code_raises_400(db_session):
    user = await _create_user(db_session, "a@example.com")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await link_telegram_account(TelegramLinkRequest(code="000000"), current_user=user, db=db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_link_with_already_used_code_raises_400(db_session):
    user = await _create_user(db_session, "a@example.com")
    await _create_code(db_session)
    await db_session.commit()
    await link_telegram_account(TelegramLinkRequest(code="123456"), current_user=user, db=db_session)

    other_user = await _create_user(db_session, "b@example.com")
    await db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        await link_telegram_account(TelegramLinkRequest(code="123456"), current_user=other_user, db=db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_link_with_expired_code_raises_400(db_session):
    user = await _create_user(db_session, "a@example.com")
    await _create_code(db_session, expires_in_minutes=-5)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await link_telegram_account(TelegramLinkRequest(code="123456"), current_user=user, db=db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_relinking_a_telegram_account_unlinks_it_from_the_previous_owner(db_session):
    old_owner = await _create_user(db_session, "old@example.com", telegram_user_id=999)
    new_owner = await _create_user(db_session, "new@example.com")
    await _create_code(db_session, telegram_user_id=999)
    await db_session.commit()

    await link_telegram_account(TelegramLinkRequest(code="123456"), current_user=new_owner, db=db_session)

    assert new_owner.telegram_user_id == 999
    assert old_owner.telegram_user_id is None


# --- Bot "Free Get" Diamond (daily_free_get callback_query) - 2026-09-18,
# xavfsizlik auditi: avval CHEKSIZ edi (har bosishda kredit qilinardi),
# endi foydalanuvchiga kuniga FAQAT BIR MARTA, FIKSIRLANGAN
# (`economy_config.FREE_GET_DIAMOND_AMOUNT`) miqdorda beriladi. ---


def _daily_free_get_update(telegram_user_id: int = 777, callback_query_id: str = "cb1") -> dict:
    return {
        "callback_query": {
            "id": callback_query_id,
            "from": {"id": telegram_user_id},
            "message": {"chat": {"id": telegram_user_id}},
            "data": "daily_free_get",
        }
    }


@pytest.mark.anyio
async def test_daily_free_get_credits_the_configured_amount_for_a_linked_user(db_session):
    # Boshlang'ich balans 0 - `FREE_GET_DIAMOND_AMOUNT` (200) aynan
    # `wallet.DIAMOND_BALANCE_CAP`ga teng, shuning uchun bu test faqat
    # to'liq miqdor kredit qilinishini tekshiradi (chegara sinovi
    # pastdagi alohida testda).
    user = await _create_user(db_session, "a@example.com", telegram_user_id=777, diamond_balance=0)
    await db_session.commit()

    await telegram_bot.handle_update(db_session, _daily_free_get_update())

    assert user.diamond_balance == economy_config.DEFAULTS[economy_config.FREE_GET_DIAMOND_AMOUNT]
    tx = (await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))).scalar_one()
    assert tx.currency == "diamond"
    assert tx.amount == economy_config.DEFAULTS[economy_config.FREE_GET_DIAMOND_AMOUNT]
    assert tx.reason == "daily_free_get"
    assert user.last_free_diamond_at is not None


@pytest.mark.anyio
async def test_daily_free_get_never_pushes_balance_above_the_hard_cap(db_session):
    # 2026-09-26, foydalanuvchi qarori: hech kimning diamond balansi
    # 200dan (wallet.DIAMOND_BALANCE_CAP) oshmasin - allaqachon 150si
    # bor foydalanuvchi kunlik 200ni to'liq ololmaydi, faqat chegaragacha.
    user = await _create_user(db_session, "b@example.com", telegram_user_id=778, diamond_balance=150)
    await db_session.commit()

    await telegram_bot.handle_update(db_session, _daily_free_get_update(telegram_user_id=778))

    assert user.diamond_balance == wallet.DIAMOND_BALANCE_CAP


@pytest.mark.anyio
async def test_daily_free_get_for_unlinked_telegram_user_does_not_crash_or_credit_anyone(db_session):
    await telegram_bot.handle_update(db_session, _daily_free_get_update(telegram_user_id=4242))

    assert (await db_session.execute(select(CurrencyTransaction))).scalars().all() == []


@pytest.mark.anyio
async def test_daily_free_get_is_rejected_on_a_second_claim_the_same_day(db_session):
    user = await _create_user(db_session, "a@example.com", telegram_user_id=777, diamond_balance=0)
    await db_session.commit()

    await telegram_bot.handle_update(db_session, _daily_free_get_update())
    first_balance = user.diamond_balance
    assert first_balance > 0

    await telegram_bot.handle_update(db_session, _daily_free_get_update(callback_query_id="cb2"))

    # Balans ikkinchi urinishdan keyin o'zgarmagan - faqat bitta ledger
    # yozuvi bor.
    assert user.diamond_balance == first_balance
    rows = (await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))).scalars().all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_daily_free_get_is_allowed_again_on_a_new_day(db_session):
    user = await _create_user(db_session, "a@example.com", telegram_user_id=777, diamond_balance=0)
    # "Kecha" allaqachon olingan deb belgilaymiz.
    user.last_free_diamond_at = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    await db_session.commit()

    await telegram_bot.handle_update(db_session, _daily_free_get_update())

    assert user.diamond_balance == economy_config.DEFAULTS[economy_config.FREE_GET_DIAMOND_AMOUNT]


@pytest.mark.anyio
async def test_stale_buy_callback_never_credits_anyone(db_session):
    # Eski (2026-09-18'dan oldingi) "Bepul olish" tugmalari foydalanuvchi
    # chatida eski xabar sifatida saqlanib qolgan bo'lishi mumkin - bosilsa
    # endi hech narsa kredit qilmasligi kerak.
    user = await _create_user(db_session, "a@example.com", telegram_user_id=777, diamond_balance=0)
    await db_session.commit()

    await telegram_bot.handle_update(
        db_session,
        {
            "callback_query": {
                "id": "cb1",
                "from": {"id": 777},
                "message": {"chat": {"id": 777}},
                "data": "buy:small",
            }
        },
    )

    assert user.diamond_balance == 0
    assert (await db_session.execute(select(CurrencyTransaction))).scalars().all() == []
