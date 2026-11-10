from common import *
with sync_playwright() as p:
    b,pg,errs=open_page(p)
    pg.on('console',lambda m: print('C:',m.text))
    r=pg.evaluate("""async()=>{const t=[];for(const u of ['/api/runs','/api/builder/specs','/api/plugins','/api/forward/portfolios','/api/data/meta?symbol=XAUUSD','/api/lifecycle/LabBreakout']){const s=performance.now();try{const r=await (window.labNet?labNet.fetch(u):fetch(u));t.push([u,r.status,Math.round(performance.now()-s)])}catch(e){t.push([u,String(e)])}}return t}""")
    print(r); b.close()
