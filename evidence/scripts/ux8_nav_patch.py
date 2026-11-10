import sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()

def swap(start, end, new):
    global s
    a = s.index(start); b = s.index(end, a)
    s = s[:a] + new + s[b:]

swap('const NAV = [', 'const info = tip =>', '''const SECTIONS = [
  {id: 'home', label: 'Home', icon: 'dashboard', subs: [['overzicht', 'Overzicht', 'dashboard']]},
  {id: 'strategieen', label: 'Strategieën', icon: 'strategies', subs: [['overzicht', 'Overzicht', 'strategies'], ['bouwen', 'Bouwen', 'builder'], ['bibliotheek', 'Bibliotheek', 'library']]},
  {id: 'onderzoeken', label: 'Onderzoeken', icon: 'backtest', subs: [['backtest', 'Backtest', 'backtest'], ['grafiek', 'Grafiek', 'backtest'], ['trades', 'Trades', 'backtest'], ['inspector', 'Inspector', 'backtest'], ['optimalisatie', 'Optimalisatie', 'backtest'], ['validatie', 'Validatie', 'validate'], ['journaal', 'Journaal', 'backtest'], ['automatisch', 'Automatisch onderzoek', 'discover']]},
  {id: 'portfolio', label: 'Portfolio', icon: 'portfolio', subs: [['forward', 'Forwardtest', 'forward'], ['risico', 'Portfolio en risico', 'portfolio']]},
  {id: 'data', label: 'Data', icon: 'data', subs: [['overzicht', 'Overzicht', 'data']]},
];
const SUB_TIPS = {
  'onderzoeken/backtest': 'Strategie, markt en periode kiezen en de backtest uitvoeren.',
  'onderzoeken/grafiek': 'Grote grafiek met trades, entry, exit, stop loss en take profit.',
  'onderzoeken/trades': 'Alle trades van de geselecteerde run; klik een trade om hem in de grafiek te zien.',
  'onderzoeken/inspector': 'Beslislogboek: welke voorwaarden op een candle slaagden of faalden.',
  'onderzoeken/optimalisatie': 'Parameters zoeken; de laatste candles blijven ongezien als controle.',
  'onderzoeken/validatie': 'Walk-forward, holdout, Monte Carlo en kostenstress: probeer de strategie kapot te maken.',
  'onderzoeken/journaal': 'Run- en foutmeldingen en het researchlogboek met alle geteste varianten.',
  'onderzoeken/automatisch': 'Automatisch veel varianten op meerdere markten testen, met historische out-of-sample controle.',
  'strategieen/overzicht': 'Alle strategieën met hun status en bewijs.',
  'strategieen/bouwen': 'Regels samenstellen met keuzelijsten, zonder te programmeren.',
  'strategieen/bibliotheek': 'Researchdossiers als startpunt voor eigen varianten.',
  'portfolio/forward': 'Bevroren strategieën die alleen nieuwe dagen verwerken (vertraagd, niet realtime).',
  'portfolio/risico': 'Gedeeld kapitaal, open risico en waarschuwingen; ook losse backtests combineren.',
};
const LEGACY = {dashboard: 'home', strategies: 'strategieen/overzicht', builder: 'strategieen/bouwen', library: 'strategieen/bibliotheek', backtest: 'onderzoeken/backtest', optimize: 'onderzoeken/optimalisatie', validate: 'onderzoeken/validatie', discover: 'onderzoeken/automatisch', forward: 'portfolio/forward', portfolio: 'portfolio/risico', data: 'data'};
function resolveRoute(id) {
  id = String(id || '').replace(/^#/, '');
  if (LEGACY[id]) id = LEGACY[id];
  const [sec, sub] = id.split('/');
  const section = SECTIONS.find(s => s.id === sec) || SECTIONS[0];
  const entry = section.subs.find(x => x[0] === sub) || section.subs[0];
  return {section: section.id, sub: entry[0], view: entry[2], route: section.subs.length > 1 ? `${section.id}/${entry[0]}` : section.id};
}
''')

