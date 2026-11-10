"""Real browser: start a long Jesse backtest in the tester, cancel it, verify the server stopped it."""
import json, time, urllib.request
from playwright.sync_api import sync_playwright
base = "http://127.0.0.1:8767"; res = {}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome"); pg = b.new_page(viewport={"width": 1600, "height": 900})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}))")
    pg.goto(base + "/#onderzoeken/backtest"); pg.wait_for_selector("#strategy option", state="attached", timeout=60000); pg.wait_for_timeout(4000)
    pg.select_option("#strategy", "LabBreakout"); pg.select_option("#timeframe", "M15")
    pg.fill("#start", "2020-01-01"); pg.fill("#end", "2026-09-30"); pg.dispatch_event("#end", "change"); pg.wait_for_timeout(1500)
    pg.click("#top-run"); pg.wait_for_selector("#run-cancel", timeout=20000); pg.wait_for_timeout(6000)
    res["active_before"] = json.load(urllib.request.urlopen(base + "/api/runs/active"))
    pg.screenshot(path="evidence/v9/browser/cancel-1-running.png")
    reqs = []; pg.on("request", lambda r: "/api/runs" in r.url and reqs.append((round(time.time() - t, 1), r.method, r.url[22:])))
    pg.on("response", lambda r: "/api/runs" in r.url and reqs.append((round(time.time() - t, 1), r.status, r.url[22:])))
    t = time.time(); pg.click("#run-cancel")
    for _ in range(30):
        pg.wait_for_timeout(2000)
        if "Geannuleerd" in pg.inner_text("#run-status"): break
    res["requests"] = reqs
    res["cancel_seconds"] = round(time.time() - t, 1)
    res["status_text"] = pg.inner_text("#run-status")
    res["active_after"] = json.load(urllib.request.urlopen(base + "/api/runs/active"))
    res["buttons_enabled"] = pg.is_enabled("#top-run")
    pg.screenshot(path="evidence/v9/browser/cancel-2-cancelled.png")
    res["js_errors"] = errs; b.close()
open("evidence/v9/browser/cancel.json", "w").write(json.dumps(res, indent=2))
print(json.dumps(res, indent=1))
