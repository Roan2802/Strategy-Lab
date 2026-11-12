"""V102: a market whose account conversion cannot be built is reported FX_RATE_UNAVAILABLE with no money result."""
import test_v10_multimarket as base
from strategy_lab.research import multimarket

env, strategy, Ctx = base.env, base.strategy, base.Ctx


def test_missing_eurusd_blocks_the_de40_cell_instead_of_running_on_a_placeholder_rate(env, strategy):
    dm, led = env
    del dm.frames["EURUSD"]
    out = multimarket.run_cross_market(Ctx(), dm, strategy, ["DE40"], ["H1"], ledger=led, stability=False)
    cell = out["results"][0]
    assert cell["status"] == "FX_RATE_UNAVAILABLE" and cell["money_blocked"] and cell["edge_positive"] is False
    assert "FX_RATE_UNAVAILABLE" in cell["flags"] and cell["currency_status"] == "FX_RATE_UNAVAILABLE"
    assert "metrics" not in cell                      # no fake amounts


def test_available_eurusd_still_converts_causally(env, strategy):
    dm, led = env
    out = multimarket.run_cross_market(Ctx(), dm, strategy, ["DE40"], ["H1"], ledger=led, stability=False)
    cell = out["results"][0]
    assert cell["status"] != "FX_RATE_UNAVAILABLE" and cell["currency_conversion"] == "CONVERTED_CAUSAL"
