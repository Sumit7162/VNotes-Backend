"""Transactional email, delivered through Brevo.

Brevo (https://app.brevo.com) is used rather than raw SMTP for two practical
reasons: its free plan covers 300 emails a day, which is far more than signups
need, and it is a plain HTTPS API, so it works from Cloud Run where outbound
SMTP ports are blocked.

Setting it up, once, in the Brevo dashboard:

  1. Senders, Domains & Dedicated IPs -> Senders -> add the address the mail
     will come from and click the confirmation link Brevo sends to it. Brevo
     rejects a send from an unverified sender, so this step is not optional.
  2. SMTP & API -> API Keys -> Generate a new API key. Put it in BREVO_API_KEY.
  3. Optionally authenticate the sending domain on the same page (SPF, DKIM and
     DMARC records). Without it the mail still sends, but is much more likely
     to land in spam.

If BREVO_API_KEY is empty the service stays in "log only" mode: it writes the
link to the application log instead of sending it, so local development works
without an account.
"""

from typing import Optional
from urllib.parse import quote

import httpx

from app.core.config import get_settings
from app.utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class EmailDeliveryError(Exception):
    """Raised when Brevo refused the message or could not be reached."""


class EmailService:
    def __init__(self) -> None:
        self.api_key = settings.brevo_api_key
        self.api_url = settings.brevo_api_url
        self.sender_email = settings.brevo_sender_email
        self.sender_name = settings.brevo_sender_name

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.sender_email)

    # -- transport ---------------------------------------------------------

    def send(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str,
        to_name: Optional[str] = None,
    ) -> bool:
        """Send one transactional email. True when Brevo accepted it."""
        if not self.is_configured:
            # Development fallback. The body carries the link, so the flow can
            # be completed from the log without an email provider at all.
            logger.warning(
                "email_not_sent_brevo_unconfigured",
                to=to_email,
                subject=subject,
                body=text_content,
            )
            return False

        recipient = {"email": to_email}
        if to_name:
            recipient["name"] = to_name

        payload = {
            "sender": {"name": self.sender_name, "email": self.sender_email},
            "to": [recipient],
            "subject": subject,
            "htmlContent": html_content,
            "textContent": text_content,
        }

        try:
            response = httpx.post(
                self.api_url,
                json=payload,
                headers={
                    "api-key": self.api_key,
                    "content-type": "application/json",
                    "accept": "application/json",
                },
                timeout=15.0,
            )
        except httpx.HTTPError as e:
            logger.error("brevo_request_failed", to=to_email, error=str(e))
            raise EmailDeliveryError("Could not reach the email provider") from e

        if response.status_code >= 400:
            # Brevo's own message is the useful part here - an unverified
            # sender and a bad API key look identical without it.
            logger.error(
                "brevo_send_rejected",
                to=to_email,
                status=response.status_code,
                body=response.text[:500],
            )
            raise EmailDeliveryError("The email provider rejected the message")

        logger.info("brevo_send_ok", to=to_email, subject=subject)
        return True

    # -- messages ----------------------------------------------------------

    def send_verification_email(
        self, to_email: str, token: str, full_name: Optional[str] = None
    ) -> bool:
        link = f"{settings.frontend_url.rstrip('/')}/verify-email?token={quote(token)}"
        hours = settings.email_verification_expire_hours
        greeting = f"Hi {full_name}," if full_name else "Hi,"

        text = (
            f"{greeting}\n\n"
            "Confirm your email address to finish setting up your V-Notes AI account:\n\n"
            f"{link}\n\n"
            f"The link works for {hours} hours. If you did not sign up, you can ignore "
            "this email.\n\n"
            "- V-Notes AI"
        )
        html = _wrap_html(
            heading="Confirm your email address",
            greeting=greeting,
            body="Confirm your email address to finish setting up your V-Notes AI account.",
            button_label="Verify my email",
            link=link,
            note=(
                f"The link works for {hours} hours. If you did not sign up, you can "
                "ignore this email."
            ),
        )
        return self.send(to_email, "Verify your V-Notes AI email address", html, text, full_name)

    def send_password_reset_email(
        self, to_email: str, token: str, full_name: Optional[str] = None
    ) -> bool:
        link = f"{settings.frontend_url.rstrip('/')}/reset-password?token={quote(token)}"
        minutes = settings.password_reset_expire_minutes
        greeting = f"Hi {full_name}," if full_name else "Hi,"

        text = (
            f"{greeting}\n\n"
            "Use this link to choose a new V-Notes AI password:\n\n"
            f"{link}\n\n"
            f"The link works for {minutes} minutes. If you did not ask for it, your "
            "password is unchanged.\n\n"
            "- V-Notes AI"
        )
        html = _wrap_html(
            heading="Reset your password",
            greeting=greeting,
            body="Use the button below to choose a new V-Notes AI password.",
            button_label="Choose a new password",
            link=link,
            note=(
                f"The link works for {minutes} minutes. If you did not ask for it, your "
                "password is unchanged."
            ),
        )
        return self.send(to_email, "Reset your V-Notes AI password", html, text, full_name)


def _wrap_html(
    heading: str, greeting: str, body: str, button_label: str, link: str, note: str
) -> str:
    """One plain email layout in the product's blue.

    Deliberately inline-styled and free of web fonts: mail clients strip
    stylesheets, so anything in a <style> block would simply not apply.
    """
    return f"""<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px;background:#f1f5f9;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#0f172a;">
    <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:14px;padding:32px;border:1px solid #e2e8f0;">
      <p style="margin:0 0 4px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#2563eb;font-weight:600;">V-Notes AI</p>
      <h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;">{heading}</h1>
      <p style="margin:0 0 8px;font-size:15px;">{greeting}</p>
      <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#334155;">{body}</p>
      <a href="{link}" style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;padding:12px 22px;border-radius:9px;">{button_label}</a>
      <p style="margin:24px 0 6px;font-size:13px;color:#64748b;">Or paste this address into your browser:</p>
      <p style="margin:0 0 24px;font-size:12px;word-break:break-all;color:#2563eb;">{link}</p>
      <hr style="border:none;border-top:1px solid #e2e8f0;margin:0 0 16px;" />
      <p style="margin:0;font-size:12px;color:#64748b;line-height:1.6;">{note}</p>
    </div>
  </body>
</html>"""


_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
