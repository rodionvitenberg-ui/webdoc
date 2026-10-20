from django.db import transaction
from django.utils import timezone
from .models import MedicalAnalysis, AnalysisIndicator, PatientProfile
import re
from datetime import datetime
import datetime as dt

# Дневные лимиты на запуск анализа (в т.ч. пересчёты)
FREE_DAILY_LIMIT = 5
PRO_DAILY_LIMIT = 10


def claim_analyses_to_user(analysis_uids, user):
    """
    Привязывает орфан-анализы (гость до claim) к пользователю.

    Орфан-профиль переносится на пользователя; при совпадении имени —
    сливается с существующим профилем. Возвращает True, если что-то привязалось.
    """
    analyses = MedicalAnalysis.objects.filter(uid__in=analysis_uids)
    changed = False

    with transaction.atomic():
        for analysis in analyses:
            if analysis.user:
                continue  # уже привязан

            analysis.user = user

            # Если у анализа есть профиль-сирота, привязываем его к юзеру
            if analysis.patient and analysis.patient.user is None:
                existing_profile = PatientProfile.objects.filter(
                    user=user, full_name=analysis.patient.full_name
                ).first()

                if existing_profile:
                    old_orphan = analysis.patient
                    analysis.patient = existing_profile
                    analysis.save(update_fields=['user', 'patient'])
                    old_orphan.delete()
                else:
                    analysis.patient.user = user
                    analysis.patient.save(update_fields=['user'])
                    analysis.save(update_fields=['user'])
            else:
                main_profile = PatientProfile.objects.filter(user=user).order_by('created_at').first()
                analysis.patient = main_profile
                analysis.save(update_fields=['user', 'patient'])

            AnalysisIndicator.objects.filter(analysis=analysis).update(patient=analysis.patient)
            changed = True

    return changed


def get_daily_analysis_limit(user) -> int:
    """Дневной лимит запусков для пользователя."""
    return PRO_DAILY_LIMIT if getattr(user, 'is_pro', False) else FREE_DAILY_LIMIT


def count_todays_analyses(user) -> int:
    """Сколько анализов пользователь создал сегодня (до проверки лимита)."""
    return MedicalAnalysis.objects.filter(
        user=user, created_at__date=timezone.now().date()
    ).count()


def count_todays_launches(user) -> int:
    """Сколько анализов уже реально запущено/обработано сегодня (не pending)."""
    return MedicalAnalysis.objects.filter(
        user=user,
        created_at__date=timezone.now().date(),
        status__in=[
            MedicalAnalysis.Status.PROCESSING,
            MedicalAnalysis.Status.COMPLETED,
            MedicalAnalysis.Status.FAILED,
        ],
    ).count()

def save_atomic_indicators(analysis: MedicalAnalysis, ai_result: dict):
    """
    Парсит JSON-результат и сохраняет показатели в таблицу AnalysisIndicator.
    """
    if not analysis.patient:
        print(f"⚠️ Пропуск сохранения показателей для {analysis.uid}: Нет пациента.")
        return

    indicators_data = ai_result.get('indicators', [])
    
    analysis_date = analysis.created_at.date() if analysis.created_at else dt.date.today()
    
    # 1. Сначала ищем там, где она должна быть (в patient_info)
    extracted_date_str = None
    patient_info = ai_result.get('patient_info', {})
    if isinstance(patient_info, dict):
        extracted_date_str = patient_info.get('extracted_date')

    # 2. Парсинг даты ТОЛЬКО из явного поля patient_info.extracted_date (без «агрессивного поиска»)
    if isinstance(extracted_date_str, str) and extracted_date_str:
        try:
            match_iso = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', extracted_date_str.strip())
            match_ru = re.fullmatch(r'(\d{2})[./-](\d{2})[./-](\d{4})', extracted_date_str.strip())
            if match_iso:
                analysis_date = datetime.strptime(match_iso.group(0), "%Y-%m-%d").date()
                print(f"✅ Успешно установлена ISO дата: {analysis_date}")
            elif match_ru:
                # Группы: 1-день, 2-месяц, 3-год
                analysis_date = datetime.strptime(
                    f"{match_ru.group(3)}-{match_ru.group(2)}-{match_ru.group(1)}", "%Y-%m-%d"
                ).date()
                print(f"✅ Успешно установлена локальная дата: {analysis_date}")
        except Exception as e:
            print(f"⚠️ Ошибка парсинга даты '{extracted_date_str}': {e}. Используем дату загрузки.")
    else:
        print("⚠️ ИИ не вернул дату, используем дату загрузки.")

    new_records = []
    
    for item in indicators_data:
        slug = item.get('slug')
        if not slug:
            continue
            
        raw_value = item.get('value', '')
        num_value = None
        
        try:
            clean_val = str(raw_value).replace(',', '.').replace(' ', '')
            clean_val = re.sub(r'[^\d.]', '', clean_val)
            if clean_val:
                num_value = float(clean_val)
        except ValueError:
            pass 

        record = AnalysisIndicator(
            analysis=analysis,
            patient=analysis.patient,
            slug=slug,
            name=item.get('name', 'Unknown'),
            value=num_value,
            string_value=str(raw_value)[:50],
            unit=item.get('unit'),
            date=analysis_date
        )
        new_records.append(record)

    if new_records:
        with transaction.atomic():
            # Удаляем старые показатели этого анализа (если это ре-анализ)
            AnalysisIndicator.objects.filter(analysis=analysis).delete()
            AnalysisIndicator.objects.bulk_create(new_records)
        print(f"✅ Сохранено {len(new_records)} показателей для профиля: {analysis.patient.full_name} (Дата: {analysis_date})")