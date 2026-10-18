from pydantic import BaseModel, Field


class ReferralOut(BaseModel):
    code: str
    # Ulashiladigan havola (`GET /r/{kod}`).
    link: str

    # Mukofot shartlari - ilova matnlarni shu qiymatlar bilan ko'rsatadi,
    # shuning uchun admin paneldan o'zgartirilsa ilovani yangilash shart emas.
    reward_coins: int
    reward_diamonds: int
    min_questions: int

    registered_count: int
    rewarded_count: int
    coins_earned: int
    diamonds_earned: int

    # Hali hech kim taklif qilmagan va yaqinda ro'yxatdan o'tgan bo'lsa,
    # kodni qo'lda kiritish mumkin.
    can_apply_code: bool


class ApplyReferralRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=12)
