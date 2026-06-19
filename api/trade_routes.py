"""Trade import and history API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ai.llm.factory import get_llm
from db.trades import (
    create_trade_account,
    daily_trade_stats,
    get_import_dedup_sets,
    get_trade_account,
    import_trade_events,
    is_duplicate_event,
    list_trade_accounts,
    list_trade_events,
    symbol_trade_stats,
    trade_summary_stats,
)
from imports.schwab_transactions import parse_schwab_transactions_bytes

router = APIRouter(prefix="/api/trades", tags=["trades"])


class CreateAccountBody(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    broker: str = Field(default="schwab", min_length=1, max_length=40)


class ReviewBody(BaseModel):
    account_id: str | None = None
    symbol: str | None = None
    from_date: str | None = None
    to_date: str | None = None
    focus: str | None = None


def _event_to_api(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event["id"],
        "account_id": event["account_id"],
        "source": event["source"],
        "event_type": event["event_type"],
        "trade_date": event["trade_date"],
        "symbol": event["symbol"],
        "side": event["side"],
        "quantity": event["quantity"],
        "price": event["price"],
        "fees": event["fees"],
        "amount": event["amount"],
        "description": event["description"],
        "imported_at": event["imported_at"],
    }


@router.get("/accounts")
def api_list_trade_accounts() -> dict[str, Any]:
    return {"accounts": list_trade_accounts()}


@router.post("/accounts")
def api_create_trade_account(body: CreateAccountBody) -> dict[str, Any]:
    account = create_trade_account(body.label, body.broker)
    return {"account": account}


@router.get("/events")
def api_list_trade_events(
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    symbol: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    events = list_trade_events(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        symbol=symbol,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return {"events": [_event_to_api(e) for e in events], "count": len(events)}


@router.get("/stats/summary")
def api_trade_summary(
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return trade_summary_stats(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/stats/daily")
def api_trade_daily(
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return {"days": daily_trade_stats(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )}


@router.get("/stats/by-symbol")
def api_trade_by_symbol(
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return {"symbols": symbol_trade_stats(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )}


@router.post("/import/preview")
async def api_import_preview(
    file: UploadFile = File(...),
    account_id: str | None = Form(default=None),
) -> dict[str, Any]:
    data = await file.read()
    try:
        parsed = parse_schwab_transactions_bytes(
            data,
            account_id=account_id or "preview",
        )
    except (ValueError, UnicodeDecodeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    existing_keys: set[str] = set()
    existing_broker_txn_ids: set[str] = set()
    if account_id:
        existing_keys, existing_broker_txn_ids = get_import_dedup_sets(account_id)

    would_insert = 0
    duplicates = 0
    sample = []
    for event in parsed.events:
        if account_id and is_duplicate_event(
            event,
            external_keys=existing_keys,
            broker_txn_ids=existing_broker_txn_ids,
        ):
            duplicates += 1
        else:
            would_insert += 1
        if len(sample) < 50:
            sample.append(
                {
                    "trade_date": event.trade_date.isoformat(),
                    "event_type": event.event_type,
                    "symbol": event.symbol,
                    "side": event.side,
                    "quantity": str(event.quantity) if event.quantity is not None else None,
                    "price": str(event.price) if event.price is not None else None,
                    "fees": str(event.fees),
                    "amount": str(event.amount),
                    "description": event.description,
                }
            )

    return {
        "from_date": parsed.from_date,
        "to_date": parsed.to_date,
        "parsed": len(parsed.events),
        "would_insert": would_insert,
        "duplicates": duplicates,
        "skipped": parsed.skipped,
        "warnings": parsed.warnings[:20],
        "failed": [],
        "sample": sample,
    }


@router.post("/import")
async def api_import_trades(
    file: UploadFile = File(...),
    account_id: str = Form(...),
) -> dict[str, Any]:
    if get_trade_account(account_id) is None:
        return JSONResponse({"error": "Unknown account_id"}, status_code=404)

    data = await file.read()
    try:
        parsed = parse_schwab_transactions_bytes(data, account_id=account_id)
    except (ValueError, UnicodeDecodeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    result = import_trade_events(
        account_id,
        parsed.events,
        source="schwab_json_import",
        filename=file.filename,
        from_date=parsed.from_date,
        to_date=parsed.to_date,
        skipped=parsed.skipped,
        warnings=parsed.warnings,
    )

    return {
        "batch_id": result.batch_id,
        "parsed": result.parsed,
        "inserted": result.inserted,
        "duplicates": result.duplicates,
        "skipped": result.skipped,
        "warnings": result.warnings[:20],
        "failed": result.failed,
        "from_date": parsed.from_date,
        "to_date": parsed.to_date,
    }


@router.post("/review")
def api_trade_review(body: ReviewBody = Body(...)) -> dict[str, Any]:
    events = list_trade_events(
        account_id=body.account_id,
        from_date=body.from_date,
        to_date=body.to_date,
        symbol=body.symbol,
        limit=100,
    )
    if not events:
        return JSONResponse({"error": "No trades match filters"}, status_code=404)

    stats = trade_summary_stats(
        account_id=body.account_id,
        from_date=body.from_date,
        to_date=body.to_date,
    )

    compact = [_event_to_api(e) for e in events[:40]]
    focus = body.focus or "Review my trading patterns, discipline, and notable wins/losses."

    prompt = f"""You are a trading coach reviewing imported brokerage history.

Focus: {focus}

Summary stats: {stats}

Recent trades (up to 40 rows):
{compact}

Write a concise review (3-5 short paragraphs): overall performance, best/worst decisions, patterns, and 2-3 actionable lessons. Reference specific symbols and dates from the data."""

    llm = get_llm()
    review = llm.complete(
        [
            {"role": "system", "content": "You analyze trade history with specific references to the data provided."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=900,
    )

    return {
        "review": review.strip(),
        "stats": stats,
        "trade_count": len(events),
    }
