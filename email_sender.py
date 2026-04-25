from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from config import Settings
from models import Business, OutreachDraft


@dataclass(slots=True)
class EmailSendResult:
    status: str
    error: str | None = None


def send_outreach_email(settings: Settings, business: Business, draft: OutreachDraft) -> EmailSendResult:
    if not settings.enable_email_sending:
        return EmailSendResult(status="skipped", error="email_sending_disabled")
    if not business.email:
        return EmailSendResult(status="skipped", error="missing_recipient_email")
    if not settings.gmail_sender_email or not settings.gmail_app_password:
        return EmailSendResult(status="skipped", error="missing_gmail_credentials")

    message = EmailMessage()
    sender_display = settings.email_sender_name.strip() or settings.gmail_sender_email
    message["From"] = f"{sender_display} <{settings.gmail_sender_email}>"
    message["To"] = business.email
    message["Subject"] = draft.subject
    message.set_content(draft.body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(settings.gmail_sender_email, settings.gmail_app_password)
            server.send_message(message)
        return EmailSendResult(status="sent")
    except Exception as exc:
        return EmailSendResult(status="failed", error=str(exc))
