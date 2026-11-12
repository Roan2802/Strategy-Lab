"""V102: broker profiles (manual input with source, effective-from history, MT5 export import) and the registry statuses."""
import json

import pytest

from strategy_lab.research import broker_profiles as bp, instruments


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "PROFILE_DIR", tmp_path / "profiles")
    monkeypatch.setattr(instruments, "CACHE", tmp_path / "stats.json")
    monkeypatch.setattr(instruments, "BROKER_OVERRIDES", tmp_path / "overrides.json")
    bp.create_profile("demo", "synthetic test profile")
    return tmp_path


def test_a_value_without_a_source_is_refused_and_unknown_fields_or_values_too(env):
    with pytest.raises(ValueError, match="source"):
        bp.set_field("demo", "DE40", "contract_size", 1, "")
    with pytest.raises(ValueError, match="source"):
        bp.set_field("demo", "DE40", "contract_size", 1, None)
    with pytest.raises(ValueError, match="unknown field"):
        bp.set_field("demo", "DE40", "banana", 1, "x")
    with pytest.raises(ValueError, match="invalid value"):
        bp.set_field("demo", "DE40", "lot_step", -0.01, "x")
    with pytest.raises(ValueError, match="invalid value"):
        bp.set_field("demo", "DE40", "margin_initial_rate", 3, "x")
    with pytest.raises(ValueError, match="status"):
        bp.set_field("demo", "DE40", "lot_step", 0.1, "x", status="VERIFIED_FROM_DATA")
    assert bp.load_profile("demo")["symbols"] == {}


def test_effective_from_history_selects_the_value_in_force_at_the_time(env):
    bp.set_field("demo", "DE40", "contract_size", 1, "spec sheet 2020")
    bp.set_field("demo", "DE40", "contract_size", 10, "notice 2023-01", effective_from="2023-01-01")
    prof = bp.load_profile("demo")
    assert bp.value_at(prof, "DE40", "contract_size", "2022-06-01")["value"] == 1
    assert bp.value_at(prof, "DE40", "contract_size", "2023-01-01T00:00:00Z")["value"] == 10
    assert bp.value_at(prof, "DE40", "contract_size")["value"] == 10
    assert len(bp.history(prof, "DE40", "contract_size")) == 2
    assert bp.value_at(prof, "DE40", "max_lot") is None                      # absent = UNAVAILABLE
    bp.set_field("demo", "DE40", "contract_size", 10, "corrected notice", effective_from="2023-01-01")   # replaces, no dup
    assert len(bp.history(bp.load_profile("demo"), "DE40", "contract_size")) == 2


def test_spec_for_reports_verified_derived_assumed_unavailable_and_never_opens_the_gate_with_assumptions(env):
    bp.set_field("demo", "DE40", "contract_size", 10, "broker spec 2026-01", effective_from="2026-01-01")
    bp.set_field("demo", "DE40", "lot_step", 0.1, "my estimate", status="ASSUMED")
    bp.set_field("demo", "DE40", "leverage", 20, "account statement", status="BROKER_VERIFIED")
    bp.set_field("demo", "DE40", "swap_long", -6.5, "broker spec", status="BROKER_VERIFIED")
    bp.set_field("demo", "DE40", "swap_short", 2.1, "broker spec", status="ASSUMED")
    spec = instruments.spec_for("DE40", None, profile="demo", as_of="2026-06-01")
    f = spec["fields"]
    assert f["contract_size"]["value"] == 10 and f["contract_size"]["status"] == "BROKER_VERIFIED"
    assert f["contract_size"]["verification"] == "VERIFIED" and f["contract_size"]["source"] == "broker spec 2026-01"
    assert f["lot_step"]["status"] == "ASSUMED" and f["lot_step"]["verification"] == "ASSUMED"
    assert f["swap"]["verification"] == "ASSUMED"                            # weakest component wins
    assert f["margin"]["status"] == "BROKER_VERIFIED" and f["margin"]["value"]["leverage"] == 20
    assert f["tick_value"]["verification"] == "DERIVED" and f["tick_value"]["status"] == "ASSUMED"   # tick x size, tick not measured
    assert f["max_lot"]["verification"] == "UNAVAILABLE" and f["commission_per_lot_side"]["verification"] == "UNAVAILABLE"
    assert f["pip_value"]["verification"] in ("UNAVAILABLE", "DERIVED")
    assert "lot_step" in spec["money_gate"]["blocking_fields"] and not spec["money_gate"]["money_approval_allowed"]
    assert spec["broker_profile"] == "demo"
    assert "contract_size" in f["contract_history"]["value"]
    before = instruments.spec_for("DE40", None, profile="demo", as_of="2025-06-01")["fields"]["contract_size"]
    assert before["status"] == "ASSUMED" and before["value"] == 1.0          # not yet in force: back to the lab default


def test_every_registry_field_has_a_verification_class(env):
    fields = instruments.spec_for("XAUUSD", None)["fields"]
    assert {v["verification"] for v in fields.values()} <= {"VERIFIED", "DERIVED", "ASSUMED", "UNAVAILABLE"}
    assert fields["swap"]["verification"] == "UNAVAILABLE" and fields["contract_size"]["verification"] == "ASSUMED"
    assert fields["tick_value"]["verification"] == "DERIVED"


