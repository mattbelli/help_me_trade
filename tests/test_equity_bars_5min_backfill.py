"""1.14f outcome store: the fetch/store mechanics, hermetic (HTTP fully mocked).

The store is registered before it is fetched, so the properties under test are
the ones the registration leans on: RTH-only bars, complete NA accounting, and a
partial session that can never look complete.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from tools import equity_bars_5min_backfill as bf

SESSION = date(2025, 7, 28)  # EDT: 09:30 ET == 13:30Z
HEADERS = {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}


def _bar(t: str, c: float = 10.0, v: int = 100) -> dict:
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "v": v, "n": 1, "vw": c}


def _query(url: str) -> dict[str, str]:
    from urllib.parse import parse_qs, urlparse

    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class FakeApi:
    """Records each request's parsed query and replays queued responses."""

    def __init__(self) -> None:
        self.queries: list[dict[str, str]] = []
        self.responses: list[dict] = []

    def __call__(self, url: str, headers: dict[str, str]) -> dict:
        self.queries.append(_query(url))
        return self.responses.pop(0) if self.responses else {"bars": {}}

    def __len__(self) -> int:
        return len(self.queries)


@pytest.fixture
def api(monkeypatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(bf, "_get", fake)
    return fake


def test_batching_splits_universe_and_pins_the_registered_params(api, monkeypatch):
    monkeypatch.setattr(bf, "SYMBOL_BATCH", 2)
    bf.fetch_session(SESSION, ["AAA", "BBB", "CCC"], HEADERS)

    assert [q["symbols"] for q in api.queries] == ["AAA,BBB", "CCC"]
    q = api.queries[0]
    assert (q["timeframe"], q["feed"], q["adjustment"]) == ("5Min", "sip", "split")
    # RTH window, tz-aware ET (DST-correct via zoneinfo, not a hardcoded offset).
    assert q["start"] == "2025-07-28T09:30:00-04:00"
    assert q["end"] == "2025-07-28T16:00:00-04:00"


def test_dst_window_uses_est_in_winter(api):
    bf.fetch_session(date(2026, 1, 5), ["AAA"], HEADERS)
    assert api.queries[0]["start"] == "2026-01-05T09:30:00-05:00"


def test_pagination_stitches_pages_for_the_same_symbol(api):
    api.responses.extend(
        [
            {"bars": {"AAA": [_bar("2025-07-28T13:30:00Z", 1.0)]}, "next_page_token": "p2"},
            {"bars": {"AAA": [_bar("2025-07-28T13:35:00Z", 2.0)]}, "next_page_token": None},
        ]
    )
    per_sym, n_req = bf.fetch_session(SESSION, ["AAA"], HEADERS)

    assert n_req == 2
    assert api.queries[1]["page_token"] == "p2"
    closes = np.concatenate([p[4] for p in per_sym["AAA"]])
    assert closes.tolist() == [1.0, 2.0]


def test_rth_filter_drops_pre_and_post_session_bars(api):
    api.responses.append(
        {
            "bars": {
                "AAA": [
                    _bar("2025-07-28T13:25:00Z"),  # 09:25 ET — pre-open
                    _bar("2025-07-28T13:30:00Z"),  # 09:30 ET — first RTH bar
                    _bar("2025-07-28T19:55:00Z"),  # 15:55 ET — last RTH bar
                    _bar("2025-07-28T20:00:00Z"),  # 16:00 ET — covers after-hours
                ]
            }
        }
    )
    per_sym, _ = bf.fetch_session(SESSION, ["AAA"], HEADERS)

    kept = np.concatenate([p[0] for p in per_sym["AAA"]]).astype("datetime64[ns]")
    assert [str(t)[11:16] for t in kept] == ["13:30", "19:55"]


def test_symbol_whose_bars_are_all_outside_rth_is_absent(api):
    api.responses.append({"bars": {"AAA": [_bar("2025-07-28T20:05:00Z")]}})
    per_sym, _ = bf.fetch_session(SESSION, ["AAA"], HEADERS)
    assert per_sym == {}


def test_skip_manifest_classifies_index_and_adjusted_roots():
    requested, skipped = bf.classify_universe(["AAPL", "SPX", "VIXW", "ABC1", "AIV2", "MSFT"])

    assert requested == ["AAPL", "MSFT"]
    assert skipped == {
        "SPX": "index_root",
        "VIXW": "index_root",
        "ABC1": "adjusted_root",
        "AIV2": "adjusted_root",
    }


def test_write_session_layout_and_empty_accounting(tmp_path: Path, api):
    api.responses.append(
        {
            "bars": {
                "BBB": [_bar("2025-07-28T13:30:00Z", 2.0, 20)],
                "AAA": [_bar("2025-07-28T13:35:00Z", 1.0, 10), _bar("2025-07-28T13:40:00Z", 1.5)],
            }
        }
    )
    per_sym, n_req = bf.fetch_session(SESSION, ["AAA", "BBB", "CCC"], HEADERS)
    stats = bf.write_session(SESSION, tmp_path, ["AAA", "BBB", "CCC"], per_sym, n_req, 1.0)

    with np.load(tmp_path / "2025-07-28.npz") as z:
        assert z["symbols"].tolist() == ["AAA", "BBB"]  # index table, sorted
        assert z["sym_idx"].tolist() == [0, 0, 1]  # rows grouped by symbol
        assert z["close"].tolist() == [1.0, 1.5, 2.0]
        assert z["volume"].tolist() == [10, 100, 20]
        assert z["t_ns"].dtype == np.int64

    # CCC was requested and returned nothing: NA-counted, never silently dropped.
    assert stats["empty_symbols"] == ["CCC"]
    assert (stats["symbols_requested"], stats["symbols_with_bars"], stats["bars"]) == (3, 2, 3)


def test_write_session_leaves_no_part_file_and_stats_marks_completion(tmp_path: Path):
    bf.write_session(SESSION, tmp_path, ["AAA"], {}, 1, 0.5)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["2025-07-28.json", "2025-07-28.npz"]


def test_a_crash_mid_write_never_leaves_a_session_looking_complete(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bf.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        bf.write_session(SESSION, tmp_path, ["AAA"], {}, 1, 0.5)

    # The .part exists but neither the final npz nor the completion marker do,
    # so the resume check re-fetches the session.
    assert (tmp_path / "2025-07-28.npz.part").exists()
    assert not (tmp_path / "2025-07-28.npz").exists()
    assert not (tmp_path / "2025-07-28.json").exists()


def test_resume_skips_only_sessions_with_a_stats_json(tmp_path: Path, api):
    (tmp_path / "2025-07-28.json").write_text("{}")
    bf.backfill_session(SESSION, ["AAA"], tmp_path, HEADERS)
    assert len(api) == 0  # stats present -> no HTTP at all

    # npz alone is NOT a completion marker (the "size > 0 skip-guard" trap).
    (tmp_path / "2025-07-29.npz").write_text("junk")
    bf.backfill_session(date(2025, 7, 29), ["AAA"], tmp_path, HEADERS)
    assert len(api) == 1


def _http_error(code: int):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "nope", {}, None)

    return boom


@pytest.mark.parametrize("code", [401, 403])
def test_auth_failure_is_fatal_rather_than_a_silently_empty_store(
    tmp_path: Path, monkeypatch, code: int
):
    """Mocks urlopen, not _get — the 401/403 conversion is what is under test."""
    monkeypatch.setattr(bf.urllib.request, "urlopen", _http_error(code))
    monkeypatch.setattr(bf.time, "sleep", lambda s: None)

    with pytest.raises(bf.AuthError):
        bf.backfill_session(SESSION, ["AAA"], tmp_path, HEADERS)
    assert not list(tmp_path.iterdir())


def test_server_errors_are_retried_then_give_up(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(bf, "MAX_RPM", 10**9)  # take the pacer's sleeps out of the picture
    monkeypatch.setattr(bf.time, "sleep", slept.append)
    monkeypatch.setattr(bf.urllib.request, "urlopen", _http_error(429))

    with pytest.raises(RuntimeError, match="gave up"):
        bf._get("https://example.test/x", HEADERS)
    # RETRIES attempts, exponentially backed off BETWEEN them (no wasted final sleep).
    assert slept == [1.0, 2.0, 4.0, 8.0]


def test_a_client_error_that_retrying_cannot_fix_raises_immediately(monkeypatch):
    monkeypatch.setattr(bf, "MAX_RPM", 10**9)
    monkeypatch.setattr(bf.time, "sleep", lambda s: pytest.fail("retried a 422"))
    monkeypatch.setattr(bf.urllib.request, "urlopen", _http_error(422))

    with pytest.raises(bf.urllib.error.HTTPError):
        bf._get("https://example.test/x", HEADERS)


def test_manifest_rolls_up_skips_and_never_resolved(tmp_path: Path):
    for d, empty in (("2025-07-28", ["CCC", "DDD"]), ("2025-07-29", ["DDD"])):
        (tmp_path / f"{d}.json").write_text(
            json.dumps(
                {
                    "session": d,
                    "symbols_requested": 4,
                    "symbols_with_bars": 2,
                    "bars": 5,
                    "requests": 1,
                    "elapsed_s": 1.0,
                    "bytes": 100,
                    "empty_symbols": empty,
                }
            )
        )
    sessions = [date(2025, 7, 28), date(2025, 7, 29), date(2025, 7, 30)]
    m = bf.build_manifest(
        tmp_path,
        universe=["AAA", "BBB", "CCC", "DDD", "SPX"],
        requested=["AAA", "BBB", "CCC", "DDD"],
        skipped={"SPX": "index_root"},
        sessions=sessions,
    )

    assert m == json.loads((tmp_path / "manifest.json").read_text())
    assert m["skipped"] == {"SPX": "index_root"}
    assert (m["sessions_complete"], m["sessions_expected"]) == (2, 3)
    assert m["span"] == {"start": "2025-07-28", "end": "2025-07-30"}
    # CCC came back on 07-29, so only DDD is never-resolved.
    assert m["never_resolved"] == ["DDD"]
    assert m["never_resolved_basis_sessions"] == 2
    assert m["sessions"]["2025-07-28"]["symbols_empty"] == 2


def test_never_resolved_ignores_a_leftover_smoke_session(tmp_path: Path):
    """A session written against a smaller universe must not shrink the NA count."""
    for d, requested, empty in (
        ("2025-07-28", 4, ["CCC", "DDD"]),
        ("2025-07-29", 1, []),  # leftover `--limit 1` run: knows nothing about CCC/DDD
    ):
        (tmp_path / f"{d}.json").write_text(
            json.dumps({"symbols_requested": requested, "bars": 1, "empty_symbols": empty})
        )
    m = bf.build_manifest(
        tmp_path,
        universe=["AAA", "BBB", "CCC", "DDD"],
        requested=["AAA", "BBB", "CCC", "DDD"],
        skipped={},
        sessions=[date(2025, 7, 28), date(2025, 7, 29)],
    )

    assert m["never_resolved"] == ["CCC", "DDD"]
    assert m["never_resolved_basis_sessions"] == 1
    datetime.fromisoformat(m["generated_at"])


def test_importing_the_module_fires_no_network_and_no_writes(monkeypatch):
    """script-mains-deny-by-default: import must be inert."""
    import importlib
    import urllib.request

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: pytest.fail("import opened a connection")
    )
    importlib.reload(bf)
