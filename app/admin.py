import hmac
import time
from collections import defaultdict

from starlette.requests import Request
from wtforms import SelectField, StringField
from wtforms.validators import DataRequired

from sqladmin import ModelView
from sqladmin.authentication import AuthenticationBackend

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.currency_transaction import CurrencyTransaction
from app.models.question_submission import QuestionSubmission
from app.models.quiz import Category, Question
from app.models.reported_question import ReportedQuestion
from app.models.user import User

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


class CategoryAdmin(ModelView, model=Category):
    name = "Kategoriya"
    name_plural = "Kategoriyalar"
    icon = "fa-solid fa-shapes"

    column_list = [Category.id, Category.name, Category.icon_name, Category.color_key, Category.sort_order, Category.is_active]
    column_searchable_list = [Category.name]
    column_sortable_list = [Category.id, Category.sort_order, Category.name]
    form_columns = [Category.name, Category.icon_name, Category.color_key, Category.sort_order, Category.is_active]


class QuestionAdmin(ModelView, model=Question):
    name = "Savol"
    name_plural = "Savollar"
    icon = "fa-solid fa-circle-question"

    column_list = [Question.id, Question.category, Question.question_text, Question.is_active]
    column_searchable_list = [Question.question_text]
    column_sortable_list = [Question.id, Question.is_active]
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
    """Faqat kuzatish uchun - foydalanuvchi yuborgan savollar AI tomonidan
    so'rov paytida sinxron tasdiqlanadi/rad etiladi, admin qo'lda hech
    narsani o'zgartirmaydi (shuning uchun to'liq read-only)."""

    name = "Savol taklifi"
    name_plural = "Foydalanuvchi savol takliflari"
    icon = "fa-solid fa-inbox"

    can_create = False
    can_edit = False
    can_delete = False

    column_list = [
        QuestionSubmission.id,
        QuestionSubmission.submitter_user_id,
        QuestionSubmission.question_text,
        QuestionSubmission.status,
        QuestionSubmission.resulting_category,
        QuestionSubmission.ai_feedback,
        QuestionSubmission.created_at,
    ]
    column_searchable_list = [QuestionSubmission.question_text]
    column_sortable_list = [QuestionSubmission.id, QuestionSubmission.status, QuestionSubmission.created_at]
    column_default_sort = [(QuestionSubmission.created_at, True)]


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
            if currency == "coin":
                user.coin_balance += amount
                balance_after = user.coin_balance
            elif currency == "diamond":
                user.diamond_balance += amount
                balance_after = user.diamond_balance
            else:
                raise ValueError(f"Noma'lum valyuta: {currency}")
            await db.commit()

        data["reason"] = "admin_adjustment"
        data["balance_after"] = balance_after
