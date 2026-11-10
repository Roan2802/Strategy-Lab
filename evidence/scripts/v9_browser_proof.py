"""Real-browser proof: full validation of a Builder, a Jesse and a plugin strategy through the Validatie view.

Usage: v9_browser_proof.py <outdir> [base] [channel] [names...]
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8767"
channel = sys.argv[3] if len(sys.argv) > 3 else "chrome"
CASES = {
    "builder": {"spec": "spec:EmaRsiPullback", "tf": "M15", "start": "2025-01-01", "end": "2026-10-01",
                "wf": {"val-train": 120, "val-test": 30, "val-step": 30, "val-combos": 12}, "mode": "rolling"},
    "jesse": {"spec": "jesse:LabBreakout", "tf": "M15", "start": "2026-01-01", "end": "2026-10-01",
              "wf": {"val-train": 60, "val-test": 20, "val-step": 20, "val-combos": 4, "val-pbo-combos": 4}, "mode": "rolling"},
    "plugin": {"spec": "plugin:gold_reaper", "tf": None, "start": "2025-07-01", "end": "2026-10-01",
               "wf": {"val-train": 90, "val-test": 30, "val-step": 30, "val-combos": 4, "val-pbo-combos": 4}, "mode": "rolling"},
}
names = sys.argv[4:] or list(CASES)
summary, errors = [], []
with sync_playwright() as p:
    b = p.chromium.launch(channel=channel)
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}))")
    for name in names:
        c = CASES[name]
        pg.goto(base + "/#onderzoeken/validatie"); pg.wait_for_selector("#val-spec option[value='%s']" % c["spec"], state="attached", timeout=60000)
        pg.wait_for_timeout(1500)
        pg.select_option("#val-spec", c["spec"]); pg.dispatch_event("#val-spec", "change"); pg.wait_for_timeout(1500)
        if c["tf"] and pg.locator("#val-tf").is_enabled():
            pg.select_option("#val-tf", c["tf"])
        for sel, val in (("#val-start", c["start"]), ("#val-end", c["end"])):
            pg.fill(sel, val); pg.dispatch_event(sel, "change")
        for box in ("#val-wf-on", "#val-stab-on", "#val-pbo-on"):
            if pg.locator(box).count() and pg.locator(box).is_enabled() and not pg.locator(box).is_checked():
                pg.check(box)
        if pg.locator("#val-mode").count():
            pg.select_option("#val-mode", c["mode"])
        for fid, val in c["wf"].items():
            if pg.locator("#" + fid).count() and pg.locator("#" + fid).is_enabled():
                pg.fill("#" + fid, str(val)); pg.dispatch_event("#" + fid, "change")
        caps = pg.inner_text("#val-caps") if pg.locator("#val-caps").count() else ""
        pg.screenshot(path=str(out / f"{name}-1-form.png"), full_page=True)
        t0 = time.time()
        pg.click("#val-run")
        pg.wait_for_timeout(8000)
        pg.screenshot(path=str(out / f"{name}-2-progress.png"))
        pg.wait_for_selector("#val-result .verdict", timeout=3600_000)
        pg.wait_for_timeout(3000)
        verdict = pg.inner_text("#val-result .verdict")
        report = pg.inner_text("#val-result")
        pg.set_viewport_size({"width": 1600, "height": 5200}); pg.wait_for_timeout(1500)
        pg.locator("#val-result").screenshot(path=str(out / f"{name}-3-report.png"))
        pg.set_viewport_size({"width": 1600, "height": 1000})
        summary.append({"case": name, "strategy": c["spec"], "period": [c["start"], c["end"]], "seconds": round(time.time() - t0),
                        "verdict": verdict.splitlines()[0] if verdict else None, "capabilities_text": caps[:600],
                        "report_excerpt": report[:4000]})
        print(name, summary[-1]["verdict"], summary[-1]["seconds"], "s", flush=True)
    b.close()
(out / f"proof-{channel}.json").write_text(json.dumps({"summary": summary, "js_errors": errors}, indent=2, ensure_ascii=False), encoding="utf-8")
print("JS errors:", len(errors))
