import sys, json
from playwright.sync_api import sync_playwright
route = sys.argv[1]; js = sys.argv[2]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome"); pg = b.new_page(viewport={"width": 1600, "height": 900})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text + " " + str(m.location.get('url',''))))
    pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}))")
    pg.goto("http://127.0.0.1:8768/#" + route); pg.wait_for_timeout(3000)
    print(json.dumps(pg.evaluate(js), ensure_ascii=False, indent=1)[:4000]); print("ERRORS", errs[:10])
    b.close()
