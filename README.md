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
| Деплой | Docker Compose: db + redis + backend (gunicorn) + celery + frontend |

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
├── docker-compose.yml
└── requirements.txt     # (полный замороженный список зависимостей backend)
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
- Docker + Docker Compose (для PostgreSQL и Redis)
- Python 3.13+ (проверено на 3.14), Node 22

### 1. Инфраструктура: PostgreSQL + Redis

```bash
docker compose up -d db redis
```

Порты на хосте (только loopback): PostgreSQL `127.0.0.1:5433`, Redis `127.0.0.1:6380`.

Если Django пишет `connection to server at "127.0.0.1", port 5433 failed: Connection refused` —
контейнеры не запущены: `docker compose up -d db redis`.

`backend/.env` для запуска из терминала должен смотреть на них:

```dotenv
DB_HOST=127.0.0.1
DB_PORT=5433
CELERY_BROKER_URL=redis://127.0.0.1:6380/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6380/0
```

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
celery -A config worker -l INFO
```

### 4. Frontend (отдельный терминал)

```bash
cd frontend
npm install
npm run dev
```

http://localhost:3000 — адрес API берётся из `frontend/.env.local`
(`NEXT_PUBLIC_API_URL=http://localhost:8000`, файл создаётся вручную).

### Быстрый старт через Docker (всё сразу)

```bash
docker compose up --build
```

Сервисы:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000 (Swagger: http://localhost:8000/docs)
- Admin: http://localhost:8000/admin

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
| `CRYPTOMUS_MERCHANT_ID`, `CRYPTOMUS_API_KEY` | оплата PRO |
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