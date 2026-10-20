# backend/Dockerfile
FROM python:3.13-slim

# Системные пакеты не нужны: psycopg ставится как psycopg-binary (libpq внутри колеса),
# PyMuPDF — готовое колесо. Tesseract/poppler/libgl удалены вместе с OCR-пайплайном.

WORKDIR /app

# Оптимизация кэширования слоев Docker: сначала копируем только requirements
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем весь код проекта
COPY . .

# Собираем статику (WhiteNoise)
RUN python manage.py collectstatic --noinput

# Открываем порт
EXPOSE 8000

# Запускаем Gunicorn: gthread — чтобы SSE-стрим статуса не блокировал sync-воркер
# (поток живёт до 180с, поэтому таймаут выше него).
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--worker-class", "gthread", "--workers", "2", "--threads", "4", "--timeout", "200", "config.wsgi:application"]