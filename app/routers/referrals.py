from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.dependencies.auth import get_current_user
from app.models.referral import ReferralClick, ReferralReward
from app.models.user import User
from app.schemas.referral import ApplyReferralRequest, ReferralOut
from app.services import economy_config, referral

router = APIRouter()
public_router = APIRouter()


def _public_base_url(request: Request) -> str:
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.rstrip("/")
    base = str(request.base_url).rstrip("/")
    # Railway/Render proksi orqasida so'rov ichkarida http bo'lib ko'rinadi.
    host = request.url.hostname or ""
    if base.startswith("http://") and host not in ("localhost", "127.0.0.1"):
        base = "https://" + base[len("http://") :]
    return base


def play_store_url(code: str | None = None) -> str:
    """Play Store havolasi - `referrer` ilova birinchi marta ochilganda
    Install Referrer API orqali o'qiladi (`ref=KOD`)."""
    url = f"https://play.google.com/store/apps/details?id={settings.ANDROID_PACKAGE_ID}"
    if code:
        url += f"&referrer={quote(f'ref={code}', safe='')}"
    return url


@router.get("/me", response_model=ReferralOut, summary="Mening taklif kodim, havolam va statistikam")
async def get_my_referral(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    code = await referral.ensure_referral_code(db, current_user)

    registered_count = (
        await db.execute(select(func.count()).select_from(User).where(User.referred_by_user_id == current_user.id))
    ).scalar_one()
    totals = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(ReferralReward.referrer_coins), 0),
                func.coalesce(func.sum(ReferralReward.referrer_diamonds), 0),
            ).where(ReferralReward.referrer_user_id == current_user.id)
        )
    ).one()

    return ReferralOut(
        code=code,
        link=f"{_public_base_url(request)}/r/{code}",
        reward_coins=await economy_config.get_int(db, economy_config.REFERRAL_REWARD_COINS),
        reward_diamonds=await economy_config.get_int(db, economy_config.REFERRAL_REWARD_DIAMONDS),
        min_questions=await economy_config.get_int(db, economy_config.REFERRAL_MIN_QUESTIONS),
        registered_count=registered_count,
        rewarded_count=totals[0],
        coins_earned=totals[1],
        diamonds_earned=totals[2],
        can_apply_code=referral.can_apply_code(current_user),
    )


@router.post("/apply", status_code=status.HTTP_204_NO_CONTENT, summary="Do'stning taklif kodini kiritish")
@limiter.limit("10/minute")
async def apply_referral_code(
    request: Request,
    data: ApplyReferralRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await referral.apply_code(db, current_user, data.code)
    except referral.ApplyCodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    await db.commit()


_LANDING = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zukkor - do'stingiz sizni taklif qildi</title>
<meta property="og:title" content="Zukkor - bilim musobaqasi">
<meta property="og:description" content="Do'stingiz sizni Zukkorga taklif qildi. Birinchi o'yindan keyin ikkalangiz ham sovg'a olasiz.">
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: #FBF6F2; color: #211410; margin: 0; padding: 40px 20px; text-align: center; }}
  .card {{ max-width: 420px; margin: 0 auto; background: #fff; border: 1px solid #F0E4DF; border-radius: 20px; padding: 32px 24px; }}
  h1 {{ font-size: 24px; margin: 0 0 8px; }}
  p {{ color: #55423B; line-height: 1.5; }}
  .code {{ font-size: 28px; font-weight: 800; letter-spacing: 4px; background: #FBF6F2; border-radius: 14px; padding: 14px; margin: 20px 0; user-select: all; }}
  a.btn {{ display: block; background: #F4502C; color: #fff; text-decoration: none; font-weight: 700; padding: 16px; border-radius: 14px; }}
</style>
</head>
<body>
<div class="card">
  <h1>Zukkor</h1>
  <p>Do'stingiz sizni bilim musobaqasiga taklif qildi.</p>
  <p>Ro'yxatdan o'tishda quyidagi kodni kiriting:</p>
  <div class="code">{code}</div>
  <a class="btn" href="{play_url}">Google Play'dan yuklab olish</a>
</div>
</body>
</html>
"""


@public_router.get("/r/{code}", include_in_schema=False)
@limiter.limit("60/minute")
async def open_referral_link(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Ommaviy taklif havolasi. Android'da to'g'ridan-to'g'ri Play Store'ga
    (`referrer` bilan), boshqa qurilmalarda kodni ko'rsatadigan sahifaga."""
    code = referral.normalize_code(code)
    referrer = await referral.find_referrer(db, code)
    if referrer is None:
        return HTMLResponse(
            _LANDING.format(code="-", play_url=escape(play_store_url())), status_code=status.HTTP_404_NOT_FOUND
        )

    is_android = "android" in request.headers.get("user-agent", "").lower()
    db.add(ReferralClick(code=code, platform="android" if is_android else "other"))
    await db.commit()

    if is_android:
        return RedirectResponse(play_store_url(code), status_code=status.HTTP_302_FOUND)
    return HTMLResponse(_LANDING.format(code=escape(code), play_url=escape(play_store_url(code))))

