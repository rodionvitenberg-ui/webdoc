# WebDoc.life (Checkups)

AI-интерпретатор медицинских анализов крови. Пользователь загружает бланк анализа (PDF/фото), система отдаёт изображение бланка vision-модели, которая сама распознаёт текст, и возвращает структурированную расшифровку: показатели, отклонения, возможные причины и рекомендации. Дополнительно есть ИИ-чат по каждому анализу, динамика показателей (графики), профили пациентов и PRO-подписка.

> Ветка `lite-vision` — демонстрационная сборка под слабый сервер: OCR на своей стороне
> (Tesseract) и анонимизация (Presidio) удалены, бланк уходит в LLM как есть.
> Проект не предназначен для приёма реальных медицинских документов.

## Стек

| Слой | Технологии |
|------|-----------|
| Backend | Django 6, django-ninja (API), django-ninja-jwt, Celery, PostgreSQL, Redis |
| OCR / обработка | PyMuPDF (PDF → JPEG; poppler и Pillow не нужны) |
| LLM | DeepSeek (`deepseek-flash`, vision) через OpenAI SDK (мультиключ + fallback) |
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind CSS 4, next-intl (ru/en/es) |
| UI-библиотеки | framer-motion, react-three-fiber, recharts, @react-pdf/renderer |
| Payments | Cryptomus (webhook → выдача PRO) |
| Деплой | gunicorn (gthread) + celery + Next.js; PostgreSQL и Redis — системные службы |

## Структура репозитория (монорепо)

```
checkups/
├── backend/
│   ├── config/          # settings.py, urls.py, celery.py, wsgi/asgi
│   ├── core/            # User, PatientProfile, MedicalAnalysis, AnalysisIndicator + API
│   ├── analysis/        # PromptTemplate + AnalysisPipeline (LLM: extract → interpret)
│   ├── premium/         # Trait, PatientTraitLink, ChatMessage, ChatSettings, Transaction
│   └── locale/          # переводы (modeltranslation)
├── frontend/
│   ├── app/[locale]/    # ru/en/es страницы: upload, analysis, claim, dashboard, auth...
│   ├── components/      # analysis/, dashboard/, home/, layout/, ui/
│   ├── lib/             # api.ts (axios), types.ts, store.ts (zustand)
│   ├── i18n/            # routing.ts, request.ts (next-intl)
│   └── messages/        # en.json, es.json, ru.json
├── requirements.txt     # (полный замороженный список зависимостей backend)
```

## Доменная модель

| Сущность | Назначение |
|----------|-----------|
| `User` | кастомный пользователь (логин по email, без username). Поле `pro_expires_at` + свойство `is_pro`. |
| `PatientProfile` | профиль пациента (ФИО, дата рождения, пол, вес, рост). Может быть «сиротой» (`user=null`) до привязки гостем. |
| `MedicalAnalysis` | один загруженный бланк. Статусы: pending → processing → completed/failed. Хранит `ai_result` (JSON), `parent_analysis` (для reanalyze), `chat_summary`. |
| `AnalysisIndicator` | «атомарный» показатель (slug, value, unit, date) — для графиков динамики. |
| `PromptTemplate` | системные промпты по ролям (extractor / interpreter / chat_assistant), переводятся через modeltranslation. |
| `Trait` + `PatientTraitLink` | справочник характеристик пациента (заболевания, привычки, особенности) и связь M2M с деталями. |
| `ChatMessage` | сообщения ИИ-чата по анализу. Флаг `is_summarized` — для архивации старых сообщений. |
| `Transaction` | платёж Cryptomus (order_id, amount, status). |
| `ChatSettings` | singleton-настройка «оптимизация токенов» (JSON-диета / суммаризация). |

## Пайплайн обработки анализа

```
upload (PDF/фото)
  → PDF → JPEG через PyMuPDF (до 5 страниц), картинки уходят как есть
  → сборка контекста пациента (пол, возраст, ИМТ, история показателей за 180 дней, premium-черты)
  → Stage 1: "extractor" — vision-модель читает изображения и извлекает сырые показатели/даты (JSON)
  → Stage 2: "interpreter" — по извлечённому JSON выдаёт reasoning + summary + indicators + causes + recommendations (JSON)
  → сохранение ai_result + атомарных показателей
  → SSE-стрим статуса для фронтенда
```

