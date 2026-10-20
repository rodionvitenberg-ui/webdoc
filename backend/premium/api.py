import uuid
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import json
import logging

from .models import Transaction
from .services import CryptomusService, SUBSCRIPTION_AMOUNT
from typing import List
from django.http import StreamingHttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from ninja import Router
from ninja_jwt.authentication import JWTAuth
from .tasks import summarize_chat_history_task

from core.models import MedicalAnalysis
from .models import Trait, PatientTraitLink, ChatMessage
from .schemas import (
    TraitSchema, 
    CreateCustomTraitSchema, 
    LinkTraitSchema, 
    PatientTraitLinkSchema,
    ChatRequestSchema,
    ChatMessageResponseSchema
)
from .services import ChatAssistant

# Создаем роутер. Нам НЕ НУЖЕН здесь объект NinjaAPI
router = Router(auth=JWTAuth())
logger = logging.getLogger(__name__)
# --- ЭНДПОИНТЫ ХАРАКТЕРИСТИК (Traits) ---

@router.get("/traits", response=List[TraitSchema])
def list_traits(request):
    """Список доступных характеристик (системные + кастомные юзера)"""
    traits = Trait.objects.filter(is_custom=False) | Trait.objects.filter(created_by=request.user)
    return traits.order_by('category', 'name')

@router.post("/traits/custom", response=TraitSchema)
def create_custom_trait(request, payload: CreateCustomTraitSchema):
    """Создание своей характеристики"""
    trait = Trait.objects.create(
        name=payload.name,
        category=payload.category,
        is_custom=True,
        created_by=request.user
    )
    return trait

@router.post("/patients/{patient_id}/traits", response=PatientTraitLinkSchema)
def link_patient_trait(request, patient_id: int, payload: LinkTraitSchema):
    """Привязка характеристики к профилю пациента"""
    from core.models import PatientProfile
    profile = get_object_or_404(PatientProfile, id=patient_id, user=request.user)
    trait = get_object_or_404(Trait, id=payload.trait_id)
    
    link = PatientTraitLink.objects.create(
        patient=profile,
        trait=trait,
        details=payload.details
    )
    return link

@router.delete("/patients/traits/{link_id}")
def remove_patient_trait(request, link_id: int):
    """Удаление характеристики у пациента"""
    link = get_object_or_404(PatientTraitLink, id=link_id, patient__user=request.user)
    link.delete()
    return {"success": True}

@router.get("/patients/{patient_id}/traits", response=List[PatientTraitLinkSchema])
def get_patient_traits(request, patient_id: int):
    """Получение всех характеристик конкретного пациента"""
    from core.models import PatientProfile
    profile = get_object_or_404(PatientProfile, id=patient_id, user=request.user)
    return profile.premium_traits.all()


# --- ЭНДПОИНТЫ ИИ-ЧАТА (Chat) ---

@router.get("/analyses/{uid}/chat", response=List[ChatMessageResponseSchema])
def get_chat_history(request, uid: str):
    """Загрузка истории переписки по анализу из БД"""
    analysis = get_object_or_404(MedicalAnalysis, uid=uid, user=request.user)
    return analysis.chat_messages.all().order_by('created_at')

@router.post("/analyses/{uid}/chat")
def chat_with_analysis(request, uid: str, payload: ChatRequestSchema):
    """Отправка сообщения ИИ и получение потокового ответа со сохранением в БД"""
    today = timezone.now().date()
    msg_count = ChatMessage.objects.filter(
        analysis__user=request.user,
        role='user',
        created_at__date=today
    ).count()
    
    limit = 50 if request.user.is_pro else 5
    if msg_count >= limit:
        return JsonResponse(
            {"message": "limit_reached", "limit": limit}, 
            status=403
        )
    analysis = get_object_or_404(MedicalAnalysis, uid=uid, user=request.user)
    
    if not analysis.ai_result:
        return JsonResponse({"message": _("Анализ еще не расшифрован")}, status=400)

    # 1. Сохраняем новое сообщение пользователя в БД
    user_msg_content = payload.messages[-1].content
    ChatMessage.objects.create(analysis=analysis, role='user', content=user_msg_content)

    # 2. Собираем историю сообщений из БД для контекста ИИ (последние 10)
    db_history_qs = analysis.chat_messages.filter(is_summarized=False).order_by('-created_at')[:15]
    db_history = list(db_history_qs)
    db_history.reverse()

    # 3. Собираем медицинский контекст (демография + характеристики)
    context_lines = []
    if analysis.patient:
        p = analysis.patient
        if p.gender: context_lines.append(f"Пол: {p.get_gender_display()}")
        if p.birth_date: context_lines.append(f"Дата рождения: {p.birth_date}")
        if p.weight and p.height:
            context_lines.append(f"Вес: {p.weight} кг, Рост: {p.height} см")
        
        traits = p.premium_traits.all()
        if traits.exists():
            context_lines.append("Особенности пациента:")
            for t in traits:
                context_lines.append(f"- {t.trait.name}: {t.details or 'без деталей'}")

    if analysis.chat_summary:
        context_lines.insert(0, f"КРАТКАЯ ИСТОРИЯ ПРОШЛЫХ ОБСУЖДЕНИЙ:\n{analysis.chat_summary}\n---")
    
    patient_context = "\n".join(context_lines)

    # 4. Стриминг ответа
    assistant = ChatAssistant(language_code=getattr(request, 'LANGUAGE_CODE', 'ru'))
    
    def event_stream():
        full_response = []
        try:
            stream_generator = assistant.stream_chat(
                analysis_data=analysis.ai_result, 
                patient_context=patient_context, 
                chat_history=db_history
            )
            for chunk in stream_generator:
                full_response.append(chunk)
                # Экранируем переносы строк для формата SSE
                yield f"data: {chunk.replace('\n', '\\n')}\n\n"
        finally:
            # 5. Сохраняем полный ответ ИИ в БД даже при обрыве стрима
            assistant_final_text = "".join(full_response)
            if assistant_final_text:
                ChatMessage.objects.create(
                    analysis=analysis,
                    role='assistant',
                    content=assistant_final_text
                )

                # ЗАПУСКАЕМ ФОНОВУЮ ТАСКУ Celery!
                summarize_chat_history_task.delay(analysis.uid)

        yield "data: [DONE]\n\n"

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')

