"""Отправка писем с inline-логотипом (не удалёнными картинками, которые блокируют почтовики)."""
import logging
import os

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from email.mime.image import MIMEImage

logger = logging.getLogger(__name__)


def attach_logo(message: EmailMultiAlternatives) -> None:
    """
    Прикрепляет логотип как inline-вложение cid:logo.png.

    message.attach(filename, data, 'image/png') НЕ ставит header Content-ID.
    Без него ссылка cid:logo.png в шаблоне не резолвится — логотип не
    отображается в почтовых клиентах. Поэтому строим MIMEImage явно.
    """
    path = os.path.join(os.path.dirname(__file__), 'assets', 'logo.png')
    with open(path, 'rb') as f:
        data = f.read()

    part = MIMEImage(data, 'png')
    part['Content-ID'] = '<logo.png>'
    part['Content-Disposition'] = 'inline; filename="logo.png"'
    message.attach(part)


def send_html_email(subject: str, text_body: str, html_body: str, to: list[str]) -> bool:
    """Письмо с html_body и text_body, логотип встроен как cid:logo.png.

    Возвращает True при успешной отправке, False при сбое (сбой логируется с traceback).
    """
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=to,
    )
    attach_logo(message)
    message.attach_alternative(html_body, 'text/html')
    try:
        message.send(fail_silently=False)
        return True
    except Exception:
        # Не прячем ошибку SMTP: иначе «письмо не дошло» без единого следа в логах.
        logger.exception("Ошибка отправки письма '%s' → %s", subject, to)
        return False


def demo() -> None:
    """Самопроверка: письмо собирается с inline-логотипом cid:logo.png."""
    import django
    from django.conf import settings as dj_settings

    if not dj_settings.configured:
        templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'core', 'templates')
        dj_settings.configure(
            INSTALLED_APPS=[],
            EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
            DEFAULT_FROM_EMAIL='noreply@example.com',
            BASE_DIR=os.path.dirname(os.path.dirname(__file__)),
            TEMPLATES=[{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [templates_dir], 'APP_DIRS': False, 'OPTIONS': {'context_processors': []}}],
        )
        django.setup()

    from django.template.loader import get_template

    msg = EmailMultiAlternatives('Тест', 'текст', 'noreply@example.com', ['to@example.com'])
    attach_logo(msg)
    html = get_template('emails/reset_password_email.html').render({'reset_link': 'https://webdoc.life/auth/reset-password'})
    msg.attach_alternative(html, 'text/html')

    rendered = str(msg.message())
    assert 'Content-ID: <logo.png>' in rendered, 'cid:logo.png не имеет Content-ID — логотип не отрисуется'
    assert 'cid:logo.png' in html, 'шаблон должен ссылаться на cid:logo.png'
    print('mail helper OK: письмо собрано, логотип cid:logo.png с Content-ID')


if __name__ == '__main__':
    demo()