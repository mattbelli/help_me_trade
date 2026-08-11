"""Vendor keys ride in query strings; httpx logs full URLs. Prove they never render."""

from __future__ import annotations

import logging
import sys

import httpx

from config.log_redaction import RedactingFormatter, install_log_redaction


def _render(msg: str, *args: object, exc_info: object = None) -> str:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, exc_info)  # type: ignore[arg-type]
    return RedactingFormatter("%(message)s").format(record)


def test_redacts_each_vendors_key_param() -> None:
    # The three real leakers measured on hmt-backend 2026-08-11.
    for url, secret in [
        ("https://finnhub.io/api/v1/quote?symbol=AAPL&token=d8sFINNHUBa0", "d8sFINNHUBa0"),
        ("https://api.stlouisfed.org/fred/series?id=DGS10&api_key=4b9FREDce", "4b9FREDce"),
        ("https://www.alphavantage.co/query?function=X&apikey=418AVKEY8W", "418AVKEY8W"),
    ]:
        out = _render("HTTP Request: GET %s", url)
        assert secret not in out, out
        assert "REDACTED" in out


def test_leaves_alpaca_pagination_cursor_alone() -> None:
    # page_token is a cursor, not a credential, and it is useful when debugging paging.
    url = "https://data.alpaca.markets/v2/stocks/bars?page_token=QVZBUkNVUlNPUg=="
    out = _render("HTTP Request: GET %s", url)
    assert "QVZBUkNVUlNPUg==" in out
    assert "REDACTED" not in out


def test_redacts_inside_a_rendered_traceback() -> None:
    # The reason this is a formatter and not `getLogger("httpx").setLevel(WARNING)`:
    # HTTPStatusError carries the full URL in its own message, so silencing INFO
    # would still spill the key on a vendor 401 — when logs are actually read.
    url = "https://finnhub.io/api/v1/quote?symbol=AAPL&token=d8sFINNHUBa0"
    try:
        httpx.Response(401, request=httpx.Request("GET", url)).raise_for_status()
    except httpx.HTTPStatusError:
        exc_info = sys.exc_info()

    out = _render("Finnhub earnings calendar fetch failed", exc_info=exc_info)
    assert "d8sFINNHUBa0" not in out, out
    assert "REDACTED" in out


def test_redaction_stops_at_the_next_param() -> None:
    # A secret param followed by more params must not swallow the rest of the URL.
    out = _render(
        "GET %s", "https://finnhub.io/x?token=SECRETVALUE&symbol=AAPL&from=2026-01-01"
    )
    assert "SECRETVALUE" not in out
    assert "symbol=AAPL" in out
    assert "from=2026-01-01" in out


def test_install_wraps_existing_root_handlers() -> None:
    root = logging.getLogger()
    # Capture BEFORE installing — install_log_redaction mutates every root handler,
    # including pytest's, and leaking that into other tests would be a real bug.
    saved = [(h, h.formatter) for h in root.handlers]
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    try:
        install_log_redaction("%(message)s")
        assert isinstance(handler.formatter, RedactingFormatter)
    finally:
        root.removeHandler(handler)
        for h, fmt in saved:
            h.setFormatter(fmt)
