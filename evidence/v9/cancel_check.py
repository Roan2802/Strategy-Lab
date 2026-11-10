from playwright.sync_api import sync_playwright
import time
with sync_playwright() as p:
    b=p.chromium.launch(channel='chrome'); pg=b.new_page(viewport={'width':1600,'height':900}); errs=[]
    pg.on('pageerror',lambda e:errs.append(str(e)))
    pg.add_init_script("""(()=>{const of=window.fetch.bind(window);let res;window.__mock=true;window.fetch=(u,o={})=>{const url=String(u);const m=(o.method||'GET').toUpperCase();
 if(window.__fail404&&m==='POST'&&/\/api\/runs$/.test(url))return Promise.resolve(new Response(JSON.stringify({detail:'Not Found'}),{status:404}));
 if(m==='POST'&&/\/api\/runs$/.test(url))return new Promise(r=>{res=r});
 if(m==='POST'&&/\/api\/runs\/cancel$/.test(url)){res&&res(new Response(JSON.stringify({detail:'Run geannuleerd'}),{status:500}));return Promise.resolve(new Response('{"ok":true}',{status:200}))}
 return of(u,o)}})()""")
    pg.goto('http://127.0.0.1:8768/#onderzoeken/backtest'); pg.wait_for_timeout(3000)
    pg.evaluate("document.querySelector('#tour-offer')?.remove()")
    pg.click('#top-run'); pg.wait_for_selector('#run-cancel'); pg.wait_for_timeout(2200)
    print(pg.inner_text('#run-status'), pg.is_disabled('#top-run'))
    pg.screenshot(path='ui/tester-running.png')
    pg.click('#run-cancel'); pg.wait_for_timeout(1500)
    print(pg.inner_text('#run-status'), pg.is_disabled('#top-run'))
    # error box
    pg.evaluate('window.__fail404=true')
    pg.click('#top-run'); pg.wait_for_timeout(1500); pg.screenshot(path='ui/tester-error.png',clip={'x':0,'y':0,'width':1600,'height':400})
    print(pg.inner_text('#run-status')); print(errs)
    # compare 3 runs
    pg.evaluate('window.__fail404=false')
    pg.click('[data-dock-tab="comparison"]'); 
    o=pg.eval_on_selector_all('#compare-a option','o=>o.map(x=>x.value).filter(Boolean)')
    pg.select_option('#compare-a',o[0]);pg.select_option('#compare-b',o[1]);pg.select_option('#compare-c',o[2]);pg.click('#compare');pg.wait_for_timeout(2500)
    print(pg.locator('#comparison table').count(), pg.locator('#comparison th').count())
    pg.locator('#comparison').scroll_into_view_if_needed(); pg.screenshot(path='ui/compare-3.png'); print(errs)
    b.close()
