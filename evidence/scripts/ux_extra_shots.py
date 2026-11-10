from playwright.sync_api import sync_playwright
out = "evidence/ux_v8/final/"
import os; os.makedirs(out, exist_ok=True)
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome"); pg = b.new_page(viewport={"width": 1600, "height": 900})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script("localStorage.setItem('labTourSeen','1')")
    pg.goto("http://127.0.0.1:8767/#home"); pg.wait_for_timeout(4000); pg.screenshot(path=out + "home.png")
    pg.evaluate("labGo('onderzoeken/backtest')"); pg.wait_for_timeout(3000)
    pg.evaluate("(async()=>{const s=document.querySelector('#run-select');const o=[...s.options].find(o=>/LabBreakout/.test(o.textContent));if(o){s.value=o.value;await loadSelectedRun()}})()"); pg.wait_for_timeout(5000)
    if pg.locator("#trades tr").count():
        pg.locator("#trades tr").nth(1).click(); pg.wait_for_timeout(3500)
    pg.screenshot(path=out + "tester-dark.png")
    pg.evaluate("labGo('onderzoeken/grafiek')"); pg.wait_for_timeout(2500); pg.screenshot(path=out + "grafiek-dark.png")
    pg.evaluate("setTheme('light-green')"); pg.evaluate("labGo('onderzoeken/backtest')"); pg.wait_for_timeout(2500); pg.screenshot(path=out + "tester-light.png")
    pg.evaluate("labGo('home')"); pg.wait_for_timeout(2000); pg.screenshot(path=out + "home-light.png")
    pg.evaluate("setTheme('dark')")
    pg.evaluate("labGo('onderzoeken/backtest')"); pg.wait_for_timeout(1500)
    pg.evaluate("window.labOnboarding.start({restart:true})"); pg.wait_for_timeout(2500); pg.screenshot(path=out + "onboarding-1.png")
    pg.evaluate("document.querySelector('#workflow-slot [data-step],#workflow-slot button')?.click()"); pg.wait_for_timeout(1200); pg.screenshot(path=out + "workflow-guide.png")
    for r in ["strategieen/overzicht", "onderzoeken/validatie", "portfolio/risico", "data"]:
        pg.evaluate(f"labGo('{r}')"); pg.wait_for_timeout(2500); pg.screenshot(path=out + r.replace('/', '-') + ".png")
    b.close()
print("errors", errs[:5])
