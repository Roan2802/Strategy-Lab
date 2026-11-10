"""Open the furthest-reaching candidate of the big real run in the factory UI (dark + light) and screenshot the detail."""
import json, urllib.request
from playwright.sync_api import sync_playwright
base = "http://127.0.0.1:8767"; run = "fr_20261110_094132_eafc58"
c = json.load(urllib.request.urlopen(f"{base}/api/factory/runs/{run}/candidates"))
c = c if isinstance(c, list) else c.get("candidates", [])
best = sorted(c, key=lambda x: (x["stage"], x.get("profit_factor") or 0), reverse=True)[0]
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    for theme in ("dark", "light"):
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}));"
                           f"localStorage.setItem('labFactoryRun','{run}');localStorage.setItem('strategyLabTheme','{'light-green' if theme == 'light' else 'dark'}')")
        pg.goto(base + "/#onderzoeken/automatisch"); pg.wait_for_selector("#fx-showrej", timeout=60000); pg.wait_for_timeout(2500)
        pg.check("#fx-showrej"); pg.wait_for_timeout(1000)
        pg.locator(f'tr[data-cand="{best["id"]}"]').click(); pg.wait_for_selector("#fx-drawer", timeout=20000); pg.wait_for_timeout(4000)
        pg.screenshot(path=f"evidence/v9/factory_browser/05-detail-L5-{theme}.png")
        d = pg.locator("#fx-drawer"); d.evaluate("e => e.scrollTop = e.scrollHeight / 2"); pg.wait_for_timeout(1500)
        pg.screenshot(path=f"evidence/v9/factory_browser/06-detail-L5-mid-{theme}.png")
        pg.close()
    b.close()
print(best["name"], best["stage"], "errors", errs[:3])
