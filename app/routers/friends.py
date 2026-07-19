from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.friend_request import FriendRequest
from app.models.friendship import Friendship
from app.models.notification import Notification
from app.models.user import User
from app.schemas.friends import (
    FriendOut,
    FriendRequestCreate,
    FriendSearchOut,
    FriendSearchResultOut,
    FriendsOut,
    IncomingFriendRequestOut,
    IncomingFriendRequestsOut,
)

router = APIRouter()


def _friend_out(user: User) -> FriendOut:
    return FriendOut(
        id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        avatar_color=user.avatar_color,
        avatar_image_path=user.avatar_image_path,
    )


async def _are_friends(db: AsyncSession, user_id: str, other_id: str) -> bool:
    result = await db.execute(
        select(Friendship).where(Friendship.user_id == user_id, Friendship.friend_id == other_id)
    )
    return result.scalar_one_or_none() is not None


async def _create_mutual_friendship(db: AsyncSession, user_a_id: str, user_b_id: str) -> None:
    db.add(Friendship(user_id=user_a_id, friend_id=user_b_id))
    db.add(Friendship(user_id=user_b_id, friend_id=user_a_id))


@router.get("", response_model=FriendsOut, summary="Do'stlar ro'yxati")
async def list_friends(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(User)
        .join(Friendship, Friendship.friend_id == User.id)
        .where(Friendship.user_id == current_user.id)
    )
    result = await db.execute(stmt)
    friends = result.scalars().all()
    return FriendsOut(friends=[_friend_out(f) for f in friends])


@router.get("/search", response_model=FriendSearchOut, summary="Foydalanuvchi qidirish")
async def search_users(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing_friend_ids_subq = select(Friendship.friend_id).where(Friendship.user_id == current_user.id)

    pattern = f"%{q}%"
    stmt = (
        select(User)
        .where(
            User.id != current_user.id,
            User.id.notin_(existing_friend_ids_subq),
            or_(
                User.username.ilike(pattern),
                User.first_name.ilike(pattern),
                User.last_name.ilike(pattern),
            ),
        )
        .limit(limit)
    )
    result = await db.execute(stmt)
    users = result.scalars().all()

    pending_result = await db.execute(
        select(FriendRequest.to_user_id).where(
            FriendRequest.from_user_id == current_user.id, FriendRequest.status == "pending"
        )
    )
    pending_ids = {row[0] for row in pending_result.all()}

    return FriendSearchOut(
        results=[
            FriendSearchResultOut(**_friend_out(u).model_dump(), request_pending=u.id in pending_ids)
            for u in users
        ]
    )


@router.post("/requests", summary="Do'stlik so'rovi yuborish")
async def send_friend_request(
    data: FriendRequestCreate,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    to_user_id = data.to_user_id

    if to_user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="O'zingizga so'rov yubora olmaysiz")

    target_user = await db.get(User, to_user_id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foydalanuvchi topilmadi")

    if await _are_friends(db, current_user.id, to_user_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Siz allaqachon do'stsiz")

    # Men allaqachon shu userga so'rov yuborganmanmi - indempotent, xato yo'q
    existing_outgoing = await db.execute(
        select(FriendRequest).where(
            FriendRequest.from_user_id == current_user.id,
            FriendRequest.to_user_id == to_user_id,
            FriendRequest.status == "pending",
        )
    )
    if existing_outgoing.scalar_one_or_none() is not None:
        return {}

    # Boshqa user menga allaqachon so'rov yuborgan bo'lsa - avtomatik qabul qilib, do'st qilamiz
    reverse_result = await db.execute(
        select(FriendRequest).where(
            FriendRequest.from_user_id == to_user_id,
            FriendRequest.to_user_id == current_user.id,
            FriendRequest.status == "pending",
        )
    )
    reverse_request = reverse_result.scalar_one_or_none()
    if reverse_request is not None:
        reverse_request.status = "accepted"
        reverse_request.resolved_at = datetime.now(timezone.utc)
        await _create_mutual_friendship(db, current_user.id, to_user_id)
        await db.commit()
        return {}

    db.add(FriendRequest(from_user_id=current_user.id, to_user_id=to_user_id, status="pending"))
    db.add(Notification(user_id=to_user_id, kind="friend_request"))
    await db.commit()

    response.status_code = status.HTTP_201_CREATED
    return {}


@router.get("/requests/incoming", response_model=IncomingFriendRequestsOut, summary="Kelgan do'stlik so'rovlari")
async def list_incoming_requests(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(FriendRequest)
        .where(FriendRequest.to_user_id == current_user.id, FriendRequest.status == "pending")
        .order_by(FriendRequest.created_at.desc())
    )
    requests = (await db.execute(stmt)).scalars().all()

    entries = []
    for r in requests:
        from_user = await db.get(User, r.from_user_id)
        entries.append(IncomingFriendRequestOut(id=r.id, from_user=_friend_out(from_user), created_at=r.created_at))

    return IncomingFriendRequestsOut(requests=entries)


@router.post("/requests/{request_id}/accept", status_code=status.HTTP_204_NO_CONTENT, summary="So'rovni qabul qilish")
async def accept_friend_request(
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    friend_request = await db.get(FriendRequest, request_id)
    if friend_request is None or friend_request.to_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="So'rov topilmadi")
    if friend_request.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu so'rov allaqachon hal qilingan")

    friend_request.status = "accepted"
    friend_request.resolved_at = datetime.now(timezone.utc)
    await _create_mutual_friendship(db, friend_request.from_user_id, friend_request.to_user_id)
    await db.commit()


@router.post("/requests/{request_id}/decline", status_code=status.HTTP_204_NO_CONTENT, summary="So'rovni rad etish")
async def decline_friend_request(
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    friend_request = await db.get(FriendRequest, request_id)
    if friend_request is None or friend_request.to_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="So'rov topilmadi")
    if friend_request.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu so'rov allaqachon hal qilingan")

    friend_request.status = "declined"
    friend_request.resolved_at = datetime.now(timezone.utc)
    await db.commit()
