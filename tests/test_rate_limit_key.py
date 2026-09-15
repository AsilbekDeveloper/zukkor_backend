"""Rate-limit kaliti - foydalanuvchi autentifikatsiya qilingan bo'lsa
uning ID'si bo'yicha, aks holda IP bo'yicha cheklaydi (2026-09-16,
xavfsizlik auditi: faqat IP bo'yicha cheklash VPN/proksi bilan aylanib
o'tiladi - `app/core/limiter.py`)."""

from starlette.requests import Request

from app.core.limiter import _rate_limit_key
from app.core.security import create_access_token, create_refresh_token


def _request(headers: list[tuple[bytes, bytes]], client_ip: str = "9.9.9.9") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client_ip, 12345),
    }
    return Request(scope)


def test_uses_the_user_id_when_a_valid_access_token_is_present():
    token = create_access_token({"sub": "user-123"})
    request = _request([(b"authorization", f"Bearer {token}".encode())])

    assert _rate_limit_key(request) == "user:user-123"


def test_key_is_ip_independent_for_the_same_user():
    # Xuddi shu foydalanuvchi (token), lekin ikkita BUTUNLAY boshqa IP -
    # ikkalasi ham xuddi shu kalitga tushishi kerak (VPN/proksi bilan
    # limitni aylanib o'tib bo'lmasligini isbotlaydi).
    token = create_access_token({"sub": "user-456"})
    key_a = _rate_limit_key(_request([(b"authorization", f"Bearer {token}".encode())], client_ip="1.1.1.1"))
    key_b = _rate_limit_key(_request([(b"authorization", f"Bearer {token}".encode())], client_ip="2.2.2.2"))

    assert key_a == key_b == "user:user-456"


def test_falls_back_to_ip_when_no_authorization_header():
    request = _request([], client_ip="5.6.7.8")
    assert _rate_limit_key(request) == "ip:5.6.7.8"


def test_falls_back_to_ip_for_a_malformed_token():
    request = _request([(b"authorization", b"Bearer not-a-real-token")], client_ip="5.6.7.8")
    assert _rate_limit_key(request) == "ip:5.6.7.8"


def test_falls_back_to_ip_for_an_expired_or_garbage_bearer_value():
    request = _request([(b"authorization", b"Bearer ")], client_ip="5.6.7.8")
    assert _rate_limit_key(request) == "ip:5.6.7.8"


def test_falls_back_to_ip_for_a_refresh_token_used_as_bearer():
    # Refresh token amal qiladigan JWT, lekin "type": "refresh" - `sub`
    # bo'lsa ham, faqat "access" turi qabul qilinadi (get_current_user
    # bilan bir xil qoida).
    token = create_refresh_token({"sub": "user-789"})
    request = _request([(b"authorization", f"Bearer {token}".encode())])

    assert _rate_limit_key(request) == "ip:9.9.9.9"


def test_non_bearer_authorization_scheme_falls_back_to_ip():
    request = _request([(b"authorization", b"Basic dXNlcjpwYXNz")], client_ip="3.3.3.3")
    assert _rate_limit_key(request) == "ip:3.3.3.3"
