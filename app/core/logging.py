from __future__ import annotations

import logging
import re
import traceback

from app.core.config import settings

# Telegram bot tokens: "<bot_id>:<token>" (bot id = 6+ digits, token = 25+
# token characters). aiohttp errors embed the full request URL
# "https://api.telegram.org/bot<token>/..." in their str()/traceback, so this
# pattern must be scrubbed before anything reaches the log files/console.
_BOT_TOKEN_PATTERN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{25,}")

# URL userinfo credentials: "scheme://user:pass@..." -> keep the scheme and
# everything after the @, drop the credential pair (e.g. TELEGRAM_PROXY_URL
# with socks5://user:pass@host). Hosts without userinfo are never matched.
_URL_CREDENTIALS_PATTERN = re.compile(
    r"\b([A-Za-z][A-Za-z0-9+.\-]*)://[^/\s:@]+:[^@\s/]*@"
)

_TOKEN_PLACEHOLDER = "[REDACTED_TOKEN]"
_CREDENTIALS_PLACEHOLDER = r"\1://[REDACTED]@"


def redact_secrets(text: str) -> str:
    """Redact bot tokens and URL userinfo credentials from ``text``.

    Idempotent: already-redacted text comes back unchanged (the placeholders
    contain no characters the patterns can re-match).
    """
    if not text:
        return text
    text = _BOT_TOKEN_PATTERN.sub(_TOKEN_PLACEHOLDER, text)
    return _URL_CREDENTIALS_PATTERN.sub(_CREDENTIALS_PLACEHOLDER, text)


class SecretRedactionFilter(logging.Filter):
    """Scrubs secrets from every record emitted through its handler.

    - message: msg + args are rendered eagerly and replaced ONLY when
      redaction actually changed something, so %-style lazy formatting is
      preserved for clean records.
    - exception text: record.exc_text is what logging.Formatter appends for
      exc_info (tokens embedded in e.g. aiohttp ClientResponseError
      tracebacks land HERE, not in msg). The filter formats the traceback
      once, redacts it, and caches it on the record; Formatter.format() then
      reuses that cached text, so every handler emits the redacted form.
    - the filter always returns True: it is a scrubber, not a gate.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = None
        if message is not None:
            redacted = redact_secrets(message)
            if redacted != message:
                record.msg = redacted
                record.args = None

        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = redact_secrets(
                    "".join(traceback.format_exception(*record.exc_info))
                )
            except Exception:
                record.exc_text = None
        elif record.exc_text:
            record.exc_text = redact_secrets(str(record.exc_text))
        return True


def _attach_redaction_filter() -> None:
    """Attach the scrubber to every handler configure_logging manages.

    Guards against duplicate attachment so repeated configure_logging() calls
    stay idempotent.
    """
    redaction_filter = SecretRedactionFilter()
    for handler in logging.getLogger().handlers:
        if not any(
            isinstance(existing, SecretRedactionFilter)
            for existing in handler.filters
        ):
            handler.addFilter(redaction_filter)


def configure_logging() -> None:
    # NOTE: configure_logging intentionally creates NO FileHandler (root
    # StreamHandler only, file routing is left to the service supervisors),
    # so there is no plain FileHandler to migrate to RotatingFileHandler
    # here; the redaction filter below is attached to whatever handlers exist.
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    _attach_redaction_filter()
