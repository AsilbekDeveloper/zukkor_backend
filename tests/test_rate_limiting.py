import pytest
from slowapi.errors import RateLimitExceeded

from app.routers.auth import login, register
from app.schemas.auth import LoginRequest, RegisterRequest
from conftest import make_request


@pytest.mark.anyio
async def test_register_blocks_the_6th_attempt_from_the_same_ip_within_an_hour(db_session):
    ip = "203.0.113.1"
    for i in range(5):
        await register(make_request(ip), RegisterRequest(email=f"user{i}@example.com", password="Parol1234"), db=db_session)

    with pytest.raises(RateLimitExceeded):
        await register(make_request(ip), RegisterRequest(email="user6@example.com", password="Parol1234"), db=db_session)


@pytest.mark.anyio
async def test_register_from_a_different_ip_is_not_affected_by_another_ips_limit(db_session):
    for i in range(5):
        await register(
            make_request("203.0.113.2"), RegisterRequest(email=f"a{i}@example.com", password="Parol1234"), db=db_session
        )

    # Boshqa IP - o'z alohida hisoblagichi, avvalgisi to'lganidan ta'sirlanmasligi kerak.
    result = await register(
        make_request("203.0.113.3"), RegisterRequest(email="b@example.com", password="Parol1234"), db=db_session
    )
    assert result.access_token


@pytest.mark.anyio
async def test_login_blocks_the_11th_attempt_from_the_same_ip_within_a_minute(db_session):
    ip = "203.0.113.4"
    await register(make_request(ip), RegisterRequest(email="loginlimit@example.com", password="Parol1234"), db=db_session)

    for _ in range(10):
        with pytest.raises(Exception):
            # Noto'g'ri parol - bizni qiziqtirgani 401 emas, balki 11-chaqiruvda 429ga aylanishi
            await login(make_request(ip), LoginRequest(email="loginlimit@example.com", password="Notogri"), db=db_session)

    with pytest.raises(RateLimitExceeded):
        await login(make_request(ip), LoginRequest(email="loginlimit@example.com", password="Notogri"), db=db_session)
