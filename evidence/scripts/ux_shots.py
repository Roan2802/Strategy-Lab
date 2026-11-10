"""Screenshot every view of the Strategy Lab. Usage: ux_shots.py <outdir> [base] [channel] [WxH]"""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8768"
channel = sys.argv[3] if len(sys.argv) > 3 else "chrome"
w, h = map(int, (sys.argv[4] if len(sys.argv) > 4 else "1600x900").split("x"))
views = sys.argv[5].split(",") if len(sys.argv) > 5 else None
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(channel=channel)
    pg = b.new_page(viewport={"width": w, "height": h})
    pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardSeen','1')")
    pg.goto(base + "/#dashboard"); pg.wait_for_timeout(2500)
    ids = views or pg.eval_on_selector_all("[data-nav]", "els => els.map(e => e.dataset.nav)")
    for v in ids:
        pg.evaluate(f"location.hash = '#{v}'"); pg.wait_for_timeout(2500)
        pg.screenshot(path=str(out / f"{v}-{channel}-{w}x{h}.png"))
    b.close()
print(json.dumps({"views": ids, "errors": errors[:20]}, ensure_ascii=False))
