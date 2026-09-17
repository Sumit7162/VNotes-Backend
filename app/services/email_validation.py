"""Deciding whether an email address is worth accepting.

There are three separate questions, and only the first two can be answered at
signup time:

  1. Is it shaped like an address?  Pydantic's EmailStr already answers this,
     so anything reaching this module has passed that check.
  2. Can that domain receive mail at all?  Answered by looking up the domain's
     MX records. This is what catches "gmial.com", "gmail.con" and made-up
     domains before an email is wasted on them.
  3. Does the mailbox exist, and does it belong to whoever typed it?  No
     lookup can answer this - SMTP probing is unreliable and gets the sender
     blocklisted. The verification link is the answer: the account stays
     switched off until somebody opens the mailbox and clicks it.

Disposable-address domains are refused too, since a throwaway inbox defeats
the point of verifying at all.
"""

from typing import Optional, Tuple

from email_validator import EmailNotValidError, validate_email

from app.core.config import get_settings
from app.utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


# A short list of the throwaway-inbox services that show up most. It is
# deliberately not exhaustive - a complete list is impossible to maintain, and
# this only needs to raise the effort of farming free-plan quota.
DISPOSABLE_DOMAINS = {
    "10minutemail.com",
    "guerrillamail.com",
    "mailinator.com",
    "tempmail.com",
    "temp-mail.org",
    "throwawaymail.com",
    "yopmail.com",
    "getnada.com",
    "trashmail.com",
    "sharklasers.com",
    "dispostable.com",
    "fakeinbox.com",
    "maildrop.cc",
    "mintemail.com",
    "spam4.me",
}


def normalize_and_check_email(email: str) -> Tuple[str, Optional[str]]:
    """Normalise an address and say why it is unusable, if it is.

    Returns (normalised_address, error). `error` is None when the address
    looks deliverable. The normalised form is what gets stored, so that
    "User@Gmail.com" and "user@gmail.com" cannot become two accounts.
    """
    try:
        # check_deliverability does the MX lookup. It needs outbound DNS, so it
        # is behind a setting: where DNS is blocked the syntax check still runs
        # and signup keeps working.
        result = validate_email(
            email,
            check_deliverability=settings.email_check_deliverability,
        )
    except EmailNotValidError as e:
        # The library's messages are written for end users, so they are passed
        # straight through ("The domain name gmial.com does not exist.").
        return email.strip().lower(), str(e)
    except Exception as e:
        # A DNS timeout must not take signup down with it: fall back to the
        # syntax-only verdict, which has already effectively passed.
        logger.warning("email_deliverability_check_failed", error=str(e))
        return email.strip().lower(), None

    normalized = result.normalized.lower()
    domain = normalized.rsplit("@", 1)[-1]

    if domain in DISPOSABLE_DOMAINS:
        return normalized, "Disposable email addresses are not accepted. Please use a permanent address."

    return normalized, None