swap('function buildRail() {', 'function buildViews() {', '''function buildRail() {
  const rail = document.createElement('nav');
  rail.className = 'rail'; rail.setAttribute('aria-label', 'Hoofdmenu');
  rail.innerHTML = SECTIONS.map(s => `<button type="button" data-nav="${s.id}" title="${esc(s.label)}"><svg viewBox="0 0 24 24">${ICONS[s.icon]}</svg><span>${esc(s.label)}</span>${s.id === 'home' || s.id === 'portfolio' ? `<i class="rail-badge" data-badge="${s.id === 'home' ? 'dashboard' : 'portfolio'}" hidden></i>` : ''}</button>`).join('')
    + `<div class="rail-foot"><button type="button" id="rail-collapse" title="Zijbalk in- of uitklappen"><svg viewBox="0 0 24 24">${ICONS.collapse}</svg><span>Inklappen</span></button></div>`;
  document.body.insertBefore(rail, q('main'));
  rail.addEventListener('click', e => {
    const b = e.target.closest('[data-nav]'); if (!b) return;
    const last = localStorage.getItem(`labLastSub:${b.dataset.nav}`);
    go(last ? `${b.dataset.nav}/${last}` : b.dataset.nav);
  });
  q('#rail-collapse').onclick = () => {
    document.body.classList.toggle('rail-collapsed');
    localStorage.setItem('labRailCollapsed', document.body.classList.contains('rail-collapsed') ? '1' : '');
    window.dispatchEvent(new Event('resize'));
  };
  if (localStorage.getItem('labRailCollapsed')) document.body.classList.add('rail-collapsed');
}

function buildSubnav() {
  const bar = document.createElement('div');
  bar.className = 'subnav'; bar.setAttribute('role', 'tablist'); bar.setAttribute('aria-label', 'Onderdelen');
  bar.addEventListener('click', e => { const b = e.target.closest('[data-sub]'); if (b) go(b.dataset.sub); });
  const slot = document.createElement('div');
  slot.id = 'workflow-slot';
  q('main').prepend(bar, slot);
}
function renderSubnav(r) {
  const bar = q('.subnav'); const section = SECTIONS.find(s => s.id === r.section);
  bar.hidden = section.subs.length < 2;
  bar.innerHTML = `<span class="subnav-title">${esc(section.label)}</span>` + section.subs.map(([id, label]) => {
    const route = `${section.id}/${id}`;
    return `<button type="button" role="tab" data-sub="${route}" aria-selected="${id === r.sub}" ${SUB_TIPS[route] ? `data-tip="${esc(SUB_TIPS[route])}"` : ''}>${esc(label)}</button>`;
  }).join('');
  q('#workflow-slot').hidden = r.section !== 'onderzoeken';
}

''')

swap('function go(id, opts = {}) {', 'function body(id) {', '''const TESTER_FOCUS = {backtest: ['default', null], grafiek: ['grafiek', 'inspector'], trades: [null, 'trades'], inspector: ['grafiek', 'inspector'], optimalisatie: ['optimization', 'optimization'], journaal: [null, 'logs']};
function go(id, opts = {}) {
  const r = resolveRoute(id);
  const target = r.view;
  qa('.view').forEach(v => v.classList.toggle('active', v.dataset.view === target));
  qa('[data-nav]').forEach(b => b.toggleAttribute('aria-current', b.dataset.nav === r.section));
  renderSubnav(r);
  localStorage.setItem(`labLastSub:${r.section}`, r.sub);
  shell.view = r.route; shell.legacy = target; document.body.dataset.activeView = target; document.body.dataset.section = r.section;
  if (location.hash.slice(1) !== r.route) history.replaceState(null, '', `#${r.route}`);
  if (target === 'backtest') {
    const [profile, dock] = TESTER_FOCUS[r.sub] || [null, null];
    window.labTester?.focus?.(profile, dock, r.sub);
    setTimeout(() => window.dispatchEvent(new Event('resize')), 30);
  }
  document.dispatchEvent(new CustomEvent('lab:route', {detail: {section: r.section, sub: r.sub}}));
  const loader = LOADERS[target];
  if (loader) loader().catch(err => showError(target, err));
}
window.labGo = id => go(id);

''')

s = s.replace("buildRail(); buildViews(); initTips();", "buildRail(); buildViews(); buildSubnav(); initTips();")
s = s.replace("localStorage.getItem('labLastView') || 'dashboard';", "localStorage.getItem('labLastView') || 'home';")
s = s.replace("if (shell.view === 'dashboard' && !document.hidden)", "if (shell.legacy === 'dashboard' && !document.hidden)")
s = s.replace("laatste candle ${esc(shortDate(s.last_candle || s.watermark || s.last_utc))}",
              "laatste candle ${esc(shortDate(s.last_candle_at || s.last_candle || s.watermark || s.last_utc))}${s.pending_days ? ` · ${esc(s.pending_days)} dag(en) achter` : ''}")
s = s.replace('<b style="width:80px">${esc(s.symbol)}</b>', '<b class="feed-sym" title="${esc(s.symbol)}">${esc(MARKET_LABEL[s.symbol] || s.symbol)}</b>')
open(p, 'w', encoding='utf-8').write(s)
print('ok')
