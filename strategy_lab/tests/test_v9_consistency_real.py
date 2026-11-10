"""V9 consistency on small slices of the real Dukascopy data (read-only; skipped when the data is absent).

Each test is a miniature of an evidence script in evidence/v9/consistency/ (full-size runs and JSON there).
"""
from datetime import date, timedelta
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd
import pytest

EVIDENCE = Path(__file__).resolve().parents[2] / "evidence" / "v9" / "consistency"
sys.path.insert(0, str(EVIDENCE))

from strategy_lab.data_manager import DEFAULT_SOURCE  # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "data_cache"
pytestmark = pytest.mark.skipif(
    not (DEFAULT_SOURCE.is_dir() and (CACHE / "XAUUSD_M1_bidask.parquet").exists()
         and (CACHE / "XAUUSD_M1_bidask_manifest.json").exists()),
    reason="real Dukascopy XAUUSD data and its parquet cache are required")

import check1_lookahead as c1  # noqa: E402
import check2_parity as c2  # noqa: E402
import check3_execution as c3  # noqa: E402
import check4_idempotence as c4  # noqa: E402
import check5_jesse_forward as c5  # noqa: E402
import parity_lib as pl  # noqa: E402


@pytest.fixture(scope="module")
def dm():
    return pl.data_manager()


def test_real_xauusd_prefix_equals_full_evaluation_on_m5_and_h1(dm):
    import random
    rng = random.Random(3)
    c1.SAMPLE_SIGNALS, c1.SAMPLE_RANDOM = 6, 10
    tested = 0
    for timeframe in ("M5", "H1"):
        frame, _ = dm.candles("XAUUSD", timeframe, None, "2026-10-12")
        arrays = c1.arrays(frame.iloc[-900:])
        for name, strategy in c1.spec_set(timeframe).items():
            if name in ("opening_range_breakout",) or pl.engine.validate_spec(strategy):
                continue
            outcome = c1.prefix_test(strategy, arrays, rng)
            assert outcome["pass"], (timeframe, name, outcome["mismatches"][:2])
            tested += outcome["bars_tested"]
    assert tested > 300


def test_real_aggregation_closed_bars_are_final_and_forward_aggregator_agrees(dm):
    import random
    from strategy_lab import forward_rules
    rng = random.Random(9)
    m1, _ = dm.candles("XAUUSD", "M1", "2026-09-28", "2026-10-09")
    for timeframe, freq, minutes in (("M15", "15min", 15), ("H1", "1h", 60), ("H4", "4h", 240), ("D1", "1D", 1440)):
        full, _ = dm.candles("XAUUSD", timeframe, "2026-09-28", "2026-10-09")
        for _ in range(4):
            cut = m1.index[rng.randrange(len(m1) // 3, len(m1))]
            part = dm._aggregate(m1[m1.index < cut], freq)
            closed = part[part.index + pd.Timedelta(minutes=minutes) <= cut]
            cols = ["bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"]
            assert np.allclose(closed[cols].to_numpy(), full.loc[closed.index, cols].to_numpy())
            rows = forward_rules.aggregate_bid(m1[m1.index < cut], timeframe, until=cut.to_pydatetime())
            assert len(rows) == len(closed)
            assert np.allclose([r[1:5] for r in rows], closed[cols[:4]].to_numpy())


def test_real_m1_forward_trades_equal_the_backtest_exactly(tmp_path):
    spec, params, *_ = c2.CASES["rich_m1_market"]
    result = pl.run_case("rich_m1_market", spec, params, date(2026, 9, 22), date(2026, 9, 22), workdir=tmp_path)
    comparison = result["forward_vs_backtest_aligned"]
    assert comparison["trades_backtest"] >= 30
    assert comparison["trades_forward"] == comparison["trades_backtest"] == comparison["exact_trades"]
    assert abs(comparison["net_pnl_difference"]) < 1e-9


def test_real_m15_forward_differs_from_the_backtest_only_in_exit_timestamps(tmp_path):
    spec, params, *_ = c2.CASES["ema_rsi_m15"]
    result = pl.run_case("ema_rsi_m15", spec, params, date(2026, 9, 14), date(2026, 9, 18), workdir=tmp_path)
    comparison = result["forward_vs_backtest_aligned"]
    assert comparison["trades_backtest"] >= 5 and comparison["trades_forward"] == comparison["matched"] == comparison["trades_backtest"]
    assert set(comparison["causes"]) <= {"EXACT", "TIMESTAMP_GRANULARITY_ONLY"}
    assert abs(comparison["net_pnl_difference"]) < 1e-6
    longer = result["backtest_longer_history_vs_aligned"]
    assert longer["exact_trades"] == longer["trades_backtest"] and not longer["missing_in_forward"]


def test_real_fills_use_the_executable_quote_side_with_slippage_and_commission():
    for name in ("rich_m1_market", "rich_m1_stop"):
        out = c3.verify_case(name, days=1)
        n = out["trades"]
        assert n >= 20
        for key in ("entry_side_ok", "entry_price_rule_ok", "exit_side_rule_ok", "exit_price_rule_ok",
                    "spread_open_ok", "pnl_formula_ok", "lot_step_ok", "sizing_ok"):
            assert out[key] == n, (name, key)
        assert out["sl_wins_when_both"] == out["ambiguous_exits"]
        if name == "rich_m1_stop":
            p = out["pending"]
            assert p["cancelled"] == p["cancel_exact_expiry_candle"] == p["cancelled_without_touch"] > 0
            assert p["filled"] == p["filled_on_first_touch"] == n


def test_real_restart_and_repeated_days_leave_the_ledger_unchanged(tmp_path):
    spec, params, *_ = c2.CASES["rich_m1_stop"]
    first, last = date(2026, 9, 22), date(2026, 9, 23)
    base = tmp_path / "base.sqlite"
    _, _, portfolio, _, _ = pl.build_forward(base, spec, params, first, last)
    continuous = tmp_path / "continuous.sqlite"
    shutil.copy(base, continuous)
    engine = c4.fresh(continuous)()
    pl.advance_daily(engine, portfolio, first, last)
    reference = c4.signature(engine, portfolio)
    assert len(c4.trade_keys(reference)) >= 20
    restarted = tmp_path / "restarted.sqlite"
    shutil.copy(base, restarted)
    pl.advance_daily(None, portfolio, first, last, fresh_engine=c4.fresh(restarted))
    assert c4.fresh(restarted)().advance(portfolio, as_of=pl.observed_after(last))["committed_days"] == []
    assert c4.signature(c4.fresh(restarted)(), portfolio) == reference


def test_real_forward_labbreakout_follows_the_stored_jesse_tester_run(tmp_path):
    run = next((p for p in c5.RUNS.iterdir() if p.name.startswith("05cecfcb")), None)
    if run is None:
        pytest.skip("stored Jesse run 05cecfcb is not available")
    import json
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest["strategy_sha256"] != c5.LAB_SHA256:
        pytest.skip("stored run used a different LabBreakout version")
    forward, _, _ = c5.run_forward(manifest, tmp_path)
    result = c5.compare(c5.jesse_trades(run), forward)
    assert result["jesse_trades"] == 13 and result["matched_entries"] >= 12
    assert result["entry_same_candle"] == result["matched_entries"] and result["extra_forward"] == 0
