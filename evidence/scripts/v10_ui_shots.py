"""Screenshots of main views. Usage: v10_ui_shots.py <outdir> <route,route,...> [WxH] [theme]"""
import sys, tempfile, shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
routes = sys.argv[2].split(",")
w, h = map(int, (sys.argv[3] if len(sys.argv) > 3 else "1600x900").split("x"))
theme = sys.argv[4] if len(sys.argv) > 4 else "dark"
prof = tempfile.mkdtemp(prefix="lab-shot-")
errs = []
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(prof, channel="chrome", viewport={"width": w, "height": h})
    pg = ctx.pages[0]
    pg.on("pageerror", lambda e: errs.append(str(e)))
    ctx.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}));localStorage.setItem('strategyLabTheme','%s')" % theme)
    for r in routes:
        pg.goto("http://127.0.0.1:8767/#" + r); pg.reload(); pg.wait_for_timeout(3500)
        pg.screenshot(path=str(out / (r.replace("/", "_") + f"-{w}-{theme}.png")), full_page=True)
    ctx.close()
shutil.rmtree(prof, ignore_errors=True)
print("errors", errs)
