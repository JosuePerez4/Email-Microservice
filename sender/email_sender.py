import os

import resend


def send_email_message(payload: dict) -> None:
    resend.api_key = os.getenv("RESEND_API_KEY")
    if not resend.api_key:
        raise ValueError("Missing RESEND_API_KEY environment variable")

    to_email = payload.get("to")
    subject = payload.get("subject", "No subject")
    message = payload.get("message", "")
    from_email = payload.get("from", os.getenv("DEFAULT_FROM_EMAIL"))

    if not to_email:
        raise ValueError("Payload must include 'to' field")
    if not from_email:
        raise ValueError("Missing DEFAULT_FROM_EMAIL or payload 'from'")

    response = resend.Emails.send(
        {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": f"<p>{message}</p>",
        }
    )
    print("Correo enviado:", response)