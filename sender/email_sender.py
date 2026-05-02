from django.core.mail import EmailMultiAlternatives
from django.conf import settings


def send_email_message(payload: dict) -> None:
    to_email = payload.get("to")
    subject = payload.get("subject", "No subject")
    message = payload.get("message", "")
    html_content = payload.get("html")
    from_email = payload.get("from", settings.DEFAULT_FROM_EMAIL)

    if not to_email:
        raise ValueError("Payload must include 'to' field")
    if not from_email:
        raise ValueError("Payload must include 'from' or DEFAULT_FROM_EMAIL must be configured")

    plain_text = message or "Notificacion de Loyalty Ops"
    html_body = html_content or f"<p>{plain_text}</p>"
    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_text,
        from_email=from_email,
        to=[to_email],
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)
    print(f"Correo enviado via SMTP a {to_email}")