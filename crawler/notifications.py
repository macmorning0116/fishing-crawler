from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from crawler.env import load_dotenv


logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    load_dotenv()
    return os.environ.get(name, default).strip()


def _parse_bool(value: str, default: bool = True) -> bool:
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def send_email_report(subject: str, body: str) -> bool:
    smtp_host = _env("SMTP_HOST")
    smtp_port = int(_env("SMTP_PORT", "587"))
    smtp_username = _env("SMTP_USERNAME")
    smtp_password = _env("SMTP_PASSWORD")
    mail_from = _env("NOTIFY_EMAIL_FROM")
    mail_to = _env("NOTIFY_EMAIL_TO")
    use_tls = _parse_bool(_env("SMTP_USE_TLS", "true"), default=True)

    if not all([smtp_host, mail_from, mail_to]):
        logger.warning("메일 발송 설정이 비어 있어서 알림 메일을 건너뜁니다")
        return False

    recipients = [value.strip() for value in mail_to.split(",") if value.strip()]
    if not recipients:
        logger.warning("NOTIFY_EMAIL_TO 가 비어 있어서 알림 메일을 건너뜁니다")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = mail_from
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)

    logger.info("알림 메일 발송 완료 recipients=%s subject=%s", len(recipients), subject)
    return True
