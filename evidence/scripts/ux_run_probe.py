import sys
from playwright.sync_api import sync_playwright
out = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome"); pg = b.new_page(viewport={"width": 1600, "height": 900})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true,dismissed:true}))")
    pg.goto("http://127.0.0.1:8768/#onderzoeken/backtest"); pg.wait_for_timeout(3000)
    pg.click("#top-run"); pg.wait_for_function("document.querySelector('#run-status').textContent.includes('afgerond') || document.querySelector('#run-status').classList.contains('error')", timeout=180000)
    pg.wait_for_function("document.querySelectorAll('#trades tr').length>0 || document.querySelector('#run-status').classList.contains('error')", timeout=30000); pg.wait_for_timeout(2500); print("status:", pg.inner_text("#run-status"))
    pg.screenshot(path=out + "-1-after-run.png")
    rows = pg.query_selector_all("#trades tr")
    print("trades:", len(rows))
    if rows:
        pg.evaluate("labGo('onderzoeken/trades')"); pg.wait_for_timeout(800)
        rows[min(2, len(rows)-1)].click(); pg.wait_for_timeout(2000)
        pg.screenshot(path=out + "-2-trade.png", full_page=False)
        print("detail:", pg.inner_text("#trade-detail")[:400])
    print("interp:", pg.inner_text("#interpretation")[:500])
    print("ERRORS", errs[:10]); b.close()
