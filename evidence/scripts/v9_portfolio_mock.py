"""Render the filled Portfolio/Forward views against MOCKED API responses (layout check only; numbers are fake and never stored)."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
PID = "11111111-1111-1111-1111-111111111111"
days = [f"2026-10-{d:02d}" for d in range(1, 21)]
eq, peak, curve = 10000.0, 10000.0, []
for i, d in enumerate(days):
    eq += (40 if i % 3 else -55) + i
    peak = max(peak, eq)
    curve.append({"day": d, "equity": eq, "drawdown_percent": (peak - eq) / peak * 100})
metrics = lambda n, p, dd: {"trade_count": n, "net_profit": p, "winrate_percent": 45.0, "expectancy": p / max(n, 1), "profit_factor": 1.2,
                            "recovery_factor": 1.1, "sharpe_daily": 0.1, "sortino_daily": 0.2, "max_drawdown_percent": dd,
                            "max_underwater_days": 4, "average_reward_to_risk": 1.0, "average_mae": 5, "average_mfe": 9,
                            "excursion_covered": n, "excursion_unverified": 0, "sample_days": 20, "risk_rejections": 0}
members = [
    {"registration_id": "r1", "joined_at": "2026-10-01T00:00:00", "frozen_at": "2026-09-30T12:00:00", "allocation": 5000,
     "snapshot_sha256": "a" * 64, "configuration": {"strategy": "LabBreakout", "symbol": "XAUUSD", "timeframe": "M15"},
     "state": {"position": None, "last_candle_at": "2026-10-20T23:59:00"}},
    {"registration_id": "r2", "joined_at": "2026-10-03T00:00:00", "frozen_at": "2026-10-02T12:00:00", "allocation": 5000,
     "snapshot_sha256": "b" * 64, "configuration": {"strategy": "StatefulRsiPending", "symbol": "EURUSD", "timeframe": "M5"},
     "state": {"position": {"side": "long"}, "last_candle_at": "2026-10-20T23:59:00"}},
]
perf_members = {"r1": {**metrics(12, 210.0, 1.4), "status": "FORWARD_TESTING", "last_signal": None, "last_trade": None},
                "r2": {**metrics(31, -90.0, 2.9), "status": "FORWARD_TESTING", "risk_rejections": 2, "last_signal": None, "last_trade": None}}
detail = {"portfolio": {"id": PID, "name": "Mock portfolio", "currency": "EUR", "balance": eq, "starting_capital": 10000.0, "risk_limit": 0.02, "last_day": days[-1]},
          "days": [], "members": members,
          "events": [{"id": 1, "kind": "ORDER_SUBMITTED", "registration_id": "r1", "candle_at": "2026-10-05T10:00:00", "detail": {"quote_observed_at": "2026-10-05T10:01:00", "net_pnl": None}},
                     {"id": 2, "kind": "POSITION_CLOSED", "registration_id": "r1", "candle_at": "2026-10-06T10:00:00", "detail": {"quote_observed_at": "2026-10-06T10:01:00", "net_pnl": 12.5}}],
          "historical_references": [],
          "performance": {"members": perf_members, "model_current": True, "trade_excursions": {},
                          "portfolio": {**metrics(43, 120.0, 2.1), "equity": eq, "curve": curve, "unallocated_capital": 0, "portfolio_stop_budget": 200.0,
                                        "open_pnl": 3, "gross_open_exposure": 1800.0, "open_margin": 90, "open_stop_risk": 40.0}}}
listing = {"portfolios": [{**detail["portfolio"], "members": 2}]}

def handle(route):
    url = route.request.url
    if route.request.method == "GET" and url.rstrip("/").endswith("/api/forward/portfolios"):
        return route.fulfill(json=listing)
    if route.request.method == "GET" and PID in url:
        return route.fulfill(json=detail)
    route.continue_()

errors = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    for w, h in [tuple(map(int, s.split("x"))) for s in sys.argv[2].split(",")]:
        pg = b.new_page(viewport={"width": w, "height": h})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.route("**/api/forward/portfolios**", handle)
        pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardSeen','1')")
        pg.goto("http://127.0.0.1:8767/#dashboard"); pg.wait_for_timeout(1500)
        for theme in ("dark", "light-green"):
            pg.evaluate(f"setTheme('{theme}')")
            for v in ("risico", "forward"):
                pg.evaluate(f"location.hash='#portfolio/{v}'"); pg.wait_for_timeout(2200)
                name = f"mock-{v}-{theme.split('-')[0]}-{w}x{h}"
                pg.screenshot(path=str(out / f"{name}.png"), full_page=True)
                over = pg.evaluate("document.documentElement.scrollWidth>innerWidth")
                if over: errors.append(f"horizontal overflow {name}")
        pg.close()
    b.close()
print(json.dumps({"errors": errors[:20]}))
