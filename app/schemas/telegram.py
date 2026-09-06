from pydantic import BaseModel, Field


class TelegramLinkRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class TelegramLinkOut(BaseModel):
    diamond_balance: int
    coin_balance: int
