from slowapi import Limiter
from slowapi.util import get_remote_address

# IP bo'yicha cheklaydi (uvicorn --proxy-headers Render/Railway kabi proksi
# orqasida haqiqiy klient IP'ini X-Forwarded-For'dan olishini ta'minlaydi -
# aks holda barcha so'rovlar bitta IP (proksi) sifatida ko'rinib, limit
# ma'nosiz bo'lib qolardi).
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
