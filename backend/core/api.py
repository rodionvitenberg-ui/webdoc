import uuid
import time
from typing import List, Optional, Any
from ninja import NinjaAPI, UploadedFile, File, Schema, Form
from ninja_jwt.authentication import JWTAuth

# Django imports
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, StreamingHttpResponse
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.conf import settings
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _  # ДОБАВЛЕНО ДЛЯ ЛОКАЛИЗАЦИИ

# JWT imports
from ninja_jwt.tokens import RefreshToken
from ninja_jwt.exceptions import TokenError

# Local imports
from .models import MedicalAnalysis, PatientProfile, User, AnalysisIndicator
from .schemas import (
    AnalysisResponseSchema,
    AuthResponseSchema,
    PatientProfileSchema,
    CreateProfileSchema,
    AssignProfileRequest,
    ChartResponseSchema,
    RefreshRequestSchema,
    ClaimRequestOTPSchema,
    ClaimVerifyOTPSchema,
    UpdateProfileSchema
)
from .tasks import process_analysis_task
from .services import (
    claim_analyses_to_user,
    get_daily_analysis_limit,
    count_todays_launches,
)
from django.template.loader import render_to_string
from .mail import send_html_email

# --- Схемы для Авторизации ---

class LoginSchema(Schema):
    email: str
    password: str

class RegisterSchema(Schema):
    email: str
    phone: str = None
    password: str = None

class ResetPasswordRequestSchema(Schema):
    email: str

class ResetPasswordConfirmSchema(Schema):
    uidb64: str
    token: str
    new_password: str

class ChangePasswordSchema(Schema):
    old_password: str
    new_password: str   

# --- Инициализация API ---

api = NinjaAPI()
User = get_user_model()

class OptionalJWTAuth(JWTAuth):
    """
    Кастомный класс авторизации.
    Валидный токен -> владелец. Нет/битый токен -> аноним (без 401),
    чтобы гостевой флоу по UUID работал. Вернуть None нельзя:
    django-ninja на falsy-результате auth сам отдаёт 401.
    """
    def __call__(self, request: HttpRequest) -> Optional[Any]:
        try:
            user = super().__call__(request)
        except Exception:
            return AnonymousUser()
        return user if user else AnonymousUser()

# ==========================================
# 1. АВТОРИЗАЦИЯ И УПРАВЛЕНИЕ АККАУНТОМ
# ==========================================

@api.post("/auth/register", response=AuthResponseSchema)
def register(request, payload: RegisterSchema):
    if User.objects.filter(email=payload.email).exists():
        return api.create_response(request, {"message": _("Пользователь с таким email уже существует")}, status=400)
    
    with transaction.atomic():
        user = User.objects.create(email=payload.email, phone=payload.phone)
        password = payload.password or get_random_string(6, allowed_chars='0123456789')
        user.set_password(password)
        user.save()
        
        # Переводим название дефолтного профиля
        PatientProfile.objects.create(user=user, full_name=_("Мой профиль"))
        
        if not payload.password:
            try:
                # Оставляем текстовое сообщение как fallback (для старых почтовиков)
                mail_subject = _("Регистрация в WebDoc.life")
                mail_message = _("Добро пожаловать в WebDoc.life!\n\nВаши данные для входа:\nЛогин: {email}\nВаш пароль: {password}\n\nПожалуйста, сохраните эти данные или смените пароль в личном кабинете.").format(email=user.email, password=password)
                
                # Рендерим HTML
                html_message = render_to_string('emails/register_email.html', {
                    'email': user.email,
                    'password': password
                })
                
                send_html_email(
                    subject=mail_subject,
                    text_body=mail_message,
                    html_body=html_message,
                    to=[user.email],
                )
            except Exception as e:
                print(f"❌ Ошибка отправки письма при регистрации: {e}")

    refresh = RefreshToken.for_user(user)
    return {
        "token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "user_email": user.email,
        "is_pro": user.is_pro,
        "pro_expires_at": user.pro_expires_at.isoformat() if user.pro_expires_at else None,
    }

