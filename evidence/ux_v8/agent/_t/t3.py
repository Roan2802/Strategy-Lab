from common import *
with sync_playwright() as p:
    b,pg,errs=open_page(p)
    pg.evaluate("document.getElementById('strategy').dispatchEvent(new Event('change',{bubbles:true}))"); pg.wait_for_timeout(14000)
    print(pg.evaluate("[...document.querySelectorAll('.wf-step')].map(b=>b.dataset.state+' '+b.title)"))
    print(errs); b.close()
