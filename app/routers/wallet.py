from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.currency_transaction import CurrencyTransaction
from app.models.user import User
from app.schemas.wallet import WalletTransactionsOut

router = APIRouter()


@router.get(
    "/transactions",
    response_model=WalletTransactionsOut,
    summary="Coin/Diamond tarixi",
    description="Joriy foydalanuvchining Coin va Diamond bo'yicha barcha "
    "harakatlari (topilgan/sarflangan), eng yangisidan boshlab.",
)
async def get_wallet_transactions(
    limit: int = 30,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    stmt = (
        select(CurrencyTransaction)
        .where(CurrencyTransaction.user_id == current_user.id)
        .order_by(CurrencyTransaction.created_at.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = (await db.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    return WalletTransactionsOut(entries=rows[:limit], has_more=has_more)
