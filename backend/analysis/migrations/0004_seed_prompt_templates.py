from django.db import migrations

from analysis.prompts import DEFAULT_PROMPTS, MIN_PROMPT_LENGTH


def seed_prompts(apps, schema_editor):
    """
    Заполняет PromptTemplate каноническими промптами.

    Существующие длинные промпты (отредактированные в админке) не трогаем:
    обновляем только записи-заглушки и записи с незаполненными переводами.
    """
    PromptTemplate = apps.get_model("analysis", "PromptTemplate")

    for role, payload in DEFAULT_PROMPTS.items():
        prompt = payload["system_prompt"]
        context = payload.get("context_data") or ""
        obj, created = PromptTemplate.objects.get_or_create(
            role=role,
            defaults={
                "name": payload["name"],
                "system_prompt": prompt,
                "system_prompt_ru": prompt,
                "system_prompt_en": prompt,
                "system_prompt_es": prompt,
                "context_data": context,
                "context_data_ru": context,
                "context_data_en": context,
                "context_data_es": context,
                "is_active": True,
            },
        )
        if created:
            continue

        current = (obj.system_prompt_ru or obj.system_prompt or "").strip()
        if len(current) >= MIN_PROMPT_LENGTH:
            # Промпт уже отредактирован — не перезаписываем, только дозаполняем
            # отсутствующие переводы тем же текстом.
            changed_fields = []
            if not (obj.system_prompt or "").strip():
                obj.system_prompt = current
                changed_fields.append("system_prompt")
            if not (obj.system_prompt_en or "").strip():
                obj.system_prompt_en = current
                changed_fields.append("system_prompt_en")
            if not (obj.system_prompt_es or "").strip():
                obj.system_prompt_es = current
                changed_fields.append("system_prompt_es")
            if changed_fields:
                obj.save(update_fields=changed_fields)
            continue

        obj.name = payload["name"]
        obj.system_prompt = prompt
        obj.system_prompt_ru = prompt
        obj.system_prompt_en = prompt
        obj.system_prompt_es = prompt
        obj.context_data = context
        obj.context_data_ru = context
        obj.context_data_en = context
        obj.context_data_es = context
        obj.is_active = True
        obj.save()


class Migration(migrations.Migration):

    dependencies = [
        ("analysis", "0003_alter_prompttemplate_role"),
    ]

    operations = [
        migrations.RunPython(seed_prompts, migrations.RunPython.noop),
    ]
