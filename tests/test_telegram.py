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
from app.services import telegram_bot


async def _create_user(db, email: str, **kwargs) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"), **kwargs)
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
async def test_start_command_creates_a_link_code_for_a_new_telegram_user(db_session):
    await telegram_bot.handle_update(
        db_session,
        {"message": {"text": "/start", "from": {"id": 111}, "chat": {"id": 222}}},
    )

    code = (await db_session.execute(select(TelegramLinkCode))).scalar_one()
    assert code.telegram_user_id == 111
    assert code.telegram_chat_id == 222
    assert code.is_used is False
    assert len(code.code) == 6


@pytest.mark.anyio
async def test_start_command_for_an_already_linked_user_does_not_create_a_new_code(db_session):
    await _create_user(db_session, "a@example.com", telegram_user_id=111)
    await db_session.commit()

    await telegram_bot.handle_update(
        db_session,
        {"message": {"text": "/start", "from": {"id": 111}, "chat": {"id": 222}}},
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


# --- Bot "Free Get" Diamond purchase (callback_query) ---


@pytest.mark.anyio
async def test_buy_callback_credits_diamond_for_a_linked_user(db_session):
    user = await _create_user(db_session, "a@example.com", telegram_user_id=777, diamond_balance=10)
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

    assert user.diamond_balance == 10 + 50
    tx = (await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))).scalar_one()
    assert tx.currency == "diamond"
    assert tx.amount == 50
    assert tx.reason == "purchase"


@pytest.mark.anyio
async def test_buy_callback_for_unlinked_telegram_user_does_not_crash_or_credit_anyone(db_session):
    await telegram_bot.handle_update(
        db_session,
        {
            "callback_query": {
                "id": "cb1",
                "from": {"id": 4242},
                "message": {"chat": {"id": 4242}},
                "data": "buy:small",
            }
        },
    )

    assert (await db_session.execute(select(CurrencyTransaction))).scalars().all() == []


@pytest.mark.anyio
async def test_buy_callback_with_unknown_package_id_does_not_crash(db_session):
    user = await _create_user(db_session, "a@example.com", telegram_user_id=777)
    await db_session.commit()

    await telegram_bot.handle_update(
        db_session,
        {
            "callback_query": {
                "id": "cb1",
                "from": {"id": 777},
                "message": {"chat": {"id": 777}},
                "data": "buy:nonexistent",
            }
        },
    )

    assert user.diamond_balance == 0