@api.post("/auth/login", response=AuthResponseSchema)
def login(request, payload: LoginSchema):
    user = authenticate(username=payload.email, password=payload.password)
    if not user:
        return api.create_response(request, {"message": _("Неверный email или пароль")}, status=401)
    
    refresh = RefreshToken.for_user(user)
    return {
        "token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "user_email": user.email,
        "is_pro": user.is_pro,
        "pro_expires_at": user.pro_expires_at.isoformat() if user.pro_expires_at else None,
    }

@api.post("/auth/claim-request")
def claim_request(request, payload: ClaimRequestOTPSchema):
    analyses = MedicalAnalysis.objects.filter(uid__in=payload.analysis_uids)
    if not analyses.exists():
        raise Http404(_("Анализы не найдены"))

    user = User.objects.filter(email=payload.email).first()

    with transaction.atomic():
        if not user:
            user = User.objects.create(email=payload.email, phone=payload.phone)
            pin_code = get_random_string(6, allowed_chars='0123456789')
            user.set_password(pin_code)
            user.save()
            PatientProfile.objects.create(user=user, full_name=_("Мой профиль"))

            try:
                mail_subject = _("Код доступа к результатам | WebDoc.life")
                mail_message = _("Ваши анализы готовы!\n\nВаш PIN-код для просмотра результатов: {pin_code}\n\nНикому не сообщайте этот код.").format(pin_code=pin_code)
                
                # Рендерим HTML
                html_message = render_to_string('emails/claim_code_email.html', {
                    'pin_code': pin_code
                })
                
                send_html_email(
                    subject=mail_subject,
                    text_body=mail_message,
                    html_body=html_message,
                    to=[user.email],
                )
            except Exception as e:
                print(f"❌ Ошибка отправки письма: {e}")

            return {"message": _("PIN-код отправлен на почту"), "status": "pin_sent"}
        else:
            return {"message": _("Email найден. Введите пароль."), "status": "requires_password"}

@api.post("/auth/claim-verify", response=AuthResponseSchema)
def claim_verify(request, payload: ClaimVerifyOTPSchema):
    pwd = getattr(payload, 'password', None) or getattr(payload, 'code', None)
    user = authenticate(username=payload.email, password=pwd)
    if not user:
        return api.create_response(request, {"message": _("Неверный код или пароль")}, status=401)

    claim_analyses_to_user(payload.analysis_uids, user)
    analyses = MedicalAnalysis.objects.filter(uid__in=payload.analysis_uids)

    # === ИЗМЕНЕНИЕ: ПРОВЕРКА ЛИМИТОВ ПЕРЕД ЗАПУСКОМ ИИ ===
    from .tasks import process_analysis_task
    limit = get_daily_analysis_limit(user)
    lang = getattr(request, 'LANGUAGE_CODE', 'en')

    # Сколько «запусков» уже потрачено сегодня
    launches_today = count_todays_launches(user)

    # Берем только те, которые еще ждут обработки
    pending_analyses = list(analyses.filter(status=MedicalAnalysis.Status.PENDING))

    for pending in pending_analyses:
        if launches_today < limit:
            # Вписывается в лимит — запускаем нейросеть
            process_analysis_task.delay(pending.uid, lang)
            launches_today += 1
        else:
            # ЖЕСТКАЯ ЗАЩИТА: Лимит превышен! Блокируем обработку, не тратим деньги
            pending.status = MedicalAnalysis.Status.FAILED
            pending.ai_result = {"summary": {"general_comment": _("Превышен дневной лимит бесплатных анализов. Пожалуйста, оформите PRO-подписку.")}}
            pending.save(update_fields=['status', 'ai_result'])

    refresh = RefreshToken.for_user(user)
    return {
        "token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "user_email": user.email,
        "is_pro": user.is_pro,
        "pro_expires_at": user.pro_expires_at.isoformat() if user.pro_expires_at else None,
    }


@api.post("/auth/refresh")
def refresh_token(request, payload: RefreshRequestSchema):
    try:
        refresh = RefreshToken(payload.refresh)
        return {"access": str(refresh.access_token)}
    except TokenError:
        return api.create_response(request, {"message": _("Токен устарел или недействителен")}, status=401)

