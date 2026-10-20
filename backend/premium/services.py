import os
import json
import base64
import hashlib
import requests

from core.llm import LLMClient
from analysis.models import PromptTemplate
from analysis.prompts import DEFAULT_PROMPTS, MIN_PROMPT_LENGTH
from .models import ChatSettings

class ChatAssistant:
    def __init__(self, language_code='ru'):
        self.llm = LLMClient(base_url="https://api.deepseek.com", model_name="deepseek-chat")
        self.language_code = language_code

    def _get_prompt(self) -> str:
        # Используем роль из приложения analysis
        template = PromptTemplate.objects.filter(role='chat_assistant', is_active=True).first()
        if template:
            prompt_text = (template.system_prompt or "").strip()
            # Заглушка вроде "Говори." не задаёт контракт — берём default.
            if len(prompt_text) >= MIN_PROMPT_LENGTH:
                if template.context_data:
                    prompt_text += f"\n\n{template.context_data}"
                return prompt_text

        default = DEFAULT_PROMPTS['chat_assistant']
        prompt_text = default["system_prompt"]
        if default.get("context_data"):
            prompt_text += f"\n\n{default['context_data']}"
        return prompt_text
        
    def _minify_analysis(self, full_data: dict) -> dict:
        """
        Удаляет всю текстовую 'воду' (причины, рекомендации, выводы), 
        оставляя только голые цифры и статусы для экономии токенов.
        """
        summary = full_data.get("summary") or {}
        minified = {
            "summary": {
                "is_critical": summary.get("is_critical", False)
            },
            "indicators": []
        }
        
        for ind in (full_data.get("indicators") or []):
            if not isinstance(ind, dict):
                continue
            minified["indicators"].append({
                "name": ind.get("name"),
                "value": ind.get("value"),
                "unit": ind.get("unit"),
                "ref_range": ind.get("ref_range"),
                "status": ind.get("status")
            })
            
        return minified

    def stream_chat(self, analysis_data: dict, patient_context: str, chat_history: list):
        # 1. ПРОВЕРЯЕМ ТУМБЛЕР ИЗ АДМИНКИ
        settings = ChatSettings.get_settings()
        
        # Если оптимизация включена — сажаем анализ на диету!
        if settings.optimize_tokens:
            payload_data = self._minify_analysis(analysis_data)
        else:
            payload_data = analysis_data # Кидаем огромный JSON как есть

        # Используем общий LLMClient — пул ключей и ротация внутри
        language_instruction = (
            f"\n\nCRITICAL LANGUAGE INSTRUCTION: Answer ONLY in the language with ISO code "
            f"'{self.language_code}' and never switch languages."
        )
        sys_prompt = f"{self._get_prompt()}{language_instruction}"
        context_block = f"ДАННЫЕ ПАЦИЕНТА:\n{patient_context}\n\nАНАЛИЗ (JSON):\n{json.dumps(payload_data, ensure_ascii=False)}"

        messages = []
        for msg in chat_history:
            role = getattr(msg, 'role', msg.get('role') if isinstance(msg, dict) else 'user')
            content = getattr(msg, 'content', msg.get('content') if isinstance(msg, dict) else '')
            messages.append({"role": role, "content": content})

        yield from self.llm.stream(sys_prompt=f"{sys_prompt}\n\n{context_block}", messages=messages)

# Стоимость подписки PRO (USD) — единый источник цены
SUBSCRIPTION_AMOUNT = "10.00"


class PaymentNotConfigured(RuntimeError):
    """Ключи платёжного провайдера не заданы в окружении."""


class PaymentProviderError(RuntimeError):
    """Провайдер ответил ошибкой (ключи есть, но запрос отклонён)."""


class CryptomusService:
    API_URL = "https://api.cryptomus.com/v1"
    REQUEST_TIMEOUT = 20

    def __init__(self):
        self.merchant_id = os.environ.get("CRYPTOMUS_MERCHANT_ID")
        self.payment_key = os.environ.get("CRYPTOMUS_API_KEY")

    @property
    def is_configured(self) -> bool:
        return bool(self.merchant_id and self.payment_key)

    def _get_signature(self, json_body: str) -> str:
        """Cryptomus: md5(base64(JSON-тело) + API_KEY).

        Подписывать и отправлять нужно ОДНУ И ТУ ЖЕ строку: пересборка тела
        через `json=payload` меняет разделители и подпись перестаёт сходиться.
        """
        encoded_payload = base64.b64encode(json_body.encode('utf-8')).decode('utf-8')
        return hashlib.md5(f"{encoded_payload}{self.payment_key}".encode('utf-8')).hexdigest()

    def create_payment(self, order_id: str, amount: str, email: str) -> str:
        """
        Создает платеж и возвращает URL для редиректа юзера.

        url_return/url_success — куда попадает юзер после оплаты (фронтенд).
        url_callback — куда Cryptomus шлёт webhook (API бэкенда, путь без /api).
        """
        if not self.is_configured:
            raise PaymentNotConfigured("CRYPTOMUS_MERCHANT_ID / CRYPTOMUS_API_KEY не заданы")

        site_url = os.environ.get("SITE_URL", "https://webdoc.life").rstrip("/")
        backend_url = os.environ.get("BACKEND_URL", "https://api.webdoc.life").rstrip("/")
        payload = {
            "amount": str(amount),
            "currency": "USD",
            "order_id": str(order_id),
            "email": email,
            "url_return": f"{site_url}/dashboard",
            "url_success": f"{site_url}/dashboard",
            "url_callback": f"{backend_url}/premium/payment/webhook",
        }
        json_body = json.dumps(payload, separators=(',', ':'))

        response = requests.post(
            f"{self.API_URL}/payment",
            data=json_body,
            headers={
                "merchant": self.merchant_id,
                "sign": self._get_signature(json_body),
                "Content-Type": "application/json",
            },
            timeout=self.REQUEST_TIMEOUT,
        )
        data = response.json()

        if data.get("state") == 0:
            return data["result"]["url"]
        raise PaymentProviderError(data.get("message") or f"HTTP {response.status_code}")

    def verify_webhook(self, raw_body: bytes, sign: str | None = None) -> bool:
        """Проверяет подлинность вебхука по СЫРОМУ телу запроса (требование Cryptomus).

        Cryptomus подписывает md5(base64(raw_body) + API_KEY).
        Парсить тело и пересобирать JSON нельзя — порядок ключей/экранирование
        не обязаны совпадать с исходным запросом.
        """
        if not sign or not self.payment_key:
            return False

        encoded = base64.b64encode(raw_body).decode('utf-8')
        expected_sign = hashlib.md5(f"{encoded}{self.payment_key}".encode('utf-8')).hexdigest()

        return sign == expected_sign
