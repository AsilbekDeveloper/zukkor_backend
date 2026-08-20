FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# --proxy-headers + --forwarded-allow-ips='*': Render/Railway o'zi bitta
# ichki proksi orqali so'rovlarni yo'naltiradi (aniq IP oralig'i noma'lum) -
# bularsiz `request.client.host` doim proksining o'zini ko'rsatib, IP
# bo'yicha rate limiting ma'nosiz bo'lib qolardi.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'
