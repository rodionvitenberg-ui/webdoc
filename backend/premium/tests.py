from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import base64
import hashlib
import json
import os

from django.test import TestCase
from django.utils import timezone

from core.models import User
from .models import Transaction


class PaymentIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="payment@example.com", password="testpass123"
        )

    def test_reuses_pending_order(self):
        """Повторные запросы возвращают тот же pending-ордер, а не плодят дубли."""
        tx = Transaction.objects.create(user=self.user, amount=10.00, status="pending")
        same = Transaction.objects.filter(user=self.user, status="pending").first()
        self.assertEqual(tx.id, same.id)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)


class ProExtensionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="pro@example.com", password="testpass123"
        )

    def test_pro_extended_from_current_expiry(self):
        """Продление считает +30 дней от текущей даты окончания, а не от now()."""
        existing = timezone.now() + timedelta(days=20)
        self.user.pro_expires_at = existing
        self.user.save()

        current = self.user.pro_expires_at or timezone.now()
        new_expiry = max(current, timezone.now()) + timedelta(days=30)
        self.user.pro_expires_at = new_expiry
        self.user.save(update_fields=["pro_expires_at"])

        # 20 + 30 = 50 дней от now()
        self.assertAlmostEqual(
            (self.user.pro_expires_at - timezone.now()).total_seconds(),
            timedelta(days=50).total_seconds(),
            delta=5,
        )


class PaymentCreationTests(TestCase):
    """Создание платежа: конфигурация провайдера, подпись и тело запроса."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="create@example.com", password="testpass123"
        )

    def test_service_raises_when_keys_are_missing(self):
        from premium.services import CryptomusService, PaymentNotConfigured

        with patch.dict(os.environ, {"CRYPTOMUS_MERCHANT_ID": "", "CRYPTOMUS_API_KEY": ""}):
            service = CryptomusService()
            self.assertFalse(service.is_configured)
            with self.assertRaises(PaymentNotConfigured):
                service.create_payment("order-1", "10.00", self.user.email)

    def test_endpoint_returns_503_when_not_configured(self):
        from premium.api import create_subscription_payment

        with patch.dict(os.environ, {"CRYPTOMUS_MERCHANT_ID": "", "CRYPTOMUS_API_KEY": ""}):
            response = create_subscription_payment(SimpleNamespace(user=self.user))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)["message"], "payment_not_configured")
        # Ордер не создаём, пока провайдер не подключён
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)

    @patch("premium.services.requests.post")
    def test_signed_body_is_sent_verbatim(self, mock_post):
        """Подпись считается от той же строки, которая уходит в теле запроса."""
        from premium.services import CryptomusService

        mock_post.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {"state": 0, "result": {"url": "https://pay.example/abc"}},
        )

        with patch.dict(os.environ, {"CRYPTOMUS_MERCHANT_ID": "mid", "CRYPTOMUS_API_KEY": "secret"}):
            url = CryptomusService().create_payment("order-1", "10.00", self.user.email)

        self.assertEqual(url, "https://pay.example/abc")

        kwargs = mock_post.call_args.kwargs
        body = kwargs["data"]
        expected_sign = hashlib.md5(
            (base64.b64encode(body.encode()).decode() + "secret").encode()
        ).hexdigest()
        self.assertEqual(kwargs["headers"]["sign"], expected_sign)
        self.assertEqual(json.loads(body)["order_id"], "order-1")
        self.assertEqual(json.loads(body)["amount"], "10.00")

    @patch("premium.services.requests.post")
    def test_provider_error_is_reported(self, mock_post):
        from premium.services import CryptomusService, PaymentProviderError

        mock_post.return_value = SimpleNamespace(
            status_code=401, json=lambda: {"state": 1, "message": "Invalid Merchant Uuid."}
        )

        with patch.dict(os.environ, {"CRYPTOMUS_MERCHANT_ID": "mid", "CRYPTOMUS_API_KEY": "secret"}):
            with self.assertRaises(PaymentProviderError):
                CryptomusService().create_payment("order-1", "10.00", self.user.email)


class PaymentWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="webhook@example.com", password="testpass123"
        )
        self.tx = Transaction.objects.create(user=self.user, amount="10.00", status="pending")

    @patch("premium.services.CryptomusService.verify_webhook", return_value=True)
    def test_webhook_fail_marks_transaction(self, mock_verify):
        """Webhook со статусом fail помечает транзакцию как неуспешную."""
        import json

        payload = {"order_id": str(self.tx.order_id), "status": "fail"}
        response = self.client.post(
            "/premium/payment/webhook",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "fail")

    @patch("premium.services.CryptomusService.verify_webhook", return_value=True)
    def test_webhook_paid_grants_pro(self, mock_verify):
        """Webhook со статусом paid выдаёт PRO и не трогает повторные оплаты."""
        import json

        payload = {"order_id": str(self.tx.order_id), "status": "paid", "amount": "10.00"}
        response = self.client.post(
            "/premium/payment/webhook",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "paid")
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_pro)

    @patch("premium.services.CryptomusService.verify_webhook", return_value=True)
    def test_webhook_underpayment_does_not_grant_pro(self, mock_verify):
        """Оплата меньше суммы заказа PRO не выдаёт."""
        import json

        payload = {"order_id": str(self.tx.order_id), "status": "paid", "amount": "1.00"}
        response = self.client.post(
            "/premium/payment/webhook",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["status"], "amount_mismatch")

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "fail")
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_pro)

    @patch("premium.services.CryptomusService.verify_webhook", return_value=True)
    def test_repeated_paid_webhook_does_not_extend_pro_twice(self, mock_verify):
        """Повторный paid-вебхук не продлевает подписку ещё на 30 дней."""
        import json

        payload = {"order_id": str(self.tx.order_id), "status": "paid", "amount": "10.00"}
        for _ in range(2):
            self.client.post(
                "/premium/payment/webhook",
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.user.refresh_from_db()
        days_left = (self.user.pro_expires_at - timezone.now()).days
        self.assertEqual(days_left, 29)  # 30 дней, без второго продления


class PaymentStatusEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="status@example.com", password="testpass123"
        )

    def test_payment_status_returns_expected_shape(self):
        """Эндпоинт статуса возвращает is_pro и pro_expires_at."""
        from premium.api import payment_status

        payload = {"user": self.user}
        result = payment_status(type("Request", (), payload)())
        self.assertFalse(result["is_pro"])
        self.assertIsNone(result["pro_expires_at"])

        self.user.pro_expires_at = timezone.now() + timedelta(days=7)
        self.user.save()
        result = payment_status(type("Request", (), {"user": self.user})())
        self.assertTrue(result["is_pro"])
        self.assertIsNotNone(result["pro_expires_at"])


class ChatAssistantNullSafetyTests(TestCase):
    def test_chat_prompt_falls_back_to_default(self):
        from premium.services import ChatAssistant

        with patch("premium.services.PromptTemplate.objects") as mock_objects:
            mock_objects.filter.return_value.first.return_value = None
            prompt = ChatAssistant(language_code="en")._get_prompt()

        self.assertIn("WebDoc.life", prompt)

    def test_minify_handles_legacy_null_payload(self):
        from premium.services import ChatAssistant

        result = ChatAssistant()._minify_analysis({"summary": None, "indicators": None})

        self.assertEqual(result, {"summary": {"is_critical": False}, "indicators": []})