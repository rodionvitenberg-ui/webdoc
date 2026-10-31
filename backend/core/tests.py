from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from .mail import send_html_email
from .models import AnalysisIndicator, MedicalAnalysis, PatientProfile, User
from .services import (
    FREE_DAILY_LIMIT,
    PRO_DAILY_LIMIT,
    claim_analyses_to_user,
    count_todays_launches,
    get_daily_analysis_limit,
    save_atomic_indicators,
)


class AnalysisAccessTests(TestCase):
    """Доступ к анализу: владелец — ок, чужак — 403, гость к орфану — ок."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com", password="testpass123"
        )
        self.other = User.objects.create_user(
            email="other@example.com", password="testpass123"
        )
        self.owner_profile = PatientProfile.objects.create(
            user=self.owner, full_name="Владелец"
        )

    def _auth_headers(self, user):
        from ninja_jwt.tokens import RefreshToken

        token = RefreshToken.for_user(user).access_token
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_owner_can_read_own_analysis(self):
        """Владелец с валидным JWT читает свой анализ (регрессия: auth=None давал 403)."""
        analysis = MedicalAnalysis.objects.create(
            user=self.owner,
            patient=self.owner_profile,
            status=MedicalAnalysis.Status.COMPLETED,
        )
        response = self.client.get(
            f"/analyses/{analysis.uid}", **self._auth_headers(self.owner)
        )
        self.assertEqual(response.status_code, 200)

    def test_other_user_forbidden(self):
        """Чужой авторизованный пользователь получает 403."""
        analysis = MedicalAnalysis.objects.create(
            user=self.owner,
            patient=self.owner_profile,
            status=MedicalAnalysis.Status.COMPLETED,
        )
        response = self.client.get(
            f"/analyses/{analysis.uid}", **self._auth_headers(self.other)
        )
        self.assertEqual(response.status_code, 403)

    def test_guest_can_read_orphan_analysis(self):
        """Гость (без токена) читает орфан-анализ по UUID."""
        orphan_profile = PatientProfile.objects.create(user=None, full_name="Гость")
        analysis = MedicalAnalysis.objects.create(
            user=None,
            patient=orphan_profile,
            status=MedicalAnalysis.Status.COMPLETED,
        )
        response = self.client.get(f"/analyses/{analysis.uid}")
        self.assertEqual(response.status_code, 200)


class SaveAtomicIndicatorsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )
        self.profile = PatientProfile.objects.create(
            user=self.user, full_name="Иван Иванов"
        )
        self.analysis = MedicalAnalysis.objects.create(
            user=self.user, patient=self.profile, status=MedicalAnalysis.Status.PENDING
        )

    def test_date_from_patient_info_iso(self):
        """Дата из patient_info.extracted_date (ISO) становится датой показателя."""
        ai_result = {
            "patient_info": {"extracted_date": "2025-05-14"},
            "indicators": [
                {
                    "slug": "wbc",
                    "name": "Лейкоциты",
                    "value": "6.2",
                    "unit": "10^9/л",
                }
            ],
        }
        save_atomic_indicators(self.analysis, ai_result)

        ind = AnalysisIndicator.objects.get(slug="wbc")
        self.assertEqual(ind.date, date(2025, 5, 14))
        self.assertEqual(ind.value, 6.2)

    def test_date_from_patient_info_local_format(self):
        """Локальный формат DD.MM.YYYY из patient_info.extracted_date парсится корректно."""
        ai_result = {
            "patient_info": {"extracted_date": "14.05.2025"},
            "indicators": [
                {
                    "slug": "wbc",
                    "name": "Лейкоциты",
                    "value": "6,2",
                    "unit": "10^9/л",
                }
            ],
        }
        save_atomic_indicators(self.analysis, ai_result)

        ind = AnalysisIndicator.objects.get(slug="wbc")
        self.assertEqual(ind.date, date(2025, 5, 14))
        # запятая в значении преобразуется в точку
        self.assertEqual(ind.value, 6.2)

    def test_date_from_note_is_ignored(self):
        """Дата из произвольного текста/note НЕ подхватывается — только явное поле."""
        ai_result = {
            "patient_info": {"extracted_date": None},
            "indicators": [
                {
                    "slug": "wbc",
                    "name": "Лейкоциты",
                    "value": "5.0",
                    "unit": "10^9/л",
                }
            ],
            "note": "дата анализа: 14.05.2025",
        }
        save_atomic_indicators(self.analysis, ai_result)

        ind = AnalysisIndicator.objects.get(slug="wbc")
        # Используется дата загрузки анализа, а не «случайная» из текста
        self.assertEqual(ind.date, self.analysis.created_at.date())
        self.assertEqual(ind.value, 5.0)

    def test_indicator_without_slug_skipped(self):
        """Показатель без slug пропускается — не должно быть 500."""
        ai_result = {
            "indicators": [
                {"name": "Без slug", "value": "1"},
            ]
        }
        save_atomic_indicators(self.analysis, ai_result)
        self.assertEqual(AnalysisIndicator.objects.count(), 0)


class ClaimAnalysisTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="claim@example.com", password="testpass123"
        )

    def test_orphan_analysis_attached_to_user(self):
        """Орфан-анализ после claim привязывается к пользователю."""
        orphan_profile = PatientProfile.objects.create(user=None, full_name="Аноним")
        orphan_analysis = MedicalAnalysis.objects.create(
            user=None, patient=orphan_profile, status=MedicalAnalysis.Status.PENDING
        )

        changed = claim_analyses_to_user([orphan_analysis.uid], self.user)

        orphan_analysis.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(orphan_analysis.user, self.user)
        # Орфан-профиль унаследован пользователем (совпадения по имени нет)
        self.assertEqual(orphan_analysis.patient.user, self.user)

    def test_already_attached_analysis_not_changed(self):
        """Анализ, уже принадлежащий пользователю, не перезаписывается."""
        profile = PatientProfile.objects.create(user=self.user, full_name="Мой профиль")
        analysis = MedicalAnalysis.objects.create(
            user=self.user, patient=profile, status=MedicalAnalysis.Status.PENDING
        )

        changed = claim_analyses_to_user([analysis.uid], self.user)

        self.assertFalse(changed)
        self.assertEqual(analysis.user, self.user)


class DailyLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="limit@example.com", password="testpass123"
        )

    def test_free_limit_5_pro_10(self):
        self.assertEqual(FREE_DAILY_LIMIT, 5)
        self.assertEqual(get_daily_analysis_limit(self.user), FREE_DAILY_LIMIT)
        self.user.pro_expires_at = timezone.now() + timedelta(days=30)
        self.user.save()
        self.assertEqual(get_daily_analysis_limit(self.user), PRO_DAILY_LIMIT)

    def test_count_todays_launches(self):
        for _ in range(2):
            MedicalAnalysis.objects.create(
                user=self.user, status=MedicalAnalysis.Status.COMPLETED
            )
        # pending не считается "запуском"
        MedicalAnalysis.objects.create(
            user=self.user, status=MedicalAnalysis.Status.PENDING
        )
        self.assertEqual(count_todays_launches(self.user), 2)


class SendHtmlEmailTests(TestCase):
    """Сбой SMTP не глотается молча: ошибка логируется с traceback, возвращается False."""

    @patch("core.mail.EmailMultiAlternatives")
    def test_smtp_failure_returns_false_and_logs(self, mock_email_cls):
        import smtplib

        mock_email_cls.return_value.send.side_effect = smtplib.SMTPException("boom")

        with self.assertLogs("core.mail", level="ERROR") as logs:
            sent = send_html_email("Тема", "текст", "<p>html</p>", ["to@example.com"])

        self.assertFalse(sent)
        self.assertTrue(any("boom" in line for line in logs.output))

    @patch("core.mail.EmailMultiAlternatives")
    def test_success_returns_true(self, mock_email_cls):
        mock_email_cls.return_value.send.return_value = 1

        sent = send_html_email("Тема", "текст", "<p>html</p>", ["to@example.com"])

        self.assertTrue(sent)


class RegisterEmailFailureTests(TestCase):
    """Регистрация без пароля: сбой письма не валит регистрацию и виден в ответе."""

    @patch("core.api.send_html_email", return_value=False)
    def test_register_reports_email_failure_in_warning(self, mocked_send):
        response = self.client.post(
            "/auth/register",
            {"email": "newuser@example.com"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("token", data)
        self.assertTrue(data["warning"])

    @patch("core.api.send_html_email", return_value=True)
    def test_register_success_has_no_warning(self, mocked_send):
        response = self.client.post(
            "/auth/register",
            {"email": "newuser2@example.com"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["warning"])