import json
import re

from core.llm import LLMClient
from .models import PromptTemplate
from .prompts import DEFAULT_PROMPTS, MIN_PROMPT_LENGTH, RESULT_FALLBACKS, resolve_language

VALID_STATUSES = {"normal", "low", "high", "critical"}
VALID_SEVERITIES = {"green", "yellow", "red"}


def _as_text(value) -> str:
    """Приводит значение LLM к строке; никогда не возвращает None."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(part for part in (_as_text(item) for item in value) if part)
    if isinstance(value, dict):
        parts = [f"{key}: {_as_text(item)}" for key, item in value.items()]
        return ", ".join(part for part in parts if part)
    return str(value).strip()


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "да", "sí", "si"}
    return False


def _slug(value, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", _as_text(value).lower()).strip("_")
    return text or fallback


def normalize_extraction(raw_data) -> dict:
    """Валидирует ответ экстрактора. Бросает ValueError вместо сохранения null-результата."""
    if not isinstance(raw_data, dict):
        raise ValueError("Экстрактор вернул не JSON-объект")

    patient_info = raw_data.get("patient_info")
    if not isinstance(patient_info, dict):
        patient_info = {}

    indicators = raw_data.get("indicators")
    if not isinstance(indicators, list):
        raise ValueError("Экстрактор не вернул массив indicators")

    clean_indicators = []
    for item in indicators:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        value = _as_text(item.get("value"))
        if not name or not value:
            continue
        clean_indicators.append({
            "name": name,
            "value": value,
            "unit": _as_text(item.get("unit")),
            "ref_range": _as_text(item.get("ref_range")),
        })

    if not clean_indicators:
        raise ValueError("Экстрактор не извлёк ни одного показателя с названием и значением")

    gender = _as_text(patient_info.get("extracted_gender")).upper()
    if gender not in {"MALE", "FEMALE"}:
        gender = "UNKNOWN"

    return {
        "patient_info": {
            "extracted_name": _as_text(patient_info.get("extracted_name")),
            "extracted_birth_date": _as_text(patient_info.get("extracted_birth_date")),
            "extracted_gender": gender,
            "extracted_date": _as_text(patient_info.get("extracted_date")),
        },
        "indicators": clean_indicators,
    }


def normalize_ai_result(data, language_code: str = "ru") -> dict:
    """Приводит ответ интерпретатора к AIResultSchema без null-значений."""
    if not isinstance(data, dict):
        raise ValueError("Интерпретатор вернул не JSON-объект")

    texts = RESULT_FALLBACKS[resolve_language(language_code)]
    raw_patient = data.get("patient_info")
    if not isinstance(raw_patient, dict):
        raw_patient = {}

    indicators = []
    for index, item in enumerate(data.get("indicators") or [], start=1):
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name")) or texts["indicator"].format(index=index)
        status = _as_text(item.get("status")).lower()
        indicators.append({
            "slug": _slug(item.get("slug"), f"indicator_{index}"),
            "name": name,
            "value": _as_text(item.get("value")),
            "unit": _as_text(item.get("unit")),
            "ref_range": _as_text(item.get("ref_range")) or "-",
            "status": status if status in VALID_STATUSES else "normal",
            "comment": _as_text(item.get("comment")),
            "category": _as_text(item.get("category")) or texts["category"],
        })

    raw_summary = data.get("summary")
    if not isinstance(raw_summary, dict):
        raw_summary = {}
    general_comment = _as_text(raw_summary.get("general_comment")) or texts["summary"]
    is_critical = _as_bool(raw_summary.get("is_critical")) or any(
        indicator["status"] == "critical" for indicator in indicators
    )

    causes = []
    for item in data.get("causes") or []:
        if not isinstance(item, dict):
            continue
        title = _as_text(item.get("title"))
        description = _as_text(item.get("description"))
        if not title and not description:
            continue
        severity = _as_text(item.get("severity")).lower()
        causes.append({
            "title": title or texts["cause_title"],
            "description": description or texts["cause_description"],
            "severity": severity if severity in VALID_SEVERITIES else "yellow",
        })

    recommendations = []
    for item in data.get("recommendations") or []:
        if not isinstance(item, dict):
            continue
        text = _as_text(item.get("text"))
        if not text:
            continue
        recommendations.append({
            "type": _as_text(item.get("type")) or "lifestyle",
            "text": text,
        })
    if not recommendations:
        recommendations.append({"type": "visit", "text": texts["recommendation"]})

    return {
        "reasoning": _as_text(data.get("reasoning")),
        "patient_info": {
            "extracted_name": _as_text(raw_patient.get("extracted_name")),
            "extracted_birth_date": _as_text(raw_patient.get("extracted_birth_date")),
            "extracted_gender": _as_text(raw_patient.get("extracted_gender")) or "UNKNOWN",
            "extracted_date": _as_text(raw_patient.get("extracted_date")),
        },
        "summary": {"is_critical": is_critical, "general_comment": general_comment},
        "indicators": indicators,
        "causes": causes,
        "recommendations": recommendations,
    }


class AnalysisPipeline:
    def __init__(self, language_code='ru'):
        # deepseek-flash — vision-модель: принимает изображения бланка напрямую
        self.llm = LLMClient(base_url="https://api.deepseek.com", model_name="deepseek-flash")
        self.language_code = language_code

    def _get_prompt(self, role: str) -> str:
        template = PromptTemplate.objects.filter(role=role, is_active=True).first()
        if template:
            prompt_text = (template.system_prompt or "").strip()
            # Слишком короткая запись — заглушка вроде "Анализируй."; используем default.
            if len(prompt_text) >= MIN_PROMPT_LENGTH:
                if template.context_data:
                    prompt_text += f"\n\n{template.context_data}"
                return prompt_text

        default = DEFAULT_PROMPTS.get(role)
        if not default:
            raise ValueError(f"❌ Промпт для роли '{role}' не найден ни в БД, ни в default-ах!")
        prompt_text = default["system_prompt"]
        if default.get("context_data"):
            prompt_text += f"\n\n{default['context_data']}"
        return prompt_text

    def run_pipeline(self, images: list[str], patient_context: str = None) -> dict:
        if not images:
            raise ValueError("❌ Документ не дал ни одного изображения: интерпретация невозможна")

        print(f"--- Stage 1: Секретарь (Extraction) [{self.llm.model_name}, Lang: {self.language_code}, изображений: {len(images)}] ---")
        raw_data = self._step_extract(images)

        # --- УМНОЕ ОБОГАЩЕНИЕ КОНТЕКСТА ДЕМОГРАФИЕЙ ИЗ БЛАНКА ---
        patient_info = raw_data.get('patient_info', {})
        ext_gender = patient_info.get('extracted_gender')
        ext_dob_str = patient_info.get('extracted_birth_date')
        ext_date_str = patient_info.get('extracted_date')

        additional_context = ""

        # Если Экстрактор нашел пол, а в профиле его нет
        if ext_gender in ('MALE', 'FEMALE') and "Пол:" not in (patient_context or ""):
            gender_ru = "Мужской" if ext_gender == "MALE" else "Женский"
            additional_context += f"\nПол (извлечено из бланка): {gender_ru}"

        # Если Экстрактор нашел дату рождения, а в профиле ее нет
        if ext_dob_str and "ТОЧНЫЙ ВОЗРАСТ" not in (patient_context or ""):
            try:
                from datetime import datetime
                dob_year = int(ext_dob_str.split('-')[0])
                current_year = int(ext_date_str.split('-')[0]) if ext_date_str else datetime.now().year
                age = current_year - dob_year
                if age >= 0:
                    additional_context += f"\nВозраст (вычислен по бланку): ~{age} лет."
            except Exception:
                pass # Если дата в кривом формате, просто пропускаем

        # Приклеиваем извлеченные данные к контексту
        if additional_context:
            patient_context = (patient_context or "КОНТЕКСТ ПАЦИЕНТА:\n") + additional_context
            print(f"💡 Контекст обогащен данными из бланка: {additional_context}")

        print(f"--- Stage 2: Профессор (Interpretation) [{self.llm.model_name}] ---")
        interpreted_data = self._step_interpret(raw_data, patient_context)

        # Если интерпретатор потерял демографию — возвращаем её из экстрактора.
        for key, value in patient_info.items():
            if not value:
                continue
            current = interpreted_data["patient_info"].get(key, "")
            if not current or (key == "extracted_gender" and current == "UNKNOWN"):
                interpreted_data["patient_info"][key] = value

        if not interpreted_data["indicators"]:
            raise ValueError("❌ Интерпретатор не вернул ни одного показателя")

        return interpreted_data

    def _step_extract(self, images: list[str]):
        sys_prompt = self._get_prompt(PromptTemplate.Role.EXTRACTOR)
        
        # --- ЖЕСТКИЕ ПРАВИЛА ДЛЯ ЭКСТРАКТОРА ---
        sys_prompt += "\n\nВАЖНО: Верни ответ СТРОГО в формате валидного JSON объекта."
        sys_prompt += f"\nCRITICAL LANGUAGE INSTRUCTION: You MUST translate and return all text values in the language corresponding to the ISO code '{self.language_code}'. The JSON keys must remain in English."
        sys_prompt += "\nCRITICAL DATE INSTRUCTION: For 'extracted_date', find the date the biomaterial was collected/taken. IGNORE the print date. Format MUST be exactly 'YYYY-MM-DD'. If unknown, return an empty string, NEVER null."
        sys_prompt += """
