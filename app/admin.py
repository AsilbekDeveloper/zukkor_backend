import hmac
import time
from collections import defaultdict

from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse
from wtforms import SelectField, StringField
from wtforms.validators import DataRequired

from sqladmin import BaseView, ModelView, expose
from sqladmin.authentication import AuthenticationBackend

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.app_config import AppConfig
from app.models.currency_transaction import CurrencyTransaction
from app.models.question_submission import QuestionSubmission
from app.models.quiz import Category, Question
from app.models.reported_question import ReportedQuestion
from app.models.user import User
from app.services import economy_config, wallet
from app.services.admin_analytics import AdminAnalytics, compute_analytics

_ANALYTICS_PAGE = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="utf-8">
<title>Zukkor - Statistika</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: #1a1c23; color: #e4e6eb; padding: 32px; }}
  h1 {{ margin-bottom: 24px; }}
  h2 {{ margin-top: 40px; font-size: 16px; color: #a0a4ad; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }}
  .card {{ border: 1px solid #333846; border-radius: 8px; padding: 16px; background: #22252e; }}
  .card .label {{ font-size: 13px; color: #888; }}
  .card .value {{ font-size: 26px; font-weight: 700; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #333846; }}
  a {{ color: #ff7a50; }}
</style>
</head>
<body>
<h1>Zukkor - umumiy holat</h1>
<div class="cards">
{cards}
</div>

<h2>Eng ko'p o'ynalgan kategoriyalar</h2>
<table>
<thead><tr><th>Kategoriya</th><th>O'ynalgan marta</th></tr></thead>
<tbody>{top_categories_rows}</tbody>
</table>

<h2>Eksport (so'nggi 30 kun)</h2>
<div class="cards">
{export_cards}
</div>

<h2>AI generatsiya</h2>
<div class="cards">
{ai_cards}
</div>
<p style="margin-top: 32px;"><a href="/admin/">← Admin panelga qaytish</a></p>
</body>
</html>"""


def _card(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div></div>'


def _render_analytics_page(stats: AdminAnalytics) -> str:
    cards = "".join(
        [
            _card("Jami foydalanuvchi", stats.total_users),
            _card("Faol (bloklanmagan)", stats.active_users),
            _card("Bugun o'ynaganlar (DAU)", stats.dau),
            _card("So'nggi 7 kunda (WAU)", stats.wau),
            _card("Jami o'ynalgan o'yinlar", stats.total_games_played),
            _card("Aylanmadagi Coin", stats.total_coin_in_circulation),
            _card("Aylanmadagi Diamond", stats.total_diamond_in_circulation),
        ]
    )
    top_categories_rows = "".join(
        f"<tr><td>{c.name}</td><td>{c.play_count}</td></tr>" for c in stats.top_categories
    ) or "<tr><td colspan=2>Hali ma'lumot yo'q</td></tr>"
    export_cards = "".join(
        [
            _card("Eksport soni", stats.export_count_30d),
            _card("Sarflangan Diamond", stats.export_diamond_spent_30d),
        ]
    )
    ai_cards = "".join(
        [
            _card("Muvaffaqiyatli generatsiya", stats.ai_jobs_completed),
            _card("Muvaffaqiyatsiz generatsiya", stats.ai_jobs_failed),
        ]
    )
    return _ANALYTICS_PAGE.format(
        cards=cards, top_categories_rows=top_categories_rows, export_cards=export_cards, ai_cards=ai_cards,
    )


class AnalyticsAdmin(BaseView):
    """"Admin hamma narsani bilib turishi kerak" (2026-09-29, foydalanuvchi
    so'rovi) - SQLAdmin'ning o'zi faqat xom jadval ko'rsatadi, bu sahifa
    ularni bir nechta tushunarli songa birlashtirib beradi. Faqat
    O'QISH uchun - o'zgartirish kerak bo'lgan narsalar tegishli
    ModelView'lardan (masalan balans - `CurrencyTransactionAdmin`)
    amalga oshiriladi, bu yerda emas."""

    name = "Statistika"
    icon = "fa-solid fa-chart-line"

    @expose("/analytics", methods=["GET"])
    async def analytics_page(self, request: Request) -> HTMLResponse:
        async with AsyncSessionLocal() as db:
            stats = await compute_analytics(db)
        return HTMLResponse(_render_analytics_page(stats))

_OPTION_FIELD_NAMES = ["option_1", "option_2", "option_3", "option_4"]

# Admin login uchun oddiy IP bo'yicha lockout - SQLAdmin o'z login yo'lini
# o'zi ro'yxatdan o'tkazgani uchun slowapi dekoratorini bu yerga qo'yib
# bo'lmaydi, shuning uchun xotirada saqlanadigan hisoblagich yetarli (bitta
# instansiya, admin panel kam ishlatiladi).
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 15 * 60
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def _is_locked_out(ip: str) -> bool:
    cutoff = time.monotonic() - _LOCKOUT_SECONDS
    attempts = [t for t in _failed_attempts[ip] if t > cutoff]
    _failed_attempts[ip] = attempts
    return len(attempts) >= _MAX_ATTEMPTS


class AdminAuth(AuthenticationBackend):
    def __init__(self, secret_key: str) -> None:
        super().__init__(secret_key)
        # SQLAdmin'ning standart sozlamasi sessiya cookie'siga `Secure`
        # belgisini QO'YMAYDI (`https_only=False`) - bu Starlette'ning
        # o'zida ham xavfsiz standart emas. Bu belgi faqat brauzerga
        # "bu cookie'ni faqat HTTPS ustida yubor" deydi, so'rovning ichki
        # sxemasini tekshirmaydi - shuning uchun Railway'ning proksi
        # sozlamasidan qat'i nazar xavfsiz (2026-09-13 prod-tayyorlik
        # auditi topilmasi).
        self.middlewares = [
            Middleware(SessionMiddleware, secret_key=secret_key, https_only=True),
        ]

    async def login(self, request: Request) -> bool:
        client_ip = request.client.host if request.client else "unknown"
        if _is_locked_out(client_ip):
            return False

        form = await request.form()
        username = form.get("username") or ""
        password = form.get("password") or ""
        # hmac.compare_digest - oddiy `==` solishtirish satr uzunligi/mos
        # belgilar soniga qarab bir oz farqli vaqt sarflaydi, bu nazariy
        # jihatdan parolni belgi-belgilab topishga (timing attack) imkon
        # beradi.
        username_ok = hmac.compare_digest(username, settings.ADMIN_USERNAME)
        password_ok = hmac.compare_digest(password, settings.ADMIN_PASSWORD)
        if username_ok and password_ok:
            _failed_attempts.pop(client_ip, None)
            request.session.update({"admin_authenticated": True})
            return True

        _failed_attempts[client_ip].append(time.monotonic())
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool(request.session.get("admin_authenticated"))


class UserAdmin(ModelView, model=User):
    """2026-09-29, foydalanuvchi so'rovi: "admin hamma narsani ko'rib
    tura olishi, o'zgartira olishi kerak" - avval `User` jadvali uchun
    UMUMAN admin ko'rinishi yo'q edi (qidirish/bloklash imkonsiz edi).

    `coin_balance`/`diamond_balance` ATAYLAB formada YO'Q - ularni shu
    yerdan to'g'ridan-to'g'ri o'zgartirish `CurrencyTransaction` daftarini
    (audit) chetlab o'tardi, balans/tarix mos kelmay qolardi. Balansni
    tuzatish kerak bo'lsa - `CurrencyTransactionAdmin`dan yangi qator
    qo'shish orqali (u yerda tuzatish avtomatik audit qilinadi).
    `hashed_password` ham YO'Q - xavfsizlik."""

    name = "Foydalanuvchi"
    name_plural = "Foydalanuvchilar"
    icon = "fa-solid fa-users"

    can_create = False
    can_delete = False

    column_list = [
        User.id,
        User.email,
        User.username,
        User.is_active,
        User.coin_balance,
        User.diamond_balance,
        User.level,
        User.total_xp,
        User.games_played,
        User.created_at,
    ]
    column_searchable_list = [User.email, User.username]
    column_sortable_list = [
        User.created_at,
        User.total_xp,
        User.level,
        User.games_played,
        User.coin_balance,
        User.diamond_balance,
    ]
    column_filters = [User.is_active]
    column_default_sort = [(User.created_at, True)]

    # `is_active = False` - haqiqiy "Kill Switch": `get_current_user`
    # (`app/dependencies/auth.py`) va login (`app/routers/auth.py`)
    # buni ANIQ tekshiradi - admin shu yerdan `False` qilib qo'ysa,
    # foydalanuvchi darhol kira olmay qoladi (mavjud token bilan ham).
    # Boshqa maydonlar - faqat aniq xatoni tuzatish uchun (masalan
    # noto'g'ri yozilgan ism), balans/statistika EMAS.
    form_columns = [User.is_active, User.username, User.first_name, User.last_name, User.email]


class CategoryAdmin(ModelView, model=Category):
    name = "Kategoriya"
    name_plural = "Kategoriyalar"
    icon = "fa-solid fa-shapes"

    column_list = [Category.id, Category.name, Category.icon_name, Category.color_key, Category.sort_order, Category.is_active]
    column_searchable_list = [Category.name]
    column_sortable_list = [Category.id, Category.sort_order, Category.name]
    form_columns = [Category.name, Category.icon_name, Category.color_key, Category.sort_order, Category.is_active]

    # Bu panel faqat hammaga ochiq GLOBAL kategoriyalarni (admin
    # boshqaradigan quiz bankini) ko'rsatishi kerak - `owner_user_id`
    # filtrsiz bo'lsa, har bir foydalanuvchining AI/qo'lda yaratgan
    # SHAXSIY quizlari ham xuddi shu ro'yxatga aralashib, haqiqiy
    # kategoriyalarni yo'qotib yuborardi (2026-10-02, admin shu holatni
    # topdi - "jizzax", "laptop", "uzb" kabi shaxsiy quiz nomlari global
    # kategoriyalar qatorida ko'rinib turardi).
    def list_query(self, request):
        return super().list_query(request).where(Category.owner_user_id.is_(None))

    def count_query(self, request):
        return super().count_query(request).where(Category.owner_user_id.is_(None))


class QuestionAdmin(ModelView, model=Question):
    """"Kill Switch" - AI (avtomatik moderatsiya YOKI AI-generatsiya orqali)
    tasdiqlagan savol keyinchalik yomon/noto'g'ri ekani ma'lum bo'lsa, admin
    buni bevosita shu yerdan tuzatadi. `can_edit`/`can_delete`ni ATAYLAB
    ANIQ (explicit) `True` deb yozamiz - SQLAdmin'ning standart qiymati
    ham `True`, lekin bu yerda ANIQ yozib qo'yish kelajakda kimdir
    boshqa admin view'larga (masalan `QuestionSubmissionAdmin`) qarab
    "ehtiyot shart" deb bu yerni ham `False` qilib qo'yishining oldini
    oladi - savol ustidan qo'lda nazorat ATAYLAB doim ochiq turishi
    kerak (2026-09-19, sifat auditi)."""

    name = "Savol"
    name_plural = "Savollar"
    icon = "fa-solid fa-circle-question"

    can_edit = True
    can_delete = True

    column_list = [
        Question.id,
        Question.category,
        Question.question_text,
        Question.is_active,
        Question.created_by_user_id,
        # Sof hisoblangan ustun (`ReportedQuestion` soni) -
        # `app/models/reported_question.py`dagi `column_property`ga
        # qarang. Admin bu yerdan qaysi savol necha marta report
        # qilinganini, `is_active=False`ga avtomatik o'tganini bir
        # qarashda ko'radi.
        Question.report_count,
    ]
    column_searchable_list = [Question.question_text]
    column_sortable_list = [Question.id, Question.is_active, Question.report_count]
    form_columns = [Question.category, Question.question_text, Question.is_active]

    async def scaffold_form(self):
        form_class = await super().scaffold_form()
        form_class.option_1 = StringField("1-variant", validators=[DataRequired()])
        form_class.option_2 = StringField("2-variant", validators=[DataRequired()])
        form_class.option_3 = StringField("3-variant", validators=[DataRequired()])
        form_class.option_4 = StringField("4-variant", validators=[DataRequired()])
        form_class.correct_option = SelectField(
            "To'g'ri javob",
            choices=[("0", "1-variant"), ("1", "2-variant"), ("2", "3-variant"), ("3", "4-variant")],
            validators=[DataRequired()],
        )
        return form_class

    async def get_object_for_edit(self, value):
        # Savolni tahrirlash oynasi ochilganda mavjud 4 variant va to'g'ri
        # javobni forma maydonlariga oldindan to'ldirish uchun - bu maydonlar
        # modelda yo'q (options/correct_option_index JSON/int sifatida
        # saqlanadi), shuning uchun WTForms ularni obj orqali topa olishi
        # uchun vaqtinchalik atribut sifatida qo'yib beramiz.
        obj = await super().get_object_for_edit(value)
        if obj is not None:
            options = list(obj.options or [])
            for i, field_name in enumerate(_OPTION_FIELD_NAMES):
                setattr(obj, field_name, options[i] if i < len(options) else "")
            setattr(obj, "correct_option", str(obj.correct_option_index))
        return obj

    async def on_model_change(self, data: dict, model, is_created: bool, request: Request) -> None:
        options = [data.pop(name, "") or "" for name in _OPTION_FIELD_NAMES]
        correct_option = data.pop("correct_option", "0")
        data["options"] = options
        data["correct_option_index"] = int(correct_option)


class ReportedQuestionAdmin(ModelView, model=ReportedQuestion):
    name = "Shikoyat"
    name_plural = "Savol shikoyatlari"
    icon = "fa-solid fa-flag"

    column_list = [
        ReportedQuestion.id,
        ReportedQuestion.question,
        ReportedQuestion.reason,
        ReportedQuestion.comment,
        ReportedQuestion.status,
        ReportedQuestion.created_at,
    ]
    column_sortable_list = [ReportedQuestion.id, ReportedQuestion.status, ReportedQuestion.created_at]
    column_default_sort = [(ReportedQuestion.created_at, True)]
    form_columns = [ReportedQuestion.status]


class QuestionSubmissionAdmin(ModelView, model=QuestionSubmission):
    """Asosan kuzatish uchun - foydalanuvchi yuborgan savollar AI tomonidan
    so'rov paytida sinxron tasdiqlanadi/rad etiladi, admin navbatda
    KUTMAYDI. LEKIN (2026-09-19, sifat auditi - "AI galyutsinatsiyasi"
    himoyasi #2, False Negative) - AI rad etgan taklifga foydalanuvchi
    `POST /questions/submissions/{id}/appeal` orqali e'tiroz bildirsa,
    status `'pending_manual_review'`ga o'tadi va ENDI admin buni shu
    yerdan QO'LDA ko'rib chiqishi, kerak bo'lsa matnini/variantlarini
    tuzatib, `status`ni `'approved'`ga o'zgartirishi mumkin - shunda
    haqiqiy `Question` qatori AVTOMATIK yaratiladi (pastdagi
    `on_model_change`ga qarang), xuddi AI o'zi tasdiqlagandek.

    `can_delete = False` ATAYLAB - bu jadval audit/tarix hujjati
    (`QuestionSubmission` docstring'iga qarang), allaqachon yaratilgan
    haqiqiy `Question`ni o'chirish/o'zgartirish kerak bo'lsa buning
    o'rni bu yer emas, `QuestionAdmin` ("Kill Switch")."""

    name = "Savol taklifi"
    name_plural = "Foydalanuvchi savol takliflari"
    icon = "fa-solid fa-inbox"

    can_create = False
    can_edit = True
    can_delete = False

    column_list = [
        QuestionSubmission.id,
        QuestionSubmission.submitter_user_id,
        QuestionSubmission.question_text,
        QuestionSubmission.status,
        QuestionSubmission.resulting_category,
        QuestionSubmission.ai_feedback,
        QuestionSubmission.appealed_at,
        QuestionSubmission.created_at,
    ]
    column_searchable_list = [QuestionSubmission.question_text]
    column_sortable_list = [
        QuestionSubmission.id,
        QuestionSubmission.status,
        QuestionSubmission.appealed_at,
        QuestionSubmission.created_at,
    ]
    # Admin bir bosishda faqat e'tiroz bildirilgan (inson ko'rib chiqishini
    # kutayotgan) takliflarni filtrlab ko'ra oladi.
    column_filters = [QuestionSubmission.status]
    column_default_sort = [(QuestionSubmission.created_at, True)]
    form_columns = [
        QuestionSubmission.question_text,
        QuestionSubmission.resulting_category,
        QuestionSubmission.status,
    ]

    async def scaffold_form(self):
        form_class = await super().scaffold_form()
        form_class.option_1 = StringField("1-variant", validators=[DataRequired()])
        form_class.option_2 = StringField("2-variant", validators=[DataRequired()])
        form_class.option_3 = StringField("3-variant", validators=[DataRequired()])
        form_class.option_4 = StringField("4-variant", validators=[DataRequired()])
        form_class.correct_option = SelectField(
            "To'g'ri javob",
            choices=[("0", "1-variant"), ("1", "2-variant"), ("2", "3-variant"), ("3", "4-variant")],
            validators=[DataRequired()],
        )
        # Kutilgan qiymatlarni cheklaymiz - erkin matn maydoni bo'lganda
        # admin xato bilan noma'lum status yozib qo'yishi (masalan
        # "aproved") mumkin edi, bu esa taklifni "chalajon" holatda
        # abadiy qoldirardi (hech qanday filtr uni topa olmaydi).
        form_class.status = SelectField(
            "Holat",
            choices=[
                ("pending_manual_review", "Ko'rib chiqilmoqda (e'tiroz)"),
                ("approved", "Tasdiqlash"),
                ("rejected", "Rad etish"),
            ],
            validators=[DataRequired()],
        )
        return form_class

    async def get_object_for_edit(self, value):
        # `QuestionAdmin.get_object_for_edit`dagi bilan bir xil naqsh -
        # options/correct_option_index JSON/int sifatida saqlanadi, WTForms
        # ularni alohida maydon sifatida kutadi.
        obj = await super().get_object_for_edit(value)
        if obj is not None:
            options = list(obj.options or [])
            for i, field_name in enumerate(_OPTION_FIELD_NAMES):
                setattr(obj, field_name, options[i] if i < len(options) else "")
            setattr(obj, "correct_option", str(obj.correct_option_index))
        return obj

    async def on_model_change(self, data: dict, model, is_created: bool, request: Request) -> None:
        options = [data.pop(name, "") or "" for name in _OPTION_FIELD_NAMES]
        correct_option = data.pop("correct_option", "0")
        data["options"] = options
        data["correct_option_index"] = int(correct_option)

        was_already_approved = model.status == "approved" and model.resulting_question_id is not None
        if data.get("status") == "approved" and not was_already_approved:
            resulting_category = data.get("resulting_category")
            category_id = resulting_category.id if resulting_category is not None else model.resulting_category_id
            if category_id is None:
                raise ValueError("Tasdiqlash uchun kategoriya tanlanishi shart")

            async with AsyncSessionLocal() as db:
                new_question = Question(
                    category_id=category_id,
                    question_text=data.get("question_text", model.question_text),
                    options=data["options"],
                    correct_option_index=data["correct_option_index"],
                    is_active=True,
                    created_by_user_id=model.submitter_user_id,
                )
                db.add(new_question)
                await db.flush()
                new_question_id = new_question.id
                await db.commit()

            data["resulting_question_id"] = new_question_id


class CurrencyTransactionAdmin(ModelView, model=CurrencyTransaction):
    """Coin/Diamond daftari - asosan faqat kuzatish uchun (ledger qatorlari
    tarixiy hujjat, tahrirlanmaydi/o'chirilmaydi), lekin admin bu YERDAN
    YANGI qator YARATIB qo'lda balans tuzatishi mumkin (masalan xatolik
    yuz berganda kompensatsiya). Yaratishda `reason` avtomatik
    "admin_adjustment"ga, `balance_after` esa foydalanuvchining YANGI
    balansiga o'rnatiladi - ikkalasi ham qo'lda kiritilmaydi."""

    name = "Coin/Diamond tranzaksiyasi"
    name_plural = "Coin/Diamond tarixi"
    icon = "fa-solid fa-coins"

    can_edit = False
    can_delete = False

    column_list = [
        CurrencyTransaction.id,
        CurrencyTransaction.user_id,
        CurrencyTransaction.currency,
        CurrencyTransaction.amount,
        CurrencyTransaction.reason,
        CurrencyTransaction.balance_after,
        CurrencyTransaction.created_at,
    ]
    column_searchable_list = [CurrencyTransaction.user_id]
    column_sortable_list = [CurrencyTransaction.created_at, CurrencyTransaction.currency]
    column_default_sort = [(CurrencyTransaction.created_at, True)]

    form_columns = [CurrencyTransaction.user_id, CurrencyTransaction.currency, CurrencyTransaction.amount]

    async def scaffold_form(self):
        form_class = await super().scaffold_form()
        form_class.currency = SelectField(
            "Valyuta", choices=[("coin", "Coin"), ("diamond", "Diamond")], validators=[DataRequired()]
        )
        return form_class

    async def on_model_change(self, data: dict, model, is_created: bool, request: Request) -> None:
        if not is_created:
            # `can_edit = False` bo'lgani uchun bu holat aslida yuz
            # bermaydi, lekin xavfsizlik uchun ikki marta tekshiramiz.
            return

        user_id = data.get("user_id")
        currency = data.get("currency")
        amount = int(data.get("amount") or 0)

        async with AsyncSessionLocal() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise ValueError(f"Foydalanuvchi topilmadi: {user_id}")
            # `user.coin_balance += amount` EMAS - bu yerda ham xuddi
            # `wallet.py`dagi kabi atomik SQL UPDATE ishlatiladi, aks
            # holda admin tuzatishi va shu paytda tugagan o'yin (ikkalasi
            # ham balansni o'zgartiradi) bir-birining natijasini
            # "yo'qolgan yangilanish" bilan bekor qilib qo'yishi mumkin
            # edi (2026-09-13, prod-tayyorlik auditi topilmasi - avval
            # faqat `wallet.py`ning o'zi tuzatilgan, bu yerga qaramay
            # qolgan edi).
            balance_after = await wallet.apply_atomic_balance_delta(db, user, currency=currency, amount=amount)
            await db.commit()

        data["reason"] = "admin_adjustment"
        data["balance_after"] = balance_after


class AppConfigAdmin(ModelView, model=AppConfig):
    """Coin iqtisodiyoti parametrlari (`app.services.economy_config`) -
    bu yerda qiymatni o'zgartirish darhol (qayta deploy'siz) kuchga
    kiradi. Yangi qator qo'shib bo'lmaydi (`can_create = False`) - ilova
    ishga tushganda kerakli kalitlarning barchasi avtomatik yaratiladi
    (`economy_config.seed_defaults`), shuning uchun bu yerdan faqat
    MAVJUD qatorning `value`sini tahrirlash kerak."""

    name = "Iqtisodiyot sozlamasi"
    name_plural = "Coin iqtisodiyoti sozlamalari"
    icon = "fa-solid fa-sliders"

    can_create = False
    can_delete = False

    column_list = [AppConfig.key, AppConfig.value, AppConfig.description]
    form_columns = [AppConfig.value]
    column_default_sort = [(AppConfig.key, False)]

    async def on_model_change(self, data: dict, model, is_created: bool, request: Request) -> None:
        # Xato kiritilgan qiymat (manfiy son, yoki foiz uchun 100dan
        # katta) forma xatosi sifatida qaytariladi, saqlanmaydi - aks
        # holda `wallet.charge_for_question_play`dagi hisob-kitob
        # (masalan muallif ulushi) buzilib qolardi.
        economy_config.validate_value(model.key, data.get("value", ""))
