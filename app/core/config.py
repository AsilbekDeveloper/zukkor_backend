from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    FIREBASE_SERVICE_ACCOUNT_PATH: str = "/etc/secrets/firebase-service-account.json"

    # Cloudinary — avatar rasmlari uchun doimiy saqlash (rasmga ixtisoslashgan,
    # ichki CDN + optimizatsiya). Bitta URL: cloudinary://<key>:<secret>@<cloud>.
    # Bo'sh bo'lsa lokal fayl tizimiga tushib qolinadi (faqat dev uchun;
    # Render'ning vaqtinchalik diskida rasmlar restartda yo'qoladi).
    CLOUDINARY_URL: str = ""

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
