from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    # Admin panel (SQLAdmin) sessiya cookie'sini imzolash uchun alohida
    # kalit - JWT imzolash bilan bitta SECRET_KEY'ni bo'lishmaslik uchun
    # (ikkalasi turli maqsad, muammo bittasida ikkinchisiga tarqalmasin).
    ADMIN_SESSION_SECRET: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    FIREBASE_SERVICE_ACCOUNT_PATH: str = "/etc/secrets/firebase-service-account.json"
    # Render'ning "Secret File" xususiyati (yuqoridagi PATH) boshqa
    # hostlarda (masalan Railway) yo'q - shu hostlarda buning o'rniga
    # xizmat hisobi JSON'ining o'zi to'g'ridan-to'g'ri shu o'zgaruvchiga
    # qo'yiladi. Ikkalasi ham bo'sh bo'lsa, Google kirish o'chiq qoladi.
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""

    # Cloudflare R2 (S3-mos) — avatar rasmlari uchun doimiy saqlash.
    # Bo'sh bo'lsa lokal fayl tizimiga tushib qolinadi (faqat dev uchun;
    # Render'ning vaqtinchalik diskida rasmlar restartda yo'qoladi).
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""
    # Bucket'ning ommaviy bazaviy URL'i (masalan https://cdn.zukkor.app yoki
    # r2.dev subdomeni) — oxirida '/' bo'lmasin.
    R2_PUBLIC_BASE_URL: str = ""

    # Admin panel (SQLAdmin, /admin) — kategoriya/savol boshqaruvi uchun.
    # Default yo'q (SECRET_KEY/DATABASE_URL kabi) — shu ikkalasi Render
    # environment variable orqali o'rnatilmasa, ilova butunlay ishga
    # tushmaydi, bo'sh qiymat bilan jim ishlab turmaydi.
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str

    # Gemini — foydalanuvchi yuklagan hujjatdan AI orqali quiz generatsiya
    # qilish uchun. Bo'sh bo'lsa ilova baribir ishga tushadi - shunday
    # holda /ai-quiz/generate xizmat mavjud emasligi haqida xato qaytaradi.
    GEMINI_API_KEY: str = ""

    # Parolni tiklash kodini emailga yuborish uchun (Gmail SMTP - App
    # Password bilan). SMTP_USERNAME bo'sh bo'lsa ilova baribir ishga
    # tushadi - shunday holda forgot-password email jo'natmasdan jim
    # o'tkazib yuboradi (R2/Gemini kabi, hali sozlanmagan bo'lsa ham
    # dev/deploy to'xtab qolmasin deb).
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    # Oddiy Gmail parol emas - Google hisobda 2FA yoqilgach yaratiladigan
    # 16 xonali "App Password".
    SMTP_PASSWORD: str = ""
    # Bo'sh qoldirilsa SMTP_USERNAME'ning o'zi ishlatiladi.
    SMTP_FROM_EMAIL: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    @field_validator("DATABASE_URL")
    @classmethod
    def use_asyncpg_driver(cls, v: str) -> str:
        # Ba'zi provayderlar (masalan Render) postgresql:// yoki postgres:// shaklida beradi,
        # bizga esa asyncpg drayveri uchun postgresql+asyncpg:// kerak
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v


settings = Settings()
