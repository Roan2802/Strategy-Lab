"""Re-render the latest finished validation jobs (per strategy) in the Validatie view and screenshot the full report."""
import json, sys, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
base = "http://127.0.0.1:8767"; out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
jobs = json.load(urllib.request.urlopen(base + "/api/jobs"))["jobs"]
done = [j for j in jobs if j.get("kind") == "validation" and j.get("status") == "done"]
picked = {}
for j in done:
    t = j.get("title", "")
    for key in ("EmaRsiPullback", "LabBreakout", "gold_reaper"):
        if key in t and key not in picked:
            picked[key] = j
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome"); pg = b.new_page(viewport={"width": 1600, "height": 1000})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    hist = [{"id": j["id"], "at": 0, "label": k} for k, j in picked.items()]
    pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}));"
                       f"localStorage.setItem('labValHistory',{json.dumps(json.dumps(hist))})")
    pg.goto(base + "/#onderzoeken/validatie"); pg.wait_for_selector("#val-history option", state="attached", timeout=30000); pg.wait_for_timeout(2500)
    for k, j in picked.items():
        pg.select_option("#val-history", j["id"]); pg.dispatch_event("#val-history", "change")
        pg.wait_for_selector("#val-result .verdict", timeout=30000); pg.wait_for_timeout(2500)
        pg.set_viewport_size({"width": 1600, "height": 5600}); pg.wait_for_timeout(1500)
        pg.locator("#val-result").screenshot(path=str(out / f"report-{k}.png"))
        pg.set_viewport_size({"width": 1600, "height": 1000})
        print(k, j["id"], pg.inner_text("#val-result .verdict").splitlines()[0])
    b.close()
print("errors", errs[:5])
