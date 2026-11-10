from playwright.sync_api import sync_playwright
import time
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome"); pg = b.new_page(viewport={"width": 1600, "height": 900})
    t0=time.time(); log=[]
    pg.on("request", lambda r: log.append((round(time.time()-t0,2),'>',r.url[22:90])))
    pg.on("response", lambda r: log.append((round(time.time()-t0,2),r.status,r.url[22:90])))
    pg.on("pageerror", lambda e: log.append(('ERR',str(e))))
    pg.goto("http://127.0.0.1:8768/#onderzoeken/backtest"); pg.wait_for_timeout(8000)
    for l in log: print(l)
    b.close()
