import logging
import os
import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoAPIError(Exception):
    pass


class BrevoTransientError(BrevoAPIError):
    pass


class BrevoPermanentError(BrevoAPIError):
    pass


@shared_task(
    name="signals.send_password_reset_email",
    autoretry_for=(BrevoTransientError, requests.RequestException),
    retry_backoff=True,
    max_retries=3,
    retry_kwargs={"max_retries": 3}
)
def send_password_reset_email(_subject: str, _message: str, recipient_email: str):
    api_key = os.environ.get("BREVO_API_KEY") or getattr(settings, "BREVO_API_KEY", "")
    sender_email = os.environ.get("BREVO_SENDER_EMAIL") or getattr(settings, "BREVO_SENDER_EMAIL", "") or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@resqgrid.com")
    sender_name = os.environ.get("BREVO_SENDER_NAME") or getattr(settings, "BREVO_SENDER_NAME", "") or "ResQGrid"

    if "<" in sender_email and ">" in sender_email:
        name_part, addr_part = sender_email.split("<", 1)
        sender_email = addr_part.rstrip(">").strip()
        if name_part.strip() and not os.environ.get("BREVO_SENDER_NAME"):
            sender_name = name_part.strip()

    logger.info("[PasswordResetTask] Dispatching password reset email to %s", recipient_email)

    if api_key:
        headers = {
            "api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "sender": {
                "name": sender_name,
                "email": sender_email,
            },
            "to": [
                {"email": recipient_email}
            ],
            "subject": _subject,
            "textContent": _message,
        }

        try:
            response = requests.post(BREVO_API_URL, json=payload, headers=headers, timeout=(5.0, 10.0))
        except requests.RequestException as exc:
            logger.error("[PasswordResetTask] Network error calling Brevo API for %s: %s", recipient_email, type(exc).__name__)
            raise exc

        status_code = response.status_code

        if 200 <= status_code < 300:
            message_id = response.json().get("messageId", "ok") if response.content else "ok"
            logger.info("[PasswordResetTask] Brevo HTTPS API email successfully sent to %s (status=%d, messageId=%s)", recipient_email, status_code, message_id)
            return {"status": "sent", "recipient": recipient_email, "status_code": status_code, "message_id": message_id}
        elif status_code == 429 or status_code >= 500:
            logger.warning("[PasswordResetTask] Brevo API transient failure (status=%d) for %s. Retrying...", status_code, recipient_email)
            raise BrevoTransientError(f"Brevo API transient HTTP error: {status_code}")
        else:
            logger.error("[PasswordResetTask] Brevo API permanent failure (status=%d) for %s", status_code, recipient_email)
            raise BrevoPermanentError(f"Brevo API permanent HTTP error: {status_code}")
    else:
        try:
            from_email = f"{sender_name} <{sender_email}>" if sender_name else sender_email
            sent_count = send_mail(
                subject=_subject,
                message=_message,
                from_email=from_email,
                recipient_list=[recipient_email],
                fail_silently=False,
            )
            logger.info("[PasswordResetTask] Local fallback email sent to %s (count=%d)", recipient_email, sent_count)
            return {"status": "sent", "recipient": recipient_email, "count": sent_count}
        except Exception as exc:
            logger.error("[PasswordResetTask] Local fallback send_mail failed for %s: %s", recipient_email, exc)
            raise exc
