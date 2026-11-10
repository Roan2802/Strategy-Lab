from common import *
import sys, time
def snap(pg,name): pg.screenshot(path=OUT+name+'.png')
with sync_playwright() as p:
    b,pg,errs=open_page(p, 1366, 800)
    pg.on('console',lambda m: print('C:',m.text[:200]) if m.type=='error' else None)
    try: pg.click('text=Niet nu', timeout=1500)
    except Exception: pass
    pg.evaluate("labOnboarding.start({restart:true})"); pg.wait_for_timeout(600)
    snap(pg,'ob1_intro')
    def nxt(): pg.click('.wfo [data-act="next"]'); pg.wait_for_timeout(500)
    nxt()  # step 2
    pg.wait_for_selector('#wfo-save, .wfo-box.good', timeout=20000); snap(pg,'ob2_strategy_before')
    if pg.query_selector('#wfo-save'):
        pg.click('#wfo-save'); pg.wait_for_selector('.wfo-box.good', timeout=60000)
    print('strategy sel', pg.evaluate("document.getElementById('strategy').value"))
    snap(pg,'ob2_strategy_after'); nxt()
    pg.wait_for_selector('#wfo-apply', timeout=20000); pg.wait_for_timeout(800); snap(pg,'ob3_data')
    print('data', pg.evaluate("['symbol','timeframe','start','end'].map(i=>document.getElementById(i).value)")); nxt()
    pg.click('#wfo-run'); t=time.time()
    pg.wait_for_selector('.wfo-kv', timeout=400000); print('backtest s', int(time.time()-t)); pg.wait_for_timeout(1500); snap(pg,'ob4_backtest')
    print(pg.inner_text('.wfo-body')[:700]); nxt()
    pg.click('#wfo-run'); t=time.time(); pg.wait_for_timeout(2500); snap(pg,'ob5_optimize_running')
    pg.wait_for_selector('.wfo-box.warn', timeout=580000); print('opt s', int(time.time()-t)); pg.wait_for_timeout(1500); snap(pg,'ob5_optimize')
    print(pg.inner_text('.wfo-body')[:900]); nxt()
    pg.click('#wfo-run'); t=time.time(); pg.wait_for_timeout(4000); snap(pg,'ob6_validate_running')
    pg.wait_for_selector('.wfo-box.good, .wfo-box.bad, .wfo-box.warn', timeout=3000000); print('val s', int(time.time()-t)); pg.wait_for_timeout(1500); snap(pg,'ob6_validate')
    print(pg.inner_text('.wfo-body')[:1200]); nxt()
    pg.wait_for_timeout(2500); snap(pg,'ob7_forward'); print(pg.inner_text('.wfo-body')[:700]); nxt()
    snap(pg,'ob8_done'); print('done?',pg.evaluate("labOnboarding.isDone()"))
    pg.click('.wfo [data-act="next"]'); print('done after finish',pg.evaluate("labOnboarding.isDone()"))
    pg.wait_for_timeout(1500); pg.evaluate("labGo('onderzoeken/backtest')"); pg.wait_for_timeout(15000); snap(pg,'stepper_after_tour')
    print([x.replace('\n',' ') for x in pg.evaluate("[...document.querySelectorAll('.wf-step')].map(b=>b.innerText+' '+b.title)")])
    print('ERRS',errs); b.close()
