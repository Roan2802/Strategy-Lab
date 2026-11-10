"""Real browser: start an automatic research run from Onderzoeken > Automatische strategieen, follow it, open a candidate.

Usage: v9_factory_browser.py <outdir> [channel] [profile] [WxH] [theme]
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
channel = sys.argv[2] if len(sys.argv) > 2 else "chrome"
profile = sys.argv[3] if len(sys.argv) > 3 else "snel"
w, h = map(int, (sys.argv[4] if len(sys.argv) > 4 else "1600x900").split("x"))
theme = sys.argv[5] if len(sys.argv) > 5 else "dark"
base = "http://127.0.0.1:8767"
tag = f"{channel}-{w}x{h}-{theme}"
res, errors = {"tag": tag, "profile": profile}, []
with sync_playwright() as p:
    b = p.chromium.launch(channel=channel); pg = b.new_page(viewport={"width": w, "height": h})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
    pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}));"
                       "if(!sessionStorage.getItem('fxInit')){sessionStorage.setItem('fxInit','1');localStorage.removeItem('labFactoryRun');}")
    pg.goto(base + "/#onderzoeken/automatisch"); pg.wait_for_selector("#fx-start", timeout=60000); pg.wait_for_timeout(2500)
    if theme == "light":
        pg.evaluate("setTheme('light-green')"); pg.wait_for_timeout(500)
    # markets: ensure XAUUSD, US30, DE40 selected; timeframes M15, H1
    for grp, wanted in (("markets", {"XAUUSD", "US30", "DE40", "USA30IDXUSD"}), ("tfs", {"M15", "H1"})):
        chips = pg.locator(f'[data-group="{grp}"] [data-v], [data-group="{grp}"] button, [data-group="{grp}"] input')
        res[f"{grp}_available"] = chips.count()
        pg.evaluate("""([grp, wanted]) => { const root = document.querySelector(`[data-group="${grp}"]`);
          for (const el of root.querySelectorAll('[data-v],input,button')) { const v = el.dataset?.v || el.value || el.textContent.trim();
            const on = el.classList.contains('on') || el.checked || el.getAttribute('aria-pressed') === 'true';
            if (wanted.includes(v) !== on) el.click(); } }""", [grp, list(wanted)])
    pg.click(f'#fx-profile .fx-seg-btn[data-v="{profile}"]'); pg.wait_for_timeout(2500)
    res["plan"] = pg.inner_text("#fx-plan")[:600]
    pg.screenshot(path=str(out / f"01-setup-{tag}.png"), full_page=True)
    lay = pg.evaluate("document.documentElement.scrollWidth > innerWidth + 2")
    res["horizontal_overflow"] = lay
    t0 = time.time(); pg.click("#fx-start"); pg.wait_for_timeout(12000)
    pg.screenshot(path=str(out / f"02-running-{tag}.png"), full_page=True)
    res["running_text"] = pg.inner_text("#fx-run")[:500] if pg.locator("#fx-run").count() else ""
    # wait until finished (funnel + candidate table) up to 60 min
    for _ in range(720):
        txt = pg.inner_text("body")
        if pg.locator("tr[data-cand]").count() or "Afgerond" in txt or "Klaar voor forward" in txt and "Toon afgewezen" in txt:
            if pg.locator("#fx-showrej").count():
                break
        pg.wait_for_timeout(5000)
    res["seconds"] = round(time.time() - t0)
    pg.wait_for_timeout(3000)
    pg.screenshot(path=str(out / f"03-finished-{tag}.png"), full_page=True)
    if pg.locator("#fx-showrej").count() and not pg.locator("tr[data-cand]").count():
        pg.check("#fx-showrej"); pg.wait_for_timeout(1500)
    res["candidate_rows"] = pg.locator("tr[data-cand]").count()
    if res["candidate_rows"]:
        pg.locator("tr[data-cand]").first.click(); pg.wait_for_selector("#fx-drawer", timeout=20000); pg.wait_for_timeout(4000)
        res["detail_text"] = pg.inner_text("#fx-drawer")[:1500]
        pg.screenshot(path=str(out / f"04-detail-{tag}.png"), full_page=False)
    res["js_errors"] = errors
    b.close()
(out / f"factory-{tag}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: res[k] for k in ("tag", "seconds", "candidate_rows", "horizontal_overflow") if k in res}), "JS errors:", len(errors))
