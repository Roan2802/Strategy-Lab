"""UI check for the validation view. Usage: ui_check.py <mode: live|mock> [out_dir]
mock: intercepts /api/research/strategies, /api/validate and /api/jobs/* with contract-shaped data."""
import json, sys, time
from playwright.sync_api import sync_playwright

MODE = sys.argv[1] if len(sys.argv) > 1 else 'live'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'C:/AI/Jesse/Jesse-Audit/evidence/v9/ui'
URL = 'http://127.0.0.1:8768/'

STRATS = [
    {'kind': 'builder', 'id': 'b1', 'name': 'MijnBuilder', 'label_nl': 'MijnBuilder (regels)', 'params': {}, 'capabilities': dict(backtest=True, optimize=True, inspect=True, validate=True, walk_forward=True, stability=True, cost_stress_spread=True, forward=True), 'limitations_nl': []},
    {'kind': 'jesse', 'id': 'LabBreakout', 'name': 'LabBreakout', 'label_nl': 'LabBreakout', 'params': [{'name': 'lookback', 'label': 'Lookback', 'type': 'int', 'default': 20, 'min': 2, 'max': 200}], 'capabilities': dict(backtest=True, optimize=True, inspect=True, validate=True, walk_forward=True, stability=True, cost_stress_spread=False, forward=False), 'limitations_nl': ['Spreadscenario\'s zijn niet mogelijk: Jesse gebruikt een vaste fee.', 'Elke combinatie is een volledige Jesse-backtest en dus traag.']},
    {'kind': 'plugin', 'id': 'pl1', 'name': 'Plugin 1', 'label_nl': 'Plugin 1', 'params': [], 'capabilities': dict(backtest=True, optimize=False, inspect=False, validate=True, walk_forward=False, stability=False, cost_stress_spread=True, forward=False), 'limitations_nl': ['Geen zoekruimte: walk-forward en PBO zijn uitgeschakeld.']},
]
n = 60
RESULT = {
    'scorecard': {'verdict': 'NEEDS_MORE_DATA', 'checks': [
        {'name': 'Minimaal aantal trades', 'status': 'FAIL', 'value': 18, 'threshold': 30, 'text': 'Slechts 18 trades; minstens 30 nodig.'},
        {'name': 'Walk-forward efficiency', 'status': 'INSUFFICIENT', 'value': 0.31, 'threshold': 0.5, 'text': 'Te weinig vensters met voldoende trades.'},
        {'name': 'Kostenstress', 'status': 'PASS', 'value': 1.2, 'threshold': 1.0, 'text': 'Blijft winstgevend onder hogere kosten.'}]},
    'evidence': {
        'n_trades': 18, 'net_profit': 412.5, 'profit_factor': 1.34, 'max_drawdown_pct': 8.2,
        'cost_stress': {'scenarios': {'baseline': {'net_profit': 412.5, 'profit_factor': 1.34, 'n_trades': 18, 'degradation': {'net_profit_rel': 0}}, 'commission_x2': {'net_profit': 300, 'profit_factor': 1.2, 'n_trades': 18, 'degradation': {'net_profit_rel': -0.27}}}, 'unsupported': [{'name': 'spread_+25%', 'reason_nl': 'Jesse gebruikt een vaste fee; spread is niet te variëren.'}]},
        'walk_forward': {'mode': 'rolling', 'wfe': 0.31, 'windows': [{'train': ['2025-01-01', '2025-05-01'], 'test': ['2025-05-01', '2025-06-01'], 'best_params': {'lookback': 20}, 'train_metrics': {'net_profit': 120}, 'test_metrics': {'net_profit': 40, 'n_trades': 5}, 'status': 'OK'}]},
        'oos_equity': [{'time': f'2025-05-{d:02d}', 'equity': 10000 + d * 12} for d in range(1, 29)],
        'parameter_stability': {'frac_positive': 0.6, 'base_net_profit': 412.5, 'neighbours': [{'param': 'lookback', 'value': 18, 'net_profit': 300, 'n_trades': 17, 'rel_change': -0.27}]},
        'pbo_result': {'pbo': 0.42, 'status': 'PASS'},
        'monte_carlo': {'method': 'block_bootstrap', 'max_drawdown_pct': {'p5': 4, 'p50': 9, 'p95': 18}, 'risk_of_ruin': 0.02, 'equity_bands': {'p5': [10000 + i * 5 for i in range(n)], 'p50': [10000 + i * 9 for i in range(n)], 'p95': [10000 + i * 14 for i in range(n)]}},
        'drop_best': {'net_profit': 120.0, 'profit_factor': 1.1}, 'yearly': [{'year': 2025, 'net_profit': 300, 'n_trades': 12}, {'year': 2026, 'net_profit': 112.5, 'n_trades': 6}],
        'psr': 0.81, 'dsr': 0.44, 'min_trl': 55,
        'holdout': {'start': '2026-05-01', 'end': '2026-10-01', 'metrics': {'net_profit': 55, 'profit_factor': 1.1}, 'trades': 4, 'untouched': True, 'previous_exposures': 0},
        'trial_accounting': {'ledger_prior': 10, 'family_prior': 25, 'optimizer_trials': 40, 'in_run_configs': 13, 'total': 88}},
    'report_md': '# Rapport\n- punt', 'ledger_id': 'led-123',
    'config': {'strategy': {'kind': 'jesse', 'name': 'LabBreakout'}, 'symbol': 'XAUUSD', 'timeframe': 'M15', 'start': '2025-01-01', 'end': '2026-10-01', 'capital': 10000, 'holdout_percent': 20, 'walk_forward': {'enabled': True, 'mode': 'expanding', 'train_days': 90, 'test_days': 20, 'step_days': 20, 'max_combos': 8, 'min_trades': 5}, 'stability': {'enabled': True}, 'pbo': {'enabled': False, 'max_combos': 16}, 'monte_carlo': {'n': 500, 'method': 'bootstrap'}, 'params': {'lookback': 25}},
    'forward_gaps_nl': ['Er is nog geen onaangeroerde holdout.', 'Te weinig trades voor een betrouwbaar oordeel.'], 'n_trials': 88,
}


