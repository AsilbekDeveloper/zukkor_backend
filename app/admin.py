import hmac
import time
from collections import defaultdict

from starlette.requests import Request
from wtforms import SelectField, StringField
from wtforms.validators import DataRequired

from sqladmin import ModelView
from sqladmin.authentication import AuthenticationBackend

from app.core.config import settings
from app.models.quiz import Category, Question
from app.models.reported_question import ReportedQuestion

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
