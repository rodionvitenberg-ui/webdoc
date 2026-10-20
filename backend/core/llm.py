"""
Единый LLM-клиент (deep module).

Инкапсулирует: пул AI_API_KEY* ключей, переключение при лимитах,
retry с backoff, complete/stream поверх OpenAI-совместимого API.
Интерфейс крошечный (complete / stream / has_keys) — вся сложность внутри.
"""
import os
import time

from openai import OpenAI


class LLMClient:
    def __init__(self, base_url: str = "https://api.deepseek.com", model_name: str = "deepseek-chat"):
        self._keys: list[str] = [
            val for key, val in os.environ.items()
            if key.startswith("AI_API_KEY") and val
        ]
        self.base_url = base_url
        self.model_name = model_name

    @property
    def has_keys(self) -> bool:
        return bool(self._keys)

    def _get_client(self) -> OpenAI:
        # Гарантируется вызов только при len(self._keys) > 0 (см. complete/stream)
        return OpenAI(api_key=self._keys[0], base_url=self.base_url)

    def _rotate_key(self) -> bool:
        """Перемещает первый ключ в конец и возвращает True (нет — если ключей 0)."""
        if len(self._keys) <= 1:
            return False
        self._keys.append(self._keys.pop(0))
        return True

    def complete(self, sys_prompt: str, user_prompt: str, require_json: bool = False,
                 temperature: float = 0.1, max_attempts: int | None = None,
                 images: list[str] | None = None) -> str | dict:
        """
        Синхронный вызов без стрима. Возвращает текст или dict (require_json=True).

        images — data-URL картинок; уходят в user-сообщение (в system/assistant
        vision-модель их не принимает).
        """
        if not self._keys:
            raise RuntimeError("AI_API_KEY не найдены в окружении")

        user_content: str | list[dict] = user_prompt
        if images:
            user_content = [{"type": "text", "text": user_prompt}] + [
                {"type": "image_url", "image_url": {"url": url, "detail": "high"}}
                for url in images
            ]

        max_attempts = max_attempts or len(self._keys) * 2
        for attempt in range(max_attempts):
            try:
                kwargs = {
                    "model": self.model_name,
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": temperature,
                }
                if require_json:
                    kwargs["response_format"] = {"type": "json_object"}

                response = self._get_client().chat.completions.create(**kwargs)
                result_text = response.choices[0].message.content
                return json_loads(result_text) if require_json else result_text

            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate limit" in err_str or "insufficient_quota" in err_str:
                    if self._rotate_key():
                        continue
                    time.sleep(20)
                else:
                    time.sleep(2)

        raise RuntimeError("Failed to call AI API after multiple retries")

    def stream(self, sys_prompt: str, messages: list[dict], temperature: float = 0.3):
        """Генератор текстовых чанков. Внутри — ротация ключей при лимитах."""
        if not self._keys:
            raise RuntimeError("AI_API_KEY не найдены в окружении")

        for _ in range(max(1, len(self._keys))):
            try:
                context = [{"role": "system", "content": sys_prompt}] + messages
                response = self._get_client().chat.completions.create(
                    model=self.model_name,
                    messages=context,
                    temperature=temperature,
                    stream=True,
                )
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except Exception:
                if not self._rotate_key():
                    yield "Сервис временно недоступен."
                    return


def json_loads(text: str) -> dict:
    """Парсит JSON-объект, снимая markdown-обёртки. Корень-не-объект — ошибка."""
    import json

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("LLM must return a JSON object")
    return data
