from playwright.sync_api import sync_playwright
OUT='C:/AI/Jesse/Jesse-Audit/evidence/ux_v8/agent/'
ST='C:/AI/Jesse/Jesse-Audit/strategy_lab/static/'
def open_page(p, w=1366, h=800):
    b=p.chromium.launch(channel='chrome'); pg=b.new_page(viewport={'width':w,'height':h})
    errs=[]; pg.on('console',lambda m: errs.append(m.text) if m.type=='error' and '404' not in m.text else None); pg.on('pageerror',lambda e: errs.append(str(e)))
    pg.goto('http://localhost:8768/'); pg.wait_for_timeout(2500)
    pg.add_style_tag(content=open(ST+'workflow.css',encoding='utf8').read())
    pg.add_script_tag(content=open(ST+'workflow.js',encoding='utf8').read())
    pg.evaluate("window.labGo('onderzoeken/backtest')"); pg.wait_for_timeout(15000)
    return b,pg,errs
