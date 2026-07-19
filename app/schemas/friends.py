from datetime import datetime

from pydantic import BaseModel


class FriendOut(BaseModel):
    id: str
    username: str | None
    first_name: str | None
    last_name: str | None
    avatar_color: str | None
    avatar_image_path: str | None


class FriendsOut(BaseModel):
    friends: list[FriendOut]


class FriendSearchResultOut(FriendOut):
    request_pending: bool


class FriendSearchOut(BaseModel):
    results: list[FriendSearchResultOut]


class FriendRequestCreate(BaseModel):
    to_user_id: str


class IncomingFriendRequestOut(BaseModel):
    id: str
    from_user: FriendOut
    created_at: datetime


class IncomingFriendRequestsOut(BaseModel):
    requests: list[IncomingFriendRequestOut]
