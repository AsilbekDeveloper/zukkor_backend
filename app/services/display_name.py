from app.models.user import User


def display_name(user: User) -> str:
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if full_name:
        return full_name
    if user.username:
        return user.username
    return "Foydalanuvchi"
