import sys
sys.argv=['x','mock']
exec(open('ui_check.py').read().split("def main():")[0])
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(channel='chrome'); pg=b.new_context(viewport={'width':1600,'height':2600}).new_page()
    pg.route('**/api/research/strategies', lambda r: r.fulfill(json=STRATS))
    pg.route('**/api/validate', lambda r: r.fulfill(json={'job_id':'job-mock-1'}))
    pg.route('**/api/jobs/job-mock-1', lambda r: r.fulfill(json={'id':'job-mock-1','status':'done','progress':1,'result':RESULT}))
    pg.goto(URL+'#onderzoeken/validatie'); pg.wait_for_selector('#val-spec',state='attached'); pg.wait_for_timeout(1000)
    pg.select_option('#val-spec','jesse:LabBreakout'); pg.click('#val-run'); pg.wait_for_selector('#val-result .verdict'); pg.wait_for_timeout(1500)
    pg.evaluate("document.querySelector('#tour-offer')?.remove()")
    pg.locator('#val-result').screenshot(path='ui/val-mock-8-report.png')
    b.close()
