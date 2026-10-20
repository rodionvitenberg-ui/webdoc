import re

from django.db import migrations

# Старая формулировка (текст после OCR) → vision-формулировка.
# Меняем только эти фрагменты, чтобы не затереть правки промпта из админки.
# Правила идемпотентны: повторный прогон ничего не портит.
SUBSTITUTIONS = (
    (re.compile(r"подаётся\s+обезличенный текст бланка"), "подаются изображения бланка"),
    (re.compile(r"изображения бланка \(распознай текст сам\)"), "изображения бланка"),
    (re.compile(r"медицинский\s+OCR-экстрактор"), "медицинский экстрактор"),
)

PROMPT_FIELDS = ("system_prompt", "system_prompt_ru", "system_prompt_en", "system_prompt_es")


def update_extractor_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("analysis", "PromptTemplate")
    prompt = PromptTemplate.objects.filter(role="extractor").first()
    if not prompt:
        return

    changed_fields = []
    for field in PROMPT_FIELDS:
        current = getattr(prompt, field) or ""
        updated = current
        for pattern, replacement in SUBSTITUTIONS:
            updated = pattern.sub(replacement, updated)
        if updated != current:
            setattr(prompt, field, updated)
            changed_fields.append(field)

    if changed_fields:
        prompt.save(update_fields=changed_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("analysis", "0004_seed_prompt_templates"),
    ]

    operations = [
        migrations.RunPython(update_extractor_prompt, migrations.RunPython.noop),
    ]
