import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
sizes = [tuple(map(int, s.split("x"))) for s in sys.argv[2].split(",")]
base = "http://127.0.0.1:8767"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    for w, h in sizes:
        pg = b.new_page(viewport={"width": w, "height": h})
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardSeen','1')")
        pg.goto(base + "/#dashboard"); pg.wait_for_timeout(2000)
        for theme in ("dark", "light-green"):
            pg.evaluate(f"setTheme('{theme}')")
            for v in ("risico", "forward"):
                pg.evaluate(f"location.hash='#portfolio/{v}'"); pg.wait_for_timeout(2000)
                if v == "risico":
                    pg.evaluate("document.querySelectorAll('details').forEach(d=>d.closest('[data-view=portfolio]')&&(d.open=true))"); pg.wait_for_timeout(500)
                name = f"{v}-{theme.split('-')[0]}-{w}x{h}"
                pg.screenshot(path=str(out / f"{name}.png"))
                pg.screenshot(path=str(out / f"{name}-full.png"), full_page=True)
        pg.close()
    b.close()
print(json.dumps({"errors": errors[:20]}))
