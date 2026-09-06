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

    # Sentry — backend xatolarini kuzatish uchun (https://sentry.io). Bo'sh
    # bo'lsa ilova baribir ishga tushadi, shunchaki hech narsa yubormaydi -
    # R2/Gemini/SMTP kabi, hali sozlanmagan bo'lsa ham dev/deploy to'xtab
    # qolmasin deb.
    SENTRY_DSN: str = ""
    # Har bir so'rovni emas, faqat shu foizini "performance" trace sifatida
    # yuboradi - xatolarni ushlash uchun bu shart emas, lekin kvota/xarajatni
    # tejaydi. Xatolarning o'zi (capture_exception/logger.exception) bu
    # sozlamadan mustaqil ravishda har doim to'liq yuboriladi.
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    # Qaysi muhitdan yuborilganini Sentry'da ajratish uchun (masalan
    # production/staging) - Railway environment variable orqali o'rnatiladi.
    ENVIRONMENT: str = "production"

    # --- Coin/Diamond iqtisodiyoti (2026-09-06 qaror) ---
    # Ro'yxatdan o'tganda beriladigan bepul Diamond miqdori - foydalanuvchi
    # birinchi marta AI-generatsiyani hech narsa to'lamasdan sinab ko'rishi
    # uchun. Aniq raqam hali narxlash bosqichida belgilanmagan - shu
    # yerdan (yoki Railway env-var orqali) kodni o'zgartirmasdan sozlanadi.
    DEFAULT_STARTING_DIAMONDS: int = 50
    # Diamond narxlash formulasi: sotish narxi = 4 x (haqiqiy API xarajati),
    # xarajat esa 2027-yil standart Gemini 3.6 Flash tarifidan hisoblanadi
    # (joriy 2026 chegirmali tarifidan emas - ataylab marja zaxirasi
    # sifatida, [[ai_cost_architecture]] xotirasida qaror qilingan).
    GEMINI_2027_INPUT_USD_PER_1M_TOKENS: float = 1.5
    GEMINI_2027_OUTPUT_USD_PER_1M_TOKENS: float = 7.5
    DIAMOND_MARKUP_MULTIPLIER: float = 4.0
    # 1 Diamond qancha AQSH dollariga teng - Payme/Click paket narxlari
    # hali belgilanmagani uchun bu ORALIQ/vaqtinchalik qiymat: faqat "bitta
    # generatsiya nechta Diamond turadi" hisobini chiqarish uchun kerak,
    # yakuniy paket kursi bilan albatta qayta ko'rib chiqiladi.
    USD_PER_DIAMOND: float = 0.001
    # Taxminiy token soni = belgilar soni / shu son (Diamond yetarliligini
    # generatsiya BOSHLANISHIDAN oldin taxminiy tekshirish uchun - haqiqiy
    # narx har doim generatsiya tugagach, haqiqiy token sonidan hisoblanadi).
    # `GET /wallet/pricing` orqali Flutter'ga ham beriladi - shu bilan
    # ilova serverga so'rov yubormasdan JONLI taxmin ko'rsata oladi.
    CHARS_PER_TOKEN_ESTIMATE: int = 4

    # Telegram bot - Diamond sotib olish kanali. @BotFather'dan olinadi
    # (foydalanuvchi o'zi qiladi - bu qadamni Claude bosib chiqolmaydi).
    # Bo'sh bo'lsa /telegram/webhook hech narsa qilmasdan 200 qaytaradi -
    # Gemini/R2/SMTP kabi, hali sozlanmagan bo'lsa ham ilova ishga tushadi.
    TELEGRAM_BOT_TOKEN: str = ""

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
