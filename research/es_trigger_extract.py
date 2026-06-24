"""Extract $ES trading triggers from tweet history via deterministic regex rules."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from research.bars import RESEARCH_DATA_DIR

DEFAULT_TWEETS_PATH = RESEARCH_DATA_DIR / "adaamset" / "tweets.json"
DEFAULT_OUTPUT_PATH = RESEARCH_DATA_DIR / "adaamset" / "es_triggers_full.json"

ES_QUICK_RE = re.compile(r"[\$]?ES[\s:]+\d{4,5}", re.IGNORECASE)
ES_LEVEL_RE = re.compile(
    r"\$?ES\s*:?\s*(\d{4,5}(?:\.\d+)?)\s*(?:-\s*(\d{4,5}(?:\.\d+)?))?",
    re.IGNORECASE,
)


def parse_ts(ts_str: str) -> datetime:
    ts_str = ts_str.replace(".000Z", "+00:00").replace("Z", "+00:00")
    return datetime.fromisoformat(ts_str)


def is_premarket(dt: datetime) -> bool:
    """Before 13:30 UTC = pre-RTH open."""
    return dt.hour < 13 or (dt.hour == 13 and dt.minute < 30)


def sanity_range(ts: datetime) -> tuple[int, int]:
    y, m = ts.year, ts.month
    if y == 2025 and m <= 5:
        return 4800, 6200
    if y == 2025 and m <= 7:
        return 5400, 6700
    if y == 2025 and m <= 9:
        return 5800, 6800
    if y == 2025 and m <= 11:
        return 6200, 7200
    if y == 2025 or (y == 2026 and m <= 1):
        return 6400, 7300
    if y == 2026 and m <= 3:
        return 6300, 7200
    if y == 2026 and m <= 5:
        return 6700, 7700
    return 7000, 8000


def sanity_check(level: float, ts: datetime) -> bool:
    lo, hi = sanity_range(ts)
    return lo <= level <= hi


def has_es_level(text: str) -> bool:
    return bool(ES_QUICK_RE.search(text))


def extract_levels(text: str, ts: datetime) -> list[tuple[float, float | None]]:
    """Return list of (lo, hi_or_None) for all plausible ES levels in text."""
    found: list[tuple[float, float | None]] = []
    seen: set[float] = set()
    for match in ES_LEVEL_RE.finditer(text):
        lo = float(match.group(1))
        hi = float(match.group(2)) if match.group(2) else None
        if sanity_check(lo, ts) and lo not in seen:
            seen.add(lo)
            found.append((lo, hi))
    return found


def classify_direction(text: str) -> str:
    tl = text.lower()
    long_score = 0
    short_score = 0
    pivot_score = 0

    if re.search(r"\b(long from|longs from|long \$?es from|swing long)\b", tl):
        long_score += 4
    if re.search(r"\blong \$?es\s*[: ]\s*\d", tl):
        long_score += 3
    if re.search(r"\b(i'm biting|yeah i'm biting|i'm buying|levered long)\b", tl):
        long_score += 4
    if re.search(
        r"\b(two fists full long|bot when|accumulation|going long|long target|"
        r"i have \$?es.*bullets?|two bullets?\b.*(?<!short))\b",
        tl,
    ):
        long_score += 3
    if re.search(r"\b(long|longs?|bot|buying|buy|support|bid|bull|bullish|add|adding)\b", tl):
        long_score += 2
    if re.search(
        r"\b(d1 above|playing for.*above|targeting.*above|expecting.*above|"
        r"expecting.*rally|expecting.*higher|clear skies)\b",
        tl,
    ):
        long_score += 2
    if re.search(r"\b(may the.*lows? be in|asking.*to hold|recover nhod)\b", tl):
        long_score += 3
    if re.search(r"\b(accumulation.*trades \d{4,5})\b", tl):
        long_score += 2
    if re.search(r"\b(reloading)\b", tl) and not re.search(r"\bshort\b", tl):
        long_score += 2

    if re.search(r"\b(short from|shorts from|short \$?es from)\b", tl):
        short_score += 4
    if re.search(
        r"\b(limit to sell|selling rips|bearish close|all rallies to be sold|persistent bear)\b",
        tl,
    ):
        short_score += 4
    if re.search(r"\b(filled short|two bullets short|bullets short|adding.*short)\b", tl):
        short_score += 4
    if re.search(r"\b(filling.*puts|0-3de puts|0de puts)\b", tl):
        short_score += 3
    if re.search(
        r"\b(playing for.*downside|chasing.*downside|expecting.*flush|"
        r"unwind flush|selling below)\b",
        tl,
    ):
        short_score += 3
    if re.search(r"\b(warning sent|rough night)\b", tl) and re.search(r"\b\$?es\b", tl):
        short_score += 3
    if re.search(r"\b(short|shorts?|sold|sell|selling|rejection|sellers?|offer|bear|bearish)\b", tl):
        short_score += 2
    if re.search(r"\b(high of day|hod)\b", tl) and re.search(r"\b(short|sell)\b", tl):
        short_score += 2
    if re.search(r"\b(reloading)\b", tl) and re.search(r"\bshort\b", tl):
        short_score += 4
    if re.search(r"\bshorting (this|the)\b", tl):
        short_score += 4
    if re.search(r"\b(i've been shorting|been shorting|already short|am short)\b", tl):
        short_score += 4

    if re.search(r"\b(setstack pivot|set pivot|auction pivot)\b", tl):
        pivot_score += 5
    if re.search(r"\b(pivot|pivotal)\b", tl):
        pivot_score += 2

    mx = max(long_score, short_score, pivot_score)
    if mx == 0:
        return "pivot"
    if long_score == mx and long_score > short_score:
        return "long"
    if short_score == mx and short_score > long_score:
        return "short"
    if pivot_score == mx and pivot_score > long_score and pivot_score > short_score:
        return "pivot"
    if long_score > short_score:
        return "long"
    if short_score > long_score:
        return "short"
    return "pivot"


def classify_forward(text: str, dt: datetime) -> bool:
    tl = text.lower()
    forward_score = 0
    hindsight_score = 0

    if re.search(r"\b(long from|longs from|long \$?es from|swing long\s*[:\s])", tl):
        hindsight_score += 5
    if re.search(r"\b(short from|shorts from|short \$?es from)\b", tl):
        hindsight_score += 5
    if re.search(r"\b(bot|filled)\b", tl) and not re.search(r"\bfilling\b", tl):
        hindsight_score += 3
    if re.search(r"\+\d+p\b", tl):
        hindsight_score += 3
    if re.search(r":\s*now\s*[:\s]\s*\d{4,5}", tl):
        hindsight_score += 2
    if re.search(r"\bnow\s+trading\b", tl):
        hindsight_score += 2
    if re.search(r"\b(told you so|as stated|it traded|in hindsight)\b", tl):
        hindsight_score += 3
    if re.search(
        r"\b(trimmed?|trim|closed|closing|flattened|flat\b|locked|first trim|"
        r"second trim|position update|untrimmed)\b",
        tl,
    ):
        hindsight_score += 2
    if re.search(r"\bwas \$?es\b", tl):
        hindsight_score += 3
    if re.search(r"\b10/[0-9]{2}\s*:.*long from\b", tl):
        hindsight_score += 3
    if re.search(r"\b(intraday adds|open.*edit|position\(s\)|positions update)\b", tl) and not re.search(
        r"\b(adding|looking|planning)\b", tl
    ):
        hindsight_score += 2
    if re.search(r"\blong \$?es\s*[: ]\s*\d{4,5}\s*\(\s*runners?\s*\)", tl):
        hindsight_score += 5
    if re.search(r"\bposition update\b", tl) and re.search(r"\blong \$?es\b", tl):
        hindsight_score += 3
    if re.search(r"\btp\s*(i|1|ii|2)\s+of\s+(2|3)", tl):
        hindsight_score += 3
    if re.search(r"\b(fisted|fishing)\b", tl) and re.search(r"\bfill\b", tl):
        hindsight_score += 3
    if re.search(r"\bfilled short\b", tl):
        hindsight_score += 4
    if re.search(r"\bsold \$?es\s*[: ]\s*\d{4,5}", tl) and re.search(r"\breversed to long\b", tl):
        hindsight_score += 4
    if re.search(r"\bopen.*edit\b", tl) and re.search(r"\blong \$?es\b", tl):
        hindsight_score += 3

    if re.search(r"\b(setstack pivot|set pivot)\b", tl):
        forward_score += 6
    if re.search(r"\b(limit to sell|am biting|i'm biting|yeah i'm biting)\b", tl):
        forward_score += 5
    if re.search(r"\b(expecting|if we (tag|get|open|close|trade below|trade above))\b", tl):
        forward_score += 3
    if re.search(r"\b(playing for.*downside|chasing.*downside|chasing further)\b", tl):
        forward_score += 3
    if re.search(r"\b(i need \$?es|i need.*hold)\b", tl):
        forward_score += 3
    if re.search(r"\b(d1 above|playing for a d1|anything.*playing for)\b", tl):
        forward_score += 3
    if re.search(r"\b(overnight low|overnight high|cash session)\b", tl) and re.search(
        r"\b(playing for|chasing)\b", tl
    ):
        forward_score += 3
    if re.search(r"\bnow reloading\b", tl):
        forward_score += 4
    if re.search(r"\b(i'm going to be long|i'm going to long|going to be long|going to long)\b", tl):
        forward_score += 4
    if re.search(r"\b(two bullets? short|bullets? for|bullets? short|shooting.*bullets?)\b", tl):
        forward_score += 3
    if re.search(r"\b(i have \$?es.*bullets?|two bullets?)\b", tl):
        forward_score += 3
    if re.search(
        r"\b(trailing stop.*will be|stop is at|am wrong.*above|i am wrong on downside above)\b",
        tl,
    ):
        forward_score += 3
    if re.search(r"\b(am pairing with puts|filling.*puts)\b", tl):
        forward_score += 3
    if re.search(r"\b(expecting.*clear skies|asking for.*to hold|may the.*lows? be in)\b", tl):
        forward_score += 3
    if re.search(r"\blong \$?es\s*[: ]\s*\d{4,5}", tl) and not re.search(
        r"\b(long from|swing long)\b", tl
    ):
        forward_score += 3

    if is_premarket(dt):
        forward_score += 1

    if forward_score > hindsight_score:
        return True
    if hindsight_score > forward_score:
        return False
    return is_premarket(dt)


def compute_confidence(text: str) -> str:
    tl = text.lower()
    if re.search(
        r"\b(long from|short from|filled|bot|limit to sell|i'm biting|now reloading|"
        r"i'm going to be long|setstack pivot)\b",
        tl,
    ):
        return "high"
    if re.search(r"\blong \$?es\s*[: ]\s*\d", tl) and not re.search(r"\b(long from|swing long)\b", tl):
        return "high"
    if re.search(r"\b(two bullets? short|bullets? for)\b", tl):
        return "high"
    return "med"


def _has_present_tense_entry_verb(tl: str) -> bool:
    if re.search(r"\btrimming\s+into\b", tl):
        return True
    return bool(
        re.search(
            r"\b(long|bot|buying|bidding|sold|selling|short|offered|reloading|adding)\b",
            tl,
        )
    )


def classify_entry_type(text: str, direction: str, dt: datetime) -> str:
    if direction == "pivot":
        return "pivot_marker"

    tl = text.lower()

    # recap — past entry disclosed / managed
    if re.search(r"\b(long from|short from|from\s+\$?es|from\s+es)\b", tl):
        return "recap"
    if re.search(r"\+\s*\d+\s*p", tl):
        return "recap"
    if re.search(r"now\s*:", tl) or re.search(r"now\s+\d", tl):
        return "recap"
    if re.search(r"\(\+", tl):
        return "recap"
    if not _has_present_tense_entry_verb(tl) and re.search(
        r"\b(trimmed?|flat|closed|locked|stopped|took profit|tp hit)\b",
        tl,
    ):
        return "recap"

    # anticipatory — level to act on later / conditional
    if re.search(r"\blimit\b", tl):
        return "anticipatory"
    if re.search(r"\bif we tag\b", tl):
        return "anticipatory"
    if re.search(r"\bif we\b", tl):
        return "anticipatory"
    if re.search(r"\bexpecting\b", tl):
        return "anticipatory"
    if re.search(r"\blooking for\b", tl):
        return "anticipatory"
    if re.search(r"\bwatching\b", tl):
        return "anticipatory"
    if re.search(r"\bon a tag\b", tl):
        return "anticipatory"
    if re.search(r"\b(above|below)\s+\d", tl):
        return "anticipatory"
    if is_premarket(dt) and not _has_present_tense_entry_verb(tl):
        return "anticipatory"

    return "realtime_entry"


def is_shorthand_mention(text: str) -> bool:
    tl = text.lower()
    if re.search(r"\$es\b", tl) and not ES_QUICK_RE.search(text):
        if re.search(r"\b\d{2,3}0s?\b", tl) or re.search(r"\b\d\.\d?k\b", tl):
            return True
    return False


def extract_es_triggers(tweets: list[dict]) -> list[dict]:
    """Pure extraction: tweet dicts -> trigger records sorted by ``post_ts``."""
    results: list[dict] = []
    seen_post_levels: set[tuple[str, float]] = set()

    for tweet in tweets:
        text = tweet.get("text", "")
        if not text:
            continue
        if text.startswith("RT @"):
            continue
        if not has_es_level(text):
            continue

        post_id = str(tweet["id"])
        dt = parse_ts(str(tweet["created_at"]))
        post_ts_iso = dt.isoformat()
        levels = extract_levels(text, dt)
        if not levels:
            continue

        direction = classify_direction(text)
        forward = classify_forward(text, dt)
        confidence = compute_confidence(text)
        entry_type = classify_entry_type(text, direction, dt)
        raw_text = text[:240]

        for lo, hi in levels:
            if hi is not None:
                level = round((lo + hi) / 2, 1)
                conf = "med"
            else:
                level = lo
                conf = confidence

            dedup_key = (post_id, level)
            if dedup_key in seen_post_levels:
                continue
            seen_post_levels.add(dedup_key)

            target = None
            if not forward:
                match = re.search(
                    r"\$?es\s*:?\s*(\d{4,5}(?:\.\d+)?)\s*:?\s*now\s*[:\s+]*(\d{4,5}(?:\.\d+)?)",
                    text,
                    re.IGNORECASE,
                )
                if match:
                    entry_val = float(match.group(1))
                    now_val = float(match.group(2))
                    if sanity_check(now_val, dt) and entry_val == level and now_val != level:
                        target = now_val
                if target is None:
                    match2 = re.search(
                        r"now\s+trading\s+\$?es\s*:?\s*(\d{4,5})",
                        text,
                        re.IGNORECASE,
                    )
                    if match2:
                        now_val2 = float(match2.group(1))
                        if sanity_check(now_val2, dt) and now_val2 != level:
                            target = now_val2

            results.append(
                {
                    "post_id": post_id,
                    "post_ts": post_ts_iso,
                    "level": level,
                    "target": target,
                    "direction": direction,
                    "forward": forward,
                    "entry_type": entry_type,
                    "confidence": conf,
                    "raw_text": raw_text,
                }
            )

    results.sort(key=lambda row: row["post_ts"])
    return results


def write_triggers(triggers: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(triggers, indent=2, ensure_ascii=False), encoding="utf-8")


def _print_summary(tweets: list[dict], triggers: list[dict], *, dropped_shorthand: int) -> None:
    posts_with_es = sum(1 for tweet in tweets if has_es_level(tweet.get("text", "")))

    def dir_counts(recs: list[dict]) -> tuple[int, int, int]:
        return (
            sum(1 for row in recs if row["direction"] == "long"),
            sum(1 for row in recs if row["direction"] == "short"),
            sum(1 for row in recs if row["direction"] == "pivot"),
        )

    forward_rows = [row for row in triggers if row["forward"]]
    hindsight_rows = [row for row in triggers if not row["forward"]]
    all_long, all_short, all_pivot = dir_counts(triggers)
    fwd_long, fwd_short, fwd_pivot = dir_counts(forward_rows)
    h_long, h_short, h_pivot = dir_counts(hindsight_rows)
    testable = [row for row in forward_rows if row["direction"] in ("long", "short")]

    print("\n=== EXTRACTION COMPLETE ===", file=sys.stderr)
    print(f"Total posts scanned:            {len(tweets)}", file=sys.stderr)
    print(f"Posts with parseable $ES level: {posts_with_es}", file=sys.stderr)
    print(f"Records emitted:                {len(triggers)}", file=sys.stderr)
    print(f"Dropped/shorthand:              {dropped_shorthand}", file=sys.stderr)
    print("\n── Direction breakdown ──", file=sys.stderr)
    print(
        f"  All ({len(triggers)}):       long={all_long}, short={all_short}, pivot={all_pivot}",
        file=sys.stderr,
    )
    print(
        f"  Forward ({len(forward_rows)}):  long={fwd_long}, short={fwd_short}, pivot={fwd_pivot}",
        file=sys.stderr,
    )
    print(
        f"  Hindsight ({len(hindsight_rows)}): long={h_long}, short={h_short}, pivot={h_pivot}",
        file=sys.stderr,
    )
    print("\n── Testable (forward + directional) ──", file=sys.stderr)
    print(f"  Count: {len(testable)}", file=sys.stderr)
    month_counts = Counter(row["post_ts"][:7] for row in testable)
    print("\n  By month:", file=sys.stderr)
    for month in sorted(month_counts):
        print(f"    {month}: {month_counts[month]}", file=sys.stderr)
    levels_set = sorted({row["level"] for row in testable})
    print(f"\n  Distinct levels ({len(levels_set)}): {levels_set}", file=sys.stderr)
    dir_testable = Counter(row["direction"] for row in testable)
    print(
        f"\n  Direction split: long={dir_testable['long']}, short={dir_testable['short']}",
        file=sys.stderr,
    )
    print("\n── Sample forward records ──", file=sys.stderr)
    for row in forward_rows:
        print(
            f"  {row['post_ts'][:10]} | {row['direction']:7} | {row['level']:7} | "
            f"fwd={row['forward']} | {row['raw_text'][:80]}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract $ES triggers from tweet JSON.")
    parser.add_argument("--tweets", type=Path, default=DEFAULT_TWEETS_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    tweets = json.loads(args.tweets.read_text(encoding="utf-8"))
    print(f"Total posts loaded: {len(tweets)}", file=sys.stderr)
    dropped_shorthand = sum(1 for tweet in tweets if is_shorthand_mention(tweet.get("text", "")))
    triggers = extract_es_triggers(tweets)
    write_triggers(triggers, args.out)
    _print_summary(tweets, triggers, dropped_shorthand=dropped_shorthand)
    print(f"\nOutput: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
