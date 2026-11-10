import json, sys, re, math
from pathlib import Path
from playwright.sync_api import sync_playwright
out = Path(r"C:\AI\Jesse\Jesse-Audit\evidence\v9\factory_ui"); out.mkdir(parents=True, exist_ok=True)
base = "http://127.0.0.1:8767"
live = "--live" in sys.argv
theme = "light" if "--light" in sys.argv else "dark"
W, H = 1600, 900
for a in sys.argv:
    m = re.fullmatch(r"(\d+)x(\d+)", a)
    if m: W, H = int(m[1]), int(m[2])
cat = {"families": [{"id": f"f{i}", "group": g, "label_nl": l, "description_nl": "", "mechanisms": [], "iq_sources": []} for i, (g, l) in enumerate([("Trend", "Breakout"), ("Trend", "Moving average"), ("Mean reversion", "RSI reversal"), ("Volatiliteit", "ATR squeeze"), ("Sessies", "Opening range")])],
  "profiles": {"snel": {"label_nl": "Snelle verkenning", "description_nl": "Klein, in een paar minuten."}, "uitgebreid": {"label_nl": "Uitgebreid onderzoek", "description_nl": "Meer varianten."}, "diepgaand": {"label_nl": "Diepgaand onderzoek", "description_nl": "Alles, uren."}},
  "quality_profiles": {"regelmatig": {"label_nl": "Regelmatig handelend"}, "zeer_actief": {"label_nl": "Zeer actief"}, "laag_frequent": {"label_nl": "Lage handelsfrequentie"}, "custom": {"label_nl": "Eigen criteria"}},
  "markets": [{"symbol": s, "first": "2008-01-01", "last": "2026-11-01", "supported": True, "notes": ""} for s in ["XAUUSD", "EURUSD", "USA30IDXUSD", "DEUIDXEUR", "DE40", "BTCUSD"]], "timeframes": ["M5", "M15", "M30", "H1", "H4", "D1"]}
plan = {"candidates": 480, "backtests_estimate": 5200, "trials_estimate": 38000, "est_minutes": 42, "workers": 6, "cpu_note_nl": "Gebruikt maximaal 6 van 12 kernen.", "ranges": {"XAUUSD": {"dev": ["2008-01-01", "2020-12-31"], "validation": ["2021-01-01", "2023-12-31"], "sealed": ["2024-01-01", "2026-11-01"]}}, "warnings": ["Veel strategieën tegelijk: de eindtest wordt strenger gecorrigeerd."]}
mock_status = {"s": "running"}
def run(status):
    return {"status": status, "phase": "dev", "phase_nl": "Ontwikkelingsvalidatie", "progress": 0.42 if status == "running" else 1, "counts": {"generated": 480, "technically_valid": 410, "rejected": 440, "quick_passed": 90, "dev_validated": 22, "wf_passed": 12, "frozen": 6, "final_tested": 6, "oos_approved": 2, "forward_ready": 1},
      "resources": {"cpu_pct": 71, "ram_mb": 5200, "workers": 6}, "families": [{"family": "Breakout", "group": "Trend", "tested": 160, "survived": 1, "best_stage": "oos_approved"}, {"family": "Moving average", "group": "Trend", "tested": 120, "survived": 0, "best_stage": "quick_passed"}, {"family": "RSI reversal", "group": "Mean reversion", "tested": 90, "survived": 1, "best_stage": "forward_ready"}, {"family": "ATR squeeze", "group": "Volatiliteit", "tested": 70, "survived": 0, "best_stage": "rejected"}, {"family": "Opening range", "group": "Sessies", "tested": 40, "survived": 0, "best_stage": "rejected"}],
      "log_tail": ["12:01 Fase ontwikkeling gestart", "12:02 160 varianten getest"], "config": {"markets": ["XAUUSD"], "profile": "snel"}, "protocol": {"split": "60/20/20", "sealed_from": "2024-01-01"}, "multiple_testing": {"n_trials": 38000, "note_nl": "38.000 pogingen meegeteld in de correctie."}}
def st(i):
    return "forward_ready" if i == 0 else "oos_approved" if i == 1 else "dev_validated" if i < 6 else "rejected"
