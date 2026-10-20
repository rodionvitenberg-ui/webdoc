import uuid
from django.utils import timezone
import datetime
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.translation import gettext_lazy as _

# --- Менеджер для создания пользователя по Email ---
class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError(_('Email обязателен'))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)

# --- Кастомный Юзер ---
class User(AbstractUser):
    username = None
    email = models.EmailField(_('Email address'), unique=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    pro_expires_at = models.DateTimeField(null=True, blank=True, verbose_name=_("PRO до"))

    @property
    def is_pro(self):
        """Возвращает True, если подписка активна"""
        return bool(self.pro_expires_at and self.pro_expires_at > timezone.now())
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self):
        return self.email
    
class PatientProfile(models.Model):
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='patients', 
        null=True, 
        blank=True
    )
    full_name = models.CharField(max_length=255, verbose_name=_("ФИО"))
    
    birth_date = models.DateField(null=True, blank=True, verbose_name=_("Дата рождения"))
    gender = models.CharField(
        max_length=10, 
        choices=[('M', _('Мужской')), ('F', _('Женский'))],
        null=True, blank=True,
        verbose_name=_("Пол")
    )
    
    weight = models.FloatField(null=True, blank=True, help_text=_("Вес в кг"), verbose_name=_("Вес"))
    height = models.FloatField(null=True, blank=True, help_text=_("Рост в см"), verbose_name=_("Рост"))
    
    created_at = models.DateTimeField(auto_now_add=True)

    def age_at(self, on_date=None):
        """Точный возраст на указанную дату (на момент анализа)."""
        if not self.birth_date:
            return None
        on_date = on_date or timezone.now().date()
        dob = self.birth_date
        return on_date.year - dob.year - ((on_date.month, on_date.day) < (dob.month, dob.day))

    def age_display(self, on_date=None):
        """Читаемое представление возраста: 'лет' или 'месяцев' для младенцев."""
        age = self.age_at(on_date)
        if age is None:
            return None
        if age > 0:
            return f"{age} лет/года"
        # Если ребенку меньше года, считаем месяцы
        months = (
            (on_date or timezone.now().date()).year - self.birth_date.year
        ) * 12 + (on_date or timezone.now().date()).month - self.birth_date.month
        return f"{max(months, 0)} месяцев"

    def __str__(self):
        # Умный __str__ для отладки сирот
        owner = self.user.email if self.user else _("Гость")
        return f"{self.full_name} ({owner})"

# --- Модель Анализа ---
class MedicalAnalysis(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='analyses', null=True, blank=True)
    patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='analyses', null=True, blank=True)
    file = models.FileField(upload_to='analyses/%Y/%m/%d/', verbose_name=_("Файл анализа"))
    
    parent_analysis = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, blank=True, 
        related_name='reanalyses',
        verbose_name=_("Оригинальный анализ")
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Status(models.TextChoices):
        PENDING = 'pending', _('Ожидает')
        PROCESSING = 'processing', _('В работе')
        COMPLETED = 'completed', _('Готово')
        FAILED = 'failed', _('Ошибка')
        
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name=_("Статус"))
    ai_result = models.JSONField(null=True, blank=True, verbose_name=_("Результат ИИ"))
    chat_summary = models.TextField(
        blank=True, 
        null=True, 
        verbose_name=_("Краткое содержание чата (Архив)")
    )
    
    def __str__(self):
        return f"Analysis {self.uid} ({self.get_status_display()})"
    
class AnalysisIndicator(models.Model):
    analysis = models.ForeignKey(MedicalAnalysis, on_delete=models.CASCADE, related_name='atomic_indicators')
    patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='indicators')
    
    slug = models.CharField(max_length=50, db_index=True)
    name = models.CharField(max_length=255, verbose_name=_("Название"))
    value = models.FloatField(null=True, blank=True, verbose_name=_("Значение"))
    string_value = models.CharField(max_length=50)
    unit = models.CharField(max_length=50, null=True, blank=True, verbose_name=_("Ед. измерения"))
    date = models.DateField(default=datetime.date.today, verbose_name=_("Дата"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['patient', 'slug', 'date']),
        ]

    def __str__(self):
        return f"{self.patient.full_name} - {self.slug}: {self.value}"
    
class SystemAnalytics(MedicalAnalysis):
    """
    Proxy-модель для вывода дашборда аналитики в Django Admin.
    Она не создает новую таблицу в БД.
    """
    class Meta:
        proxy = True
        verbose_name = _("📈 Дашборд Аналитики")
        verbose_name_plural = _("📈 Дашборд Аналитики")
        