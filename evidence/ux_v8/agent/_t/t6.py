from common import *
with sync_playwright() as p:
    b,pg,errs=open_page(p)
    pg.on('console',lambda m: print('C:',m.text[:300]))
    print(pg.evaluate("[...document.querySelectorAll('.wf-step')].map(b=>b.dataset.state+' / '+b.title.slice(0,90))"))
    pg.click('.wf-step[data-i="5"]'); pg.wait_for_timeout(500); pg.screenshot(path=OUT+'stepper_validated.png')
    pg.click('.wf-step[data-i="6"]'); pg.wait_for_timeout(500); pg.screenshot(path=OUT+'stepper_forward_blocked.png')
    pg.evaluate("labOnboarding.start({restart:true})"); pg.wait_for_timeout(300)
    pg.keyboard.press('Escape'); print('hidden after esc', pg.evaluate("document.querySelector('.wfo').hidden"), pg.evaluate("labOnboarding.isDone()"))
    print(errs); b.close()
