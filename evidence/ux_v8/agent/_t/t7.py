from common import *
with sync_playwright() as p:
    b,pg,errs=open_page(p)
    pg.evaluate("(()=>{const s=document.getElementById('strategy');s.value='EmaRsiPullback';s.dispatchEvent(new Event('change',{bubbles:true}))})()")
    pg.wait_for_timeout(6000)
    print(pg.evaluate("[...document.querySelectorAll('.wf-step')].map(b=>b.dataset.state+' / '+b.title.slice(0,200))"))
    pg.click('.wf-step[data-i="5"]'); pg.wait_for_timeout(500); pg.screenshot(path=OUT+'stepper_validated.png')
    pg.click('.wf-step[data-i="6"]'); pg.wait_for_timeout(500); pg.screenshot(path=OUT+'stepper_forward_blocked.png')
    print(errs); b.close()