@router.post("/payment/create")
def create_subscription_payment(request):
    user = request.user
    service = CryptomusService()

    if not service.is_configured:
        # Провайдер не подключён: 503 и код ошибки вместо 500 с текстом исключения
        logger.warning("Payment provider is not configured (CRYPTOMUS_* env is empty)")
        return JsonResponse({"message": "payment_not_configured"}, status=503)

    # Переиспользуем активный pending-ордер — повторные клики не плодят дубли
    tx = Transaction.objects.filter(user=user, status='pending').first()
    if not tx:
        tx = Transaction.objects.create(
            user=user,
            amount=SUBSCRIPTION_AMOUNT,
            status='pending'
        )

    try:
        payment_url = service.create_payment(
            order_id=tx.order_id,
            amount=SUBSCRIPTION_AMOUNT,
            email=user.email
        )
    except Exception:
        # Детали провайдера — в лог, клиенту — только код ошибки
        logger.exception("Payment creation failed for transaction %s", tx.order_id)
        tx.status = 'fail'
        tx.save(update_fields=['status'])
        return JsonResponse({"message": "payment_provider_error"}, status=502)

    return {"payment_url": payment_url}


# Эндпоинт для Вебхука (auth=None, так как сюда стучится сервер Cryptomus, а не юзер)
@router.post("/payment/webhook", auth=None)
def payment_webhook(request):
    try:
        raw_body = request.body
        data = json.loads(raw_body)
        sign = data.get('sign')

        service = CryptomusService()
        if not service.verify_webhook(raw_body, sign):
            return JsonResponse({"error": "Invalid signature"}, status=400)

        order_id = data.get('order_id')
        status = data.get('status')  # 'paid', 'paid_over', etc.

        tx = Transaction.objects.filter(order_id=order_id).first()
        if not tx:
            # Неизвестный заказ: 200, чтобы провайдер не ретраил его бесконечно
            logger.warning("Webhook for unknown order %s", order_id)
            return JsonResponse({"status": "ignored"})

        if status in ('paid', 'paid_over'):
            if tx.status == 'paid':
                return JsonResponse({"status": "ok"})  # повторный вебхук — PRO не продлеваем

            # Сумма должна совпадать с заказом, иначе можно оплатить $1 и получить PRO
            paid_amount = Decimal(str(data.get('amount') or '0'))
            if paid_amount < tx.amount:
                logger.warning(
                    "Order %s: paid %s but required %s", tx.order_id, paid_amount, tx.amount
                )
                tx.status = 'fail'
                tx.save(update_fields=['status'])
                return JsonResponse({"status": "amount_mismatch"})

            tx.status = 'paid'
            tx.save(update_fields=['status'])

            # ВЫДАЕМ PRO НА 30 ДНЕЙ — продлеваем от текущей даты окончания
            user = tx.user
            current = user.pro_expires_at or timezone.now()
            new_expiry = max(current, timezone.now()) + timedelta(days=30)
            user.pro_expires_at = new_expiry
            user.save(update_fields=['pro_expires_at'])
        elif status in ('fail', 'cancel', 'system_fail', 'wrong_amount'):
            # Помечаем транзакцию как неуспешную (возврат средств и т.п.)
            if tx.status != 'paid':  # Не трогаем уже оплаченные
                tx.status = 'fail'
                tx.save(update_fields=['status'])

        return JsonResponse({"status": "ok"})
    except Exception:
        logger.exception("Webhook processing failed")
        return JsonResponse({"error": "webhook_failed"}, status=400)


# Статус подписки для фронтенда
@router.get("/payment/status")
def payment_status(request):
    user = request.user
    return {
        "is_pro": user.is_pro,
        "pro_expires_at": user.pro_expires_at.isoformat() if user.pro_expires_at else None,
    }
