from datetime import timedelta
from celery import shared_task
from django.utils import timezone
from django.utils import translation
from .models import MedicalAnalysis, AnalysisIndicator, PatientProfile
from analysis.services import AnalysisPipeline
from core.services import save_atomic_indicators

# ----------------------------------------------

def trigger_next_analysis(analysis, language_code='en'):
    """
    Ищет следующий анализ в очереди для этого пользователя и запускает его.
    Это создает последовательную цепную реакцию (один за другим).
    """
    if analysis.user:
        next_pending = MedicalAnalysis.objects.filter(
            user=analysis.user, 
            status=MedicalAnalysis.Status.PENDING
        ).exclude(uid=analysis.uid).order_by('created_at').first()
        
        if next_pending:
            print(f"🔗 Цепная реакция: Запускаем следующий анализ ({next_pending.uid}) на языке {language_code}")
            # Обязательно прокидываем язык дальше!
            process_analysis_task.delay(next_pending.uid, language_code)

# (файл → изображения вынесено в analysis.document_processor)

@shared_task(bind=True, max_retries=5)
def process_analysis_task(self, analysis_id, language_code='en'): 
    translation.activate(language_code)
    
    try:
        analysis = MedicalAnalysis.objects.select_related('patient', 'user').get(uid=analysis_id)
    except MedicalAnalysis.DoesNotExist:
        translation.deactivate()
        return False
        
    try:
        if analysis.status != MedicalAnalysis.Status.PROCESSING:
            analysis.status = MedicalAnalysis.Status.PROCESSING
            analysis.save(update_fields=['status'])
        
        # --- ШАГ 1: файл → изображения для vision-модели ---
        # Импорт внутри таски: fitz нужен только celery-воркеру, не gunicorn.
        from analysis.document_processor import document_to_images

        analysis.file.open('rb')
        try:
            file_data = analysis.file.read()
        finally:
            analysis.file.close()

        try:
            images = document_to_images(file_data, analysis.file.name)
            if not images:
                raise ValueError("документ не дал ни одного изображения")
        except ValueError as exc:
            # Постоянная ошибка файла (битый/слишком много страниц) — ретраи не помогут
            print(f"❌ {exc}")
            analysis.status = MedicalAnalysis.Status.FAILED
            analysis.save(update_fields=['status'])
            trigger_next_analysis(analysis, language_code)
            return False
        
        # --- ШАГ 3: Сборка контекста прогрессии и профиля ---
        patient_context = ""
        if analysis.patient:
            context_lines = []
            
            # 3.1. Базовые демографические параметры
            if analysis.patient.gender:
                context_lines.append(f"Пол: {analysis.patient.get_gender_display()}")
            else:
                context_lines.append("Пол: Не указан")
                
            if analysis.patient.birth_date:
                dob = analysis.patient.birth_date
                # Централизованный расчёт через PatientProfile.age_display
                age_str = analysis.patient.age_display(analysis.created_at.date()) or "возраст неизвестен"
                context_lines.append(f"Дата рождения: {dob} (ТОЧНЫЙ ВОЗРАСТ НА МОМЕНТ АНАЛИЗА: {age_str})")
            
            # 3.2. Физические параметры и расчет ИМТ (Новая логика)
            if analysis.patient.weight:
                context_lines.append(f"Вес: {analysis.patient.weight} кг")
            if analysis.patient.height:
                context_lines.append(f"Рост: {analysis.patient.height} см")
                
            if analysis.patient.weight and analysis.patient.height:
                height_m = analysis.patient.height / 100.0
                if height_m > 0:
                    bmi = round(analysis.patient.weight / (height_m ** 2), 1)
                    context_lines.append(f"Индекс массы тела (ИМТ): {bmi}")

            # 3.3. Премиум-характеристики (Хронические заболевания, привычки)
            # Обернуто в try-except для безопасности до применения миграций premium
            try:
                # Используем related_name 'premium_traits', который мы заложили в моделях
                premium_traits = analysis.patient.premium_traits.select_related('trait').all()
                if premium_traits.exists():
                    context_lines.append("\nДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ ПАЦИЕНТА:")
                    for link in premium_traits:
                        trait_name = link.trait.name
                        details = link.details if link.details else "Детали не указаны"
                        context_lines.append(f"- {trait_name}: {details}")
            except Exception as e:
                print(f"⚠️ Premium traits not available or not migrated yet: {e}")

            # Склеиваем демографию и профиль в единый блок
            patient_context = "\n".join(context_lines)
            
            # 3.4. История анализов
            six_months_ago = timezone.now().date() - timedelta(days=180)
            past_indicators = AnalysisIndicator.objects.filter(
                patient=analysis.patient, 
                date__gte=six_months_ago,
                value__isnull=False
            ).exclude(analysis=analysis).order_by('-date')
            
            if past_indicators.exists():
                hist_dict = {}
                for ind in past_indicators:
                    if ind.name not in hist_dict:
                        hist_dict[ind.name] = f"{ind.value} {ind.unit or ''} (от {ind.date.strftime('%d.%m.%Y')})"
                
                if hist_dict:
                    history_str = "\n\nИСТОРИЯ ПРЕДЫДУЩИХ АНАЛИЗОВ ПАЦИЕНТА:\n"
                    for name, val in hist_dict.items():
                        history_str += f"- {name}: {val}\n"
                    patient_context += history_str

        # --- ШАГ 4: Запуск пайплайна (передаем ИЗОБРАЖЕНИЯ) ---
        pipeline = AnalysisPipeline(language_code=language_code)
        result = pipeline.run_pipeline(images, patient_context)
        
        if result:
            analysis.refresh_from_db()
            analysis.ai_result = result
            analysis.status = MedicalAnalysis.Status.COMPLETED
            analysis.save(update_fields=['ai_result', 'status'])
            
            try:
                save_atomic_indicators(analysis, result)
            except Exception as db_err:
                print(f"⚠️ Error saving atomic indicators: {db_err}")
            
            trigger_next_analysis(analysis, language_code)
            return True
        else:
            analysis.refresh_from_db()
            analysis.status = MedicalAnalysis.Status.FAILED
            analysis.save(update_fields=['status'])
            
            trigger_next_analysis(analysis, language_code)
            return False

    except Exception as exc:
        print(f"❌ Error in Task: {exc}")
        countdown = 5 * (2 ** self.request.retries)
        try:
            print(f"⚠️ Retrying in {countdown}s... (Attempt {self.request.retries + 1}/5)")
            raise self.retry(exc=exc, countdown=countdown)
        except self.MaxRetriesExceededError:
            print("❌ Max retries exceeded.")
            try:
                # Обернуто в try/except на случай, если запись удалена
                analysis.refresh_from_db()
                analysis.status = MedicalAnalysis.Status.FAILED
                analysis.save(update_fields=['status'])
                trigger_next_analysis(analysis, language_code)
            except MedicalAnalysis.DoesNotExist:
                pass
            return False
            
    finally:
        translation.deactivate()