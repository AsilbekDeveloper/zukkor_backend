"""Google Play Console'ga yuklashda majburiy bo'lgan, HAQIQATAN OCHIQ
(autentifikatsiyasiz, ilovadan tashqarida brauzerda ochiladigan) Privacy
Policy URL - Flutter'dagi `PrivacyPolicyScreen` faqat ilova ICHIDA
ko'rinadi, Play Console buni HISOBLAMAYDI (tashqi URL talab qiladi).

2026-09-27, foydalanuvchi so'rovi: shu sahifa bilan Flutter'dagi ekran
matni bir xil MA'NOni bersin, lekin bu yerda Telegram integratsiyasi va
AI-generatsiya (hujjat Gemini'ga yuborilishi) ANIQ aytiladi - Data Safety
formasidagi deklaratsiya bilan mos kelishi uchun."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zukkor - Maxfiylik siyosati</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; max-width: 720px; margin: 0 auto; padding: 32px 20px 64px; line-height: 1.6; color: #1a1a1a; }
  h1 { font-size: 24px; margin-bottom: 4px; }
  .updated { color: #666; font-size: 13px; margin-bottom: 32px; }
  h2 { font-size: 17px; margin-top: 32px; }
  ul { padding-left: 20px; }
</style>
</head>
<body>
<h1>Zukkor - Maxfiylik siyosati</h1>
<p class="updated">Oxirgi yangilanish: 2026-09-27</p>

<h2>1. Biz to'playdigan ma'lumotlar</h2>
<ul>
  <li>Ro'yxatdan o'tishda: email, ism, parol (xesh qilingan holda saqlanadi, oddiy matnda emas).</li>
  <li>Ixtiyoriy: Telegram akkauntingizni ulasangiz, Telegram foydalanuvchi ID'ingiz.</li>
  <li>O'yin faoliyati: viktorina/duel natijalari, ballar (XP), seriyalar, o'ynalgan kategoriyalar.</li>
  <li>AI orqali savol/quiz generatsiya qilsangiz, yuklagan hujjat matni - bu matn faqat savollar yaratish uchun Google Gemini xizmatiga yuboriladi, boshqa maqsadda saqlanmaydi/ishlatilmaydi.</li>
  <li>Virtual valyuta balansi (Coin/Diamond) va ularning o'zgarish tarixi - haqiqiy pul emas, faqat ilova ichi faoliyat uchun.</li>
</ul>

<h2>2. Ulardan qanday foydalanamiz</h2>
<p>Ma'lumotlaringiz faqat Zukkorning o'zini ishga tushirish uchun ishlatiladi: hisobingizni tanib olish, dueller/xonalarda raqib bilan moslashtirish, XP/reytingni hisoblash, statistikangizni sizga ko'rsatish va Telegram bot orqali bepul Diamond berish.</p>

<h2>3. Ulashish</h2>
<p>Biz ma'lumotlaringizni HECH QACHON sotmaymiz va uchinchi shaxslarga reklama maqsadida bermaymiz. Ismingiz, avataringiz va ochiq statistikangiz (reyting, do'stlar ro'yxati, duel natijalari) o'yinning oddiy qismi sifatida boshqa o'yinchilarga ko'rinadi. AI-generatsiya uchun yuklangan hujjat matni faqat Google Gemini xizmatiga (savol yaratish uchun) yuboriladi.</p>

<h2>4. Ma'lumotlarni saqlash va o'chirish</h2>
<p>Ma'lumotlaringiz hisobingiz faol bo'lgan davomida saqlanadi. Akkauntingizni ilova sozlamalaridan o'chirishingiz mumkin - bu barcha shaxsiy ma'lumotlaringizni tizimdan olib tashlaydi.</p>

<h2>5. Aloqa</h2>
<p>Ma'lumotlaringiz haqida savollaringiz bo'lsa, ilova ichidagi Yordam markazi orqali murojaat qiling.</p>
</body>
</html>"""


@router.get("/privacy-policy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_policy() -> str:
    return _PAGE
