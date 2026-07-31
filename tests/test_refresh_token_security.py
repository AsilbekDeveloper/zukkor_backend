import pytest
from sqlalchemy import select

from app.core.security import hash_token
from app.models.user import RefreshToken, User
from app.routers.auth import change_password, login, logout, refresh_tokens, register
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RefreshRequest, RegisterRequest


async def _register(db, email="user@example.com", password="Parol1234"):
    return await register(RegisterRequest(email=email, password=password), db=db)


@pytest.mark.anyio
async def test_refresh_token_is_stored_hashed_not_plaintext(db_session):
    tokens = await _register(db_session)

    result = await db_session.execute(select(RefreshToken))
    stored = result.scalars().one()

    assert stored.token != tokens.refresh_token
    assert stored.token == hash_token(tokens.refresh_token)


@pytest.mark.anyio
async def test_refresh_rotates_token_and_chain_keeps_working_when_not_reused(db_session):
    # Suiiste'mol qilinmagan normal oqim: har safar yangi token bilan
    # oldinga siljish davom etaveradi.
    tokens = await _register(db_session)

    rotated_once = await refresh_tokens(RefreshRequest(refresh_token=tokens.refresh_token), db=db_session)
    assert rotated_once.refresh_token != tokens.refresh_token

    rotated_twice = await refresh_tokens(RefreshRequest(refresh_token=rotated_once.refresh_token), db=db_session)
    assert rotated_twice.refresh_token != rotated_once.refresh_token
    assert rotated_twice.access_token


@pytest.mark.anyio
async def test_old_rotated_out_token_stops_working(db_session):
    tokens = await _register(db_session)
    await refresh_tokens(RefreshRequest(refresh_token=tokens.refresh_token), db=db_session)

    with pytest.raises(Exception):
        await refresh_tokens(RefreshRequest(refresh_token=tokens.refresh_token), db=db_session)


@pytest.mark.anyio
async def test_reusing_a_rotated_out_token_revokes_the_whole_family(db_session):
    tokens = await _register(db_session)

    rotated = await refresh_tokens(RefreshRequest(refresh_token=tokens.refresh_token), db=db_session)

    # Eski (allaqachon aylantirilgan) tokenni qayta ishlatishga urinamiz -
    # bu o'g'irlanish belgisi sifatida talqin qilinishi va foydalanuvchining
    # BARCHA tokenlarini (yangi aylantirilgani ham) bekor qilishi kerak.
    with pytest.raises(Exception):
        await refresh_tokens(RefreshRequest(refresh_token=tokens.refresh_token), db=db_session)

    with pytest.raises(Exception):
        await refresh_tokens(RefreshRequest(refresh_token=rotated.refresh_token), db=db_session)

    result = await db_session.execute(select(RefreshToken))
    all_tokens = result.scalars().all()
    assert all_tokens, "kutilmagan holat: hech qanday token topilmadi"
    assert all(t.is_revoked for t in all_tokens)


@pytest.mark.anyio
async def test_logout_revokes_the_given_token(db_session):
    tokens = await _register(db_session)

    await logout(RefreshRequest(refresh_token=tokens.refresh_token), db=db_session)

    with pytest.raises(Exception):
        await refresh_tokens(RefreshRequest(refresh_token=tokens.refresh_token), db=db_session)


@pytest.mark.anyio
async def test_changing_password_revokes_all_existing_refresh_tokens(db_session):
    tokens_a = await _register(db_session, email="multi@example.com")

    result = await db_session.execute(select(User).where(User.email == "multi@example.com"))
    user = result.scalar_one()

    # Ikkinchi qurilmadan login qilingandek yana bitta refresh token.
    tokens_b = await login(LoginRequest(email="multi@example.com", password="Parol1234"), db=db_session)

    await change_password(
        ChangePasswordRequest(current_password="Parol1234", new_password="YangiParol1234"),
        current_user=user,
        db=db_session,
    )

    with pytest.raises(Exception):
        await refresh_tokens(RefreshRequest(refresh_token=tokens_a.refresh_token), db=db_session)

    with pytest.raises(Exception):
        await refresh_tokens(RefreshRequest(refresh_token=tokens_b.refresh_token), db=db_session)
