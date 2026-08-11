"""Keep vendor credentials out of the logs.

httpx logs every request at INFO with the full URL, and three vendors take their
key as a query parameter — Finnhub `token`, FRED `api_key`, Alpha Vantage `apikey`
— so the raw credential landed in the journal on every call. Measured on
hmt-backend over two days (2026-08-11): 630 Finnhub, 12 FRED, 4 Alpha Vantage.

Redaction lives in the FORMATTER rather than in a `logging.getLogger("httpx")
.setLevel(WARNING)`, because silencing INFO does not actually close the leak:
`httpx.HTTPStatusError` renders the full URL in its own message, so a vendor 401
spills the key at WARNING/ERROR — exactly when someone is reading the log.
Formatting is also the only layer that covers rendered tracebacks.
"""

from __future__ import annotations

import logging
import re

# The value must directly follow `?` or `&`. That keeps Alpaca's `page_token`
# pagination cursor logging in full — it is not a secret and it is useful when
# debugging paging — while a bare `token=` after `?`/`&` (Finnhub) is caught.
_SECRET_PARAM = re.compile(
    r"(?<=[?&])((?:api[_-]?key|apikey|token|secret|password|auth|signature)=)[^&\s\"'>]+",
    re.IGNORECASE,
)


class RedactingFormatter(logging.Formatter):
    """Strips credential query params out of the finished log line."""

    def format(self, record: logging.LogRecord) -> str:
        return _SECRET_PARAM.sub(r"\g<1>REDACTED", super().format(record))


def install_log_redaction(fmt: str) -> None:
    """Re-wrap every root handler with the redacting formatter (call after basicConfig)."""
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter(fmt))
