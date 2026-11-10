from common import *
with sync_playwright() as p:
    b,pg,errs=open_page(p)
    print(pg.evaluate("[...document.querySelectorAll('.wf-step')].map(b=>b.innerText)"), pg.evaluate("document.getElementById('workflow-slot').hidden"))
    pg.screenshot(path=OUT+'stepper_closed.png')
    for i in (5,6,1):
        pg.click(f'.wf-step[data-i="{i}"]'); pg.wait_for_timeout(600); pg.screenshot(path=OUT+f'stepper_step{i+1}.png')
    print(pg.evaluate("[document.getElementById('strategy').value, document.getElementById('symbol').value]"))
    print(errs); b.close()