# --- Восстановление пароля ---
@api.post("/auth/reset-password-request")
def reset_password_request(request, payload: ResetPasswordRequestSchema):
    try:
        user = User.objects.get(email=payload.email)
    except User.DoesNotExist:
        # Мы не переводим это сообщение, чтобы злоумышленник не мог "прощупывать" базу
        return {"message": _("Если такой email существует, мы отправили инструкцию.")}

    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    
    domain = settings.FRONTEND_URL
    reset_link = f"{domain}/auth/reset-password?uid={uid}&token={token}"
    
    try:
        mail_subject = _("Восстановление пароля WebDoc.life")
        mail_message = _("Вы запросили сброс пароля.\nДля установки нового пароля перейдите по ссылке:\n{reset_link}\n\nЕсли вы не запрашивали это действие, просто проигнорируйте письмо.").format(reset_link=reset_link)
        
        # Рендерим HTML
        html_message = render_to_string('emails/reset_password_email.html', {
            'reset_link': reset_link
        })
        
        send_html_email(
            subject=mail_subject,
            text_body=mail_message,
            html_body=html_message,
            to=[user.email],
        )
    except Exception as e:
        print(f"❌ Ошибка отправки письма при сбросе: {e}")

@api.post("/auth/reset-password-confirm")
def reset_password_confirm(request, payload: ResetPasswordConfirmSchema):
    try:
        uid = force_str(urlsafe_base64_decode(payload.uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return api.create_response(request, {"message": _("Неверная ссылка")}, status=400)

    if not default_token_generator.check_token(user, payload.token):
        return api.create_response(request, {"message": _("Ссылка устарела или недействительна")}, status=400)

    user.set_password(payload.new_password)
    user.save()
    return {"message": _("Пароль успешно изменен. Теперь вы можете войти.")}

@api.post("/auth/change-password", auth=JWTAuth())
def change_password(request, payload: ChangePasswordSchema):
    user = request.user
    
    if not user.check_password(payload.old_password):
        return api.create_response(request, {"message": _("Неверный текущий пароль")}, status=400)
    
    user.set_password(payload.new_password)
    user.save()
    
    return {"message": _("Пароль успешно изменен")}

# ==========================================
# 2. РАБОТА С АНАЛИЗАМИ (Гибридный доступ)
# ==========================================

@api.post("/analyses/upload", response=AnalysisResponseSchema, auth=OptionalJWTAuth())
def upload_analysis(
    request, 
    file: UploadedFile = File(...), 
    is_first: bool = Form(True),
    patient_id: str = Form(None),   
    guest_name: str = Form(None),
    guest_gender: str = Form(None),
    guest_dob: str = Form(None)
):
    # Единый механизм: OptionalJWTAuth авторизует владельца или пропускает гостя
    user = request.user if getattr(request.user, 'is_authenticated', False) else None
    patient_profile = None

    if user:
        limit = get_daily_analysis_limit(user)
        if count_todays_launches(user) >= limit:
            return api.create_response(
                request,
                {"message": "limit_reached", "limit": limit},
                status=403
            )
        
        if patient_id and patient_id != 'new':
            # Выбрали существующего пациента
            try:
                patient_profile = get_object_or_404(PatientProfile, id=int(patient_id), user=user)
            except ValueError:
                pass
        elif patient_id == 'new' and guest_name:
            # АВТОРИЗОВАННЫЙ юзер создает новый профиль прямо при загрузке
            patient_profile = PatientProfile.objects.create(
                user=user,
                full_name=guest_name,
                gender=guest_gender if guest_gender in ['M', 'F'] else None,
                birth_date=guest_dob if guest_dob else None
            )
        else:
            # Дефолтный фолбэк
            patient_profile = PatientProfile.objects.filter(user=user).order_by('created_at').first()
    else:
        # ГОСТЬ (Сирота)
        if guest_name:
            patient_profile = PatientProfile.objects.create(
                user=None,
                full_name=guest_name,
                gender=guest_gender if guest_gender in ['M', 'F'] else None,
                birth_date=guest_dob if guest_dob else None
            )

    analysis = MedicalAnalysis.objects.create(
        file=file,
        user=user,
        patient=patient_profile,
        status=MedicalAnalysis.Status.PENDING
    )

    # === ИЗМЕНЕНИЕ: ЛЕНИВАЯ ЗАГРУЗКА (LAZY PROCESSING) ===
    # Мы запускаем Celery-таску ТОЛЬКО если юзер авторизован!
    # Если это гость, файл просто лежит в БД и ждет привязки (claim_verify).
    if is_first and user:
        lang = getattr(request, 'LANGUAGE_CODE', 'en')
        from .tasks import process_analysis_task
        transaction.on_commit(lambda: process_analysis_task.delay(analysis.uid, lang))

    return analysis
# ---------------------------------------------------------

def _can_access_analysis(request, analysis) -> bool:
    """Доступ к анализу: владелец — всегда; орфан-анализ (гость до claim) — по UUID."""
    if analysis.user_id is None:
        return True
    user = getattr(request, 'user', None)
    return user is not None and user.is_authenticated and analysis.user_id == user.id


@api.get("/analyses/{uid}", response=AnalysisResponseSchema, auth=OptionalJWTAuth())
def get_analysis_result(request, uid: uuid.UUID):
    try:
        analysis = MedicalAnalysis.objects.get(uid=uid)
    except MedicalAnalysis.DoesNotExist:
        raise Http404(_("Анализ не найден"))

    if not _can_access_analysis(request, analysis):
        return api.create_response(request, {"message": _("Доступ запрещен")}, status=403)

    return analysis

@api.get("/analyses/{uid}/download", auth=OptionalJWTAuth())
def download_analysis_file(request, uid: uuid.UUID):
    analysis = get_object_or_404(MedicalAnalysis, uid=uid)

    if not _can_access_analysis(request, analysis):
        return api.create_response(request, {"message": _("Доступ запрещен")}, status=403)

    if not analysis.file:
        raise Http404(_("Файл не найден"))

    response = FileResponse(analysis.file.open('rb'))
    fname = analysis.file.name.split("/")[-1]
    response['Content-Disposition'] = f'inline; filename="{fname}"'
    return response

@api.post("/analyses/{uid}/reanalyze", response=AnalysisResponseSchema, auth=JWTAuth())
def reanalyze_document(request, uid: uuid.UUID):
    user = request.user
    
    # --- ПРОВЕРКА ЛИМИТОВ ПРИ ПЕРЕСЧЕТЕ ---
    limit = get_daily_analysis_limit(user)
    if count_todays_launches(user) >= limit:
        return api.create_response(
            request,
            {"message": "limit_reached", "limit": limit},
            status=403
        )
    # --------------------------------------

    old_analysis = get_object_or_404(MedicalAnalysis, uid=uid, user=user)
    
    new_analysis = MedicalAnalysis.objects.create(
        file=old_analysis.file,
        user=old_analysis.user,
        patient=old_analysis.patient,
        parent_analysis=old_analysis,
        status=MedicalAnalysis.Status.PENDING
    )
    
    lang = getattr(request, 'LANGUAGE_CODE', 'en')
    transaction.on_commit(lambda: process_analysis_task.delay(new_analysis.uid, lang))
    
    return new_analysis

@api.get("/analyses/{uid}/status-stream", auth=None)
def stream_analysis_status(request, uid: uuid.UUID):
    def event_stream():
        last_status = None
        # Таймаут: максимум 60 итераций по 3с = 180с, чтобы поток всегда завершался
        for _ in range(60):
            # Делаем легкий запрос только за нужным полем
            analysis = MedicalAnalysis.objects.filter(uid=uid).only('status').first()
            if not analysis:
                yield "data: not_found\n\n"
                break

            # Отправляем статус, только если он изменился
            if analysis.status != last_status:
                yield f"data: {analysis.status}\n\n"
                last_status = analysis.status

            # Если статус финальный — закрываем поток со стороны сервера
            if analysis.status in ['completed', 'failed']:
                break

            # Спим 3 секунды (реже дёргаем БД; ponytail: при росте нагрузки — Redis pub/sub)
            time.sleep(3)
        else:
            yield "data: timeout\n\n"

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')

# ==========================================
# 3. ЛИЧНЫЙ КАБИНЕТ (Защищено JWT)
# ==========================================

@api.get("/profiles", response=List[PatientProfileSchema], auth=JWTAuth())
def list_profiles(request):
    return PatientProfile.objects.filter(user=request.user).order_by('created_at')

@api.post("/profiles", response=PatientProfileSchema, auth=JWTAuth())
def create_profile(request, payload: CreateProfileSchema):
    profile = PatientProfile.objects.create(
        user=request.user,
        full_name=payload.full_name,
        birth_date=payload.birth_date,
        gender=payload.gender,
        weight=payload.weight,
        height=payload.height,
    )
    return profile

@api.put("/profiles/{profile_id}", response=PatientProfileSchema, auth=JWTAuth())
def update_profile(request, profile_id: int, payload: UpdateProfileSchema):
    profile = get_object_or_404(PatientProfile, id=profile_id, user=request.user)    
    profile.full_name = payload.full_name
    profile.birth_date = payload.birth_date
    profile.gender = payload.gender
    profile.weight = payload.weight
    profile.height = payload.height
    profile.save()
    return profile

@api.delete("/profiles/{profile_id}", auth=JWTAuth())
def delete_patient_profile(request, profile_id: int):
    # Находим профиль, принадлежащий именно этому пользователю
    profile = get_object_or_404(PatientProfile, id=profile_id, user=request.user)
    
    # Защита: не позволяем удалить основной профиль
    first_profile = PatientProfile.objects.filter(user=request.user).order_by('created_at').first()
    if profile.id == first_profile.id:
        return api.create_response(
            request, 
            {"message": _("Основной профиль нельзя удалить")}, 
            status=400
        )
        
    # Благодаря on_delete=models.CASCADE в моделях, 
    # все связанные анализы и показатели удалятся автоматически
    profile.delete()
    
    return {"success": True, "message": _("Профиль успешно удален")}

@api.get("/patients/{patient_id}/history", response=List[ChartResponseSchema], auth=JWTAuth())
def get_patient_history(request, patient_id: int, slugs: str = None):
    profile = get_object_or_404(PatientProfile, id=patient_id, user=request.user)
    
    indicators_qs = AnalysisIndicator.objects.filter(patient=profile).order_by('date')
    if slugs:
        slug_list = [s.strip() for s in slugs.split(',')]
        indicators_qs = indicators_qs.filter(slug__in=slug_list)
    
    grouped_data = {} 
    for record in indicators_qs:
        if record.slug not in grouped_data:
            grouped_data[record.slug] = {"name": record.name, "points": []}
        if record.value is not None:
            grouped_data[record.slug]["points"].append({
                "date": record.date,
                "value": record.value,
                "unit": record.unit,
                "analysis_uid": record.analysis.uid
            })
            
    response = []
    for slug, info in grouped_data.items():
        if info["points"]:
            response.append({"slug": slug, "name": info["name"], "data": info["points"]})
            
    return response

@api.get("/patients/{patient_id}/analyses", response=List[AnalysisResponseSchema], auth=JWTAuth())
def get_patient_analyses(request, patient_id: int):
    profile = get_object_or_404(PatientProfile, id=patient_id, user=request.user)
    return MedicalAnalysis.objects.filter(patient=profile).order_by('-created_at')

@api.delete("/analyses/{uid}", auth=JWTAuth())
def delete_analysis(request, uid: uuid.UUID):
    analysis = get_object_or_404(MedicalAnalysis, uid=uid)
    if analysis.user != request.user:
        return api.create_response(request, {"message": _("Доступ запрещен")}, status=403)

    analysis.delete()
    return {"success": True}

api.add_router("/premium", "premium.api.router")