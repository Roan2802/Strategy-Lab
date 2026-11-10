"""Check 2b: why scale_in specs are refused by the forward model (backtest adds to winners, forward never did).

Before the guard the frozen forward run equalled the backtest *without* scale_in (M1 parity is exact), so the
divergence is measured as backtest(with scale_in) minus backtest(without scale_in) on the same real days.
"""
from check2_parity import *

spec_scaled = rich({"type": "market"})
spec_scaled.update(name="RichScale", trailing=None, break_even=None, time_exit_bars=30,
                   scale_in={"max_adds": 2, "add_every_r": 0.5, "add_size_fraction": 0.5})
spec_plain = {k: v for k, v in spec_scaled.items() if k != "scale_in"}
first, last = D(2026, 9, 21), D(2026, 9, 23)
params = {"low_level": 20}
frozen = datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc)
start = (frozen - timedelta(days=3)).isoformat()
end = (last + timedelta(days=1)).isoformat()
with_scale = backtest_run(spec_scaled, params, start, end, frozen)
without = backtest_run(spec_plain, params, start, end, frozen)
trades = lambda r: [t for t in r["trades"] if t["exit_reason"] != "END_OF_DATA"]
payload = {"days": f"{first}..{last}", "trades_with_scale_in": len(trades(with_scale)), "trades_forward_equivalent": len(trades(without)),
           "net_pnl_with_scale_in": sum(t["pnl"] for t in trades(with_scale)),
           "net_pnl_forward_equivalent": sum(t["pnl"] for t in trades(without)),
           "scale_in_adds": with_scale["diagnostics"].get("scale_in_adds", 0)}
payload["silent_divergence_if_frozen"] = payload["net_pnl_with_scale_in"] - payload["net_pnl_forward_equivalent"]
try:
    build_forward(Path(__import__("tempfile").mkdtemp()) / "s.sqlite", spec_scaled, params, first, last)
    payload["freeze_guard"] = "NOT REFUSED"
except Exception as exc:  # noqa: BLE001
    payload["freeze_guard"] = f"{type(exc).__name__}: {exc}"
print(payload)
print(save("check2b_scale_in_divergence.json", payload))
