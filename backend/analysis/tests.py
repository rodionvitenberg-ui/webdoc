from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from analysis.models import PromptTemplate
from analysis.prompts import DEFAULT_PROMPTS, MIN_PROMPT_LENGTH
from analysis.services import AnalysisPipeline, normalize_ai_result
from core.llm import json_loads


def assert_no_nulls(test_case, value, path="result"):
    """Рекурсивно проверяет, что в структуре нет None."""
    if value is None:
        test_case.fail(f"Найден null в {path}")
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_nulls(test_case, item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_nulls(test_case, item, f"{path}[{index}]")


class PromptDefaultsTests(TestCase):
    def test_defaults_exist_for_every_pipeline_role(self):
        expected_roles = {role.value for role in PromptTemplate.Role}
        self.assertEqual(set(DEFAULT_PROMPTS), expected_roles)

        for role, payload in DEFAULT_PROMPTS.items():
            with self.subTest(role=role):
                self.assertGreater(len(payload["system_prompt"]), MIN_PROMPT_LENGTH)
                self.assertIn("JSON", payload["system_prompt"])
                self.assertIsInstance(payload.get("context_data", ""), str)

    def test_prompt_falls_back_when_db_has_placeholder(self):
        pipeline = AnalysisPipeline(language_code="ru")
        placeholder = SimpleNamespace(system_prompt="Анализируй.", context_data=None)

        with patch("analysis.services.PromptTemplate.objects") as mock_objects:
            mock_objects.filter.return_value.first.return_value = placeholder
            prompt = pipeline._get_prompt(PromptTemplate.Role.EXTRACTOR)

        self.assertEqual(prompt, DEFAULT_PROMPTS["extractor"]["system_prompt"])

    def test_prompt_falls_back_when_db_row_is_missing(self):
        pipeline = AnalysisPipeline(language_code="ru")

        with patch("analysis.services.PromptTemplate.objects") as mock_objects:
            mock_objects.filter.return_value.first.return_value = None
            prompt = pipeline._get_prompt(PromptTemplate.Role.INTERPRETER)

        self.assertIn("indicators", prompt)
        self.assertIn("СПРАВОЧНИК SLUG", prompt)


class ResultNormalizationTests(TestCase):
    def test_null_payload_becomes_valid_result(self):
        result = normalize_ai_result(
            {
                "reasoning": None,
                "patient_info": None,
                "summary": None,
                "indicators": [
                    {
                        "name": None,
                        "slug": None,
                        "value": None,
                        "unit": None,
                        "ref_range": None,
                        "status": None,
                        "comment": None,
                        "category": None,
                    }
                ],
                "causes": None,
                "recommendations": None,
            },
            language_code="ru",
        )

        assert_no_nulls(self, result)
        self.assertFalse(result["summary"]["is_critical"])
        self.assertTrue(result["summary"]["general_comment"])
        self.assertEqual(result["patient_info"]["extracted_gender"], "UNKNOWN")
        self.assertEqual(result["indicators"][0]["slug"], "indicator_1")
        self.assertEqual(result["indicators"][0]["ref_range"], "-")
        self.assertTrue(result["recommendations"])

    def test_valid_values_are_preserved(self):
        result = normalize_ai_result(
            {
                "reasoning": "Гемоглобин в норме.",
                "patient_info": {
                    "extracted_name": "",
                    "extracted_birth_date": "1990-01-01",
                    "extracted_gender": "MALE",
                    "extracted_date": "2024-05-01",
                },
                "summary": {"is_critical": False, "general_comment": "Норма."},
                "indicators": [
                    {
                        "name": "Гемоглобин",
                        "slug": "hemoglobin",
                        "value": 0,
                        "unit": "g/L",
                        "ref_range": "130-160",
                        "status": "normal",
                        "comment": "",
                        "category": "Общий анализ крови",
                    }
                ],
                "causes": [],
                "recommendations": [{"type": "lifestyle", "text": "Пить воду."}],
            },
            language_code="ru",
        )

        assert_no_nulls(self, result)
        self.assertEqual(result["indicators"][0]["value"], "0")
        self.assertEqual(result["indicators"][0]["slug"], "hemoglobin")
        self.assertEqual(result["patient_info"]["extracted_date"], "2024-05-01")
        self.assertEqual(result["recommendations"][0]["text"], "Пить воду.")

    def test_critical_indicator_forces_summary_flag(self):
        result = normalize_ai_result(
            {
                "summary": {"is_critical": False, "general_comment": "-"},
                "indicators": [{"name": "Глюкоза", "status": "critical"}],
            }
        )
        self.assertTrue(result["summary"]["is_critical"])



class PipelineResultTests(TestCase):
    @patch("analysis.services.LLMClient.complete")
    def test_pipeline_returns_non_null_result(self, mock_complete):
        mock_complete.side_effect = [
            {
                "patient_info": {
                    "extracted_name": "",
                    "extracted_birth_date": "1990-01-01",
                    "extracted_gender": "MALE",
                    "extracted_date": "2024-05-01",
                },
                "indicators": [
                    {"name": "Гемоглобин", "value": "140", "unit": "g/L", "ref_range": "130-160"}
                ],
            },
            {
                "reasoning": None,
                "patient_info": None,
                "summary": None,
                "indicators": [
                    {
                        "name": "Гемоглобин",
                        "slug": None,
                        "value": None,
                        "unit": None,
                        "ref_range": None,
                        "status": None,
                        "comment": None,
                        "category": None,
                    }
                ],
                "causes": None,
                "recommendations": None,
            },
        ]

        result = AnalysisPipeline(language_code="ru").run_pipeline("Гемоглобин 140 g/L")

        assert_no_nulls(self, result)
        self.assertEqual(result["indicators"][0]["name"], "Гемоглобин")
        self.assertEqual(result["patient_info"]["extracted_date"], "2024-05-01")
        self.assertEqual(result["patient_info"]["extracted_gender"], "MALE")
        self.assertTrue(result["recommendations"])

    @patch("analysis.services.LLMClient.complete")
    def test_pipeline_raises_when_extraction_is_empty(self, mock_complete):
        mock_complete.return_value = {"patient_info": {}, "indicators": []}

        with self.assertRaises(ValueError):
            AnalysisPipeline(language_code="ru").run_pipeline("нечитаемый текст")


class JsonParsingTests(TestCase):
    def test_strips_markdown_fence(self):
        self.assertEqual(json_loads('```json\n{"status": "ok"}\n```'), {"status": "ok"})

    def test_rejects_non_object_root(self):
        with self.assertRaises(ValueError):
            json_loads("null")