CRITICAL NULL INSTRUCTION: Never return null anywhere. Use "" for unknown text, "UNKNOWN" for unknown gender and [] for an empty indicators array.
"""
        
        full_prompt = "ИЗОБРАЖЕНИЯ БЛАНКА АНАЛИЗА — распознай текст сам и извлеки данные строго по структуре."
        
        result = self.llm.complete(
            sys_prompt=sys_prompt,
            user_prompt=full_prompt,
            require_json=True,
            temperature=0.1,
            images=images,
        )
        return normalize_extraction(result)

    def _step_interpret(self, raw_data: dict, patient_context: str = None):
        sys_prompt = self._get_prompt(PromptTemplate.Role.INTERPRETER)
        
        json_structure = """
        {
          "reasoning": "string",
          "patient_info": {"extracted_date": "string"}, 
          "summary": {"is_critical": boolean, "general_comment": "string"},
          "indicators": [{"slug": "string", "name": "string", "value": "string", "unit": "string", "ref_range": "string", "status": "normal|low|high|critical", "comment": "string", "category": "string"}],
          "causes": [{"title": "string", "description": "string", "severity": "green|yellow|red"}],
          "recommendations": [{"type": "string", "text": "string"}]
        }
        """
        
        strict_rules = f"""
        КРИТИЧЕСКИЕ ПРАВИЛА:
        1. LANGUAGE: You MUST translate ALL generated text (name, comment, category, reasoning, general_comment, recommendations, causes) into the language corresponding to the ISO code '{self.language_code}'. DO NOT leave it in the original language if it differs from '{self.language_code}'.
        2. JSON KEYS: All JSON keys (e.g., 'reasoning', 'patient_info', 'name') MUST remain in English exactly as shown in the structure.
        3. INDICATORS: Поле 'name' должно переноситься ДОСЛОВНО И ЦЕЛИКОМ (но переведено на '{self.language_code}'). Категорически запрещено обрезать названия.
        4. REF RANGE: Поле 'ref_range' переноси дословно из текста (например '1003 - 1035' или '< 5'). Если референса нет, ставь '-'.
        5. DATE: 'extracted_date' MUST be transferred exactly as it appears in the source text. Do not try to convert it to YYYY-MM-DD.
        6. NULLS: NEVER return null. Use "-" for unknown strings, 0/false for booleans and [] for empty arrays.
        """
        
        sys_prompt += f"\n\nВАЖНО: Верни ответ СТРОГО в формате валидного JSON объекта, используя следующую структуру:\n{json_structure}"
        sys_prompt += f"\n\n{strict_rules}"
        
        context_str = f"КОНТЕКСТ ПАЦИЕНТА: {patient_context}" if patient_context else "КОНТЕКСТ ПАЦИЕНТА: Неизвестен."
        full_prompt = f"{context_str}\nВОТ ИСХОДНЫЕ ДАННЫЕ (RAW JSON):\n{json.dumps(raw_data, ensure_ascii=False)}"
        
        result = self.llm.complete(
            sys_prompt=sys_prompt,
            user_prompt=full_prompt,
            require_json=True,
            temperature=0.1
        )
        return normalize_ai_result(result, self.language_code)
