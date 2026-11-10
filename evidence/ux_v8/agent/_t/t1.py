from playwright.sync_api import sync_playwright
OUT='C:/AI/Jesse/Jesse-Audit/evidence/ux_v8/agent/'
with sync_playwright() as p:
    b=p.chromium.launch(channel='chrome'); pg=b.new_page(viewport={'width':1366,'height':800})
    errs=[]; pg.on('console',lambda m: errs.append(m.text) if m.type=='error' else None); pg.on('pageerror',lambda e: errs.append(str(e)))
    pg.goto('http://localhost:8768/'); pg.wait_for_timeout(3000)
    print(pg.evaluate("[typeof window.labOnboarding, window.__labWorkflowLoaded, !!document.getElementById('workflow-slot'), !!document.querySelector('.wf')]"))
    print(pg.evaluate("document.getElementById('workflow-slot')?.outerHTML.slice(0,300)"))
    print(errs)
    b.close()
