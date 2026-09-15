"""`app/core/security.py`ning `hash_password`/`verify_password` - endi
`asyncio.to_thread` orqali ishlaydi (2026-09-16, unumdorlik auditi):
bcrypt CPU-bog'liq va sekin, to'g'ridan-to'g'ri chaqirilsa bitta
process/bitta event loop'da (Duel'ning in-memory holati buni talab
qiladi) shu vaqt davomida BUTUN serverni bloklab qo'yardi."""

import asyncio

import pytest

from app.core.security import hash_password, verify_password


def test_hash_password_is_a_coroutine_function():
    # Sinxron chaqirilib event loop'ni bloklab qo'ymasligini kafolatlash
    # uchun - bu funksiyalar ATAYLAB `async def`.
    assert asyncio.iscoroutinefunction(hash_password)


def test_verify_password_is_a_coroutine_function():
    assert asyncio.iscoroutinefunction(verify_password)


@pytest.mark.anyio
async def test_hash_then_verify_round_trips_correctly():
    hashed = await hash_password("Parol1234")

    assert await verify_password("Parol1234", hashed) is True


@pytest.mark.anyio
async def test_verify_rejects_the_wrong_password():
    hashed = await hash_password("Parol1234")

    assert await verify_password("BoshqaParol1234", hashed) is False


@pytest.mark.anyio
async def test_hash_password_never_stores_the_plaintext():
    hashed = await hash_password("Parol1234")

    assert "Parol1234" not in hashed


@pytest.mark.anyio
async def test_hashing_the_same_password_twice_gives_different_hashes():
    # bcrypt har safar yangi tuz (salt) ishlatadi - takroriy hashlarni
    # solishtirib parolni taxmin qilib bo'lmaydi.
    first = await hash_password("Parol1234")
    second = await hash_password("Parol1234")

    assert first != second
    assert await verify_password("Parol1234", first) is True
    assert await verify_password("Parol1234", second) is True
