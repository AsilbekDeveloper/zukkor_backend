import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_token, verify_password
from app.models.user import PasswordResetCode, RefreshToken, User
from app.routers.auth import forgot_password, login, refresh_tokens, register, reset_password
from app.schemas.auth import ForgotPasswordRequest, LoginRequest, RefreshRequest, RegisterRequest, ResetPasswordRequest

FIXED_CODE = "482913"


@pytest.fixture(autouse=True)
def _fixed_reset_code(monkeypatch):
    # forgot_password() kodni ichkarida generatsiya qilib, faqat hash'ini
    # saqlaydi - testda "to'g'ri kod"ni bilish uchun generatorni sobit
    # qiymatga qulflab qo'yamiz.
    monkeypatch.setattr("app.routers.auth._generate_reset_code", lambda: FIXED_CODE)


async def _register(db, email="user@example.com", password="Parol1234"):
    return await register(RegisterRequest(email=email, password=password), db=db)


@pytest.mark.anyio
async def test_forgot_password_returns_204_for_unknown_email_too(db_session):
    # Email mavjudligini oshkor qilmaslik uchun - topilmasa ham xato bermaydi.
    result = await forgot_password(ForgotPasswordRequest(email="hech-kim@example.com"), db=db_session)
    assert result is None


@pytest.mark.anyio
async def test_forgot_password_creates_a_hashed_reset_code(db_session):
    await _register(db_session)

    await forgot_password(ForgotPasswordRequest(email="user@example.com"), db=db_session)

    result = await db_session.execute(select(PasswordResetCode))
    reset_code = result.scalars().one()
    assert reset_code.code_hash == hash_token(FIXED_CODE)
    assert reset_code.is_used is False
    assert reset_code.attempts == 0


@pytest.mark.anyio
async def test_google_only_account_gets_no_reset_code(db_session):
    # Google orqali kirgan hisobda parol umuman yo'q - tiklashga hech narsa
    # yo'q, shuning uchun kod yaratilmasligi kerak.
    db_session.add(User(id="g-1", email="google@example.com", hashed_password=None, auth_provider="google"))
    await db_session.commit()

    await forgot_password(ForgotPasswordRequest(email="google@example.com"), db=db_session)

    result = await db_session.execute(select(PasswordResetCode))
    assert result.scalars().first() is None


@pytest.mark.anyio
async def test_reset_password_with_correct_code_changes_password(db_session):
    await _register(db_session)
    await forgot_password(ForgotPasswordRequest(email="user@example.com"), db=db_session)

    await reset_password(
        ResetPasswordRequest(email="user@example.com", code=FIXED_CODE, new_password="YangiParol1234"),
        db=db_session,
    )

    result = await db_session.execute(select(User).where(User.email == "user@example.com"))
    user = result.scalar_one()
    assert verify_password("YangiParol1234", user.hashed_password)

    # Endi shu kod bilan yangi login qilib bo'ladi.
    tokens = await login(LoginRequest(email="user@example.com", password="YangiParol1234"), db=db_session)
    assert tokens.access_token


@pytest.mark.anyio
async def test_reset_password_revokes_existing_refresh_tokens(db_session):
    tokens = await _register(db_session)
    await forgot_password(ForgotPasswordRequest(email="user@example.com"), db=db_session)

    await reset_password(
        ResetPasswordRequest(email="user@example.com", code=FIXED_CODE, new_password="YangiParol1234"),
        db=db_session,
    )

    with pytest.raises(Exception):
        await refresh_tokens(RefreshRequest(refresh_token=tokens.refresh_token), db=db_session)


@pytest.mark.anyio
async def test_reset_password_with_wrong_code_fails_and_counts_attempt(db_session):
    await _register(db_session)
    await forgot_password(ForgotPasswordRequest(email="user@example.com"), db=db_session)

    with pytest.raises(HTTPException):
        await reset_password(
            ResetPasswordRequest(email="user@example.com", code="000000", new_password="YangiParol1234"),
            db=db_session,
        )

    result = await db_session.execute(select(PasswordResetCode))
    reset_code = result.scalars().one()
    assert reset_code.attempts == 1
    assert reset_code.is_used is False


@pytest.mark.anyio
async def test_reset_password_locks_out_after_max_attempts(db_session):
    await _register(db_session)
    await forgot_password(ForgotPasswordRequest(email="user@example.com"), db=db_session)

    for _ in range(5):
        with pytest.raises(HTTPException):
            await reset_password(
                ResetPasswordRequest(email="user@example.com", code="000000", new_password="YangiParol1234"),
                db=db_session,
            )

    # Endi TO'G'RI kod bilan ham ishlamasligi kerak - urinishlar tugagan.
    with pytest.raises(HTTPException):
        await reset_password(
            ResetPasswordRequest(email="user@example.com", code=FIXED_CODE, new_password="YangiParol1234"),
            db=db_session,
        )


@pytest.mark.anyio
async def test_requesting_a_new_code_invalidates_the_previous_one(db_session):
    await _register(db_session)
    await forgot_password(ForgotPasswordRequest(email="user@example.com"), db=db_session)
    first_code_hash = hash_token(FIXED_CODE)

    # Ikkinchi so'rov - boshqa kod bilan.
    import app.routers.auth as auth_module

    original = auth_module._generate_reset_code
    auth_module._generate_reset_code = lambda: "111111"
    try:
        await forgot_password(ForgotPasswordRequest(email="user@example.com"), db=db_session)
    finally:
        auth_module._generate_reset_code = original

    result = await db_session.execute(select(PasswordResetCode).order_by(PasswordResetCode.created_at))
    codes = result.scalars().all()
    assert len(codes) == 2
    assert codes[0].code_hash == first_code_hash
    assert codes[0].is_used is True
    assert codes[1].is_used is False

    # Eski kod endi ishlamaydi.
    with pytest.raises(HTTPException):
        await reset_password(
            ResetPasswordRequest(email="user@example.com", code=FIXED_CODE, new_password="YangiParol1234"),
            db=db_session,
        )