def main():
    errors = []
    calls = {'polls': 0}
    with sync_playwright() as p:
        b = p.chromium.launch(channel='chrome')
        ctx = b.new_context(viewport={'width': 1600, 'height': 900}, accept_downloads=True)
        ctx.grant_permissions(['clipboard-read', 'clipboard-write'], origin='http://127.0.0.1:8768')
        pg = ctx.new_page()
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg.on('console', lambda m: errors.append(m.text) if m.type == 'error' and 'Failed to load resource' not in m.text else None)
        if MODE == 'mock':
            pg.route('**/api/research/strategies', lambda r: r.fulfill(json=STRATS))

            def validate(r):
                r.fulfill(json={'job_id': 'job-mock-1'})
            pg.route('**/api/validate', validate)

            def job(r):
                calls['polls'] += 1
                if calls['polls'] < 4:
                    r.fulfill(json={'id': 'job-mock-1', 'status': 'running', 'progress': calls['polls'] * 0.25, 'message': f'Walk-forward venster {calls["polls"]}/4'})
                else:
                    r.fulfill(json={'id': 'job-mock-1', 'status': 'done', 'progress': 1, 'result': RESULT})
            pg.route('**/api/jobs/job-mock-1', job)
        pg.goto(URL + '#onderzoeken/validatie')
        pg.wait_for_selector('#val-spec', state='attached', timeout=20000)
        pg.wait_for_timeout(1200)
        pg.screenshot(path=f'{OUT}/val-{MODE}-1-form.png', full_page=False)
        print('legacy note:', pg.locator('#val-legacy-note').count(), 'options:', pg.eval_on_selector_all('#val-spec option', 'o => o.map(x=>x.value)')[:12])
        if MODE == 'mock':
            pg.select_option('#val-spec', 'plugin:pl1'); pg.wait_for_timeout(300)
            print('plugin wf disabled:', pg.eval_on_selector('#val-fs-wf', 'e => e.disabled'), pg.inner_text('#val-reason-wf'))
            pg.screenshot(path=f'{OUT}/val-mock-2-plugin-caps.png')
            pg.select_option('#val-spec', 'jesse:LabBreakout'); pg.wait_for_timeout(300)
            print('cost:', pg.inner_text('#val-cost')[:160])
            pg.screenshot(path=f'{OUT}/val-mock-3-jesse.png')
            pg.click('#val-run'); pg.wait_for_selector('#val-cancel:not([hidden])', timeout=5000); pg.wait_for_timeout(900)
            pg.screenshot(path=f'{OUT}/val-mock-4-running.png')
            print('localStorage job:', pg.evaluate("localStorage.getItem('labValJob')") is not None)
            pg.wait_for_selector('#val-result .verdict', timeout=20000); pg.wait_for_timeout(1500)
            pg.screenshot(path=f'{OUT}/val-mock-5-report-top.png')
            pg.screenshot(path=f'{OUT}/val-mock-6-report-full.png', full_page=True)
            secs = pg.eval_on_selector_all('#val-result [data-sec]', 'e => e.map(x => x.dataset.sec)')
            print('sections:', secs)
            print('plots:', pg.locator('#val-result .js-plotly-plot').count())
            pg.click('[data-cfg="load"]'); pg.wait_for_timeout(300)
            pg.fill('#val-cfg-text', json.dumps(RESULT['config'])); pg.click('#val-cfg-apply'); pg.wait_for_timeout(300)
            print('cfg msg:', pg.inner_text('#val-cfg-msg'), '| mode:', pg.input_value('#val-mode'), '| train:', pg.input_value('#val-train'), '| param:', pg.input_value('[data-vp="lookback"]'))
            with pg.expect_download() as d:
                pg.click('[data-cfg="download"]')
            print('download:', d.value.suggested_filename)
            # reload: resume from history
            pg.reload(); pg.wait_for_selector('#val-spec', state='attached', timeout=20000); pg.wait_for_timeout(2500)
            print('after reload verdict shown:', pg.locator('#val-result .verdict').count(), 'history opts:', pg.locator('#val-history option').count())
            pg.screenshot(path=f'{OUT}/val-mock-7-after-reload.png')
        print('JS errors:', errors)
        b.close()


main()
