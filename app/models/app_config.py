from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AppConfig(Base):
    """Admin panel (SQLAdmin) orqali ish vaqtida o'zgartiriladigan
    kalit-qiymat sozlamalar - `app.core.config.Settings`dagi (env-var,
    faqat deploy paytida o'zgaradi) konstantalardan farqli. Hozircha
    faqat Coin iqtisodiyoti parametrlari uchun (`app.services.economy_config`)
    ishlatiladi, lekin umumiy - kelajakda boshqa runtime sozlamalar ham
    shu jadvalga qo'shilishi mumkin.

    Qiymat har doim satr sifatida saqlanadi (SQLAdmin formasi oddiy
    matn maydoni) - o'qiydigan tomon (`economy_config.get_int`) o'zi
    kerakli turga o'giradi.
    """

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __str__(self) -> str:
        return f"{self.key} = {self.value}"
