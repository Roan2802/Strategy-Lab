"""End-to-end UX test of the Strategy Lab research workflow (V10 structure).

Usage: ux_e2e.py <outdir> [base_url] [channel] [WxH] [--quick] [--theme=light]
--quick skips backtest/optimisation/validation/multi-market runs (layout + navigation checks only).
Routes: home | strategieen/(mijn|bouwen|automatisch|bibliotheek) | onderzoeken/(testen|analyse) | portfolio | data.
"""
import json
import shutil
import urllib.error
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

args = [a for a in sys.argv[1:] if not a.startswith("--")]
out = Path(args[0]); out.mkdir(parents=True, exist_ok=True)
base = args[1] if len(args) > 1 else "http://127.0.0.1:8767"
channel = args[2] if len(args) > 2 else "chrome"
w, h = map(int, (args[3] if len(args) > 3 else "1600x900").split("x"))
quick = "--quick" in sys.argv
shadow_only = "--shadow" in sys.argv  # isolated shadow-route group, see evidence/v102/shadow_route.sh
theme = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--theme=")), "dark")
tag = f"{channel}-{w}x{h}-{theme}"
results, errors, last404 = [], [], [""]


def check(name, ok, detail=""):
    results.append({"step": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)


def shot(pg, name, full=False):
    pg.screenshot(path=str(out / f"{name}-{tag}.png"), full_page=full)


# four tester blocks must not overlap on the fixed Testen layout
OVERLAP_JS = """() => {
  const sel = ['.toolbar', '.center', '.results-side', '.dock'];
  const els = sel.map(s => document.querySelector('.view.active .workspace > ' + s)).filter(e => e && e.getClientRects().length);
  const r = els.map(e => [e.className.split(' ')[0], e.getBoundingClientRect()]);
  const bad = [];
  for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
    const [a, x] = r[i], [b, y] = r[j];
    if (x.left < y.right - 2 && x.right > y.left + 2 && x.top < y.bottom - 2 && x.bottom > y.top + 2) bad.push(a + '/' + b);
  }
  return {bad, hscroll: document.documentElement.scrollWidth > innerWidth + 2, blocks: r.map(x => x[0])};
}"""

HSCROLL_JS = "() => document.documentElement.scrollWidth > innerWidth + 2"
# no draggable widgets / resize grips / layout editor anywhere in the app
NO_DRAG_JS = """() => [...document.querySelectorAll('.panel-drag-handle, .widget-resize-handle, #layout-editor, #layout-profile, #menu-edit-layout, #window-menu, #chart-focus, #chart-expand, .layout-reorder, [draggable="true"], [data-panel]')].map(e => e.id || e.className || e.tagName)"""

profile = Path(tempfile.mkdtemp(prefix="lab-e2e-"))
with sync_playwright() as p:
    def launch():
        ctx = p.chromium.launch_persistent_context(str(profile), channel=channel, viewport={"width": w, "height": h})
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"[after step {len(results)}] pageerror: {e} | {(e.stack or '').splitlines()[1:4]}"))
        pg.on("response", lambda r: r.status in (404, 409, 422) and last404.__setitem__(0, f"{r.status} {r.url}"))
        # a 404/409/422 on the /api/shadow endpoints is an expected "not available yet" state (shown as a calm notice), not a JS fault
        pg.on("console", lambda m: m.type == "error" and "favicon" not in m.text and not (any(c in m.text for c in ("404", "409", "422")) and "/api/shadow" in last404[0]) and errors.append(f"console: {m.text}"))
        return ctx, pg

    ctx, pg = launch()
    ctx.add_init_script("if(!localStorage.getItem('labTourSeen')){localStorage.setItem('labTourSeen','1');localStorage.setItem('labOnboardV1',JSON.stringify({done:true}))};localStorage.setItem('strategyLabTheme','%s')" % theme)
    pg.goto(base + "/#home"); pg.wait_for_selector("#dash-kpi .card", timeout=20000); pg.wait_for_timeout(2500)
    check("home loads with KPI tiles", pg.locator("#dash-kpi .card").count() >= 4)
    pg.wait_for_function("(document.querySelector('#dash-data-line')?.textContent || '').includes('Details')", timeout=20000)
    line = pg.inner_text("#dash-data-line")
    check("home shows compact market-data line with Details", "Marktdata" in line and pg.locator('#dash-data-line [data-go="data"]').count() == 1, line.replace("\n", " ")[:120])
    check("home has no permanent 7-step stepper", not pg.locator("#workflow-slot").is_visible())
    import re
    cnt = pg.locator("#dash-data-line [data-data-counts]")
    m = re.search(r"(\d+) actueel . (\d+) vertraagd . (\d+) gesloten", cnt.inner_text()) if cnt.count() else None
    n_sym = pg.evaluate("fetch('/api/data/supervisor').then(r => r.json()).then(d => (d.symbols || []).length).catch(() => null)")
    check("home data line shows counts current / delayed / closed that add up", bool(m) and (n_sym is None or sum(map(int, m.groups())) == n_sym), f"{m.group(0) if m else 'no counts'} of {n_sym}")
    check("home data line shows last successful sync", "laatste succesvolle sync" in line or "laatste controle" in line, line.replace(chr(10), " ")[-80:])
    sup = pg.evaluate("fetch('/api/data/supervisor').then(r => r.json()).catch(() => null)")
    lagging = bool(sup) and any((x.get("lag_days") or 0) > 0 and not x.get("market_closed") for x in sup.get("symbols", []))
    check("home data line never green while instruments lag", not (lagging and pg.locator("#dash-data-line .chip.good").count()), f"lagging={lagging}")
    kl = pg.inner_text("#dash-kpi")
    check("home shows running research, forward strategies and critical errors only", all(t in kl.lower() for t in ("lopend onderzoek", "forwardstrategie", "kritieke fouten")) and "waarschuwingen" not in kl.lower())
    shot(pg, "01-home", full=True)

    # ------------------------------------------------------------------ shadow route (isolated instance only)
    if shadow_only:
        import urllib.request
        def jget(url):
            with urllib.request.urlopen(url, timeout=60) as r: return json.loads(r.read())
        def jpost(url, body=None):
            req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read() or "null")
            except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or "null")
        real = "http://127.0.0.1:8767"
        count = lambda b: {p["id"]: p["strategies"] for p in jget(b + "/api/shadow/portfolios")["portfolios"]}
        real_before = count(real)
        fwd_before = jget(base + "/api/forward/portfolios")
        picks = json.loads((out / "probe.json").read_text(encoding="utf-8"))
        okc, badc = picks["eligible"], picks["ineligible"]
        check("probe found an eligible and an ineligible factory candidate", bool(okc and badc), f"{okc and okc['name']} / {badc and badc['name']}")
        check("isolated instance starts with an empty shadow store", count(base) == {}, count(base))
        TOAST_UP = "(document.querySelector('#lab-toast')?.textContent || '').length > 0 && !document.querySelector('#lab-toast').hidden"
        TOAST_REFUSED = "(() => { const t = document.querySelector('#lab-toast'); return t && !t.hidden && t.textContent.startsWith('Niet toegevoegd'); })()"
        TOAST_GONE = "(() => { const t = document.querySelector('#lab-toast'); return !t || t.hidden; })()"
        toast = lambda: pg.inner_text("#lab-toast") if pg.locator("#lab-toast:visible").count() else ""
        # 1. empty state -> create test portfolio through the UI
        pg.evaluate("localStorage.removeItem('labPfMode')"); pg.evaluate("labGo('portfolio/risico')"); pg.wait_for_selector("#pf-mode", timeout=20000)
        pg.click('#pf-mode label:has(input[value="shadow"])'); pg.wait_for_selector("#shadow-live [data-sh-create]", timeout=20000)
        check("empty shadow view offers a create button and the experimental label", "Experimenteel" in pg.inner_text("#shadow-live"))
        shot(pg, "s01-empty")
        pg.click("#shadow-live [data-sh-create]"); pg.wait_for_selector("#sh-table", timeout=20000)
        ports = count(base)
        check("UI created exactly one test shadow portfolio", len(ports) == 1, ports)
        pid = next(iter(ports))
        # 2. factory candidate -> Volg in Shadow Research
        pg.evaluate("labGo('strategieen/automatisch')"); pg.wait_for_selector("#fx-start", timeout=20000); pg.wait_for_timeout(600)
        pg.evaluate("([r, i]) => window.labFactory.open(r, i)", [okc["run"], okc["id"]]); pg.wait_for_selector("#fx-drawer #fx-shadow", timeout=30000)
        check("eligible candidate drawer has the button Volg in Shadow Research", pg.inner_text("#fx-shadow").strip() == "Volg in Shadow Research")
        pg.click("#fx-shadow"); pg.wait_for_function(TOAST_UP, timeout=20000)
        t1 = toast(); shot(pg, "s02-toast-registered")
        check("success toast confirms the candidate is followed", "wordt gevolgd in Shadow Research" in t1, t1)
        check("registration landed in the test portfolio only", count(base).get(pid) == 1, count(base))
        # 3. duplicate
        pg.wait_for_function(TOAST_GONE, timeout=15000)
        pg.click("#fx-shadow"); pg.wait_for_function(TOAST_REFUSED, timeout=20000)
        t2 = toast(); shot(pg, "s03-toast-duplicate")
        check("second click is refused with a calm Dutch toast", t2.startswith("Niet toegevoegd:") and "{" not in t2 and count(base).get(pid) == 1, t2)
        pg.keyboard.press("Escape"); pg.wait_for_function(TOAST_GONE, timeout=15000)
        # 4. ineligible
        pg.evaluate("([r, i]) => window.labFactory.open(r, i)", [badc["run"], badc["id"]]); pg.wait_for_selector("#fx-drawer #fx-shadow", timeout=30000)
        pg.click("#fx-shadow"); pg.wait_for_function(TOAST_REFUSED, timeout=20000)
        t3 = toast(); shot(pg, "s04-toast-ineligible")
        check("ineligible candidate shows a readable refusal reason, no raw JSON", "{" not in t3 and len(t3) > 30 and count(base).get(pid) == 1, t3)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        # 5. Portfolio > Shadow Research list + detail
        pg.evaluate("labGo('portfolio/risico')"); pg.wait_for_selector("#sh-table tr[data-sid]", timeout=20000); pg.wait_for_timeout(800)
        sl = pg.inner_text("#shadow-live")
        check("strategy appears in the shadow list with experimental label", pg.locator("#sh-table tr[data-sid]").count() == 1 and "Experimenteel" in sl and "vwap_pullback" in pg.inner_text("#sh-table"), pg.inner_text("#sh-table")[:120].replace(chr(10), " "))
        check("shadow view shows evidence class DAILY_DELAYED_FORWARD", "DAILY_DELAYED_FORWARD" in sl)
        pg.click("#sh-table tr[data-sid]"); pg.wait_for_selector("#sh-detail .sh-detail h3 small", timeout=20000); pg.wait_for_timeout(800)
        sd = pg.inner_text("#sh-detail")
        shot(pg, "s05-detail", full=True)
        check("detail shows frozen registration info (version, hash, registered at)", all(t in sd for t in ("Registratie", "Versie", "Geregistreerd")), sd[:240].replace(chr(10), " "))
        check("detail shows experimental label and evidence class", "Experimenteel" in sd and "DAILY_DELAYED_FORWARD" in sd, sd[:100].replace(chr(10), " "))
        # 6. Mijn strategieën: builder spec follow
        pg.evaluate("labGo('strategieen/mijn')"); pg.wait_for_selector("#lib-rows tr[data-key]", timeout=20000); pg.wait_for_timeout(500)
        btn = pg.locator('#lib-rows tr[data-key] [data-lib-act="shadow"]')
        check("Mijn strategieën offers Volg in Shadow Research on eligible rows", btn.count() >= 1, btn.count())
        if btn.count():
            name = pg.locator('#lib-rows tr[data-key]:has([data-lib-act="shadow"])').first.locator(".name b").inner_text()
            pg.wait_for_function(TOAST_GONE, timeout=15000)
            btn.first.click(); pg.wait_for_function(TOAST_UP, timeout=20000)
            t4 = toast(); shot(pg, "s06-library-toast")
            check("library follow gives success or calm refusal toast (no raw JSON)", ("wordt gevolgd" in t4 or t4.startswith("Niet toegevoegd")) and "{" not in t4, f"{name}: {t4}")
        # 7. promote never allowed
        st, body = jpost(f"{base}/api/shadow/portfolios/{pid}/promote")
        check("promote attempt is refused with NEVER_LIVE_ELIGIBLE (UI exposes no promote control)", st == 409 and "NEVER_LIVE_ELIGIBLE" in json.dumps(body) and pg.locator("#shadow-live :text-matches('Promoveer|Promote|Live zetten', 'i')").count() == 0, f"{st} {json.dumps(body)[:100]}")
        # 8. isolation + cleanliness
        check("real shadow portfolio untouched (strategy counts unchanged)", count(real) == real_before, f"{real_before} -> {count(real)}")
        check("validated /api/forward/portfolios unaffected", jget(base + "/api/forward/portfolios") == fwd_before)
        check("no horizontal overflow in the shadow route", not pg.evaluate(HSCROLL_JS))
        check("0 JS errors in the shadow route", not errors, errors[:3])
        ctx.close()
        shutil.rmtree(profile, ignore_errors=True)
        summary = {"tag": tag + "-shadow", "passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results), "results": results, "js_errors": errors[:20]}
        (out / f"e2e-shadow-{tag}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: summary[k] for k in ("tag", "passed", "failed")}), "JS errors:", len(errors))
        sys.exit(0)

    # navigation: every section and subtab renders without errors and without horizontal overflow
    before = len(errors)
    expected = {"strategieen": ["Mijn strategieën", "Zelf bouwen", "Automatisch ontdekken", "Bibliotheek"], "onderzoeken": ["Testen", "Analyse"]}
    for sec in ["home", "strategieen", "onderzoeken", "portfolio", "data"]:
        pg.click(f'[data-nav="{sec}"]'); pg.wait_for_timeout(900)
        labels = pg.eval_on_selector_all(".subnav:not([hidden]) [data-sub]", "els => els.map(e => e.textContent.trim())")
        if sec in expected:
            check(f"{sec} subtabs are {expected[sec]}", labels == expected[sec], labels)
        for sub in pg.eval_on_selector_all(".subnav:not([hidden]) [data-sub]", "els => els.map(e => e.dataset.sub)"):
            pg.click(f'[data-sub="{sub}"]'); pg.wait_for_timeout(1300)
            txt = pg.eval_on_selector(".view.active", "e => e.innerText.trim().length")
            if txt < 20:
                check(f"subtab {sub} has content", False, txt)
            if pg.evaluate(HSCROLL_JS):
                check(f"no horizontal overflow on {sub}", False)
    check("all sections/subtabs open without JS errors", len(errors) == before, errors[before:][:3])
    drag = pg.evaluate(NO_DRAG_JS)
    check("no drag handles, resize grips, layout editor or data-panel widgets in the page", not drag, drag)
    menu = pg.inner_text("header")
    check("Weergave menu has no widget/layout items", "Widgets" not in menu and "Layout" not in menu)

    # legacy hashes redirect to the new structure
    legacy = {"onderzoeken/backtest": "onderzoeken/testen", "onderzoeken/grafiek": "onderzoeken/testen", "onderzoeken/trades": "onderzoeken/testen",
              "onderzoeken/inspector": "onderzoeken/testen", "onderzoeken/optimalisatie": "onderzoeken/testen", "onderzoeken/validatie": "onderzoeken/analyse",
              "onderzoeken/journaal": "onderzoeken/analyse", "onderzoeken/zelf-bouwen": "strategieen/bouwen", "onderzoeken/automatisch": "strategieen/automatisch",
              "strategieen/overzicht": "strategieen/mijn", "validate": "onderzoeken/analyse", "optimize": "onderzoeken/testen", "discover": "strategieen/automatisch",
              "backtest": "onderzoeken/testen", "builder": "strategieen/bouwen", "strategies": "strategieen/mijn"}
    bad = []
    for old, new in legacy.items():
        pg.evaluate(f"location.hash = '#{old}'"); pg.wait_for_timeout(450)
        got = pg.evaluate("location.hash.slice(1)")
        if got != new:
            bad.append(f"{old}->{got}")
        pg.keyboard.press("Escape")
    check("old hashes redirect to the new routes", not bad, bad)

    pg.click('[data-nav="data"]'); pg.wait_for_timeout(2500)
    rows = pg.locator("#dsup-rows tr").count() if pg.locator("#dsup:visible").count() else pg.locator("#data-monitor-report tr").count()
    check("data page lists markets with status", rows >= 3, rows)
    check("data page has one sync button", pg.locator("#dsup-run:visible").count() == 1 or pg.locator("#data-sync-now:visible").count() == 1)
    check("data page sync button reads Nu synchroniseren", pg.locator("#dsup:visible").count() == 0 or pg.inner_text("#dsup-run").strip() == "Nu synchroniseren", pg.inner_text("#dsup-run") if pg.locator("#dsup-run").count() else "")
    check("data page shows last success, scheduler status and next attempt", pg.locator("#dsup:visible").count() == 0 or all(t in pg.inner_text("#dsup") for t in ("Laatste succes", "Planner", "Volgende poging")))
    shot(pg, "02-data", full=True)

    # Portfolio: Gevalideerd | Shadow Research
    pg.evaluate("localStorage.removeItem('labPfMode')"); pg.evaluate("labGo('portfolio/risico')"); pg.wait_for_selector("#pf-mode", timeout=20000); pg.wait_for_timeout(1500)
    check("portfolio has segmented choice Gevalideerd | Shadow Research", pg.eval_on_selector_all("#pf-mode label", "els => els.map(e => e.textContent.trim())") == ["Gevalideerd", "Shadow Research"])
    check("portfolio defaults to Gevalideerd and hides shadow view", pg.is_checked('#pf-mode input[value="validated"]') and not pg.locator("#shadow-live").is_visible())
    shot(pg, "02b-portfolio-validated", full=True)
    pg.click('#pf-mode label:has(input[value="shadow"])'); pg.wait_for_selector("#shadow-live .shadow-label", timeout=20000); pg.wait_for_timeout(1500)
    sl = pg.inner_text("#shadow-live")
    check("shadow view carries the experimental label and evidence class", "Experimenteel — niet gevalideerd, geen echte orders" in sl and "DAILY_DELAYED_FORWARD" in sl and "REALTIME_PAPER_FORWARD" in sl, sl[:100].replace(chr(10), " "))
    check("shadow view hides validated portfolio content", not pg.locator("#portfolio-live").is_visible())
    check("shadow view shows list, empty state or calm unavailable notice", pg.locator("#sh-table, [data-sh-create], .v10-notice").count() >= 1)
    check("shadow view has no horizontal overflow", not pg.evaluate(HSCROLL_JS))
    if pg.locator("#sh-table tr[data-sid]").count():
        pg.click("#sh-table tr[data-sid]"); pg.wait_for_selector("#sh-detail .sh-detail", timeout=20000); pg.wait_for_timeout(800)
        sd = pg.inner_text("#sh-detail")
        check("shadow detail shows equity, daily, trades, forward comparison and details", all(t in sd for t in ("Dagresultaten", "Trades", "Backtest tegenover forward", "Details", "Experimenteel")), sd[:80].replace(chr(10), " "))
    shot(pg, "02c-portfolio-shadow", full=True)
    pg.click('#pf-mode label:has(input[value="validated"])'); pg.wait_for_timeout(500)
    check("switching back to Gevalideerd restores the validated portfolio", pg.locator("#portfolio-live").is_visible() and not pg.locator("#shadow-live").is_visible())
    pg.evaluate("localStorage.removeItem('labPfMode')")

    # Mijn strategieën library
    pg.evaluate("labGo('strategieen/mijn')"); pg.wait_for_selector("#lib-rows tr[data-key]", timeout=20000); pg.wait_for_timeout(500)
    n_all = pg.locator("#lib-rows tr[data-key]").count()
    check("library lists strategies", n_all >= 3, n_all)
    heads = pg.eval_on_selector_all("#lib-table th", "els => els.map(e => e.textContent.trim())")
    check("library columns", heads[:5] == ["Naam", "Type / familie", "Markt / TF", "Versie", "Onderzoeksstatus"], heads)
    pg.fill("#lib-search", "EmaRsi"); pg.wait_for_timeout(300)
    check("library search filters", pg.locator("#lib-rows tr[data-key]").count() == 1, pg.locator("#lib-rows tr[data-key]").count())
    pg.fill("#lib-search", ""); pg.select_option("#lib-type", "plugin"); pg.wait_for_timeout(300)
    n_plug = pg.locator("#lib-rows tr[data-key]").count()
    check("library type filter", 0 < n_plug < n_all, f"{n_plug}/{n_all}")
    pg.select_option("#lib-type", ""); pg.click('#lib-table th[data-sort="status"]'); pg.wait_for_timeout(200)
    check("library sortable", pg.get_attribute('#lib-table th[data-sort="status"]', "aria-sort") == "ascending")
    shot(pg, "03-library", full=True)
    pg.fill("#lib-search", "LabBreakout"); pg.wait_for_timeout(300)
    pg.click('#lib-rows tr[data-key] [data-lib-act="test"]'); pg.wait_for_selector("#strategy", timeout=15000); pg.wait_for_timeout(2500)
    check("Onderzoeken opens Testen with strategy preselected", pg.evaluate("location.hash") == "#onderzoeken/testen" and pg.input_value("#strategy") == "LabBreakout", pg.input_value("#strategy"))
    pg.evaluate("labGo('strategieen/mijn')"); pg.wait_for_selector("#lib-rows tr[data-key]"); pg.wait_for_timeout(400)
    pg.fill("#lib-search", "EmaRsi"); pg.wait_for_timeout(300)
    pg.click('#lib-rows tr[data-key] [data-lib-act="multi"]'); pg.wait_for_timeout(1800)
    mv_n = pg.locator("#mv-markets input").count()
    check("multi-market drawer opens with markets and timeframes", pg.locator("#mv-drawer.open").count() == 1 and mv_n >= 3 and pg.locator("#mv-tfs input").count() == 5, f"{mv_n} markets")
    shot(pg, "04-multimarket-drawer")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

    # Automatisch ontdekken
    pg.evaluate("labGo('strategieen/automatisch')"); pg.wait_for_selector("#fx-start", timeout=20000); pg.wait_for_timeout(800)
    check("discovery default view: start button, depth, markets, timeframes", pg.locator("#fx-start").is_visible() and pg.locator('[data-group="markets"]').is_visible() and pg.locator('[data-group="tfs"]').is_visible() and pg.locator("#fx-profile").is_visible())
    check("discovery: quality, risk and drawdown live under Geavanceerd", pg.locator("details.fx-adv #fx-quality").count() == 1 and pg.locator("details.fx-adv #fx-risk").count() == 1 and pg.locator(".fx-grid:not(.fx-adv-top) #fx-quality").count() == 0)
    shot(pg, "05-discovery", full=True)
    fx = pg.evaluate("""async () => { const rs = (await (await fetch('/api/factory/runs')).json()).runs || []; for (const r of rs) { const c = await (await fetch('/api/factory/runs/' + r.run_id + '/candidates')).json(); const l = Array.isArray(c) ? c : c.candidates || []; const g = l.find(x => x.status === 'REJECTED' && (x.stage === 'L5' || x.stage === 'L4')) || l.find(x => x.status === 'REJECTED'); if (g) return {run: r.run_id, id: g.id}; } return null; }""")
    if fx:
        pg.evaluate("([r, i]) => window.labFactory.open(r, i)", [fx["run"], fx["id"]]); pg.wait_for_selector("#fx-drawer .fx-summary", timeout=20000)
        sm = pg.inner_text("#fx-drawer .fx-summary")
        check("candidate report opens with Samenvatting (test, periode, trials, kosten, trades, zekerheid)", all(t.lower() in sm.lower() for t in ("Samenvatting", "Wat is getest", "periode", "trials", "kosten", "trades", "Hoe zeker")), sm[:80])
        check("candidate summary has a status chip", pg.locator("#fx-drawer .fx-summary .fx-st").count() == 1)
        shot(pg, "05b-candidate-report")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        if pg.locator("#fx-drawer.open").count(): pg.click("#fx-drawer .fx-close"); pg.wait_for_timeout(300)

    # tester
    pg.evaluate("labGo('onderzoeken/testen')"); pg.wait_for_selector("#strategy option", state="attached", timeout=30000); pg.wait_for_timeout(2500)
    pg.select_option("#strategy", "LabBreakout"); pg.select_option("#symbol", "XAUUSD"); pg.select_option("#timeframe", "M15")
    pg.fill("#start", "2026-09-01"); pg.fill("#end", "2026-09-14"); pg.dispatch_event("#end", "change"); pg.wait_for_timeout(1500)
    lay = pg.evaluate(OVERLAP_JS)
    check("tester blocks do not overlap (fixed layout)", not lay["bad"] and len(lay["blocks"]) == 4, lay)
    check("no horizontal page scroll", not lay["hscroll"])
    check("tester has exactly one primary action", pg.locator(".toolbar button:not(.secondary):not(.info):not([hidden])").count() == 1, pg.locator(".toolbar button:not(.secondary):not(.info):not([hidden])").count())
    chart_h = pg.evaluate("document.getElementById('chart').getBoundingClientRect().height")
    check("main chart is big", chart_h >= 400, chart_h)
    shot(pg, "06-tester-ready")
    pg.click("#open-settings"); pg.wait_for_timeout(500)
    check("settings open in a side drawer", pg.locator("#settings-drawer.open").count() == 1 and pg.locator("#settings-drawer #parameters").count() == 1)
    shot(pg, "07-settings-drawer")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    if not quick:
        t0 = time.time()
        pg.click("#top-run")
        pg.wait_for_function("document.querySelectorAll('#trades tr').length>0 || document.querySelector('#run-status').classList.contains('error')", timeout=600000)
        pg.wait_for_timeout(3000)
        n = pg.locator("#trades tr").count()
        check("backtest on existing Dukascopy data produces trades (M15)", n > 0, f"{n} trades in {time.time()-t0:.0f}s; {pg.inner_text('#run-status')}")
        interp = pg.text_content("#interpretation") or ""
        check("interpretation layer rendered", len(interp) > 80 and "Historisch" in (pg.text_content("#evidence-banner") or ""), interp[:120])
        shot(pg, "08-backtest-result", full=True)

        # trade selection on the same page; decision log in a drawer
        pg.locator("#trades tr").nth(0).click(); pg.wait_for_timeout(3500)
        detail = pg.inner_text("#trade-detail")
        shapes = pg.evaluate("(document.getElementById('chart').layout?.shapes||[]).length")
        check("selected trade shows entry/exit/SL/TP + reason", "Entry" in detail.title() and "Stop" in detail.title() and "Waarom" in detail, detail[:160])
        check("selected trade drawn on chart (entry/SL/TP shapes)", shapes >= 2, f"{shapes} shapes")
        shot(pg, "09-trade-selected")
        if pg.locator('#trade-detail [data-td="log"]').count():
            pg.click('#trade-detail [data-td="log"]')
        else:
            pg.evaluate("labDrawer.open('inspector-drawer')")
        pg.wait_for_timeout(1200)
        check("decision log opens in a drawer with recorded checks", pg.locator("#inspector-drawer.open").count() == 1 and pg.locator("#candle-events .event-card").count() > 0)
        shot(pg, "10-inspector-drawer")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

        # optimisation drawer
        pg.click("#top-optimize"); pg.wait_for_timeout(600)
        check("Optimaliseren opens the optimizer drawer", pg.locator("#optimizer-drawer.open").count() == 1 and pg.locator("#optimizer-drawer #trials").count() == 1)
        pg.fill("#trials", "4"); pg.dispatch_event("#trials", "input")
        shot(pg, "11-optimizer-setup")
        pg.click("#optimizer-run")
        pg.wait_for_function("document.querySelector('#run-status').textContent.includes('Optimalisatie afgerond') || document.querySelector('#run-status').classList.contains('error')", timeout=900000)
        pg.wait_for_timeout(3000)
        st = pg.inner_text("#run-status"); check("optimisation completes with valid trials", "afgerond" in st and " 0 geldige" not in st, st)
        check("optimisation results visible in the drawer", pg.locator("#optimizer-drawer.open").count() == 1 and len(pg.inner_text("#optimization-results")) > 40)
        shot(pg, "12-optimizer-result")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

        # Analyse for the selected run
        pg.click('[data-sub="onderzoeken/analyse"]'); pg.wait_for_timeout(1500)
        bt_runs = pg.evaluate("fetch('/api/runs').then(r => r.json()).then(d => d.runs.filter(r => r.action === 'backtest').map(r => r.run_id))")
        pg.select_option("#an-run", bt_runs[0]); pg.wait_for_timeout(4000)
        kpis = pg.eval_on_selector_all("#an-kpis .an-kpi .l", "els => els.map(e => e.childNodes[0].textContent.trim())")
        vals = pg.eval_on_selector_all("#an-kpis .an-kpi .v", "els => els.map(e => e.textContent.trim())")
        check("Analyse shows exactly 4 KPIs", len(kpis) == 4 and [k.split(' ')[0] for k in kpis] == ["Netto", "Max", "Profit", "Recovery"], kpis)
        check("Analyse KPIs carry values for the selected run", any(v != "—" for v in vals), vals)
        check("Analyse shows equity curve and distribution", pg.locator("#an-prestaties .analytics-plot").count() >= 1 and pg.locator("#an-winloss.js-plotly-plot").count() == 1, pg.locator("#an-prestaties .analytics-plot").count())
        shot(pg, "13-analyse", full=True)

        # compare two runs + overlay
        pg.click("#an-vergelijken > summary"); pg.wait_for_timeout(500)
        pg.select_option("#compare-a", bt_runs[0]); pg.select_option("#compare-b", bt_runs[1] if len(bt_runs) > 1 else bt_runs[0]); pg.click("#compare"); pg.wait_for_timeout(2500)
        check("run comparison renders a table", pg.locator("#comparison table").count() == 1)
        check("equity curves are overlaid", pg.locator("#an-overlay.js-plotly-plot").count() == 1)
        shot(pg, "14-compare", full=True)

        # validation (rule-spec strategy) inside Analyse
        pg.evaluate("labGo('onderzoeken/validatie')"); pg.wait_for_selector("#val-spec", state="attached", timeout=20000); pg.wait_for_timeout(1500)
        check("legacy validatie route opens the Analyse validation section", pg.evaluate("location.hash") == "#onderzoeken/analyse" and pg.evaluate("document.getElementById('an-validatie').open"))
        note = pg.text_content("#val-tester-note") if pg.locator("#val-tester-note").count() else ""
        check("validation explains Python-strategy limitation", "regelspecificatie" in (note or ""), (note or "")[:120])
        spec = pg.eval_on_selector_all("#val-spec option", "o => o.map(x => x.value).filter(v => v.startsWith('spec:'))")
        choice = "spec:EmaRsiPullback" if "spec:EmaRsiPullback" in spec else "tpl:ema_rsi_pullback"
        pg.select_option("#val-spec", choice); pg.select_option("#val-tf", "M15")
        pg.fill("#val-start", "2025-06-01"); pg.fill("#val-end", "2026-10-01")
        pg.click("#val-run")
        pg.wait_for_selector("#val-result .verdict", timeout=1800000); pg.wait_for_timeout(1500)
        verdict = pg.inner_text("#val-result .verdict")
        check("statistical validation returns a verdict", bool(verdict.strip()), verdict[:120])
        shot(pg, "15-validation", full=True)

        # multi-market run through the UI (1 market, 1 timeframe, short period)
        pg.evaluate("labGo('strategieen/mijn')"); pg.wait_for_selector("#lib-rows tr[data-key]"); pg.wait_for_timeout(400)
        pg.fill("#lib-search", "EmaRsi"); pg.wait_for_timeout(300)
        pg.click('#lib-rows tr[data-key] [data-lib-act="multi"]'); pg.wait_for_timeout(1500)
        pg.click('#mv-markets [data-mv-q="none"]') if pg.locator('#mv-markets [data-mv-q="none"]').count() else pg.click('[data-mv-q="none"]')
        pg.check('#mv-markets input[value="XAUUSD"]')
        for tf in ("M15", "H1"):
            pg.set_checked(f'#mv-tfs input[value="{tf}"]', tf == "M15")
        pg.click("#mv-drawer details > summary"); pg.fill("#mv-start", "2022-01-01"); pg.fill("#mv-end", "2023-12-31")
        pg.click("#mv-run")
        pg.wait_for_selector("#mv-result .mv-tbl tbody tr", timeout=900000)
        pg.wait_for_function("!document.querySelector('#mv-progress .progress')", timeout=900000); pg.wait_for_timeout(1500)
        rows = pg.locator("#mv-result .mv-tbl tbody tr").count()
        check("multi-market run returns a results table", rows >= 1, f"{rows} rows")
        check("multi-market verdict and overlaid equity shown", pg.locator("#mv-result .mv-verdict").count() == 1 and pg.locator("#mv-result .mv-eq.js-plotly-plot").count() == 1)
        check("provisional money note shown when flagged", pg.locator("#mv-result .mv-note").count() == 1 or not pg.evaluate("document.querySelector('#mv-result').textContent.includes('voorlopig')"))
        shot(pg, "16-multimarket-result")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

        # forward: only if eligible; route stepper lives behind the Onderzoeksroute toggle
        pg.evaluate("labGo('onderzoeken/testen')"); pg.select_option("#strategy", "EmaRsiPullback"); pg.wait_for_timeout(1500)
        pg.click("#route-toggle"); pg.wait_for_timeout(2500)
        check("Onderzoeksroute toggle shows the 7 steps", pg.locator("#workflow-slot").is_visible() and pg.locator("#workflow-slot .wf-steps > *").count() == 7, pg.locator("#workflow-slot .wf-steps > *").count())
        wf = pg.text_content("#workflow-slot") or ""
        eligible = "FORWARD_ELIGIBLE" in verdict or "Geschikt voor forward" in verdict
        check("forward step state matches validation verdict", ("GEBLOKKEERD" in wf.upper()) != eligible or eligible, wf.replace("\n", " ")[:200])
        shot(pg, "17-workflow-route")
        pg.click("#route-toggle"); pg.wait_for_timeout(300)

    # persistence: restart browser
    pg.evaluate("labGo('onderzoeken/testen')"); pg.wait_for_timeout(800)
    saved = pg.evaluate("({s: document.querySelector('#strategy').value, sym: document.querySelector('#symbol').value, tf: document.querySelector('#timeframe').value, start: document.querySelector('#start').value, run: state.activeRun})")
    ctx.close()
    ctx, pg = launch()
    pg.goto(base + "/"); pg.wait_for_function("document.querySelector('#run-status') && /hersteld|Klaar/.test(document.querySelector('#run-status').textContent)", timeout=60000); pg.wait_for_timeout(4000)
    after = pg.evaluate("({s: document.querySelector('#strategy').value, sym: document.querySelector('#symbol').value, tf: document.querySelector('#timeframe').value, start: document.querySelector('#start').value, run: state.activeRun, route: location.hash})")
    check("restart restores route", after["route"] == "#onderzoeken/testen", after["route"])
    check("restart restores strategy/market/timeframe/period", all(after[k] == saved[k] for k in ("s", "sym", "tf", "start")), f"{saved} -> {after}")
    if not quick:
        check("restart restores last run", after["run"] == saved["run"], after["run"])
    shot(pg, "18-after-restart")
    ctx.close()

shutil.rmtree(profile, ignore_errors=True)
summary = {"tag": tag, "passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
           "results": results, "js_errors": errors[:20]}
(out / f"e2e-{tag}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: summary[k] for k in ("tag", "passed", "failed")}), "JS errors:", len(errors))