## Запуск (локально)

### Требования
- PostgreSQL 17 и Redis — системные пакеты (Docker не используется)
- Python 3.13+ (проверено на 3.14), Node 22

### 1. Инфраструктура: PostgreSQL + Redis

Проект поднимает **свой** кластер PostgreSQL на отдельном порту, чтобы не мешать базам других проектов на машине.

```bash
PGBIN=/usr/lib/postgresql/17/bin
DATA=$HOME/.local/share/webdoc-pgdata

$PGBIN/initdb -D $DATA -U postgres --auth-local=trust --auth-host=trust --encoding=UTF8
$PGBIN/pg_ctl -D $DATA -l /tmp/webdoc-pg.log -o "-p 5434 -k /tmp -c listen_addresses=127.0.0.1" start
$PGBIN/createdb -h 127.0.0.1 -p 5434 -U postgres checkups
```

Redis (только loopback, отдельный порт):

```bash
redis-server --port 6381 --daemonize yes --dir /tmp --dbfilename webdoc-redis.rdb --logfile /tmp/webdoc-redis.log
```

`backend/.env` должен смотреть на них:

```dotenv
DB_HOST=127.0.0.1
DB_PORT=5434
CELERY_BROKER_URL=redis://127.0.0.1:6381/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6381/0
```

Если Django пишет `connection to server at "127.0.0.1", port 5434 failed` — кластер не запущен:
`$PGBIN/pg_ctl -D $DATA start`.

### 2. Backend (терминал)

```bash
cd backend
python -m venv venv && source venv/bin/activate   # если venv ещё не создан
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py createsuperuser --email admin@example.com   # без --username: вход по email
python manage.py runserver
```

- Backend API: http://localhost:8000 (маршруты в корне, без `/api`)
- Swagger: http://localhost:8000/docs
- Admin: http://localhost:8000/admin

### 3. Celery worker (отдельный терминал)

```bash
cd backend && source venv/bin/activate
celery -A config worker -l INFO --concurrency=1
```

### 4. Frontend (отдельный терминал)

```bash
cd frontend
npm install
npm run dev
```

http://localhost:3000 — адрес API берётся из `frontend/.env.local`
(`NEXT_PUBLIC_API_URL=http://localhost:8000`, файл создаётся вручную).

### Заполнить справочник характеристик (Traits)

```bash
cd backend && source venv/bin/activate
python populated_traits.py
```

## Переменные окружения (backend/.env)

Ключевые переменные, читаемые в `settings.py` / сервисах:

| Переменная | Назначение |
|-----------|-----------|
| `SECRET_KEY` | секретный ключ Django |
| `DEBUG` | True/False |
| `ALLOWED_HOSTS` | список хостов через запятую |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | подключение к PostgreSQL |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis для Celery |
| `CORS_ALLOWED_ORIGIN(S)` | разрешённые origins |
| `AI_API_KEY`, `AI_API_KEY_2`, ... | ключи LLM (DeepSeek). Мультиключ: используются все с префиксом `AI_API_KEY`. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | SMTP. Порт 465 → SSL, 587 → STARTTLS |
| `FRONTEND_URL` | публичный URL фронта для ссылок в письмах (обязателен в проде) |
| `CRYPTOMUS_MERCHANT_ID`, `CRYPTOMUS_API_KEY` | оплата PRO. Пусто → `POST /premium/payment/create` отдаёт `503 payment_not_configured` |
| `SITE_URL` | куда вернуть пользователя после оплаты (дефолт `https://webdoc.life`) |
| `BACKEND_URL` | публичный адрес API для webhook Cryptomus (`/premium/payment/webhook`) |
| `NEXT_PUBLIC_API_URL` (frontend) | базовый URL backend API |

## Тесты

```bash
# Backend
cd backend && source venv/bin/activate && python manage.py test --noinput

# Frontend (e2e — Playwright)
cd frontend && npx playwright test
```

## Документация

- `PLAN.md` — полный разбор проекта: найденные ошибки, недочёты, архитектурные замечания и детальный план улучшений.