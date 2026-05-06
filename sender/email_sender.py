from django.conf import settings
from django.core.mail import EmailMultiAlternatives
import json
import urllib.request
import urllib.error


def _send_via_resend(payload: dict, from_email: str, to_email: str, subject: str, plain_text: str, html_body: str) -> bool:
    api_key = getattr(settings, "RESEND_API_KEY", "")
    if not api_key:
        return False

    body = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": plain_text,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            if 200 <= response.status < 300:
                print(f"Correo enviado via Resend a {to_email}")
                return True
            raise RuntimeError(f"Resend API status inesperado: {response.status}")
    except urllib.error.URLError as exc:
        raise ConnectionError(f"Error enviando correo por Resend: {exc}") from exc


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
    if _send_via_resend(payload, from_email, to_email, subject, plain_text, html_body):
        return

    email = EmailMultiAlternatives(subject=subject, body=plain_text, from_email=from_email, to=[to_email])
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)
    print(f"Correo enviado via SMTP a {to_email}")