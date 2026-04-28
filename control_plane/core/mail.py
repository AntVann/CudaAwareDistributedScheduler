import os
import smtplib
from email.message import EmailMessage


def send_token_email(
    to_email: str,
    subject_name: str,
    plaintext_token: str,
) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is required to send token emails")

    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    use_starttls = os.getenv("SMTP_STARTTLS", "true").strip().lower() not in {"0", "false", "no"}

    if not sender:
        raise RuntimeError("SMTP_FROM is required to send token emails")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = "CUDA Scheduler API Token Approval"
    msg.set_content(
        "\n".join(
            [
                f"Hello {subject_name},",
                "",
                "Your token request has been approved.",
                "Use this bearer token in the admin UI or API clients:",
                "",
                f"TOKEN: {plaintext_token}",
                "",
                "Treat this token like a password.",
            ]
        )
    )

    with smtplib.SMTP(host=host, port=port, timeout=10) as smtp:
        smtp.ehlo()
        if use_starttls:
            smtp.starttls()
            smtp.ehlo()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