cands = [{"id": f"c{i}", "name": f"Breakout-{i}", "symbol": "XAUUSD", "timeframe": "M15", "family": "Breakout", "group": "Trend", "stage": st(i), "status": st(i), "evidence_class": "sealed", "trades": 320 - i * 11, "net_return_pct": 42.5 - i * 7, "max_dd_pct": 9.8 + i, "profit_factor": 1.6 - i * .05, "recovery_factor": 3.1, "reasons_nl": ["Verlies op eindtest"] if i > 5 else []} for i in range(9)]
eq = [round(100 * math.exp(0.002 * i + 0.03 * math.sin(i / 7)), 3) - 100 for i in range(200)]
det = {"candidate": {"spec": {"symbol": "XAUUSD", "timeframe": "M15"}, "rules_nl": {"entry": ["Koop als de slotkoers boven de 20-daagse high sluit."], "exit": ["Stop op 2x ATR.", "Sluit na 12 bars."]}, "params": {"lookback": 20, "atr_mult": 2.0, "bars": 12}, "passport": {"familie": "Breakout", "versie": 1}, "iq_sources": [{"label_nl": "Donchian breakout (Turtle)", "provenance": "source"}, {"label_nl": "ATR-stop variant", "provenance": "own"}, {"label_nl": "Volume als proxy: tick count", "provenance": "proxy"}], "proxies_nl": ["Tickvolume vervangt echt volume."], "hypothesis_nl": "Momentum na een range-uitbraak zet door gedurende enkele uren."},
  "dev": {"metrics": {"net_return_pct": 42.5, "max_dd_pct": 9.8, "profit_factor": 1.6, "trades": 320, "recovery_factor": 3.1}, "equity": eq[:140], "yearly": [{"year": 2010 + i, "net_return_pct": v} for i, v in enumerate([8, -3, 12, 5, -6, 9, 14, 2, -1, 7])], "monthly": None, "sessions": None, "regimes": None},
  "walk_forward": {"rolling": [{"label": f"W{i}", "net_return_pct": v} for i, v in enumerate([4, 6, -2, 8, 3])], "expanding": [{"label": f"E{i}", "net_return_pct": v} for i, v in enumerate([3, 5, 1])]},
  "robustness": {"cost_stress": [{"scenario": "Normale kosten", "net_return_pct": 42.5, "profit_factor": 1.6, "max_dd_pct": 9.8}, {"scenario": "Kosten x2", "net_return_pct": 28.0, "profit_factor": 1.35, "max_dd_pct": 12}], "monte_carlo": {"p5_dd": 14.2, "p50_dd": 10.1}, "perturbation": {"stable_share": 0.78}},
  "distribution": {"max_win_streak": 7, "max_loss_streak": 9, "top3_days_share_pct": 38}, "final_test": {"metrics": {"net_return_pct": 11.2, "profit_factor": 1.28, "max_dd_pct": 7.4, "trades": 61}, "equity": eq[140:]}, "scorecard": {"checks": [{"label_nl": "Voldoende trades", "passed": True}, {"label_nl": "Kosten x2 positief", "passed": True}, {"label_nl": "Winst niet geconcentreerd", "passed": False}]},
  "warnings_nl": ["Winst komt grotendeels uit 3 dagen"], "reasons_nl": ["Eindtest lager dan ontwikkeling: verwacht bij selectie."], "baseline_comparison": {"net_return_pct": 4.1, "profit_factor": 1.05},
  "trades_sample": [{"entry_time": "2023-03-08T09:00:00", "exit_time": "2023-03-08T12:00:00", "entry_price": 1815.0, "exit_price": 1822.5, "side": "long", "pnl": 0.4}, {"entry_time": "2023-03-10T10:00:00", "exit_time": "2023-03-10T14:00:00", "entry_price": 1865.0, "exit_price": 1858.0, "side": "long", "pnl": -0.35}], "forward_eligible": True}
def handler(route):
    u = route.request.url; p = u.split("/api/factory")[1].split("?")[0]; m = route.request.method
    def j(o, s=200): route.fulfill(status=s, content_type="application/json", body=json.dumps(o))
    if p == "/catalog": return j(cat)
    if p == "/plan": return j(plan)
    if p == "/runs" and m == "POST": return j({"run_id": "r1"})
    if p == "/runs": return j({"runs": [{"run_id": "r1", "status": "done", "created_at": "2026-11-10T12:00:00", "config": {"markets": ["XAUUSD"]}}]})
    if p == "/runs/r1": return j(run("running" if mock_status["s"] == "running" else "done"))
    if p == "/runs/r1/candidates": return j(cands)
    if p.startswith("/candidates/c") and p.endswith("/forward"): return j({"detail": "x", "reason": "Niet geschikt"}, 409)
    if p.startswith("/candidates/"): return j(det)
    return j({"detail": "nf"}, 404)
errors = []
with sync_playwright() as p:
    br = p.chromium.launch(channel="chrome")
    ctx = br.new_context(viewport={"width": W, "height": H})
    ctx.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}))")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and "favicon" not in m.text and errors.append("console: " + m.text))
    if not live: pg.route("**/api/factory/**", handler)
    suffix = f"{'live' if live else 'mock'}-{theme}-{W}"
    pg.goto(base + "/#onderzoeken/automatisch"); pg.wait_for_timeout(1500)
    if theme == "light": pg.evaluate("document.body.dataset.theme='light'")
    print("route:", pg.evaluate("location.hash"), "fx-root", pg.locator("#fx-root").count())
    pg.wait_for_selector("#fx-start", timeout=8000); pg.wait_for_timeout(900)
    pg.screenshot(path=str(out / f"01-setup-{suffix}.png"))
    print("plan:", pg.inner_text("#fx-plan")[:200].replace("\n", " | "))
    if not live:
        pg.locator("#fx-start").click(); pg.wait_for_selector(".fx-live", timeout=8000); pg.wait_for_timeout(500)
        pg.locator("[data-act=log]").click(); pg.wait_for_timeout(200)
        pg.screenshot(path=str(out / f"02-running-{suffix}.png"))
        mock_status["s"] = "done"
        pg.reload(); pg.wait_for_selector(".fx-funnel", timeout=15000); pg.wait_for_timeout(1200)
        pg.screenshot(path=str(out / f"03-finished-{suffix}.png"))
        pg.locator("#fx-showrej").check(); pg.wait_for_timeout(300)
        pg.locator("tr[data-cand=c0]").click(); pg.wait_for_selector("#fx-drawer.open"); pg.wait_for_timeout(2500)
        pg.screenshot(path=str(out / f"04-detail-{suffix}.png"))
        pg.locator("#fx-drawer").evaluate("e=>e.scrollTo(0,e.scrollHeight)"); pg.wait_for_timeout(500)
        pg.screenshot(path=str(out / f"05-detail-bottom-{suffix}.png"))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        print("hscroll:", pg.evaluate("document.documentElement.scrollWidth>innerWidth+2"))
        print("classic:", pg.locator("#dsc-classic").count(), "ledger:", pg.locator("#dsc-ledger-acc").count(), "ledger inside classic:", pg.locator("#dsc-classic #dsc-ledger-acc").count())
    print("ERRORS:", errors)
    br.close()