MT5_CSV = ("Symbol;Digits;ContractSize;TickSize;TickValue;VolumeMin;VolumeMax;VolumeStep;CurrencyBase;CurrencyProfit;"
           "CurrencyMargin;SwapMode;SwapLong;SwapShort;Swap3Day;MarginInitial;Spread;Leverage\n"
           "GER40.cash;1;1;0.1;0.11;0.1;50;0.1;EUR;EUR;EUR;5;-4.2;1.1;5;0;12;20\n"
           "XAUUSD;2;100;0.01;1;0.01;100;0.01;XAU;USD;USD;SYMBOL_SWAP_MODE_POINTS;-35;12;3;0;25;0\n")


def test_mt5_export_import_maps_columns_sources_and_skips_what_it_cannot_parse(env):
    f = env / "specs.csv"
    f.write_text(MT5_CSV.replace("12;20\n", "12;oops\n", 1), encoding="utf-8")
    out = bp.import_mt5("demo", f, symbol_map={"GER40.cash": "DE40"}, as_of="2026-03-01")
    prof = bp.load_profile("demo")
    assert out["source"].startswith("mt5_export:specs.csv:sha256=") and prof["aliases"]["GER40.CASH"] == "DE40"
    cs = bp.value_at(prof, "DE40", "contract_size")
    assert cs["value"] == 1 and cs["status"] == "BROKER_VERIFIED" and cs["source"] == out["source"]
    assert cs["effective_from"].startswith("2026-03-01")
    assert bp.value_at(prof, "DE40", "min_lot")["value"] == 0.1 and bp.value_at(prof, "DE40", "max_lot")["value"] == 50
    assert bp.value_at(prof, "DE40", "swap_triple_day")["value"] == 4          # MT5 5 = Friday -> python weekday 4
    assert bp.value_at(prof, "XAUUSD", "swap_triple_day")["value"] == 2        # MT5 3 = Wednesday
    assert bp.value_at(prof, "XAUUSD", "swap_mode")["value"] == "1"
    assert bp.value_at(prof, "DE40", "margin_per_lot") is None                 # 0 = "computed by the broker": no fact
    assert any(s["column"] == "leverage" for s in out["skipped"])              # unparseable cell reported, never guessed
    assert bp.value_at(prof, "XAUUSD", "leverage") is None                      # 0 leverage is no fact either
    spec = instruments.spec_for("DE40", None, profile="demo")["fields"]
    assert spec["swap"]["verification"] == "VERIFIED" and spec["contract_size"]["verification"] == "VERIFIED"


def test_mt5_json_export_and_swap_conversion_to_account_currency_per_lot(env):
    f = env / "specs.json"
    f.write_text(json.dumps({"DE40": {"ContractSize": 1, "TickSize": 0.1, "TickValue": 0.11, "Digits": 1, "SwapMode": 5,
                                      "SwapLong": -4.2, "SwapShort": 1.1, "Swap3Day": 5},
                             "XAUUSD": {"TickSize": 0.01, "TickValue": 1.0, "Digits": 2, "SwapMode": 1,
                                        "SwapLong": -35, "SwapShort": 12, "Swap3Day": 3}}), encoding="utf-8")
    bp.import_mt5("demo", f)
    prof = bp.load_profile("demo")
    cfg, why = bp.swap_execution_config(prof, "DE40")
    assert cfg == {"long_per_lot_per_night": -4.2, "short_per_lot_per_night": 1.1, "status": "VERIFIED", "triple_weekday": 4}
    gold, _ = bp.swap_execution_config(prof, "XAUUSD")                          # points: -35 x 0.01 / 0.01 x 1.0 = -35 per lot
    assert gold["long_per_lot_per_night"] == pytest.approx(-35.0) and gold["triple_weekday"] == 2
    bp.set_field("demo", "EURUSD", "swap_long", -5, "guess", status="ASSUMED")
    bp.set_field("demo", "EURUSD", "swap_short", 1, "guess", status="ASSUMED")
    bp.set_field("demo", "EURUSD", "swap_mode", "5", "guess", status="ASSUMED")
    none, reason = bp.swap_execution_config(bp.load_profile("demo"), "EURUSD")
    assert none is None and "not VERIFIED" in reason                            # unverified is never presented as fact
    nothing, reason = bp.swap_execution_config(prof, "GBPUSD")
    assert nothing is None and "UNAVAILABLE" in reason


def test_margin_config_is_contractual_only_with_a_verified_source(env):
    bp.set_field("demo", "DE40", "leverage", 20, "statement", status="BROKER_VERIFIED")
    bp.set_field("demo", "XAUUSD", "leverage", 10, "guess", status="ASSUMED")
    ok, _ = bp.margin_execution_config(bp.load_profile("demo"), "DE40", stop_out_level_pct=50)
    assert ok == {"leverage": 20, "stop_out_level_pct": 50, "status": "CONTRACTUAL"}
    soft, _ = bp.margin_execution_config(bp.load_profile("demo"), "XAUUSD")
    assert soft["status"] == "SIMULATION_ASSUMPTION"
    none, reason = bp.margin_execution_config(bp.load_profile("demo"), "EURUSD")
    assert none is None and "UNAVAILABLE" in reason


def test_contract_for_uses_a_real_rate_at_ts_and_raises_instead_of_guessing(env):
    from strategy_lab.research.fx import FxRateUnavailable
    with pytest.raises(FxRateUnavailable):
        instruments.contract_for("DE40", None, ts="2024-03-13T10:00:00Z")
    legacy = instruments.contract_for("XAUUSD", None, ts="2024-03-13T10:00:00Z")   # USD quote: no conversion needed
    assert legacy.quote_to_account == 1.0
