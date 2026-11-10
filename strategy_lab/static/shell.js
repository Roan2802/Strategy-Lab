/* Workflow shell for Dukascopy Strategy Lab: navigation rail + research views.
   The original Strategy Tester (app.js) stays untouched and lives in the "backtest" view. */
(() => {
'use strict';
const q = (sel, root = document) => root.querySelector(sel);
const qa = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (v, d = 2) => v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? '—' : Number(v).toLocaleString('nl-NL', {minimumFractionDigits: d, maximumFractionDigits: d});
const sign = v => Number(v) > 0 ? 'pos' : Number(v) < 0 ? 'neg' : '';
const pct = (v, d = 1) => v === null || v === undefined ? '—' : `${fmt(v, d)}%`;
const shortDate = v => v ? String(v).replace('T', ' ').slice(0, 16) : '—';

async function api(path, opts = {}) {
  const init = {...opts, headers: {'Content-Type': 'application/json', ...(opts.headers || {})}};
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const res = await (window.labNet ? labNet.fetch(path, init) : fetch(path, init));
  const text = await res.text();
  let data; try { data = text ? JSON.parse(text) : {}; } catch { data = {detail: text}; }
  if (!res.ok) {
    const d = data.detail;
    const err = new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ') : d ? JSON.stringify(d) : `HTTP ${res.status}`);
    err.status = res.status; throw err;
  }
  return data;
}

const ICONS = {
  dashboard: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z"/>',
  strategies: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  builder: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  backtest: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
  optimize: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  validate: '<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  discover: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  forward: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  portfolio: '<path d="M3 7h18v13H3z"/><path d="M8 7V4h8v3"/>',
  library: '<path d="M4 4h5v16H4zM10 4h5v16h-5z"/><path d="M16 5l4 1-3 14-4-1"/>',
  data: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  collapse: '<path d="M15 6l-6 6 6 6"/>'
};
const SECTIONS = [
  {id: 'home', label: 'Home', icon: 'dashboard', subs: [['overzicht', 'Overzicht', 'dashboard']]},
  {id: 'strategieen', label: 'Strategieën', icon: 'strategies', subs: [['mijn', 'Mijn strategieën', 'strategies'], ['bouwen', 'Zelf bouwen', 'builder'], ['automatisch', 'Automatisch ontdekken', 'discover'], ['bibliotheek', 'Bibliotheek', 'library']]},
  {id: 'onderzoeken', label: 'Onderzoeken', icon: 'backtest', subs: [['testen', 'Testen', 'backtest'], ['analyse', 'Analyse', 'analyse']]},
  {id: 'portfolio', label: 'Portfolio', icon: 'portfolio', subs: [['forward', 'Forwardtest', 'forward'], ['risico', 'Portfolio en risico', 'portfolio']]},
  {id: 'data', label: 'Data', icon: 'data', subs: [['overzicht', 'Overzicht', 'data']]},
];
const SUB_TIPS = {
  'onderzoeken/testen': 'Strategie, markt en periode kiezen, backtest uitvoeren en trades in de grafiek bekijken.',
  'onderzoeken/analyse': 'De geselecteerde run in detail: statistiek, robuustheid, validatie en vergelijking.',
  'strategieen/mijn': 'Al je strategieën op één plek, met onderzoeksstatus.',
  'strategieen/bouwen': 'Regels samenstellen met keuzelijsten, zonder te programmeren.',
  'strategieen/automatisch': 'De onderzoeksfabriek bedenkt, test en beoordeelt zelf strategieën; de eindperiode blijft afgeschermd.',
  'strategieen/bibliotheek': 'Researchdossiers als startpunt voor eigen varianten.',
  'portfolio/forward': 'Bevroren strategieën die alleen nieuwe dagen verwerken (vertraagd, niet realtime).',
  'portfolio/risico': 'Gedeeld kapitaal, open risico en waarschuwingen; ook losse backtests combineren.',
};
/* Old hashes keep working: [new route, optional action]. */
const LEGACY = {
  dashboard: ['home'], strategies: ['strategieen/mijn'], builder: ['strategieen/bouwen'], library: ['strategieen/bibliotheek'],
  backtest: ['onderzoeken/testen'], optimize: ['onderzoeken/testen', 'optimizer'], validate: ['onderzoeken/analyse', 'validatie'],
  discover: ['strategieen/automatisch'], forward: ['portfolio/forward'], portfolio: ['portfolio/risico'], data: ['data'],
  'strategieen/overzicht': ['strategieen/mijn'],
  'onderzoeken/backtest': ['onderzoeken/testen'], 'onderzoeken/grafiek': ['onderzoeken/testen'], 'onderzoeken/trades': ['onderzoeken/testen'],
  'onderzoeken/inspector': ['onderzoeken/testen', 'inspector'], 'onderzoeken/optimalisatie': ['onderzoeken/testen', 'optimizer'],
  'onderzoeken/validatie': ['onderzoeken/analyse', 'validatie'], 'onderzoeken/journaal': ['onderzoeken/analyse', 'details'],
  'onderzoeken/zelf-bouwen': ['strategieen/bouwen'], 'onderzoeken/automatisch': ['strategieen/automatisch'],
};
function resolveRoute(id) {
  id = String(id || '').replace(/^#/, '');
  let act = null;
  if (LEGACY[id]) [id, act] = LEGACY[id];
  const [sec, sub] = id.split('/');
  const section = SECTIONS.find(s => s.id === sec) || SECTIONS[0];
  const entry = section.subs.find(x => x[0] === sub) || section.subs[0];
  return {section: section.id, sub: entry[0], view: entry[2], act, route: section.subs.length > 1 ? `${section.id}/${entry[0]}` : section.id};
}
const info = tip => `<button class="info" type="button" aria-label="Uitleg" data-tip="${esc(tip)}">i</button>`;
const tipChip = (cls, label, tip) => `<span class="chip ${cls}" tabindex="0" data-tip="${esc(tip)}">${esc(label)}</span>`;
const GATES = [['A', 'Research', 'Backtest en kostencontrole slagen.'], ['B', 'Robuustheid', 'Walk-forward, holdout en Monte Carlo houden stand.'], ['C', 'Forward', 'Vertraagde forward test levert voldoende bewijs.'], ['D', 'Paper', 'Geschikt voor papertrading.'], ['E', 'Live-review', 'Menselijke beoordeling; er worden nooit automatisch live orders geplaatst.']];
const gateNorm = v => /PASS|OK|MET/i.test(v || '') ? 'PASS' : /FAIL/i.test(v || '') ? 'FAIL' : /INSUFF|PENDING|PARTIAL/i.test(v || '') ? 'INSUFFICIENT' : 'EMPTY';
function gateStates(gates) {
  const out = [null, null, null, null, null]; gates = gates || [];
  gates.forEach((g, i) => {
    const n = String(g.gate || g.name || '');
    const idx = /research|backtest|prototype/i.test(n) ? 0 : /robust|oos|valid/i.test(n) ? 1 : /forward/i.test(n) ? 2 : /paper/i.test(n) ? 3 : /live|review/i.test(n) ? 4 : (gates.length === 5 ? i : -1);
    if (idx >= 0 && !out[idx]) out[idx] = {status: g.status, text: g.explanation_nl};
  });
  return out;
}
function ladder(states) {
  const label = {PASS: 'geslaagd', FAIL: 'gefaald', INSUFFICIENT: 'onvoldoende bewijs', EMPTY: 'niet getest'};
  return `<span class="ladder" role="img" aria-label="Bewijsladder">${GATES.map(([k, n, d], i) => {
    const st = states?.[i]; const c = gateNorm(st?.status);
    return `<i class="seg ${c}" tabindex="0" data-tip="${esc([`${k} ${n}: ${label[c]}`, d, st?.text].filter(Boolean).join('\n'))}"></i>`;
  }).join('')}</span>`;
}
const researchLadder = status => ladder([{status: status === 'CANDIDATE' ? 'PASS' : status === 'REJECTED' ? 'FAIL' : ''}]);
const verdictLadder = v => ladder({
  REJECT: [{status: 'FAIL'}], NEEDS_MORE_DATA: [{status: 'INSUFFICIENT'}], RESEARCH_CANDIDATE: [{status: 'PASS'}, {status: 'INSUFFICIENT'}],
  OOS_PASSED: [{status: 'PASS'}, {status: 'PASS'}], FORWARD_ELIGIBLE: [{status: 'PASS'}, {status: 'PASS'}]}[v]);
const TITLES = {
  dashboard: ['Home', 'Dagelijkse stand van data, forwardtests, waarschuwingen en lopend onderzoek in één oogopslag.'],
  strategies: ['Mijn strategieën', 'Jesse-strategieën, zelfgebouwde regels, plugins en kandidaten uit de strategiefabriek op één plek. Een onderzoeksstatus verandert alleen op basis van vastgelegde controles; er worden nooit live orders geplaatst.'],
  analyse: ['Analyse', 'Alles over de geselecteerde run: rendement, risico, robuustheid en validatie. Kies bovenaan een andere run of vergelijk er meerdere.'],
  builder: ['Zelf bouwen', 'Stel regels samen met keuzelijsten. De gegenereerde Jesse-code gebruikt exact dezelfde regel-engine als de researchbacktest: wat je hier ziet is wat er wordt uitgevoerd.'],
  library: ['Researchbibliotheek', 'IQ Capital-dossiers als onderzoeksmateriaal. Graad A/B/C beschrijft hoe volledig de regels bekend zijn, niet of de strategie winstgevend is.'],
  discover: ['Automatisch ontdekken', 'Begrensde, automatische zoektocht naar hypotheses uit vaste bouwstenen. Iedere variant, ook iedere verliezer, wordt in het researchlogboek bewaard.'],
  forward: ['Forward test', 'Bevroren strategieën die alleen nieuwe, na registratie gepubliceerde Dukascopy-candles verwerken.'],
  portfolio: ['Portfolio', 'Gedeeld kapitaal, open risico en prestaties per strategie in de forward-portfolio\'s, plus bewakingswaarschuwingen. Dit is één gezamenlijk gesimuleerde portefeuille, niet het optellen van losse runs.'],
  data: ['Data', 'Status van de marktdata per symbool: laatste candle, achterstand en volgende poging. Alle tijden in UTC.'],
};
const BADGES = {
  strategies: ['info', 'Alleen menselijke review', 'LIVE_ELIGIBLE betekent uitsluitend "klaar voor menselijke beoordeling". Een overgang vereist bewijs uit een validatie- of forwardrecord.'],
  library: ['warn', 'Niet bewezen winstgevend', 'Dit zijn 45 researchdossiers, géén 45 bewezen winstgevende algoritmen. Originele videotranscripten zijn niet meegeleverd. Iedere eigen vertaling heet RESEARCH_VARIANT; toepassen op een ander instrument dan het origineel (bijv. NQ-futures naar XAUUSD) vereist een nieuwe, vastgelegde hypothese.'],
  forward: ['warn', 'Vertraagd, niet realtime', 'DAILY_DELAYED_FORWARD: de dagelijkse Dukascopy-update levert M1-data één dag later. Dit is een vertraagde forward-simulatie, geen bewijs van werkelijke orderuitvoering en geen realtime papertrading. Een achteraf berekende historische referentie telt nooit als forwardresultaat.'],
};

const shell = {view: null, cache: {}, timers: {}, routeOpen: localStorage.getItem('labRouteOpen') === '1'};

function buildRail() {
  const rail = document.createElement('nav');
  rail.className = 'rail'; rail.setAttribute('aria-label', 'Hoofdmenu');
  rail.innerHTML = SECTIONS.map(s => `<button type="button" data-nav="${s.id}" title="${esc(s.label)}"><svg viewBox="0 0 24 24">${ICONS[s.icon]}</svg><span>${esc(s.label)}</span>${s.id === 'home' || s.id === 'portfolio' ? `<i class="rail-badge" data-badge="${s.id === 'home' ? 'dashboard' : 'portfolio'}" hidden></i>` : ''}</button>`).join('')
    + `<div class="rail-foot"><button type="button" id="rail-collapse" title="Zijbalk in- of uitklappen"><svg viewBox="0 0 24 24">${ICONS.collapse}</svg><span>Inklappen</span></button></div>`;
  document.body.insertBefore(rail, q('main'));
  rail.addEventListener('click', e => {
    const b = e.target.closest('[data-nav]'); if (!b) return;
    const last = localStorage.getItem(`labLastSub:${b.dataset.nav}`);
    // a stored subtab from an older layout may now redirect to another section; then open the section itself
    const ok = last && resolveRoute(`${b.dataset.nav}/${last}`).section === b.dataset.nav;
    go(ok ? `${b.dataset.nav}/${last}` : b.dataset.nav);
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
  bar.addEventListener('click', e => {
    const b = e.target.closest('[data-sub]'); if (b) { go(b.dataset.sub); return; }
    if (e.target.closest('#route-toggle')) { shell.routeOpen = !shell.routeOpen; localStorage.setItem('labRouteOpen', shell.routeOpen ? '1' : ''); q('#workflow-slot').hidden = !shell.routeOpen; e.target.closest('#route-toggle').setAttribute('aria-pressed', String(shell.routeOpen)); }
  });
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
  const route = r.section === 'onderzoeken';
  if (route) bar.insertAdjacentHTML('beforeend', `<button type="button" class="route-toggle" id="route-toggle" aria-pressed="${!!shell.routeOpen}" data-tip="Toon of verberg de onderzoeksroute: de 7 stappen van strategie tot forwardtest.">Onderzoeksroute</button>`);
  q('#workflow-slot').hidden = !route || !shell.routeOpen;
}

function buildViews() {
  const main = q('main');
  const tester = document.createElement('div');
  tester.className = 'view'; tester.dataset.view = 'backtest';
  while (main.firstChild) tester.appendChild(main.firstChild);
  main.appendChild(tester);
  for (const id of Object.keys(TITLES)) {
    const v = document.createElement('section');
    v.className = 'view'; v.dataset.view = id;
    const [title, text] = TITLES[id]; const badge = BADGES[id];
    v.innerHTML = `<div class="view-head"><h2>${esc(title)}${info(text)}${badge ? tipChip(...badge) : ''}</h2><div class="view-actions"></div></div><div class="view-body"></div>`;
    main.appendChild(v);
  }
  hostDialog('forward-dialog', 'forward');
  hostDialog('data-monitor-dialog', 'data');
  const combine = document.getElementById('backtest-portfolio');
  if (combine) { combine.classList.add('card'); q('[data-view="portfolio"] .view-body').appendChild(combine); }
}

function hostDialog(dialogId, viewId) {
  const dialog = document.getElementById(dialogId); if (!dialog) return;
  const body = q(`[data-view="${viewId}"] .view-body`);
  const wrap = document.createElement('div');
  wrap.className = 'card hosted'; wrap.dataset.hosted = dialogId;
  while (dialog.firstChild) wrap.appendChild(dialog.firstChild);
  body.appendChild(wrap);
}

function go(id, opts = {}) {
  const r = resolveRoute(id);
  const target = r.view;
  qa('.view').forEach(v => v.classList.toggle('active', v.dataset.view === target));
  qa('[data-nav]').forEach(b => b.dataset.nav === r.section ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current'));
  renderSubnav(r);
  localStorage.setItem(`labLastSub:${r.section}`, r.sub);
  localStorage.setItem('labLastView', r.route);
  shell.view = r.route; shell.legacy = target; document.body.dataset.activeView = target; document.body.dataset.section = r.section;
  if (location.hash.slice(1) !== r.route) history.replaceState(null, '', `#${r.route}`);
  if (target === 'backtest') {
    window.labTester?.focus?.(r.act === 'optimizer' ? 'optimizer' : r.act === 'inspector' ? 'inspector' : null);
    setTimeout(() => window.dispatchEvent(new Event('resize')), 30);
  }
  document.dispatchEvent(new CustomEvent('lab:route', {detail: {section: r.section, sub: r.sub, act: r.act}}));
  const loader = LOADERS[target];
  if (loader) loader(r.act).catch(err => showError(target, err));
}
window.labGo = id => go(id);

function body(id) { return q(`[data-view="${id}"] .view-body`); }
function actions(id) { return q(`[data-view="${id}"] .view-actions`); }
function showError(id, err) {
  const b = body(id); if (!b) return;
  let box = q('.notice.bad[data-err]', b);
  if (!box) { box = document.createElement('div'); box.className = 'notice bad'; box.dataset.err = '1'; b.prepend(box); }
  const msg = err.message || String(err);
  box.classList.add('err-box');
  box.innerHTML = `<b>Fout</b><pre>${esc(msg)}</pre><div class="err-hint">${esc(errHint(msg))}</div>`;
}
const errHint = m => window.labErrorHint ? window.labErrorHint(m) : 'Controleer de invoer en probeer opnieuw.';
const errBox = (title, message) => `<div class="notice bad err-box"><b>${esc(title)}</b><pre>${esc(message)}</pre><div class="err-hint">${esc(errHint(message))}</div></div>`;
function clearError(id) { q('.notice.bad[data-err]', body(id))?.remove(); }

const feedChip = status => {
  const s = String(status || 'UNKNOWN').toUpperCase().replace(/_/g, ' ');
  const cls = /UP TO DATE|COMPLETE|OK/.test(s) ? 'good' : /DELAYED|INCOMPLETE|PENDING|BEHIND/.test(s) ? 'warn' : /ERROR|FAIL/.test(s) ? 'bad' : 'idle';
  const nl = {'UP TO DATE': 'ACTUEEL', DELAYED: 'ACHTER', ERROR: 'FOUT', 'MARKET CLOSED': 'MARKT DICHT', PENDING: 'WACHT', UNKNOWN: 'ONBEKEND', 'NOT CONFIGURED': 'NIET INGESTELD', INCOMPLETE: 'ONVOLLEDIG'}[s] || s;
  return `<span class="chip ${cls}" title="${esc(s)}">${esc(nl)}</span>`;
};
const lifecycleChip = s => {
  const v = String(s || 'IDEA');
  const cls = /FAILED|RETIRED/.test(v) ? 'bad' : /ELIGIBLE|VALIDATED|REVIEW/.test(v) ? 'good' : /FORWARD|OPTIMIZING|BACKTESTING/.test(v) ? 'info' : 'idle';
  return `<span class="chip ${cls}">${esc(v)}</span>`;
};
const verdictChip = v => {
  if (!v) return '<span class="chip idle">NIET GETEST</span>';
  const cls = v === 'REJECT' ? 'bad' : v === 'NEEDS_MORE_DATA' ? 'warn' : v === 'RESEARCH_CANDIDATE' ? 'info' : 'good';
  return `<span class="chip ${cls}">${esc(v)}</span>`;
};

/* ---------------- Dashboard ---------------- */
async function loadDashboard() {
  const b = body('dashboard'); clearError('dashboard');
  if (!b.dataset.ready) {
    b.dataset.ready = '1';
    b.innerHTML = `<div class="start-strip" id="dash-start">
        <button type="button" class="start-card primary" data-go="onderzoeken/testen"><b>Strategie onderzoeken</b><span>Kies strategie, markt en periode en test.</span></button>
        <button type="button" class="start-card" data-go="strategieen/bouwen"><b>Strategie bouwen</b><span>Regels samenstellen met keuzelijsten, zonder te programmeren.</span></button>
        <button type="button" class="start-card" data-go="strategieen/automatisch"><b>Automatisch zoeken</b><span>Veel varianten op meerdere markten testen, met historische out-of-sample controle.</span></button>
        <button type="button" class="start-card" data-onboard-start><b>Introductie</b><span>Leer het lab kennen met een echte mini-studie op je eigen data.</span></button>
      </div>
      <div class="data-line" id="dash-data-line" aria-live="polite"><b>Marktdata:</b> <span class="muted">controleren…</span></div>
      <div class="cards kpi" id="dash-kpi"></div>
      <div class="cards two">
        <div class="card"><h3>Kritieke fouten</h3><div id="dash-alerts"></div></div>
        <div class="card"><h3>Forward-portfolio's</h3><div id="dash-forward"></div></div>
        <div class="card"><h3>Lopend onderzoek</h3><div id="dash-jobs"></div></div>
        <div class="card"><h3>Laatste backtests</h3><div id="dash-runs"></div></div>
      </div>`;
    b.addEventListener('click', e => { if (e.target.closest('[data-onboard-start]')) { go('onderzoeken/testen'); window.labOnboarding?.start({restart: true}); return; } const t = e.target.closest('[data-go]'); if (t) go(t.dataset.go); });
  }
  const [monitor, forward, runs, alerts, jobs, ledger, factory] = await Promise.all([
    api('/api/data/monitor').catch(() => ({configured: false, symbols: []})),
    api('/api/forward/portfolios').catch(() => ({portfolios: []})),
    api('/api/runs').catch(() => ({runs: []})),
    api('/api/monitoring/alerts').catch(() => ({alerts: []})),
    api('/api/jobs').catch(() => ({jobs: []})),
    api('/api/ledger/summary').catch(() => ({})),
    api('/api/factory/runs').catch(() => ({runs: []})),
  ]);
  const ports = forward.portfolios || [];
  const equity = ports.reduce((a, p) => a + (Number(p.equity ?? p.balance) || 0), 0);
  const members = ports.reduce((a, p) => a + (Number(p.members ?? p.member_count) || 0), 0);
  const al = (alerts.alerts || []).filter(a => a.severity === 'critical');
  const live = x => /^(running|queued|paused|starting|pending)$/i.test(x.status || '');
  const jobList = (jobs.jobs || []).filter(live);
  const factoryRuns = (Array.isArray(factory) ? factory : factory.runs || []).filter(live);
  const researching = jobList.length + factoryRuns.length;
  const tile = (l, v, s) => `<div class="card"><div class="kpi-label">${esc(l)}</div><div class="kpi-value">${esc(v)}</div><div class="kpi-sub">${esc(s)}</div></div>`;
  q('#dash-kpi').innerHTML = [
    ['Lopend onderzoek', researching, researching ? `${factoryRuns.length} automatisch zoeken · ${jobList.length} taken` : 'niets bezig'],
    ['Actieve forwardstrategieën', members, `${ports.length} portfolio('s)`],
    ['Portfolio-equity', ports.length ? fmt(equity) : '—', ports.length ? (ports[0].currency || '') : 'nog geen portfolio'],
    ['Kritieke fouten', al.length, al.length ? 'zie hieronder' : 'geen'],
  ].map(a => tile(...a)).join('');
  const badge = q('[data-badge="dashboard"]'); if (badge) { badge.hidden = !al.length; badge.textContent = al.length; }
  window.labV10?.dataLine(q('#dash-data-line'));
  q('#dash-alerts').innerHTML = al.length
    ? `<div class="list">${al.slice(0, 8).map(a => `<div class="list-row"><span class="chip ${a.severity === 'critical' ? 'bad' : 'warn'}">${esc({critical: 'kritiek', warning: 'let op', info: 'info'}[a.severity] || a.severity || 'let op')}</span><span class="grow" title="${esc(a.message)}">${esc(a.message)}</span></div>`).join('')}</div>`
    : '<div class="empty">Geen kritieke fouten.</div>';
  q('#dash-forward').innerHTML = ports.length
    ? `<div class="list">${ports.map(p => `<div class="list-row"><b class="grow">${esc(p.name)}</b><span class="muted">${esc(p.last_day || 'nog niet verwerkt')}</span><span>${fmt(p.equity ?? p.balance)} ${esc(p.currency || '')}</span></div>`).join('')}</div>`
    : '<div class="empty">Nog geen forward-portfolio.<button class="secondary" data-go="forward">Forward test openen</button></div>';
  const researchRows = [...factoryRuns.map(r => ({t: 'Automatisch zoeken', m: `${r.phase_nl || ''}${Number.isFinite(Number(r.progress)) ? ` · ${Math.round(Number(r.progress) * 100)}%` : ''}`, s: r.status})), ...jobList.map(j => ({t: j.title || j.kind, m: '', s: j.status}))];
  q('#dash-jobs').innerHTML = researchRows.length
    ? `<div class="list">${researchRows.slice(0, 6).map(j => `<div class="list-row"><span class="grow">${esc(j.t)}</span><span class="muted">${esc(j.m)}</span><span class="chip info">${esc({running: 'bezig', queued: 'wacht', paused: 'gepauzeerd'}[String(j.s).toLowerCase()] || j.s)}</span></div>`).join('')}</div>`
    : '<div class="empty">Er loopt geen onderzoek.</div>';
  const rl = (runs.runs || []).slice(0, 6);
  q('#dash-runs').innerHTML = rl.length
    ? `<div class="scroll"><table class="tbl"><thead><tr><th>Strategie</th><th>Markt</th><th>Periode</th><th>Type</th></tr></thead><tbody>${rl.map(r => `<tr><td>${esc(r.strategy)}</td><td>${esc(r.symbol)} ${esc(r.timeframe)}</td><td>${esc(r.start_date)} – ${esc(r.end_date)}</td><td>${esc(r.action)}</td></tr>`).join('')}</tbody></table></div>`
    : '<div class="empty">Nog geen backtests.</div>';
}

/* ---------------- Strategies / lifecycle ---------------- */
async function openLifecycle(name, slot) {
      const h = await api(`/api/lifecycle/${encodeURIComponent(name)}`);
      const STATUSES = ['PROTOTYPE','BACKTESTING','OPTIMIZING','VALIDATION_FAILED','BACKTEST_VALIDATED','OOS_VALIDATED','FORWARD_TESTING','FORWARD_FAILED','PAPER_ELIGIBLE','LIVE_REVIEW_REQUIRED','LIVE_ELIGIBLE','RETIRED'];
      slot.innerHTML = `<div class="card" style="margin-top:14px"><h3><span>Status wijzigen · ${esc(name)} ${info('Promoties naar gevalideerde statussen vereisen een logboek-ID van een validatie met voldoende oordeel. LIVE_ELIGIBLE vereist bovendien een menselijke beoordelaar en plaatst nooit orders.')}</span><small>huidige status ${esc(h.status || '—')}</small></h3>
        <div class="form-grid"><label>Nieuwe status${sel(STATUSES.map(x => [x, x]), '', 'id="lc-to"')}</label><label>Reden<input id="lc-reason" maxlength="500" placeholder="Waarom deze overgang?"></label><label>Bewijs (logboek-ID)<input id="lc-evidence" placeholder="ID uit Valideren"></label><label>Beoordelaar (alleen live-review)<input id="lc-reviewer"></label></div>
        <button id="lc-apply">Overgang vastleggen</button> <span id="lc-status" class="muted"></span>
        ${(h.gates || []).length ? `<table class="tbl" style="margin-top:12px"><thead><tr><th>Gate</th><th>Status</th><th>Toelichting</th></tr></thead><tbody>${h.gates.map(g => `<tr><td>${esc(g.gate || g.name)}</td><td>${feedChip(g.status)}</td><td style="white-space:normal">${esc(g.explanation_nl || '')}</td></tr>`).join('')}</tbody></table>` : ''}</div>
      <div class="card" style="margin-top:14px"><h3>Historie · ${esc(name)}</h3><table class="tbl"><thead><tr><th>Tijd (UTC)</th><th>Van</th><th>Naar</th><th>Reden</th><th>Bewijs</th></tr></thead><tbody>${(h.history || []).map(x => `<tr><td>${esc(shortDate(x.at))}</td><td>${esc(x.from_status || '—')}</td><td>${lifecycleChip(x.to_status)}</td><td>${esc(x.reason)}</td><td class="muted">${esc(x.evidence_ref || '')}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">Nog geen overgangen.</td></tr>'}</tbody></table></div>`;
      q('#lc-apply').onclick = async () => {
        try {
          await api(`/api/lifecycle/${encodeURIComponent(name)}/transition`, {method: 'POST', body: {to_status: q('#lc-to').value, reason: q('#lc-reason').value, evidence_ref: q('#lc-evidence').value || null, human_reviewer: q('#lc-reviewer').value || null}});
          q('#lc-status').textContent = 'Vastgelegd.'; window.labV10?.reloadLibrary?.();
        } catch (err) { q('#lc-status').textContent = err.message; }
      };
}
async function loadStrategies() { await window.labV10.loadLibrary(); }

/* ---------------- Python plugins ---------------- */
function openPlugin(id, mode) {
  const p = (shell.cache.plugins || []).find(x => x.id === id); if (!p) return;
  const panel = q('#plugin-panel'); const validate = mode === 'validate';
  const field = x => {
    const lab = esc(x.label || x.name);
    if (x.type === 'bool') return `<label class="check-row"><input type="checkbox" data-pp="${esc(x.name)}" data-type="bool" ${x.default ? 'checked' : ''}> ${lab}</label>`;
    if (x.type === 'int' || x.type === 'float') return `<label>${lab}<input type="number" data-pp="${esc(x.name)}" data-type="${esc(x.type)}" value="${esc(x.default ?? '')}" ${x.min !== undefined ? `min="${x.min}"` : ''} ${x.max !== undefined ? `max="${x.max}"` : ''} step="${x.type === 'int' ? 1 : 'any'}"></label>`;
    return `<label>${lab}<input type="text" data-pp="${esc(x.name)}" data-type="str" value="${esc(x.default ?? '')}"></label>`;
  };
  panel.innerHTML = `<div class="card" style="margin-top:16px"><h3><span>${esc(p.name)} <span class="chip info" style="margin-left:8px">Python-plugin</span>${(p.notes || []).length ? info(p.notes.join('\n')) : ''}</span><small>${validate ? 'Valideren' : 'Backtest'}</small></h3>
    <div class="form-grid"><label>Symbool${sel((p.symbols || ['XAUUSD']).map(x => [x, x]), (p.symbols || ['XAUUSD'])[0], 'id="plg-symbol"')}</label><label>Van<input type="date" id="plg-start" value="2024-01-01"></label><label>Tot<input type="date" id="plg-end" value="${todayIso()}"></label><label>Startkapitaal<input type="number" id="plg-capital" value="10000" min="1"></label>${validate ? '<label>Holdout (%)<input type="number" id="plg-holdout" value="20" min="0" max="50"></label>' : ''}</div>
    ${(p.params || []).length ? `<fieldset class="box"><legend>Parameters</legend><div class="form-grid">${p.params.map(field).join('')}</div></fieldset>` : ''}
    <div class="row-gap"><button id="plg-run" type="button">${validate ? 'Validatie starten' : 'Backtest uitvoeren'}</button><button id="plg-close" class="secondary" type="button">Sluiten</button><span id="plg-status" class="muted"></span></div>
    <div id="plg-job"></div><div id="plg-result"></div></div>`;
  panel.scrollIntoView({block: 'nearest'});
  q('#plg-close').onclick = () => { panel.innerHTML = ''; };
  q('#plg-run').onclick = async () => {
    const params = {}; qa('[data-pp]', panel).forEach(i => { params[i.dataset.pp] = i.dataset.type === 'bool' ? i.checked : i.dataset.type === 'str' ? i.value : Number(i.value); });
    const bodyData = {symbol: q('#plg-symbol').value, start: q('#plg-start').value, end: q('#plg-end').value, params, capital: Number(q('#plg-capital').value)};
    if (validate) bodyData.holdout_percent = Number(q('#plg-holdout').value);
    const run = q('#plg-run'); run.disabled = true; q('#plg-result').innerHTML = '';
    try {
      const {job_id} = await api(`/api/plugins/${encodeURIComponent(id)}/${validate ? 'validate' : 'backtest'}`, {method: 'POST', body: bodyData});
      const j = await pollJob(job_id, j => { if (q('#plg-job')) q('#plg-job').innerHTML = jobBar(j); });
      if (j.status !== 'done') { q('#plg-job').innerHTML += `<p class="status error">${esc(j.error || j.status)}</p>`; return; }
      q('#plg-job').innerHTML = '';
      const res = q('#plg-result');
      if (validate) renderValidation(j.result, res);
      else { renderBacktestResult(res, j.result); if ((j.result.notes || []).length) res.insertAdjacentHTML('afterbegin', `<div class="row-gap">${info(j.result.notes.join('\n'))}<span class="muted">Aannames van deze plugin</span></div>`); }
    } catch (err) { q('#plg-status').textContent = `Fout: ${err.message}`; }
    finally { run.disabled = false; }
  };
}

/* ---------------- Strategy Builder ---------------- */
const IND = ['ema','sma','rsi','atr','adx','macd','macd_signal','macd_hist','bb_upper','bb_mid','bb_lower','donchian_high','donchian_low','roc','vwap','realized_vol'];
const IND_NL = {ema:'EMA',sma:'SMA',rsi:'RSI',atr:'ATR',adx:'ADX',macd:'MACD-lijn',macd_signal:'MACD-signaal',macd_hist:'MACD-histogram',bb_upper:'Bollinger boven',bb_mid:'Bollinger midden',bb_lower:'Bollinger onder',donchian_high:'Donchian hoog',donchian_low:'Donchian laag',roc:'Rate of Change',vwap:'VWAP (dag)',realized_vol:'Gerealiseerde volatiliteit'};
const OPS = [['>','boven'],['<','onder'],['>=','≥'],['<=','≤'],['crosses_above','kruist omhoog'],['crosses_below','kruist omlaag']];
const PATTERNS = {inside_bar:'Inside bar',nr4:'NR4 (smalste van 4)',nr7:'NR7 (smalste van 7)',bullish_engulfing:'Bullish engulfing',bearish_engulfing:'Bearish engulfing',breakout_high:'Uitbraak boven hoogste high',breakout_low:'Uitbraak onder laagste low',liquidity_sweep_high:'Liquidity sweep boven high',liquidity_sweep_low:'Liquidity sweep onder low',fvg_bull:'Fair value gap (bullish)',fvg_bear:'Fair value gap (bearish)'};
const LEVELS = {prev_day_high:'Gisteren high',prev_day_low:'Gisteren low',prev_week_high:'Vorige week high',prev_week_low:'Vorige week low',session_open:'Sessie-open',opening_range_high:'Opening range high',opening_range_low:'Opening range low'};
const TFS = ['M1','M5','M15','M30','H1','H4','D1'];
const sel = (opts, value, attrs = '') => `<select ${attrs}>${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value) ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
const builder = {spec: null, templates: {}, timer: null, lastResult: null};

function operandHtml(op, path) {
  op = op || {value: 0};
  const kind = op.indicator ? 'indicator' : op.price ? 'price' : op.level ? 'level' : 'value';
  let inner = '';
  const pv = v => (v && typeof v === 'object' && v.param) ? `@${v.param}` : (v ?? '');
  if (kind === 'indicator') inner = sel(IND.map(i => [i, IND_NL[i]]), op.indicator, `data-f="indicator"`) + `<input type="text" data-f="period" value="${esc(pv(op.period ?? 14))}" title="Periode (getal of @parameter)" style="flex:0 1 64px">` + sel([['', 'zelfde TF'], ...TFS.map(t => [t, t])], op.timeframe || '', `data-f="timeframe" title="Hogere timeframe (alleen gesloten candles)"`) + `<input type="number" data-f="shift" min="0" value="${op.shift || 0}" title="Candles terug">`;
  if (kind === 'price') inner = sel([['close','Close'],['open','Open'],['high','High'],['low','Low']], op.price, `data-f="price"`) + `<input type="number" data-f="shift" min="0" value="${op.shift || 0}" title="Candles terug">`;
  if (kind === 'value') inner = `<input type="text" data-f="value" value="${esc(pv(op.value ?? 0))}" title="Getal of @parameter">`;
  if (kind === 'level') inner = sel(Object.entries(LEVELS), op.level, `data-f="level"`) + (String(op.level || '').startsWith('opening_range') ? `<input type="number" data-f="minutes" min="1" value="${op.minutes || 30}" title="Minuten">` : '');
  return `<div class="operand" data-path="${path}">${sel([['indicator','Indicator'],['price','Prijs'],['value','Waarde'],['level','Niveau']], kind, `data-f="kind"`)}${inner}</div>`;
}
function condHtml(c, path, depth = 0) {
  if (c && (c.all || c.any)) {
    const key = c.all ? 'all' : 'any';
    return `<div class="cond-group" data-path="${path}"><div class="cond-head">${sel([['all','ALLE voorwaarden (EN)'],['any','MINSTENS ÉÉN (OF)']], key, `data-f="groupop"`)}
      <button class="link-btn" data-add="compare">+ voorwaarde</button><button class="link-btn" data-add="pattern">+ patroon</button>${depth < 3 ? '<button class="link-btn" data-add="group">+ groep</button>' : ''}${depth ? '<button class="icon-btn" data-del title="Groep verwijderen">✕</button>' : ''}</div>
      ${c[key].map((x, i) => condHtml(x, `${path}.${key}.${i}`, depth + 1)).join('') || '<div class="muted" style="font-size:12px;padding:4px">Nog geen voorwaarden: geen signaal.</div>'}</div>`;
  }
  if (c && c.pattern) return `<div class="cond-row pattern" data-path="${path}">${sel(Object.entries(PATTERNS), c.pattern, `data-f="pattern"`)}<input type="number" min="1" data-f="lookback" value="${c.lookback || 20}" title="Lookback (candles)"><button class="icon-btn" data-del title="Verwijderen">✕</button></div>`;
  if (c && c.not) return `<div class="cond-group" data-path="${path}"><div class="cond-head">NIET <button class="icon-btn" data-del>✕</button></div>${condHtml(c.not, `${path}.not`, depth + 1)}</div>`;
  return `<div class="cond-row" data-path="${path}">${operandHtml(c.left, `${path}.left`)}${sel(OPS, c.op, `data-f="op"`)}${operandHtml(c.right, `${path}.right`)}<button class="icon-btn" data-del title="Verwijderen">✕</button></div>`;
}
const getPath = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
function setPath(obj, path, value) {
  const keys = path.split('.'); const last = keys.pop();
  const parent = keys.reduce((o, k) => o[k], obj);
  parent[last] = value;
}
function delPath(obj, path) {
  const keys = path.split('.'); const last = keys.pop();
  const parent = keys.reduce((o, k) => o[k], obj);
  if (Array.isArray(parent)) parent.splice(Number(last), 1); else delete parent[last];
}
const parseNum = v => { const s = String(v).trim(); if (s.startsWith('@')) return {param: s.slice(1)}; const n = Number(s.replace(',', '.')); return Number.isFinite(n) ? n : 0; };

function renderBuilderForm() {
  const s = builder.spec; const f = q('#bld-form'); if (!f || !s) return;
  const stop = s.stop || {type: 'atr', mult: 1.5, period: 14};
  const tgt = s.target || null;
  const dirs = s.direction === 'both' ? ['long', 'short'] : [s.direction || 'long'];
  s.entry = s.entry || {};
  for (const d of dirs) if (!s.entry[d] || !(s.entry[d].all || s.entry[d].any)) s.entry[d] = {all: s.entry[d] ? [s.entry[d]] : []};
  f.innerHTML = `
  <fieldset class="box"><legend>Basis</legend><div class="form-grid">
    <label>Naam<input data-k="name" value="${esc(s.name)}" maxlength="48"></label>
    <label>Symbool${sel((shell.cache.datasets || ['XAUUSD']).map(x => [x, x]), s.symbol, 'data-k="symbol"')}</label>
    <label>Timeframe${sel(TFS.map(t => [t, t]), s.timeframe, 'data-k="timeframe"')}</label>
    <label>Richting${sel([['long','Alleen long'],['short','Alleen short'],['both','Long en short']], s.direction, 'data-k="direction"')}</label>
  </div></fieldset>
  ${dirs.map(d => `<fieldset class="box"><legend>WANNEER ${d === 'long' ? 'LONG' : 'SHORT'} instappen</legend>${condHtml(s.entry[d], `entry.${d}`)}</fieldset>`).join('')}
  <fieldset class="box"><legend>Order, stop en target</legend><div class="form-grid">
    <label>Ordertype${sel([['market','Market (volgende open)'],['stop','Stop boven/onder candle'],['limit','Limit']], s.order?.type || 'market', 'data-k="order.type"')}</label>
    <label>Order-offset (× ATR)<input type="number" step="0.05" min="0" data-k="order.offset_atr" value="${s.order?.offset_atr ?? 0}"></label>
    <label>Order geldig (candles)<input type="number" min="1" data-k="order.expiry_bars" value="${s.order?.expiry_bars ?? 3}"></label>
    <label>Stop-type${sel([['atr','ATR-veelvoud'],['points','Vaste afstand'],['swing','Swing high/low']], stop.type, 'data-k="stop.type"')}</label>
    ${stop.type === 'atr' ? `<label>Stop (× ATR)<input type="number" step="0.1" min="0.1" data-k="stop.mult" value="${stop.mult ?? 1.5}"></label><label>ATR-periode<input type="number" min="2" data-k="stop.period" value="${stop.period ?? 14}"></label>` : stop.type === 'points' ? `<label>Stopafstand (prijs)<input type="number" step="any" min="0" data-k="stop.value" value="${stop.value ?? 1}"></label>` : `<label>Swing-lookback<input type="number" min="2" data-k="stop.lookback" value="${stop.lookback ?? 10}"></label>`}
    <label>Take profit${sel([['r_multiple','× risico (R)'],['atr','× ATR'],['none','Geen']], tgt ? tgt.type : 'none', 'data-k="target.type"')}</label>
    ${tgt ? `<label>Target-waarde<input type="number" step="0.1" min="0.1" data-k="${tgt.type === 'atr' ? 'target.mult' : 'target.value'}" value="${tgt.type === 'atr' ? (tgt.mult ?? 3) : (tgt.value ?? 2)}"></label>` : ''}
  </div></fieldset>
  <fieldset class="box"><legend>Risico en beheer</legend><div class="form-grid">
    <label>Risico per trade (% equity)<input type="number" step="0.05" min="0.01" max="10" data-k="risk.percent" value="${s.risk?.percent ?? 0.5}"></label>
    <label>Trailing stop vanaf (R, leeg = uit)<input type="number" step="0.1" min="0" data-k="trailing.activate_r" value="${s.trailing?.activate_r ?? ''}"></label>
    <label>Trailing-afstand (× ATR)<input type="number" step="0.1" min="0.1" data-k="trailing.atr_mult" value="${s.trailing?.atr_mult ?? 1}"></label>
    <label>Break-even vanaf (R, leeg = uit)<input type="number" step="0.1" min="0" data-k="break_even.activate_r" value="${s.break_even?.activate_r ?? ''}"></label>
    <label>Tijdsexit (candles, leeg = uit)<input type="number" min="1" data-k="time_exit_bars" value="${s.time_exit_bars ?? ''}"></label>
    <label>Cooldown na trade (candles)<input type="number" min="0" data-k="cooldown_bars" value="${s.cooldown_bars ?? 0}"></label>
    <label>Max trades per dag<input type="number" min="1" data-k="max_trades_per_day" value="${s.max_trades_per_day ?? ''}"></label>
  </div></fieldset>
  <fieldset class="box"><legend>Bijkopen bij winst ${info('Voegt alleen posities toe nadat een winstgevend niveau is bereikt, nooit om een verliezer bij te kopen. Het totale worstcase-risico blijft maximaal gelijk aan het initiële risico. Leeg = uit.')}</legend><div class="form-grid">
    <label>Max bijkopen (1–5, leeg = uit)<input type="number" min="1" max="5" step="1" data-k="scale_in.max_adds" value="${s.scale_in?.max_adds ?? ''}"></label>
    <label>Elke (R winst)<input type="number" min="0.1" step="0.1" data-k="scale_in.add_every_r" placeholder="1" value="${s.scale_in?.add_every_r ?? ''}"></label>
    <label>Grootte (fractie 0–1)<input type="number" min="0.05" max="1" step="0.05" data-k="scale_in.add_size_fraction" placeholder="0.5" value="${s.scale_in?.add_size_fraction ?? ''}"></label>
  </div></fieldset>
  <fieldset class="box"><legend>Sessiefilter ${info('Sessietijden zijn lokale wandklok-tijden; zomer- en wintertijd worden per datum correct omgerekend.')}</legend><div class="form-grid">
    <label>Sessie${sel([['','Geen (24 uur)'],['America/New_York|09:30|16:00','New York 09:30–16:00'],['Europe/London|08:00|16:30','Londen 08:00–16:30'],['Asia/Tokyo|09:00|15:00','Tokio 09:00–15:00'],['Europe/Berlin|09:00|17:30','Frankfurt 09:00–17:30']], s.session ? `${s.session.timezone}|${s.session.start}|${s.session.end}` : '', 'data-k="session"')}</label>
  </div></fieldset>`;
}

function readBuilderInput(el) {
  const s = builder.spec;
  if (el.dataset.k) {
    const k = el.dataset.k; const v = el.value;
    const numeric = el.type === 'number';
    if (k === 'session') { s.session = v ? (([tz, st, en]) => ({timezone: tz, start: st, end: en}))(v.split('|')) : null; return true; }
    if (k === 'target.type') { s.target = v === 'none' ? null : v === 'atr' ? {type: 'atr', mult: 3, period: 14} : {type: 'r_multiple', value: 2}; return true; }
    if (k === 'stop.type') { s.stop = v === 'atr' ? {type: 'atr', mult: 1.5, period: 14} : v === 'points' ? {type: 'points', value: 1} : {type: 'swing', lookback: 10}; return true; }
    if (k === 'direction') { s.direction = v; return true; }
    if (k.startsWith('trailing.') || k.startsWith('break_even.')) {
      const [root, sub] = k.split('.');
      if (sub === 'activate_r' && v === '') { s[root] = null; return true; }
      s[root] = s[root] || (root === 'trailing' ? {activate_r: 1, atr_mult: 1, period: 14} : {activate_r: 1});
      s[root][sub] = Number(v); return false;
    }
    if (k.startsWith('scale_in.')) {
      const sub_ = k.split('.')[1];
      if (sub_ === 'max_adds' && v === '') { s.scale_in = null; return false; }
      s.scale_in = s.scale_in || {max_adds: 1, add_every_r: 1, add_size_fraction: 0.5};
      if (v !== '') s.scale_in[sub_] = Number(v);
      return false;
    }
    if (['time_exit_bars', 'max_trades_per_day'].includes(k)) { s[k] = v === '' ? null : Number(v); return false; }
    const parts = k.split('.');
    if (parts.length === 2) { s[parts[0]] = s[parts[0]] || {}; s[parts[0]][parts[1]] = numeric ? Number(v) : v; }
    else s[k] = numeric ? Number(v) : v;
    return k === 'order.type';
  }
  const holder = el.closest('[data-path]'); if (!holder) return false;
  const path = holder.dataset.path; const f = el.dataset.f;
  if (f === 'groupop') { const g = getPath(s, path); const items = g.all || g.any || []; setPath(s, path, {[el.value]: items}); return true; }
  if (f === 'kind') {
    setPath(s, path, el.value === 'indicator' ? {indicator: 'ema', period: 20} : el.value === 'price' ? {price: 'close', shift: 0} : el.value === 'level' ? {level: 'prev_day_high'} : {value: 0});
    return true;
  }
  const obj = getPath(s, path);
  if (f === 'period' || f === 'value') obj[f] = parseNum(el.value);
  else if (f === 'timeframe') { if (el.value) obj.timeframe = el.value; else delete obj.timeframe; }
  else if (['shift', 'lookback', 'minutes'].includes(f)) obj[f] = Number(el.value) || 0;
  else obj[f] = el.value;
  return f === 'level';
}

function scheduleValidate() {
  clearTimeout(builder.timer);
  builder.timer = setTimeout(async () => {
    const spec = builder.spec;
    q('#bld-json') && document.activeElement !== q('#bld-json') && (q('#bld-json').value = JSON.stringify(spec, null, 2));
    try {
      const r = await api('/api/builder/validate', {method: 'POST', body: {spec}});
      q('#bld-errors').innerHTML = r.errors?.length ? `<div class="notice bad"><b>Nog niet geldig:</b><ul>${r.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div>` : '<div class="valid-line"><span class="chip good">Regels geldig</span></div>';
      q('#bld-rules').textContent = r.description || '';
      q('#bld-code').textContent = r.code || '(code verschijnt zodra de regels geldig zijn)';
      q('#bld-run').disabled = !!r.errors?.length;
      q('#bld-save').disabled = !!r.errors?.length;
    } catch (err) { q('#bld-errors').innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
  }, 280);
}

async function loadBuilder() {
  const b = body('builder'); clearError('builder');
  if (!shell.cache.datasets) shell.cache.datasets = (await api('/api/datasets')).datasets.filter(d => d.available).map(d => d.symbol);
  if (!b.dataset.ready) {
    const t = await api('/api/builder/templates');
    builder.templates = t.templates || {};
    const saved = (await api('/api/builder/specs').catch(() => ({specs: []}))).specs || [];
    b.dataset.ready = '1';
    actions('builder').innerHTML = `<select id="bld-template" style="width:auto"><option value="">Nieuw vanuit sjabloon…</option>${Object.keys(builder.templates).map(k => `<option value="tpl:${esc(k)}">${esc(builder.templates[k].name || k)}</option>`).join('')}${saved.length ? `<optgroup label="Opgeslagen">${saved.map(x => `<option value="spec:${esc(x.name)}">${esc(x.name)}</option>`).join('')}</optgroup>` : ''}</select><button id="bld-save" class="secondary">Opslaan als strategie</button><button id="bld-to-validate" class="secondary">Valideren →</button>`;
    b.innerHTML = `<div class="split"><div class="card"><div class="tabs" role="tablist"><button role="tab" aria-selected="true" data-tab="form">Eenvoudig</button><button role="tab" aria-selected="false" data-tab="json">Geavanceerd (JSON)</button></div>
      <div id="bld-form"></div><div id="bld-jsonpane" hidden><textarea id="bld-json" style="height:520px"></textarea><div class="row-gap"><button id="bld-json-apply" class="secondary">JSON toepassen</button>${info('Volledige regelspecificatie, inclusief geneste groepen, NIET-voorwaarden en optimaliseerbare parameters ({"param":"naam"}).')}</div></div></div>
      <div><div id="bld-errors"></div>
        <div class="card" style="margin-bottom:14px"><div class="tabs" role="tablist"><button role="tab" aria-selected="true" data-tab2="rules">Regels in gewone taal</button><button role="tab" aria-selected="false" data-tab2="code">Gegenereerde Jesse-code</button></div>
          <div id="bld-rules" class="rules-text"></div><pre id="bld-code" class="code" hidden></pre></div>
        <div class="card"><h3><span>Researchbacktest ${info('Dukascopy Bid/Ask, uitvoering op de volgende candle. Bij twijfel in één candle geldt de stop eerst.')}</span></h3>
          <div class="form-grid"><label>Van<input type="date" id="bld-start" value="2026-06-01"></label><label>Tot<input type="date" id="bld-end" value="2026-10-01"></label><label>Startkapitaal<input type="number" id="bld-capital" value="10000"></label><label>Commissie / lot<input type="number" id="bld-comm" value="0" step="any"></label></div>
          <div class="row-gap"><button id="bld-run">Backtest uitvoeren</button><span id="bld-status" class="muted"></span></div>
          <div id="bld-result"></div></div></div></div>`;
    b.addEventListener('input', e => { if (e.target.id === 'bld-json') return; const rerender = readBuilderInput(e.target); if (rerender && e.target.tagName === 'SELECT') renderBuilderForm(); scheduleValidate(); });
    b.addEventListener('change', e => { if (e.target.tagName === 'SELECT' && e.target.closest('#bld-form')) { renderBuilderForm(); } });
    b.addEventListener('click', e => {
      const add = e.target.closest('[data-add]'); const del = e.target.closest('[data-del]');
      if (add) { const g = getPath(builder.spec, add.closest('[data-path]').dataset.path); const arr = g.all || g.any;
        arr.push(add.dataset.add === 'pattern' ? {pattern: 'inside_bar', lookback: 20} : add.dataset.add === 'group' ? {any: []} : {left: {indicator: 'ema', period: 20}, op: '>', right: {indicator: 'ema', period: 50}});
        renderBuilderForm(); scheduleValidate(); }
      if (del) { delPath(builder.spec, del.closest('[data-path]').dataset.path); renderBuilderForm(); scheduleValidate(); }
      const tab = e.target.closest('[data-tab]'); if (tab) { qa('[data-tab]', b).forEach(x => x.setAttribute('aria-selected', x === tab)); q('#bld-form').hidden = tab.dataset.tab !== 'form'; q('#bld-jsonpane').hidden = tab.dataset.tab !== 'json'; if (tab.dataset.tab === 'form') renderBuilderForm(); else q('#bld-json').value = JSON.stringify(builder.spec, null, 2); }
      const tab2 = e.target.closest('[data-tab2]'); if (tab2) { qa('[data-tab2]', b).forEach(x => x.setAttribute('aria-selected', x === tab2)); q('#bld-rules').hidden = tab2.dataset.tab2 !== 'rules'; q('#bld-code').hidden = tab2.dataset.tab2 !== 'code'; }
    });
    q('#bld-json-apply').onclick = () => { try { builder.spec = JSON.parse(q('#bld-json').value); scheduleValidate(); } catch (err) { q('#bld-errors').innerHTML = `<div class="notice bad">JSON-fout: ${esc(err.message)}</div>`; } };
    q('#bld-template').onchange = async e => {
      const v = e.target.value; if (!v) return;
      if (v.startsWith('tpl:')) builder.spec = structuredClone(builder.templates[v.slice(4)]);
      else builder.spec = (await api(`/api/builder/specs/${encodeURIComponent(v.slice(5))}`)).spec;
      renderBuilderForm(); scheduleValidate(); e.target.value = '';
    };
    q('#bld-run').onclick = runBuilderBacktest;
    q('#bld-save').onclick = async () => {
      try { const r = await api('/api/builder/save', {method: 'POST', body: {spec: builder.spec, overwrite: confirm('Bestaande strategie met dezelfde naam overschrijven? (er wordt eerst een back-up gemaakt)\nOK = overschrijven, Annuleren = alleen nieuw opslaan')}});
        q('#bld-status').textContent = `Opgeslagen als ${r.strategy}; beschikbaar in Backtest.`; } catch (err) { q('#bld-status').textContent = err.message; }
    };
    q('#bld-to-validate').onclick = () => { sessionStorage.setItem('labValidateInline', JSON.stringify(builder.spec)); go('validate'); };
  }
  const open = sessionStorage.getItem('labOpenSpec'); sessionStorage.removeItem('labOpenSpec');
  const fromLib = sessionStorage.getItem('labBuilderSeed'); sessionStorage.removeItem('labBuilderSeed');
  if (open) builder.spec = (await api(`/api/builder/specs/${encodeURIComponent(open)}`)).spec;
  else if (fromLib) builder.spec = JSON.parse(fromLib);
  if (!builder.spec) builder.spec = structuredClone(builder.templates.ema_rsi_pullback || Object.values(builder.templates)[0]);
  renderBuilderForm(); scheduleValidate();
}

async function runBuilderBacktest() {
  const st = q('#bld-status'); st.textContent = 'Bezig…';
  q('#bld-run').disabled = true;
  try {
    const r = await api('/api/builder/backtest', {method: 'POST', body: {spec: builder.spec, start: q('#bld-start').value, end: q('#bld-end').value, capital: Number(q('#bld-capital').value), commission_per_lot: Number(q('#bld-comm').value)}});
    builder.lastResult = r; st.textContent = `Klaar · ledger-id ${r.ledger_id || '—'}`;
    renderBacktestResult(q('#bld-result'), r);
  } catch (err) { st.textContent = `Fout: ${err.message}`; }
  finally { q('#bld-run').disabled = false; }
}

function metricTiles(m) {
  const tiles = [['Netto winst', fmt(m.net_profit), sign(m.net_profit)], ['Trades', m.trades ?? 0, ''], ['Profit factor', fmt(m.profit_factor), ''], ['Winrate', pct((m.win_rate ?? 0) * (m.win_rate <= 1 ? 100 : 1)), ''], ['Expectancy', fmt(m.expectancy), sign(m.expectancy)], ['Gem. R', fmt(m.avg_r), sign(m.avg_r)], ['Max drawdown', pct(m.max_drawdown_pct), 'neg'], ['Recovery factor', fmt(m.recovery_factor), ''], ['Sharpe (dag)', fmt(m.sharpe_daily), ''], ['Sortino (dag)', fmt(m.sortino_daily), '']];
  return `<div class="cards kpi" style="grid-template-columns:repeat(auto-fit,minmax(120px,1fr));margin:12px 0">${tiles.map(([l, v, c]) => `<div class="card"><div class="kpi-label">${esc(l)}</div><div class="kpi-value ${c}" style="font-size:18px">${esc(v)}</div></div>`).join('')}</div>`;
}
function renderBacktestResult(el, r) {
  const m = r.metrics || {}; const d = r.diagnostics || {};
  el.innerHTML = metricTiles(m) + `<div id="bld-eq" style="height:230px"></div>
    <details style="margin:10px 0"><summary>Aannames en datakwaliteit (${(d.assumptions || []).length})</summary><ul class="md">${(d.assumptions || []).map(a => `<li>${esc(a)}</li>`).join('')}<li>Candles: ${esc(d.bars)} · twijfelachtige SL/TP-candles: ${esc(d.ambiguous_bars)} · candles zonder Ask: ${esc(d.missing_ask_bars)}</li></ul></details>
    <div class="scroll" style="max-height:300px"><table class="tbl"><thead><tr><th>Entry (UTC)</th><th>Exit (UTC)</th><th>Kant</th><th class="num">Entry</th><th class="num">Exit</th><th class="num">R</th><th class="num">P&amp;L</th><th>Reden</th></tr></thead><tbody>${(r.trades || []).map(t => `<tr><td>${esc(shortDate(t.entry_time))}</td><td>${esc(shortDate(t.exit_time))}</td><td>${esc(t.side)}</td><td class="num">${fmt(t.entry_price, 3)}</td><td class="num">${fmt(t.exit_price, 3)}</td><td class="num ${sign(t.r_multiple)}">${fmt(t.r_multiple)}</td><td class="num ${sign(t.pnl)}">${fmt(t.pnl)}</td><td>${esc(t.exit_reason)}</td></tr>`).join('') || '<tr><td colspan="8" class="muted">Geen trades in deze periode.</td></tr>'}</tbody></table></div>`;
  if (window.Plotly && r.equity?.length) {
    const dark = !document.body.dataset.theme?.startsWith('light');
    Plotly.newPlot(q('#bld-eq', el), [{x: r.equity.map(p => p.time), y: r.equity.map(p => p.equity), type: 'scatter', mode: 'lines', line: {color: '#3b8ee0', width: 1.6}, fill: 'tozeroy', fillcolor: 'rgba(59,142,224,.08)'}],
      {margin: {l: 50, r: 10, t: 10, b: 30}, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: {color: dark ? '#9fb1c3' : '#55687a', size: 11}, xaxis: {gridcolor: dark ? '#1a2735' : '#eef2f5'}, yaxis: {gridcolor: dark ? '#1a2735' : '#eef2f5', title: 'Equity'}}, {displayModeBar: false, responsive: true});
  }
}

/* ---------------- Library ---------------- */
async function loadLibrary() {
  const b = body('library'); clearError('library');
  if (!b.dataset.ready) {
    b.dataset.ready = '1';
    b.innerHTML = `<div class="searchbar"><input type="search" id="lib-q" placeholder="Zoek op strategie, trader, mechanisme, indicator…">
      <select id="lib-grade"><option value="">Alle graden</option><option>A</option><option>B</option><option>C</option></select>
      <select id="lib-ohlc"><option value="">Alle datavereisten</option><option value="OHLC_READY">Geschikt voor Dukascopy OHLC</option><option value="OHLC_PROXY_ONLY">Alleen als proxy</option><option value="NEEDS_EXTERNAL_DATA">Externe data nodig</option><option value="UNKNOWN">Onbekend</option></select>
      <label style="flex-direction:row;align-items:center;margin:0;gap:6px"><input type="checkbox" id="lib-prio"> Prioriteit-prototypes</label>
      <span class="muted" id="lib-count"></span></div>
    <div class="lib-grid" id="lib-grid"></div>
    <div class="card" style="margin-top:16px"><h3><span>Herbruikbare bouwstenen ${info('Concepten uit de dossiers die je als bouwsteen in de Builder kunt hergebruiken.')}</span></h3><div id="lib-components"></div></div>
    <aside class="drawer" id="lib-drawer" aria-label="Dossier"></aside>`;
    let t; const refresh = () => { clearTimeout(t); t = setTimeout(renderLibrary, 180); };
    ['lib-q', 'lib-grade', 'lib-ohlc', 'lib-prio'].forEach(id => q('#' + id).addEventListener('input', refresh));
    q('#lib-grid').addEventListener('click', e => { const c = e.target.closest('[data-id]'); if (c) openDossier(c.dataset.id); });
    const comps = await api('/api/library/components');
    q('#lib-components').innerHTML = `<table class="tbl"><thead><tr><th>Concept</th><th>Trader</th><th>Herkomst</th><th>OHLC</th><th>Feature</th></tr></thead><tbody>${(comps.components || []).map(c => `<tr><td>${esc(c.concept)}</td><td>${esc(c.trader)}</td><td>${c.provenance === 'SOURCE_RULE' ? '<span class="chip info" title="Indicatief: sleutelwoord gevonden in dossiertekst">brontekst</span>' : '<span class="chip idle">eigen vertaling</span>'}</td><td>${c.ohlc_feasible ? '<span class="chip good">ja</span>' : '<span class="chip warn">nee</span>'}</td><td><code>${esc(c.feature_hint)}</code></td></tr>`).join('')}</tbody></table>`;
  }
  await renderLibrary();
}
async function renderLibrary() {
  const params = new URLSearchParams();
  const v = id => q('#' + id).value; if (v('lib-q')) params.set('q', v('lib-q')); if (v('lib-grade')) params.set('grade', v('lib-grade')); if (v('lib-ohlc')) params.set('suitability', v('lib-ohlc')); if (q('#lib-prio').checked) params.set('priority', '1');
  const r = await api(`/api/library?${params}`);
  const s = r.summary || {};
  q('#lib-count').textContent = `${r.items.length} van ${s.count ?? '?'} dossiers · A ${s.grades?.A ?? '?'} · B ${s.grades?.B ?? '?'} · C ${s.grades?.C ?? '?'}`;
  q('#lib-grid').innerHTML = r.items.map(x => `<button class="lib-card" data-id="${esc(x.id)}"><div style="display:flex;gap:8px;align-items:center"><span class="grade ${esc(x.evidence_grade)}">${esc(x.evidence_grade || '?')}</span><h4>${esc(x.title || x.id)}</h4></div>
    <div class="meta-line">${esc(x.trader || 'onbekende trader')} · ${esc([].concat(x.markets || x.market || []).join(', ') || 'markt onbekend')} · ${esc([].concat(x.timeframes || x.timeframe || []).join(', ') || 'TF onbekend')}</div>
    <p>${esc(x.mechanism || '')}</p>
    <div style="display:flex;gap:6px;flex-wrap:wrap">${suitChip(x.dukascopy_ohlc_suitability)}${x.priority_prototype ? '<span class="chip info">prioriteit</span>' : ''}${(x.unknown_rules || []).length ? `<span class="chip warn">${x.unknown_rules.length} onbekende regels</span>` : ''}</div></button>`).join('') || '<div class="empty">Geen dossiers voor deze filters.</div>';
}
const suitChip = s => ({OHLC_READY: '<span class="chip good">OHLC-geschikt</span>', OHLC_PROXY_ONLY: '<span class="chip warn">alleen proxy</span>', NEEDS_EXTERNAL_DATA: '<span class="chip bad">externe data</span>'})[s] || '<span class="chip idle">onbekend</span>';
const listBlock = (title, items) => { items = [].concat(items || []).filter(Boolean); return items.length ? `<h3>${esc(title)}</h3><ul>${items.map(i => `<li>${esc(typeof i === 'object' ? JSON.stringify(i) : i)}</li>`).join('')}</ul>` : ''; };
async function openDossier(id) {
  const d = (await api(`/api/library/${encodeURIComponent(id)}`)).dossier;
  const dr = q('#lib-drawer');
  dr.innerHTML = `<div class="section-head"><h2 style="font-size:18px;margin:0"><span class="grade ${esc(d.evidence_grade)}">${esc(d.evidence_grade)}</span> ${esc(d.title || d.id)}</h2><button class="secondary" id="lib-close">Sluiten</button></div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 12px">${suitChip(d.dukascopy_ohlc_suitability)}<span class="chip idle">${esc(d.translation_status)}</span>${d.priority_prototype ? '<span class="chip info">prioriteit-prototype</span>' : ''}</div>
    ${(d.caveats || []).length || d.instrument_transfer_note ? `<div style="margin:0 0 12px">${tipChip('warn', 'Beperkingen', [...(d.caveats || []), d.instrument_transfer_note].filter(Boolean).join('\n'))}</div>` : ''}
    <div class="md">${listBlock('Mechanisme', d.mechanism)}${listBlock('Entryregels', d.entry_rules)}${listBlock('Exitregels', d.exit_rules)}${listBlock('Risicobeheer', d.risk_management)}${listBlock('Datavereisten', d.data_requirements)}${listBlock('Onbekende of onvolledige regels', d.unknown_rules)}${listBlock('Vereist', d.requires)}
    <h3>Bronnen</h3><ul>${(d.sources || []).map(s => `<li>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title || s.url)}</a>` : esc(s.title)} <span class="muted">${esc(s.type || '')}</span></li>`).join('') || '<li>Geen bronlink.</li>'}</ul></div>
    <div class="row-gap" style="margin:14px 0"><button id="lib-variant">Maak RESEARCH_VARIANT in Builder</button>${info('Maakt een eigen, expliciete vertaling van dit dossier als regelspecificatie in de Builder. Het blijft een onderzoekshypothese tot het gevalideerd is.')}</div>
    ${Object.entries(d.markdown || {}).map(([k, v]) => `<details><summary>${esc(k)}</summary><pre class="code" style="white-space:pre-wrap">${esc(v)}</pre></details>`).join('')}`;
  dr.classList.add('open');
  q('#lib-close').onclick = () => dr.classList.remove('open');
  q('#lib-variant').onclick = async () => {
    const r = await api(`/api/library/${encodeURIComponent(id)}/variant`, {method: 'POST', body: {}});
    sessionStorage.setItem('labBuilderSeed', JSON.stringify(r.spec)); dr.classList.remove('open'); go('builder');
  };
}

/* ---------------- Jobs (validation / discovery) ---------------- */
async function pollJob(id, onUpdate) {
  for (;;) {
    const j = await api(`/api/jobs/${id}`);
    onUpdate(j);
    if (['done', 'error', 'cancelled'].includes(j.status)) return j;
    await new Promise(r => setTimeout(r, 1200));
  }
}
function jobBar(j) { const p = Math.round((j.progress || 0) * 100); return `<div class="progress"><i style="width:${p}%"></i></div><div class="muted">${esc(j.status)} · ${p}% · ${esc(j.message || '')}</div>`; }
const mdToHtml = md => esc(md).replace(/^### (.*)$/gm, '<h3>$1</h3>').replace(/^## (.*)$/gm, '<h2>$1</h2>').replace(/^# (.*)$/gm, '<h1>$1</h1>').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/^- (.*)$/gm, '<li>$1</li>').replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`).replace(/\n{2,}/g, '<br>');

/* ---------------- Validate ---------------- */
const VAL_JOB_KEY = 'labValJob', VAL_HIST_KEY = 'labValHistory', VAL_HIST_MAX = 5;
const val = {strategies: null, missing: false, missingMsg: '', tracking: null, tick: null, initP: null, inlineSpec: null, last: null};
const lsGet = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v === null || v === undefined ? d : v; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* opslag vol of geblokkeerd */ } };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const clock = ms => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const VERDICT_NL = {REJECT: 'Afwijzen', NEEDS_MORE_DATA: 'Meer data nodig', RESEARCH_CANDIDATE: 'Researchkandidaat', OOS_PASSED: 'Out-of-sample geslaagd', FORWARD_ELIGIBLE: 'Geschikt voor forward test'};
const CAP_LABEL = {backtest: 'Backtest', optimize: 'Optimalisatie', inspect: 'Inspector', validate: 'Validatie', walk_forward: 'Walk-forward', stability: 'Parameterstabiliteit', cost_stress_spread: 'Kostenstress (spread)', forward: 'Forwardtest'};
const KIND_NL = {builder: 'Builder-strategie', jesse: 'Jesse-strategie', plugin: 'Python-plugin'};
const ALL_CAPS = Object.fromEntries(Object.keys(CAP_LABEL).map(k => [k, true]));
const SCEN_NL = {baseline: 'Basis', 'spread_+25%': 'Spread +25%', 'spread_+50%': 'Spread +50%', commission_x2: 'Commissie ×2', 'slippage_+1pt': 'Slippage +1 punt', entry_delay_1bar: 'Entry 1 candle later', exit_delay_1bar: 'Exit 1 candle later'};
const KEY_NL = {net_profit: 'Netto', profit_factor: 'PF', n_trades: 'Trades', trades: 'Trades', max_drawdown_pct: 'Max drawdown %', year: 'Jaar', sharpe: 'Sharpe', win_rate: 'Winrate', total: 'Totaal', sortino: 'Sortino', expectancy: 'Verwachting', net_profit_pct: 'Netto %'};
const EVIDENCE = {
  bt: ['historische backtest', 'Uitkomst van een backtest op historische data. Het zegt iets over het verleden, niet over de toekomst.'],
  oos: ['historische OOS', 'Historische out-of-sample test: een periode die bij het kiezen van parameters niet is gebruikt. Nog geen prospectieve forwardtest.'],
  sim: ['simulatie op historische trades', 'Monte Carlo hersampelt de historische trades. Het is een simulatie op bestaand bewijs, geen nieuw bewijs.'],
  meta: ['onderzoeksadministratie', 'Registratie van wat er is geprobeerd of ingezien; geen prestatie-uitkomst.']
};
const evChip = k => tipChip('info', EVIDENCE[k][0], EVIDENCE[k][1]);
const na = (reason = 'Niet geleverd door de API') => `<span class="na" tabindex="0" data-tip="${esc(reason)}">—</span>`;
const isNum = v => typeof v === 'number' && Number.isFinite(v);
const nv = (v, d = 2, reason) => isNum(v) ? esc(fmt(v, d)) : na(reason);
const valOf = x => x && typeof x === 'object' && !Array.isArray(x) && 'value' in x ? x.value : x;
const scalarOrCount = x => Array.isArray(x) ? x.length : x;
const catalogName = s => s.name ?? s.id;
const optVal = s => `${s.kind === 'builder' ? 'spec' : s.kind}:${s.kind === 'plugin' ? s.id ?? s.name : catalogName(s)}`;

async function loadValidate() {
  const b = body('validate'); clearError('validate');
  if (!shell.cache.datasets) shell.cache.datasets = (await api('/api/datasets')).datasets.filter(d => d.available).map(d => d.symbol);
  if (!val.initP) val.initP = buildValidateForm().catch(err => { val.initP = null; throw err; });
  await val.initP;
  await prefillValidate();
  resumeValidation();
}

async function buildValidateForm() {
  const b = body('validate');
  let list = null;
  try {
    const res = await api('/api/research/strategies');
    list = Array.isArray(res) ? res : res.strategies;
    if (!Array.isArray(list)) throw new Error('Onverwacht antwoord van /api/research/strategies');
    val.missing = false;
  } catch (err) { val.missing = true; val.missingMsg = err.message; list = null; }
  val.strategies = list;
  const [specsRes, tplRes] = await Promise.all([api('/api/builder/specs').catch(() => ({specs: []})), api('/api/builder/templates').catch(() => ({templates: {}}))]);
  val.legacySpecs = specsRes.specs || [];
  builder.templates = tplRes.templates || {};
  const tplOpts = `<optgroup label="Builder-sjablonen">${Object.keys(builder.templates).map(k => `<option value="tpl:${esc(k)}">${esc(builder.templates[k].name || k)}</option>`).join('')}</optgroup>`;
  const groups = list
    ? [['builder', 'Builder-strategieën'], ['jesse', 'Jesse-strategieën'], ['plugin', 'Python-plugins']].map(([kind, label]) => `<optgroup label="${label}">${list.filter(s => s.kind === kind).map(s => `<option value="${esc(optVal(s))}">${esc(s.label_nl || catalogName(s))}</option>`).join('')}</optgroup>`).join('')
    : `<optgroup label="Opgeslagen builder-strategieën">${val.legacySpecs.map(s => `<option value="spec:${esc(s.name)}">${esc(s.name)}</option>`).join('')}</optgroup>`;
  const fld = (label, tip, control) => `<label><span class="lbl">${esc(label)}${info(tip)}</span>${control}</label>`;
  const num = (id, v, extra = '') => `<input id="${id}" type="number" value="${v}" ${extra}>`;
  const chk = (id, label, tip, on = true) => `<label class="check-row"><input type="checkbox" id="${id}" ${on ? 'checked' : ''}> ${esc(label)}${info(tip)}</label>`;
  b.innerHTML = `${val.missing ? `<div class="notice warn" id="val-legacy-note"><b>Beperkte modus.</b> Het strategieoverzicht (/api/research/strategies) is op deze server niet beschikbaar (${esc(val.missingMsg)}). Je kunt nu alleen Builder-strategieën en sjablonen valideren, zonder parameterstabiliteit en PBO. Werk de backend bij voor Jesse-strategieën en Python-plugins.</div>` : ''}
    <div class="card val-form"><h3><span>Wat wil je toetsen?</span><small>${esc('Alles wordt gemeten op historische data')}</small></h3>
      <div class="val-pick">
        <label><span class="lbl">Strategie${info('Kies één strategie. Builder-strategieën zijn regelspecificaties, Jesse-strategieën zijn Python-klassen in Jesse en Python-plugins zijn losse plugins. Wat er gevalideerd kan worden verschilt per type; zie hieronder.')}</span><select id="val-spec"><option value="">— kies —</option>${groups}${tplOpts}<option value="inline" hidden>Huidige builder-strategie</option></select></label>
        <div id="val-caps" class="val-caps"></div>
      </div>
      <fieldset class="box"><legend>Periode &amp; data ${info('Marktdata en periode voor de volledige validatie. Een langere periode geeft meer walk-forward-vensters en dus betrouwbaarder bewijs, maar kost meer rekentijd.')}</legend><div class="form-grid">
        ${fld('Symbool', 'De markt waarop gevalideerd wordt (Dukascopy-data).', sel(shell.cache.datasets.map(x => [x, x]), 'XAUUSD', 'id="val-symbol"'))}
        ${fld('Timeframe', 'Candle-grootte. Kleinere timeframes geven meer trades maar ook meer rekentijd en relatief meer kosten.', sel(TFS.map(t => [t, t]), 'M15', 'id="val-tf"'))}
        ${fld('Van', 'Eerste dag van de periode (UTC).', '<input type="date" id="val-start" value="2025-01-01">')}
        ${fld('Tot', 'Laatste dag van de periode (UTC).', '<input type="date" id="val-end" value="2026-10-01">')}
        ${fld('Startkapitaal', 'Beginsaldo voor de simulatie. Beïnvloedt drawdown in procenten en het ruïnerisico.', num('val-capital', 10000, 'min="1"'))}
      </div></fieldset>
      <fieldset class="box" id="val-fs-params" hidden><legend>Parameters ${info('Parameterwaarden van de gekozen strategie voor de basisrun. Walk-forward en stabiliteit variëren deze waarden zelf rond of binnen de zoekruimte.')}</legend><div class="form-grid" id="val-params"></div></fieldset>
      <div class="val-grid">
      <fieldset class="box" id="val-fs-wf"><legend>Walk-forward ${info('Optimaliseert herhaaldelijk op een trainingsvenster en test direct daarna op het volgende, ongeziene venster. Zo zie je of de strategie buiten de trainingsdata standhoudt.')}</legend>
        ${chk('val-wf-on', 'Walk-forward uitvoeren', 'Uit: er is geen out-of-sample walk-forward-bewijs en het oordeel kan dan niet hoger worden dan onvoldoende bewijs.')}
        <div class="form-grid">
        ${fld('Modus', 'Rollend: het trainingsvenster schuift mee en houdt dezelfde lengte. Groeiend: training begint steeds bij de start en wordt langer. Groeiend gebruikt meer historie, rollend past zich sneller aan.', sel([['rolling', 'Rollend'], ['expanding', 'Groeiend']], 'rolling', 'id="val-mode"'))}
        ${fld('Trainingsvenster (dagen)', 'Lengte van het deel waarop parameters worden gekozen. Te kort: ruis. Te lang: weinig vensters.', num('val-train', 120, 'min="10"'))}
        ${fld('Testvenster (dagen)', 'Lengte van het ongeziene deel direct na de training. Moet genoeg trades bevatten om iets te zeggen.', num('val-test', 30, 'min="5"'))}
        ${fld('Stap (dagen)', 'Hoeveel dagen het venster per ronde opschuift. Kleiner = meer overlappende vensters en meer rekentijd.', num('val-step', 30, 'min="5"'))}
        ${fld('Max combinaties per venster', 'Hoeveel parametercombinaties per trainingsvenster worden geprobeerd. Meer combinaties vergroten ook het aantal geteste hypotheses (en verlaagt dus de DSR).', num('val-combos', 12, 'min="1" max="60"'))}
        ${fld('Min. trades per venster', 'Een venster met minder trades telt als onvoldoende bewijs in plaats van als resultaat.', num('val-mintrades', 10, 'min="1"'))}
        </div><p class="val-reason" id="val-reason-wf"></p></fieldset>
      <fieldset class="box" id="val-fs-stab"><legend>Parameterstabiliteit ${info('Test de buurwaarden van de gekozen parameters. Een robuuste strategie blijft winstgevend als een parameter iets verandert; een scherpe piek wijst op overfitting.')}</legend>
        ${chk('val-stab-on', 'Parameterstabiliteit meten', 'Draait de strategie opnieuw met licht afwijkende parameterwaarden. Kost extra backtests.')}
        <p class="val-reason" id="val-reason-stab"></p></fieldset>
      <fieldset class="box" id="val-fs-pbo"><legend>Overfitting (PBO) ${info('Probability of Backtest Overfitting: de kans dat de beste variant in-sample uitkomt onder de mediaan buiten de steekproef. Lager is beter; boven ongeveer 50% is de selectie nauwelijks beter dan toeval.')}</legend>
        ${chk('val-pbo-on', 'PBO berekenen', 'Vergelijkt een raster van combinaties over meerdere deelperioden. Vergt veel backtests.', false)}
        <div class="form-grid">${fld('Max combinaties (PBO)', 'Aantal parametercombinaties in het PBO-raster. Meer combinaties = nauwkeuriger schatting, maar kwadratisch meer rekentijd.', num('val-pbo-combos', 16, 'min="4" max="64"'))}</div>
        <p class="val-reason" id="val-reason-pbo"></p></fieldset>
      <fieldset class="box"><legend>Monte Carlo ${info('Herschudt of hersampelt de trades om te zien hoe groot drawdown en ruïnerisico kunnen worden bij een andere volgorde van dezelfde trades. Block bootstrap behoudt streaks.')}</legend><div class="form-grid">
        ${fld('Simulaties', 'Aantal gesimuleerde trade-reeksen. Meer simulaties geven stabielere percentielen (P5/P50/P95).', num('val-mc', 1000, 'min="100" max="10000"'))}
        ${fld('Methode', 'Block bootstrap behoudt reeksen van winst- of verliestrades (aanbevolen). Bootstrap trekt losse trades met teruglegging. Schudden verandert alleen de volgorde.', sel([['block_bootstrap', 'Block bootstrap (aanbevolen)'], ['bootstrap', 'Bootstrap'], ['shuffle', 'Volgorde schudden']], 'block_bootstrap', 'id="val-mcmethod"'))}
      </div></fieldset>
      <fieldset class="box"><legend>Holdout ${info('De laatste periode blijft onaangeroerd tot de parameters zijn gekozen en wordt dan één keer getest en vastgelegd in het logboek. Een periode die vaker is ingezien telt niet meer als onaangeroerd.')}</legend><div class="form-grid">
        ${fld('Laatste % als holdout', 'Deel van de periode dat achteraf eenmalig als historische out-of-sample test wordt gebruikt. 0 = geen holdout.', num('val-holdout', 20, 'min="0" max="50"'))}
      </div></fieldset>
      </div>
      <div class="notice info" id="val-cost" hidden></div>
      <div class="row-gap"><button id="val-run" type="button">Validatie starten</button><button id="val-cancel" class="secondary" type="button" hidden>Annuleren</button><span id="val-start-note" class="muted"></span></div>
      <div id="val-job" aria-live="polite"></div>
      <div class="val-tools">
        <label><span class="lbl">Eerdere validaties ${info('De laatste vijf validaties van deze browser. Kies er één om het resultaat opnieuw te tonen, zolang de server het nog bewaart.')}</span><select id="val-history"></select></label>
        <button id="val-cfg-toggle" class="secondary" type="button">Configuratie laden…</button>
      </div>
      <div id="val-cfg-panel" class="box val-cfg" hidden>
        <label><span class="lbl">Configuratie (JSON) ${info('Plak een eerder gedownloade configuratie of kies een bestand. Het formulier wordt ingevuld; er wordt niets gestart.')}</span><textarea id="val-cfg-text" rows="8" spellcheck="false" placeholder="{ &quot;strategy&quot;: { … }, &quot;symbol&quot;: &quot;XAUUSD&quot;, … }"></textarea></label>
        <div class="row-gap"><input type="file" id="val-cfg-file" accept="application/json,.json" aria-label="Configuratiebestand"><button id="val-cfg-apply" type="button">Toepassen</button><span id="val-cfg-msg" class="muted"></span></div>
      </div>
    </div>
    <div id="val-result" style="margin-top:14px"><div class="empty">Nog geen validatie. Kies een strategie en start.</div></div>`;
  q('#val-spec').onchange = onStrategyChange;
  q('#val-run').onclick = startValidation;
  q('#val-cancel').onclick = cancelValidation;
  q('#val-history').onchange = e => { if (e.target.value) openHistory(e.target.value); };
  q('#val-cfg-toggle').onclick = () => toggleCfgPanel();
  q('#val-cfg-apply').onclick = () => { try { q('#val-cfg-msg').textContent = applyConfig(JSON.parse(q('#val-cfg-text').value)); } catch (err) { q('#val-cfg-msg').textContent = `Ongeldige JSON: ${err.message}`; } };
  q('#val-cfg-file').onchange = async e => { const f = e.target.files[0]; if (f) { q('#val-cfg-text').value = await f.text(); q('#val-cfg-apply').click(); } };
  ['#val-start', '#val-end', '#val-holdout', '#val-train', '#val-test', '#val-step', '#val-combos', '#val-pbo-combos', '#val-wf-on', '#val-stab-on', '#val-pbo-on'].forEach(s => q(s).addEventListener('input', updateCost));
  renderHistory();
  onStrategyChange();
}

function toggleCfgPanel(open, text) {
  const p = q('#val-cfg-panel'); p.hidden = open === undefined ? !p.hidden : !open;
  if (text !== undefined) q('#val-cfg-text').value = text;
  if (!p.hidden) p.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

function curStrategy() {
  const v = q('#val-spec')?.value || '';
  if (!v) return null;
  if (v === 'inline') return {kind: 'builder', name: val.inlineSpec?.name || 'builder', caps: ALL_CAPS, limitations_nl: [], inline: true};
  if (v.startsWith('tpl:')) return {kind: 'builder', name: v.slice(4), caps: ALL_CAPS, limitations_nl: [], template: true};
  const [kind, ...rest] = v.split(':'); const key = rest.join(':');
  const k = kind === 'spec' ? 'builder' : kind;
  const hit = (val.strategies || []).find(s => s.kind === k && (k === 'plugin' ? (s.id ?? s.name) : catalogName(s)) === key);
  if (hit) return {...hit, name: k === 'plugin' ? hit.id ?? hit.name : catalogName(hit), caps: {...ALL_CAPS, ...Object.fromEntries(Object.keys(CAP_LABEL).map(c => [c, false])), ...(hit.capabilities || {})}, limitations_nl: hit.limitations_nl || []};
  return {kind: k, name: key, caps: ALL_CAPS, limitations_nl: []};
}

function onStrategyChange() {
  const s = curStrategy();
  renderCaps(s); applyCaps(s); renderParamFields(s); restrictChoices(s); updateCost();
}

function renderCaps(s) {
  const el = q('#val-caps');
  if (!s) { el.innerHTML = '<p class="hint">Kies een strategie om te zien wat ervoor gevalideerd kan worden en welke beperkingen gelden.</p>'; return; }
  const chips = Object.keys(CAP_LABEL).map(k => s.caps[k]
    ? tipChip('good', CAP_LABEL[k], `${CAP_LABEL[k]} wordt voor deze strategie ondersteund.`)
    : tipChip('idle cap-off', CAP_LABEL[k], `${CAP_LABEL[k]} wordt voor dit type strategie niet ondersteund.`)).join(' ');
  const lim = s.limitations_nl || [];
  el.innerHTML = `<div class="val-caps-head"><span class="chip info">${esc(KIND_NL[s.kind] || s.kind)}</span><b>${esc(s.label_nl || s.name || '')}</b></div><div class="chips">${chips}</div>
    ${lim.length ? `<ul class="val-limits">${lim.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : val.missing ? '' : '<p class="hint">Geen bijzondere beperkingen gemeld.</p>'}`;
}

function applyCaps(s) {
  const caps = s?.caps || ALL_CAPS, legacy = val.missing;
  const gate = (fsId, reasonId, ok, reason) => {
    const fs = q(fsId), wasOff = fs.classList.contains('is-off'); fs.disabled = !ok; fs.classList.toggle('is-off', !ok);
    if (ok && wasOff) fs.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = c.id !== 'val-pbo-on'; });
    q(reasonId).textContent = ok ? '' : `Uitgeschakeld: ${reason}`;
    if (!ok) fs.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = false; });
  };
  gate('#val-fs-wf', '#val-reason-wf', caps.walk_forward, 'deze strategie ondersteunt geen walk-forward.');
  gate('#val-fs-stab', '#val-reason-stab', caps.stability && !legacy, legacy ? 'de server levert (nog) geen parameterstabiliteit via de nieuwe validatie-API.' : 'deze strategie heeft geen parameters die gevarieerd kunnen worden.');
  gate('#val-fs-pbo', '#val-reason-pbo', (caps.walk_forward || caps.optimize) && !legacy, legacy ? 'de server levert (nog) geen PBO via de nieuwe validatie-API.' : 'PBO heeft een zoekruimte met meerdere combinaties nodig; deze strategie heeft geen parameters met een bereik.');
  const run = q('#val-run'), note = q('#val-start-note');
  const blocked = s && caps.validate === false;
  run.disabled = !!blocked || !!val.tracking;
  note.textContent = blocked ? 'Deze strategie kan niet gevalideerd worden (zie beperkingen).' : '';
}

function renderParamFields(s) {
  const fs = q('#val-fs-params'), box = q('#val-params');
  const raw = s && s.kind !== 'builder' ? s.params : null;
  let items = [];
  if (Array.isArray(raw)) items = raw.map(p => typeof p === 'object' ? p : {name: p});
  else if (raw && typeof raw === 'object') items = Object.entries(raw).map(([name, def]) => def && typeof def === 'object' ? {name, ...def} : {name, default: def});
  fs.hidden = !items.length;
  box.innerHTML = items.map(p => {
    const meta = window.paramLabel ? window.paramLabel(p.name, p, s.name || s.id) : {}; const name = esc(p.name), label = esc(p.label || meta.label || p.name), tip = p.description || p.tip || [meta.help, meta.unit ? `Eenheid: ${meta.unit}.` : ''].filter(Boolean).join(' ') || `Waarde van parameter ${p.name}.`;
    const type = p.type || (typeof p.default === 'boolean' ? 'bool' : typeof p.default === 'number' ? 'float' : 'str');
    if (type === 'bool') return `<label class="check-row"><input type="checkbox" data-vp="${name}" data-type="bool" ${p.default ? 'checked' : ''}> ${label}${info(tip)}</label>`;
    if (type === 'int' || type === 'float') return `<label><span class="lbl">${label}${info(tip)}</span><input type="number" data-vp="${name}" data-type="num" value="${esc(p.default ?? '')}" ${p.min !== undefined ? `min="${p.min}"` : ''} ${p.max !== undefined ? `max="${p.max}"` : ''} step="${type === 'int' ? 1 : 'any'}"></label>`;
    return `<label><span class="lbl">${label}${info(tip)}</span><input type="text" data-vp="${name}" data-type="str" value="${esc(p.default ?? '')}"></label>`;
  }).join('');
}

function restrictChoices(s) {
  const fill = (id, all, allowed) => {
    const el = q(id), cur = el.value; const list = allowed?.length ? all.filter(x => allowed.includes(x)) : all;
    const use = list.length ? list : all;
    el.innerHTML = use.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
    el.value = use.includes(cur) ? cur : use[0];
  };
  fill('#val-symbol', shell.cache.datasets, s?.symbols); fill('#val-tf', TFS, s?.timeframes);
}

function enabled(id) { const el = q(id); return !!el && el.checked && !el.disabled; }

function estimateBacktests() {
  const s = curStrategy(); if (!s || s.kind === 'builder') return null;
  const n = id => Number(q(id).value) || 0;
  const days = (Date.parse(q('#val-end').value) - Date.parse(q('#val-start').value)) / 864e5;
  if (!Number.isFinite(days) || days <= 0) return null;
  const usable = days * (1 - n('#val-holdout') / 100);
  const windows = enabled('#val-wf-on') ? Math.max(0, Math.floor((usable - n('#val-train') - n('#val-test')) / Math.max(1, n('#val-step'))) + 1) : 0;
  const nParams = Math.max(1, qa('[data-vp]').length);
  return {windows, total: windows * (n('#val-combos') + 1) + (enabled('#val-stab-on') ? 2 * nParams + 1 : 0) + (enabled('#val-pbo-on') ? n('#val-pbo-combos') : 0) + 8};
}

function updateCost() {
  const el = q('#val-cost'); if (!el) return;
  const e = estimateBacktests();
  el.hidden = !e;
  if (e) el.innerHTML = `<b>Geschatte rekentijd.</b> Elke combinatie is een volledige Jesse-backtest: ±${e.total} backtests (${e.windows} walk-forward-venster${e.windows === 1 ? '' : 's'} × combinaties, plus basisrun, holdout, kostenstress${enabled('#val-stab-on') ? ', stabiliteit' : ''}${enabled('#val-pbo-on') ? ' en PBO' : ''}). Dit kan minuten tot uren duren; je kunt het tabblad sluiten en later terugkomen.`;
}

async function prefillValidate() {
  const sel = q('#val-spec');
  const inline = sessionStorage.getItem('labValidateInline');
  const named = sessionStorage.getItem('labValidateSpec');
  if (inline) {
    val.inlineSpec = JSON.parse(inline); sessionStorage.removeItem('labValidateInline');
    const o = q('#val-spec option[value="inline"]'); o.hidden = false; o.textContent = `Huidige builder-strategie (${val.inlineSpec.name})`;
    sel.value = 'inline'; onStrategyChange();
    if ([...q('#val-symbol').options].some(x => x.value === val.inlineSpec.symbol)) q('#val-symbol').value = val.inlineSpec.symbol;
    if ([...q('#val-tf').options].some(x => x.value === val.inlineSpec.timeframe)) q('#val-tf').value = val.inlineSpec.timeframe;
  }
  if (named) { sessionStorage.removeItem('labValidateSpec'); const opt = q(`#val-spec option[value="spec:${CSS.escape(named)}"]`); if (opt) { sel.value = opt.value; onStrategyChange(); } }
  const tester = q('#strategy')?.value;
  if (inline || named || !tester) return;
  if (val.missing && val.legacySpecs.some(x => x.name === tester) && !q(`#val-spec option[value="spec:${CSS.escape(tester)}"]`)) q('#val-spec optgroup').insertAdjacentHTML('beforeend', `<option value="spec:${esc(tester)}">${esc(tester)}</option>`);
  const opt = ['spec', 'jesse', 'plugin'].map(k => q(`#val-spec option[value="${k}:${CSS.escape(tester)}"]`)).find(Boolean);
  let note = q('#val-tester-note');
  if (!note) { note = document.createElement('p'); note.id = 'val-tester-note'; note.className = 'hint'; q('#val-spec').closest('label').after(note); }
  if (opt) {
    if (!sel.value) {
      sel.value = opt.value; onStrategyChange();
      for (const [a, b] of [['#val-symbol', '#symbol'], ['#val-tf', '#timeframe']]) if (q(a) && q(b) && [...q(a).options].some(o => o.value === q(b).value)) q(a).value = q(b).value;
      if (q('#start')?.value && q('#end')?.value && q('#start').value < q('#end').value) { /* periode van de tester is bewust korter dan wat walk-forward nodig heeft */ }
    }
    note.textContent = opt.value.startsWith('spec:')
      ? `Overgenomen uit de tester: ${tester}. Kies bij voorkeur een langere periode dan je backtest, zodat walk-forward meerdere vensters heeft.`
      : `Overgenomen uit de tester: ${tester}. Dit is een strategie zonder regelspecificatie: walk-forward en stabiliteit draaien elke combinatie als volledige Jesse-backtest, dus kies een periode die lang genoeg is voor meerdere vensters.`;
  } else {
    note.innerHTML = `De strategie in de tester (<b>${esc(tester)}</b>) is een Python-strategie zonder regelspecificatie en staat niet in het strategieoverzicht van deze server. Kies hieronder een Builder-strategie of sjabloon, of valideer Python-plugins via Strategieën → Overzicht → Valideren.`;
  }
}

async function buildPayload() {
  const choice = q('#val-spec').value; if (!choice) throw new Error('Kies eerst een strategie.');
  const s = curStrategy(); const num = id => Number(q(id).value);
  const symbol = q('#val-symbol').value, timeframe = q('#val-tf').value;
  const walk_forward = {train_days: num('#val-train'), test_days: num('#val-test'), step_days: num('#val-step'), mode: q('#val-mode').value, max_combos: num('#val-combos'), min_trades: num('#val-mintrades')};
  const monte_carlo = {n: num('#val-mc'), method: q('#val-mcmethod').value};
  let spec = null;
  if (s.kind === 'builder') {
    if (choice === 'inline') spec = val.inlineSpec;
    else if (choice.startsWith('tpl:')) spec = builder.templates[choice.slice(4)];
    else spec = (await api(`/api/builder/specs/${encodeURIComponent(choice.slice(5))}`)).spec;
    spec = {...spec, symbol, timeframe};
  }
  const label = `${s.label_nl || (spec && spec.name) || s.name} · ${symbol} ${timeframe}`;
  if (val.missing) {
    if (!spec) throw new Error('Deze server kan alleen Builder-strategieën valideren (beperkte modus).');
    return {payload: {spec, start: q('#val-start').value, end: q('#val-end').value, holdout_percent: num('#val-holdout'), walk_forward, monte_carlo}, label, specName: spec.name};
  }
  walk_forward.enabled = enabled('#val-wf-on');
  const payload = {
    strategy: s.kind === 'builder' ? {kind: 'builder', name: spec.name, spec} : {kind: s.kind, name: s.name},
    symbol, timeframe, start: q('#val-start').value, end: q('#val-end').value, capital: num('#val-capital'), holdout_percent: num('#val-holdout'),
    walk_forward, stability: {enabled: enabled('#val-stab-on')}, pbo: {enabled: enabled('#val-pbo-on'), max_combos: num('#val-pbo-combos')}, monte_carlo
  };
  const params = {}; qa('[data-vp]').forEach(i => { params[i.dataset.vp] = i.dataset.type === 'bool' ? i.checked : i.dataset.type === 'str' ? i.value : Number(i.value); });
  if (Object.keys(params).length) payload.params = params;
  return {payload, label, specName: spec?.name || s.name};
}

function setRunning(on) {
  const run = q('#val-run'), cancel = q('#val-cancel'); if (!run) return;
  cancel.hidden = !on; cancel.disabled = false; cancel.textContent = 'Annuleren';
  if (on) run.disabled = true; else applyCaps(curStrategy());
}

async function startValidation() {
  const job = q('#val-job');
  let built;
  try { built = await buildPayload(); } catch (err) { job.innerHTML = errBox('Kan de validatie niet starten', err.message); return; }
  if (window.Plotly) qa('.js-plotly-plot', q('#val-result')).forEach(x => { try { Plotly.purge(x); } catch { /* al opgeruimd */ } });
  q('#val-result').innerHTML = '<div class="empty">Validatie loopt…</div>';
  job.innerHTML = '';
  setRunning(true);
  try {
    const {job_id} = await api('/api/validate', {method: 'POST', body: built.payload});
    lsSet(VAL_JOB_KEY, {id: job_id, startedAt: Date.now(), label: built.label, specName: built.specName, payload: built.payload});
    trackValidation(job_id);
  } catch (err) {
    job.innerHTML = errBox('Validatie niet gestart', err.message); setRunning(false);
    q('#val-result').innerHTML = '<div class="empty">Nog geen validatie. Kies een strategie en start.</div>';
  }
}

async function cancelValidation() {
  const id = val.tracking; if (!id) return;
  const b = q('#val-cancel'); b.disabled = true; b.textContent = 'Annuleren…';
  try { await api(`/api/jobs/${id}/cancel`, {method: 'POST'}); }
  catch (err) { b.disabled = false; b.textContent = 'Annuleren'; q('#val-job').insertAdjacentHTML('beforeend', errBox('Annuleren mislukt', err.message)); }
}

function paintJob(j, started) {
  const job = q('#val-job'); if (!job) return;
  const p = Math.round((j.progress || 0) * 100);
  const st = {queued: 'In de wachtrij', running: 'Bezig', done: 'Klaar', error: 'Fout', cancelled: 'Geannuleerd'}[j.status] || j.status;
  job.innerHTML = `<div class="progress" role="progressbar" aria-valuenow="${p}" aria-valuemin="0" aria-valuemax="100"><i style="width:${p}%"></i></div>
    <div class="val-jobline"><span><b>${esc(st)}</b> · ${p}%${j.message ? ` · ${esc(j.message)}` : ''}</span><span class="muted" id="val-elapsed">Verstreken ${clock(Date.now() - started)}</span></div>`;
}

async function trackValidation(id) {
  if (val.tracking === id) return;
  val.tracking = id;
  const meta = lsGet(VAL_JOB_KEY, null)?.id === id ? lsGet(VAL_JOB_KEY, null) : {id, startedAt: Date.now(), label: 'Validatie'};
  setRunning(true);
  clearInterval(val.tick);
  val.tick = setInterval(() => { const e = q('#val-elapsed'); if (e) e.textContent = `Verstreken ${clock(Date.now() - meta.startedAt)}`; }, 1000);
  try {
    for (;;) {
      const j = await api(`/api/jobs/${id}`);
      if (val.tracking !== id) return;
      if (['done', 'error', 'cancelled'].includes(j.status)) { finishValidation(j, meta); return; }
      paintJob(j, meta.startedAt);
      await sleep(1200);
    }
  } catch (err) {
    const job = q('#val-job'); if (!job) return;
    if (err.status === 404) { localStorage.removeItem(VAL_JOB_KEY); job.innerHTML = '<div class="notice warn">Deze validatie is niet meer beschikbaar op de server (mogelijk is de server herstart). Start hem opnieuw of kies een eerdere validatie.</div>'; q('#val-result').innerHTML = '<div class="empty">Geen resultaat beschikbaar.</div>'; }
    else job.innerHTML = errBox('Voortgang ophalen mislukt', err.message) + '<p class="hint">De validatie kan op de server nog doorlopen. Heropen dit tabblad om opnieuw te verbinden.</p>';
  } finally {
    if (val.tracking === id) { val.tracking = null; clearInterval(val.tick); setRunning(false); }
  }
}

function finishValidation(j, meta) {
  localStorage.removeItem(VAL_JOB_KEY);
  const job = q('#val-job'), res = q('#val-result');
  if (j.status === 'done' && j.result) {
    job.innerHTML = `<div class="muted">Klaar na ${clock((Date.parse(j.finished_at) || Date.now()) - meta.startedAt)}.</div>`;
    showValidation(j.result, meta);
    pushHistory({id: meta.id, at: Date.now(), label: meta.label, verdict: j.result.scorecard?.verdict, payload: meta.payload || j.result.config});
    document.dispatchEvent(new CustomEvent('lab:validation', {detail: {result: j.result, specName: meta.specName}}));
  } else if (j.status === 'cancelled') {
    job.innerHTML = '<div class="notice warn"><b>Geannuleerd.</b> De validatie is gestopt; er is geen oordeel.</div>';
    res.innerHTML = '<div class="empty">Validatie geannuleerd.</div>';
  } else {
    job.innerHTML = errBox('Validatie mislukt', j.error || j.status);
    res.innerHTML = '<div class="empty">Geen resultaat door een fout.</div>';
  }
}

function pushHistory(entry) {
  lsSet(VAL_HIST_KEY, [entry, ...lsGet(VAL_HIST_KEY, []).filter(h => h.id !== entry.id)].slice(0, VAL_HIST_MAX));
  renderHistory(entry.id);
}
function renderHistory(selected) {
  const el = q('#val-history'); if (!el) return;
  const hist = lsGet(VAL_HIST_KEY, []);
  el.innerHTML = `<option value="">${hist.length ? `— kies (${hist.length}) —` : 'Nog geen eerdere validaties'}</option>${hist.map(h => `<option value="${esc(h.id)}">${esc(shortDate(new Date(h.at).toISOString()))} · ${esc(h.label || h.id)} · ${esc(VERDICT_NL[h.verdict] || h.verdict || '?')}</option>`).join('')}`;
  if (selected) el.value = selected;
  el.disabled = !hist.length;
}
async function openHistory(id, quiet) {
  const entry = lsGet(VAL_HIST_KEY, []).find(h => h.id === id); if (!entry) return;
  const job = q('#val-job');
  try {
    const j = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (j.status === 'done' && j.result) { job.innerHTML = ''; showValidation(j.result, entry); renderHistory(id); return; }
    if (!quiet) job.innerHTML = errBox('Eerdere validatie heeft geen resultaat', j.error || j.status);
  } catch (err) {
    if (quiet) return;
    job.innerHTML = `<div class="notice warn">Het resultaat van deze validatie wordt niet meer door de server bewaard (${esc(err.message)}). De instellingen zijn teruggezet in het formulier zodat je hem opnieuw kunt draaien.</div>`;
    if (entry.payload) applyConfig(entry.payload);
  }
}

function resumeValidation() {
  if (val.tracking) return;
  const cur = lsGet(VAL_JOB_KEY, null);
  if (cur?.id) { trackValidation(cur.id); return; }
  const last = lsGet(VAL_HIST_KEY, [])[0];
  if (last && !q('#val-result .verdict') && !val.last) openHistory(last.id, true);
}

function applyConfig(cfg) {
  if (!cfg || typeof cfg !== 'object') throw new Error('Configuratie is geen object');
  cfg = cfg.config && typeof cfg.config === 'object' ? cfg.config : cfg.request && typeof cfg.request === 'object' ? cfg.request : cfg;
  let n = 0;
  const set = (id, v) => { const el = q(id); if (el && v !== undefined && v !== null && typeof v !== 'object') { if (el.tagName === 'SELECT' && ![...el.options].some(o => o.value === String(v))) el.insertAdjacentHTML('beforeend', `<option value="${esc(v)}">${esc(v)}</option>`); el.value = v; n++; } };
  const st = cfg.strategy || (cfg.spec ? {kind: 'builder', name: cfg.spec.name, spec: cfg.spec} : null);
  if (st) {
    const v = st.kind === 'builder' || !st.kind ? `spec:${st.name}` : `${st.kind}:${st.name}`;
    if (q(`#val-spec option[value="${CSS.escape(v)}"]`)) { q('#val-spec').value = v; n++; }
    else if (st.spec) {
      val.inlineSpec = st.spec; const o = q('#val-spec option[value="inline"]'); o.hidden = false; o.textContent = `Geladen configuratie (${st.spec.name || 'builder'})`; q('#val-spec').value = 'inline'; n++;
    }
    onStrategyChange();
  }
  set('#val-symbol', cfg.symbol ?? cfg.spec?.symbol); set('#val-tf', cfg.timeframe ?? cfg.spec?.timeframe);
  set('#val-start', cfg.start); set('#val-end', cfg.end); set('#val-capital', cfg.capital); set('#val-holdout', cfg.holdout_percent);
  const wf = cfg.walk_forward || {};
  set('#val-mode', wf.mode); set('#val-train', wf.train_days); set('#val-test', wf.test_days); set('#val-step', wf.step_days); set('#val-combos', wf.max_combos); set('#val-mintrades', wf.min_trades);
  const box = (id, v) => { const el = q(id); if (el && typeof v === 'boolean' && !el.disabled) { el.checked = v; n++; } };
  box('#val-wf-on', wf.enabled); box('#val-stab-on', cfg.stability?.enabled); box('#val-pbo-on', cfg.pbo?.enabled);
  set('#val-pbo-combos', cfg.pbo?.max_combos); set('#val-mc', cfg.monte_carlo?.n); set('#val-mcmethod', cfg.monte_carlo?.method);
  for (const [k, v] of Object.entries(cfg.params || {})) { const el = q(`[data-vp="${CSS.escape(k)}"]`); if (el) { if (el.type === 'checkbox') el.checked = !!v; else el.value = v; n++; } }
  updateCost();
  return n ? `Configuratie geladen (${n} velden ingevuld).` : 'Geen herkenbare velden gevonden in deze configuratie.';
}

function showValidation(r, meta) {
  val.last = r;
  const el = q('#val-result');
  renderValidation(r, el, meta);
}

/* ---- research report ---- */
const statusNl = s => ({PASS: 'Geslaagd', FAIL: 'Gefaald', INSUFFICIENT: 'Onvoldoende bewijs', NEEDS_DATA: 'Onvoldoende bewijs', SKIP: 'Niet getest', NOT_RUN: 'Niet getest'}[s] || s || '—');
const statusChip = s => `<span class="chip ${s === 'PASS' ? 'good' : s === 'FAIL' ? 'bad' : 'warn'}">${esc(statusNl(s))}</span>`;
const sec = (id, title, ev, tip, inner) => `<section class="card val-sec" data-sec="${id}"><h3><span>${esc(title)}${info(tip)}</span>${ev ? evChip(ev) : ''}</h3>${inner}</section>`;
const kpi = (label, html, sub) => `<div class="val-kpi"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${html}</div>${sub ? `<div class="kpi-sub">${esc(sub)}</div>` : ''}</div>`;
const flat = v => v && typeof v === 'object' ? (Array.isArray(v) ? `[${v.map(flat).join(', ')}]` : Object.entries(v).map(([k, x]) => `${k}=${flat(x)}`).join(', ')) : v;
function autoTable(x, empty) {
  if (x === null || x === undefined) return `<div class="empty" style="padding:14px">${esc(empty || 'Niet geleverd door de API.')}</div>`;
  if (Array.isArray(x) && x.length && typeof x[0] !== 'object') return tableFrom(x.map((v, i) => ({i: i + 1, v})), [['i', '#', 'num'], ['v', 'Waarde', 'num']], empty);
  let rows;
  if (Array.isArray(x)) rows = x.map(o => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, flat(v)])));
  else if (typeof x === 'object') {
    const ents = Object.entries(x);
    if (ents.length && ents.every(([, v]) => v && typeof v === 'object' && !Array.isArray(v))) rows = ents.map(([k, v]) => ({naam: k, ...Object.fromEntries(Object.entries(v).map(([a, b]) => [a, flat(b)]))}));
    else rows = ents.map(([k, v]) => ({naam: KEY_NL[k] || k, waarde: flat(v)}));
  } else return esc(String(x));
  const keys = [...new Set(rows.flatMap(r => Object.keys(r)))];
  return tableFrom(rows, keys.map(k => [k, KEY_NL[k] || k, rows.some(r => isNum(r[k])) ? 'num' : '']), empty);
}
const plotLayout = extra => ({margin: {l: 54, r: 10, t: 6, b: 30}, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: {color: '#9fb1c3', size: 11}, legend: {orientation: 'h'}, ...extra});
const isoTime = t => isNum(t) ? new Date(t < 1e11 ? t * 1000 : t).toISOString() : t;
const range = a => Array.isArray(a) ? `${esc(a[0])} – ${esc(a[1])}` : esc(a ?? '—');

function renderValidation(r, el = q('#val-result'), meta) {
  if (window.Plotly) el.querySelectorAll('.js-plotly-plot').forEach(x => { try { Plotly.purge(x); } catch { /* al opgeruimd */ } });
  const sc = r.scorecard || {}, ev = r.evidence || {}, checks = sc.checks || [];
  const verdictNl = VERDICT_NL[sc.verdict] || sc.verdict || 'Geen oordeel';
  const ta = ev.trial_accounting || r.trial_accounting, wf = ev.walk_forward, mc = ev.monte_carlo, ps = ev.parameter_stability, pbo = ev.pbo_result, ho = ev.holdout;
  const cfg = r.config || meta?.payload || null;
  const bad = checks.filter(c => c.status !== 'PASS');
  const trials = r.n_trials ?? ta?.total;
  const checkText = c => c.text || c.explanation_nl || (c.value !== undefined && c.threshold !== undefined ? `waarde ${typeof c.value === 'number' ? fmt(c.value, 3) : c.value} tegenover grens ${typeof c.threshold === 'number' ? fmt(c.threshold, 3) : c.threshold}` : 'geen toelichting geleverd');
  const fmtCell = v => typeof v === 'number' ? fmt(v, 3) : v ?? '—';

  const verdictSec = `<div class="verdict ${esc(sc.verdict)}" data-sec="oordeel"><b>${esc(verdictNl)}</b>${verdictLadder(sc.verdict)}<span>${esc(sc.verdict || '')}${trials !== undefined && trials !== null ? ` · ${esc(trials)} hypotheses meegeteld` : ''}${r.ledger_id ? ' · ' + esc(r.ledger_id) : ''}</span>${info('Dit oordeel is geen voorspelling of kans op toekomstige winst. Het vat samen welke controles slaagden, faalden of niet getest konden worden.')}</div>
    <section class="card val-sec" data-sec="redenen"><h3><span>Waarom dit oordeel${info('De controles die niet zijn geslaagd, in gewone taal. Alleen controles die écht slaagden tellen als bewijs.')}</span>${evChip('bt')}</h3>
      ${checks.length ? (bad.length ? `<ul class="val-reasons">${bad.map(c => `<li>${statusChip(c.status)} <b>${esc(c.name)}</b>: ${esc(checkText(c))}</li>`).join('')}</ul>` : '<p class="hint">Alle uitgevoerde controles zijn geslaagd. Dat is geen garantie voor de toekomst.</p>') : `<p class="hint">De API leverde geen controles mee. ${na()}</p>`}
      <details class="accordion" ${checks.length && bad.length === 0 ? '' : 'open'}><summary>Alle controles (${checks.length})</summary><div class="acc-body"><div class="scroll"><table class="tbl"><thead><tr><th>Controle</th><th>Status</th><th class="num">Waarde</th><th class="num">Grens</th><th>Uitleg</th></tr></thead><tbody>${checks.map(c => `<tr><td>${esc(c.name)}</td><td>${statusChip(c.status)}</td><td class="num">${esc(fmtCell(c.value))}</td><td class="num">${esc(fmtCell(c.threshold))}</td><td style="white-space:normal;min-width:260px">${esc(c.text || c.explanation_nl || '')}</td></tr>`).join('')}</tbody></table></div></div></details></section>`;

  const gaps = r.forward_gaps_nl;
  const gapsSec = sec('forward-gaps', 'Wat ontbreekt nog voor een forwardtest', 'meta', 'Een forwardtest start pas als een strategie is bevroren en nieuwe, ongeziene candles verwerkt. Dit zijn de openstaande punten die de validatie heeft gevonden.',
    Array.isArray(gaps) ? (gaps.length ? `<ul class="val-reasons">${gaps.map(g => `<li>${esc(g)}</li>`).join('')}</ul>` : '<p class="hint">De validatie meldt geen openstaande punten. De forwardtest zelf moet nog steeds worden gedraaid.</p>') : `<p class="hint">${na('De API leverde geen lijst met openstaande punten.')} Niet geleverd door de API.</p>`);

  const perfSec = sec('prestaties', 'Historische prestaties', 'bt', 'Basisresultaten van de strategie op de geteste periode. Ze zeggen niets over de toekomst en zijn op zich nooit bewijs.',
    `<div class="val-kpis">${kpi('Trades', nv(ev.n_trades, 0))}${kpi('Netto resultaat', `<span class="${sign(ev.net_profit)}">${nv(ev.net_profit)}</span>`)}${kpi('Profit factor', nv(ev.profit_factor))}${kpi('Max drawdown', isNum(ev.max_drawdown_pct) ? esc(pct(ev.max_drawdown_pct)) : na())}</div>
    <p class="hint">Netto resultaat is na kosten. Profit factor = bruto winst gedeeld door bruto verlies; onder 1 verlies je geld.</p>`);

  const TA_ROWS = [['ledger_prior', 'Eerder in het researchlogboek', 'Varianten van deze strategie die eerder zijn geregistreerd en dus ook zijn meegeteld.'], ['family_prior', 'Eerder uit dezelfde strategiefamilie', 'Verwante varianten die al zijn getest; ze vergroten de kans dat een goed resultaat toeval is.'], ['optimizer_trials', 'Optimizer-trials', 'Trials uit een eerdere optimalisatie die tot deze parameters leidde.'], ['in_run_configs', 'Configuraties in deze validatie', 'Combinaties die walk-forward, stabiliteit en PBO in deze run zelf hebben geprobeerd.']];
  const taSec = sec('selectie', 'Optimalisatie & selectie', 'meta', 'Hoeveel configuraties zijn er geprobeerd voordat deze eruit kwam? Hoe meer, hoe groter de kans dat de beste alleen door toeval goed lijkt.',
    ta ? `<div class="scroll"><table class="tbl"><thead><tr><th>Bron</th><th class="num">Aantal</th><th>Betekenis</th></tr></thead><tbody>${TA_ROWS.map(([k, l, t]) => `<tr><td>${esc(l)}</td><td class="num">${nv(ta[k], 0)}</td><td style="white-space:normal">${esc(t)}</td></tr>`).join('')}<tr><td><b>Totaal meegeteld (N)</b></td><td class="num"><b>${nv(ta.total, 0)}</b></td><td style="white-space:normal">Dit aantal N gaat in de Deflated Sharpe Ratio.</td></tr></tbody></table></div>
      <p class="hint" style="margin-top:10px">De DSR corrigeert voor het aantal geprobeerde configuraties N: wie veel varianten test, vindt vanzelf een mooie uitschieter. Hoe groter N, hoe strenger de DSR en hoe lager de kans dat het resultaat overeind blijft.</p>` : `<p class="hint">${na()} De API leverde geen tellingen van geprobeerde configuraties; de DSR kan dan niet worden beoordeeld.</p>`);

  const wfRows = (wf?.windows || []).map(w => ({train: Array.isArray(w.train) ? `${w.train[0]} – ${w.train[1]}` : w.train, test: Array.isArray(w.test) ? `${w.test[0]} – ${w.test[1]}` : w.test, params: flat(w.best_params || w.params), is_np: w.train_metrics?.net_profit, oos_np: w.test_metrics?.net_profit, oos_tr: w.test_metrics?.n_trades ?? w.test_metrics?.trades, status: w.status}));
  const oosEq = ev.oos_equity || r.oos_equity || wf?.oos_equity;
  const wfSec = sec('walk-forward', 'Walk-forward', 'oos', 'Per venster is op de training geoptimaliseerd en daarna op het ongeziene testvenster gemeten. De curve plakt alleen die testvensters aan elkaar.',
    `<div class="val-kpis">${kpi('Modus', esc(wf?.mode === 'expanding' ? 'Groeiend' : wf?.mode === 'rolling' ? 'Rollend' : '—'))}${kpi('Vensters', nv(wf ? wf.windows?.length : undefined, 0))}${kpi('WFE', nv(wf?.wfe, 2), 'OOS gedeeld door IS')}</div>
    <p class="hint">WFE (walk-forward efficiency) vergelijkt de prestatie buiten de training met die erin. Rond of boven ongeveer 0,5 is redelijk; veel lager wijst op overfitting.</p>
    ${tableFrom(wfRows, [['train', 'Trainperiode'], ['test', 'Testperiode'], ['params', 'Beste parameters'], ['is_np', 'IS netto', 'num'], ['oos_np', 'OOS netto', 'num'], ['oos_tr', 'OOS trades', 'num'], ['status', 'Status']], 'Geen walk-forward-vensters: niet uitgevoerd of te weinig data.')}
    ${Array.isArray(oosEq) && oosEq.length ? '<div class="val-oos" style="height:230px;margin-top:10px"></div>' : '<p class="hint">Geen OOS-equitycurve geleverd. ' + na() + '</p>'}`);

  const mcSec = sec('monte-carlo', 'Monte Carlo', 'sim', 'Hersampelt de historische trades om te zien hoe slecht de volgorde had kunnen uitpakken. P5 is een gunstig, P95 een ongunstig scenario voor drawdown.',
    mc ? `<div class="cards two" style="margin-bottom:0"><div><div class="val-mc" style="height:230px"></div></div><div><div class="val-dd" style="height:150px"></div>
      <div class="val-kpis">${kpi('Drawdown P5', mc.max_drawdown_pct && isNum(mc.max_drawdown_pct.p5) ? esc(pct(mc.max_drawdown_pct.p5)) : na())}${kpi('Drawdown P50', mc.max_drawdown_pct && isNum(mc.max_drawdown_pct.p50) ? esc(pct(mc.max_drawdown_pct.p50)) : na())}${kpi('Drawdown P95', mc.max_drawdown_pct && isNum(mc.max_drawdown_pct.p95) ? esc(pct(mc.max_drawdown_pct.p95)) : na())}${kpi('Ruïnerisico', isNum(mc.risk_of_ruin) ? esc(pct(mc.risk_of_ruin * 100)) : na())}</div></div></div>
      <p class="hint">Methode: ${esc(mc.method || '—')}. In 5% van de simulaties is de maximale drawdown groter dan P95. Ruïnerisico is het aandeel simulaties waarin het kapitaal onder de ruïnegrens zakt.${mc.final_equity !== undefined ? ` Eindkapitaal: ${esc(typeof mc.final_equity === 'object' ? `P5 ${fmt(mc.final_equity.p5, 0)} · P50 ${fmt(mc.final_equity.p50, 0)} · P95 ${fmt(mc.final_equity.p95, 0)}` : fmt(mc.final_equity, 0))}.` : ''}</p>` : `<p class="hint">${na()} Monte Carlo is niet uitgevoerd of leverde geen resultaat.</p>`);

  const cs = ev.cost_stress, csRows = Object.entries(cs?.scenarios || {}).map(([name, m]) => ({name: SCEN_NL[name] || name, net_profit: m.net_profit, profit_factor: m.profit_factor, trades: m.n_trades ?? m.trades, deg: isNum(m.degradation?.net_profit_rel) ? m.degradation.net_profit_rel * 100 : isNum(m.degradation) ? m.degradation * 100 : null}));
  const unsup = cs?.unsupported || [];
  const csSec = sec('kostenstress', 'Kostenstress', 'bt', 'Draait dezelfde strategie met hogere kosten of vertraging. Een strategie die direct omvalt bij iets meer spread of slippage heeft nauwelijks speling.',
    `${tableFrom(csRows, [['name', 'Scenario'], ['net_profit', 'Netto', 'num'], ['profit_factor', 'PF', 'num'], ['trades', 'Trades', 'num'], ['deg', 'Verandering netto %', 'num']], 'Geen kostenstress-scenario\'s geleverd.')}
    ${unsup.length ? `<h4 class="val-sub">Niet uitgevoerde scenario's</h4><ul class="val-reasons">${unsup.map(u => `<li><b>${esc(SCEN_NL[u.name] || u.name)}</b>: ${esc(u.reason_nl || u.reason || 'reden niet opgegeven')}</li>`).join('')}</ul>` : ''}`);

  const stabSec = sec('stabiliteit', 'Parameterstabiliteit', 'bt', 'Hoe winstgevend blijven de buurwaarden van de gekozen parameters? Een brede, vlakke omgeving is betrouwbaarder dan één scherpe piek.',
    ps ? `<p class="hint">${isNum(ps.frac_positive) ? `${fmt(ps.frac_positive * 100, 0)}% van de buurwaarden is winstgevend` : `Aandeel winstgevende buurwaarden: ${na()}`}${isNum(ps.base_net_profit) ? ` (basis netto ${fmt(ps.base_net_profit)})` : ''}. Hoe hoger dit aandeel, hoe minder de uitkomst van één exacte parameterkeuze afhangt.</p>
    ${tableFrom((ps.neighbours || []).map(n => ({...n, param: n.param ?? n.name, value: n.value, change_pct: isNum(n.rel_change) ? n.rel_change * 100 : null})), [['param', 'Parameter'], ['value', 'Waarde', 'num'], ['net_profit', 'Netto', 'num'], ['n_trades', 'Trades', 'num'], ['change_pct', 'Wijziging %', 'num']], 'Geen buurwaarden geleverd.')}` : `<p class="hint">${na()} Parameterstabiliteit is niet uitgevoerd (uitgeschakeld of niet ondersteund).</p>`);

  const OF = [
    ['PBO', pbo && isNum(pbo.pbo) ? `${esc(fmt(pbo.pbo * 100, 0))}%${pbo.status ? ' ' + statusChip(pbo.status) : ''}` : na(), 'Kans dat de in-sample beste variant buiten de steekproef onder de mediaan presteert; lager is beter, boven ongeveer 50% is de selectie niet beter dan toeval.'],
    ['DSR (deflated Sharpe)', nv(valOf(ev.dsr), 3), 'Sharpe-ratio gecorrigeerd voor het aantal geprobeerde configuraties; richting 1 betekent dat de edge waarschijnlijk geen toeval is.'],
    ['PSR (probabilistic Sharpe)', nv(valOf(ev.psr), 3), 'Kans dat de werkelijke Sharpe boven nul ligt, gegeven de lengte en vorm van de returns.'],
    ['MinTRL', nv(scalarOrCount(valOf(ev.min_trl)), 0), 'Minimale tracklengte om de Sharpe significant te noemen; ligt de werkelijke lengte eronder, dan is er te weinig bewijs.']
  ];
  const ofSec = sec('overfitting', 'Overfitting', 'bt', 'Statistische controles op de vraag of het resultaat aan selectie en toeval te danken kan zijn.',
    `<div class="scroll"><table class="tbl"><thead><tr><th>Maat</th><th class="num">Waarde</th><th>Wat zegt dit?</th></tr></thead><tbody>${OF.map(([n, v, t]) => `<tr><td>${esc(n)}</td><td class="num">${v}</td><td style="white-space:normal">${esc(t)}</td></tr>`).join('')}</tbody></table></div>
    <h4 class="val-sub">Zonder de beste trades ${info('Resultaat nadat de beste trades zijn weggelaten. Blijft het positief, dan hangt de winst niet van een paar uitschieters af.')}</h4>${ev.drop_best?.by_trades ? tableFrom(Object.entries(ev.drop_best.by_trades).map(([k, x]) => ({k: `beste ${k} trade(s) weg`, removed: x.removed_pnl, net: x.net_after, pf: x.profit_factor_after, ok: x.still_positive ? 'nog positief' : 'niet meer positief'})), [['k', 'Scenario'], ['removed', 'Weggehaalde winst', 'num'], ['net', 'Netto daarna', 'num'], ['pf', 'PF daarna', 'num'], ['ok', 'Oordeel']]) : autoTable(ev.drop_best, 'Niet geleverd door de API.')}
    <h4 class="val-sub">Consistentie per jaar ${info('Resultaat per kalenderjaar. Winst in slechts één jaar is een zwak teken; liever een redelijke spreiding.')}</h4>${ev.yearly?.years ? tableFrom(Object.entries(ev.yearly.years).map(([y, x]) => ({y, n_trades: x.n_trades, net: x.net_profit, pf: x.profit_factor, wr: x.win_rate == null ? null : x.win_rate * 100})), [['y', 'Jaar'], ['n_trades', 'Trades', 'num'], ['net', 'Netto', 'num'], ['pf', 'PF', 'num'], ['wr', 'Winrate %', 'num']]) : autoTable(ev.yearly, 'Niet geleverd door de API.')}`);

  const hoSec = sec('holdout', 'Holdout / OOS', 'oos', 'De laatste periode die buiten alle optimalisatie is gehouden en één keer is getest. Alleen een onaangeroerde holdout telt als echte historische out-of-sample test.',
    ho ? `<div class="val-kpis">${kpi('Periode', `<span style="font-size:15px">${ho.start || ho.end ? `${esc(ho.start ?? '—')} → ${esc(ho.end ?? '—')}` : na()}</span>`)}${kpi('Trades', nv(scalarOrCount(ho.trades), 0))}${kpi('Onaangeroerd', ho.untouched === true ? '<span class="chip good">Ja</span>' : ho.untouched === false ? '<span class="chip bad">Nee</span>' : na())}${kpi('Eerdere inzage', ho.previous_exposures === undefined ? na() : nv(scalarOrCount(ho.previous_exposures), 0), 'keren eerder bekeken')}</div>
      ${ho.untouched === false ? '<div class="notice warn">Deze holdout is eerder ingezien en telt daarom niet als onaangeroerde out-of-sample test.</div>' : ''}${ho.metrics ? tableFrom([{trades: ho.metrics.n_trades ?? ho.metrics.trades, net: ho.metrics.net_profit, pf: ho.metrics.profit_factor, wr: ho.metrics.win_rate == null ? null : ho.metrics.win_rate * 100, dd: ho.metrics.max_drawdown_pct}], [['trades', 'Trades', 'num'], ['net', 'Netto', 'num'], ['pf', 'PF', 'num'], ['wr', 'Winrate %', 'num'], ['dd', 'Max DD %', 'num']]) : '<p class="hint">Geen holdout-metrics geleverd.</p>'}` : `<p class="hint">${na()} Er is geen holdout uitgevoerd (holdout 0% of niet ondersteund).</p>`);

  const cfgJson = cfg ? JSON.stringify(cfg, null, 2) : '';
  const repSec = sec('reproduceerbaarheid', 'Reproduceerbaarheid', 'meta', 'De volledige configuratie van deze validatie. Met dezelfde configuratie en dezelfde data moet het resultaat opnieuw te maken zijn.',
    cfg ? `<div class="row-gap"><button type="button" class="secondary" data-cfg="copy">Configuratie kopiëren</button><button type="button" class="secondary" data-cfg="download">Configuratie downloaden</button><button type="button" class="secondary" data-cfg="load">Configuratie laden</button><span class="muted" data-cfg-msg></span></div><pre class="val-json">${esc(cfgJson)}</pre>` : `<p class="hint">${na('De API leverde geen configuratie.')} Er is geen reproduceerbare configuratie geleverd.</p>`)
    + (r.report_md ? `<details class="accordion"><summary>Volledig rapport (tekst)</summary><div class="acc-body"><div class="md">${mdToHtml(r.report_md)}</div></div></details>` : '');

  el.innerHTML = verdictSec + gapsSec + perfSec + taSec + wfSec + mcSec + csSec + stabSec + ofSec + hoSec + repSec;

  if (window.Plotly) {
    if (Array.isArray(oosEq) && oosEq.length && q('.val-oos', el)) Plotly.newPlot(q('.val-oos', el), [{x: oosEq.map(p => isoTime(p.time)), y: oosEq.map(p => p.equity), line: {color: '#3fc488', width: 1.6}, name: 'OOS-equity'}], plotLayout({yaxis: {gridcolor: '#1a2735'}, xaxis: {gridcolor: '#1a2735'}}), {displayModeBar: false, responsive: true});
    const bands = mc?.equity_bands;
    if (bands?.p50 && q('.val-mc', el)) { const x = bands.p50.map((_, i) => i); Plotly.newPlot(q('.val-mc', el), [
      {x, y: bands.p95, line: {width: 0}, showlegend: false, hoverinfo: 'skip'},
      {x, y: bands.p5, fill: 'tonexty', fillcolor: 'rgba(59,142,224,.15)', line: {width: 0}, name: 'P5–P95'},
      {x, y: bands.p50, line: {color: '#3b8ee0', width: 1.6}, name: 'Mediaan'}], plotLayout({xaxis: {title: 'trade #', gridcolor: '#1a2735'}, yaxis: {gridcolor: '#1a2735'}}), {displayModeBar: false, responsive: true}); }
    const dd = mc?.max_drawdown_pct;
    if (dd && q('.val-dd', el)) Plotly.newPlot(q('.val-dd', el), [{type: 'bar', x: ['P5', 'P50', 'P95'], y: [dd.p5, dd.p50, dd.p95], marker: {color: ['#3fc488', '#e8b04a', '#ef6f6f']}, hovertemplate: '%{x}: %{y:.1f}%<extra></extra>'}], plotLayout({margin: {l: 40, r: 6, t: 14, b: 24}, yaxis: {title: 'max drawdown %', gridcolor: '#1a2735'}, showlegend: false}), {displayModeBar: false, responsive: true});
  }
  qa('[data-cfg]', el).forEach(b => b.onclick = () => {
    const msg = q('[data-cfg-msg]', el), act = b.dataset.cfg;
    if (act === 'copy') { (navigator.clipboard?.writeText(cfgJson) || Promise.reject(new Error('geen klembord'))).then(() => { msg.textContent = 'Gekopieerd naar het klembord.'; }, () => { const ta = q('.val-json', el); const range = document.createRange(); range.selectNodeContents(ta); getSelection().removeAllRanges(); getSelection().addRange(range); msg.textContent = 'Klembord geweigerd; de tekst is geselecteerd, druk op Ctrl+C.'; }); }
    else if (act === 'download') { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([cfgJson], {type: 'application/json'})); a.download = `validatie-config-${r.ledger_id || Date.now()}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); msg.textContent = 'Download gestart.'; }
    else if (act === 'load') { if (el.id === 'val-result') toggleCfgPanel(true, cfgJson); else { go('validate'); loadValidate().then(() => toggleCfgPanel(true, cfgJson)); } }
  });
}
function tableFrom(rows, cols, empty) {
  rows = rows || []; if (!rows.length) return `<div class="empty" style="padding:14px">${esc(empty || 'Niet getest of onvoldoende data.')}</div>`;
  return `<div class="scroll" style="max-height:260px"><table class="tbl"><thead><tr>${cols.map(([, l, c]) => `<th class="${c || ''}">${esc(l)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(([k, , c]) => `<td class="${c || ''} ${c === 'num' ? sign(r[k]) : ''}">${esc(typeof r[k] === 'number' ? fmt(r[k], Number.isInteger(r[k]) && /trade|^n_|count/i.test(k) ? 0 : 2) : r[k] ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

/* ---------------- Discover: one-click automatic research ---------------- */
const MARKET_DEFAULTS = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'US30', 'US500', 'USATECHIDXUSD', 'DE40', 'BTCUSD'];
const MARKET_LABEL = {USATECHIDXUSD: 'USTEC'};
const OOS_TIP = 'Historische out-of-sample test: de beste varianten worden getoetst op een periode die bij het zoeken niet is gebruikt. Dit is geen prospectieve forward test; een echte forward test start pas nadat je een strategie hebt bevroren.';
const todayIso = () => new Date().toISOString().slice(0, 10);
const dsc = {job: null, running: false};

async function loadDiscover() {
  const root = body('discover'); clearError('discover');
  if (window.labFactory && !root.dataset.fx) {
    root.dataset.fx = '1';
    root.innerHTML = '<div id="fx-root"></div><details class="accordion" id="dsc-classic"><summary>Klassieke snelle verkenning</summary><div class="acc-body" id="dsc-classic-body"></div></details><div id="dsc-ledger-slot"></div>';
    window.labFactory.mount(q('#fx-root'));
  }
  const b = q('#dsc-classic-body') || root;
  if (!shell.cache.datasets) shell.cache.datasets = (await api('/api/datasets')).datasets.filter(d => d.available).map(d => d.symbol);
  if (!b.dataset.ready) {
    b.dataset.ready = '1';
    const fam = await api('/api/discovery/families');
    const markets = shell.cache.datasets;
    b.innerHTML = `<div class="card"><div class="disc-start"><div class="disc-fields">
        <div><div class="field-label">Markten ${info('Kies de markten die het lab mag doorzoeken. Standaard staan alle belangrijkste markten aan.')}</div>
          <div class="chip-select" id="dsc-markets">${markets.map(m => `<label><input type="checkbox" value="${esc(m)}" ${MARKET_DEFAULTS.includes(m) ? 'checked' : ''}><span>${esc(MARKET_LABEL[m] || m)}</span></label>`).join('')}</div></div>
        <div class="form-grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr))">
          <div><div class="field-label">Backtestperiode ${info('Op deze periode zoekt en rangschikt het lab de varianten (in-sample).')}</div><div class="date-pair"><input type="date" id="dsc-is-start" value="2004-01-01" aria-label="Backtest van"><span>→</span><input type="date" id="dsc-is-end" value="2024-01-01" aria-label="Backtest tot"></div></div>
          <div><div class="field-label">Out-of-sample (historisch) ${info(OOS_TIP)}</div><div class="date-pair"><input type="date" id="dsc-oos-start" value="2024-01-01" aria-label="Out-of-sample van"><span>→</span><input type="date" id="dsc-oos-end" value="${todayIso()}" aria-label="Out-of-sample tot"></div></div>
        </div>
        <div class="form-grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,220px))">
          <label>Aantal combinaties<input type="number" id="dsc-max" value="60" min="1" max="500"></label>
          <label>Max looptijd (min)<input type="number" id="dsc-min" value="30" min="1" max="240"></label>
        </div></div>
        <div class="row-gap" style="margin:0"><button id="dsc-run" type="button">Start automatisch onderzoek</button><button id="dsc-cancel" class="secondary" type="button" hidden>Stoppen</button></div></div>
        <div id="dsc-job"></div></div>
      <div id="dsc-results"></div>
      <details class="accordion" id="dsc-adv"><summary>Geavanceerd</summary><div class="acc-body">
        <fieldset class="box"><legend>Strategie-families</legend><div class="chip-select">${(fam.families || []).map(f => `<label><input type="checkbox" name="fam" value="${esc(f.id)}" ${f.default ? 'checked' : ''}><span>${esc(f.name)}</span>${f.description ? info(f.description) : ''}</label>`).join('')}</div></fieldset>
        <fieldset class="box"><legend>Timeframes</legend><div class="chip-select" id="dsc-tfs">${TFS.slice(1).map(t => `<label><input type="checkbox" value="${t}" ${t === 'M15' ? 'checked' : ''}><span>${t}</span></label>`).join('')}</div></fieldset>
        <fieldset class="box"><legend>Budget ${info('Harde grenzen: de run stopt zodra een van deze grenzen is bereikt.')}</legend><div class="form-grid"><label>Max parallelle workers<input type="number" id="dsc-workers" value="2" min="1" max="8"></label><label>Max RAM (MB)<input type="number" id="dsc-ram" value="4096" min="512"></label></div></fieldset>
        <fieldset class="box"><legend>Minimale kwaliteit</legend><div class="form-grid"><label>Min. trades<input type="number" id="dsc-mintr" value="30"></label><label>Min. profit factor<input type="number" step="0.05" id="dsc-pf" value="1.1"></label><label>Max drawdown %<input type="number" id="dsc-dd" value="25"></label></div></fieldset>
        <fieldset class="box"><legend>Selectiescore ${info('De score rangschikt kandidaten; het is géén kans op toekomstige winst. De gewichten zijn zichtbaar en aanpasbaar. Kandidaten moeten daarna nog door Valideren.')}</legend><div class="form-grid" id="dsc-weights">${Object.entries(fam.weights || {}).map(([k, v]) => `<label>${esc(k)}<input type="number" step="0.05" min="0" data-w="${esc(k)}" value="${v}"></label>`).join('')}</div></fieldset>
      </div></details>
      <details class="accordion" id="dsc-ledger-acc"><summary>Researchlogboek (alle geteste varianten) ${info('Iedere geteste variant komt hier terecht, ook de verliezers. Zo telt het aantal geteste hypotheses mee bij de beoordeling.')}<small id="dsc-ledger-note" class="muted" style="margin-left:auto"></small></summary><div class="acc-body">
        <div class="searchbar"><select id="dsc-filter"><option value="">Alles</option><option value="CANDIDATE">Kandidaten</option><option value="REJECTED">Afgewezen</option><option value="ERROR">Fouten</option></select></div><div id="dsc-ledger"></div></div></details>
      <aside class="drawer" id="dsc-drawer" aria-label="Detail"></aside>`;
    if (q('#dsc-ledger-slot')) q('#dsc-ledger-slot').append(q('#dsc-ledger-acc'));
    q('#dsc-run').onclick = startDiscovery;
    q('#dsc-filter').onchange = renderLedger;
    q('#dsc-ledger-acc').addEventListener('toggle', () => { if (q('#dsc-ledger-acc').open) renderLedger().catch(err => showError('discover', err)); });
    q('#dsc-results').addEventListener('click', e => { const r = e.target.closest('[data-hyp]'); if (r) openHypothesis(r.dataset.hyp); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') q('#dsc-drawer')?.classList.remove('open'); });
  }
  await resumeDiscovery();
}

async function resumeDiscovery() {
  if (dsc.running) return;
  const jobs = (await api('/api/jobs').catch(() => ({jobs: []}))).jobs || [];
  const last = jobs.find(j => j.kind === 'discovery');
  if (!last) return;
  const full = await api(`/api/jobs/${last.id}`).catch(() => null);
  if (!full) return;
  if (['running', 'queued'].includes(full.status)) trackDiscovery(full.id);
  else if (full.status === 'done' && full.result) renderDiscoveryResult(full.result);
}

async function startDiscovery() {
  const checked = (root, sel = 'input:checked') => qa(sel, q(root)).map(i => i.value);
  const n = id => Number(q(id).value);
  const symbols = checked('#dsc-markets');
  if (!symbols.length) { q('#dsc-job').innerHTML = '<p class="status error">Kies minstens één markt.</p>'; return; }
  const timeframes = checked('#dsc-tfs'); if (!timeframes.length) timeframes.push('M15');
  const inS = {start: q('#dsc-is-start').value, end: q('#dsc-is-end').value};
  const oos = {start: q('#dsc-oos-start').value, end: q('#dsc-oos-end').value};
  if (!inS.start || !inS.end || !oos.start || !oos.end || inS.start >= inS.end || oos.start >= oos.end) { q('#dsc-job').innerHTML = '<p class="status error">Controleer de perioden: begin moet voor einde liggen.</p>'; return; }
  const weights = Object.fromEntries(qa('[data-w]').map(i => [i.dataset.w, Number(i.value)]));
  const payload = {families: qa('input[name=fam]:checked').map(i => i.value), symbols, timeframes,
    start: inS.start, end: oos.end, in_sample: inS, out_of_sample: oos,
    budget: {max_candidates: n('#dsc-max'), max_minutes: n('#dsc-min'), max_workers: n('#dsc-workers'), max_ram_mb: n('#dsc-ram')},
    criteria: {min_trades: n('#dsc-mintr'), min_profit_factor: n('#dsc-pf'), max_drawdown_pct: n('#dsc-dd')}, weights};
  q('#dsc-run').disabled = true;
  try {
    const {job_id} = await api('/api/discovery/run', {method: 'POST', body: payload});
    trackDiscovery(job_id);
  } catch (err) { q('#dsc-job').innerHTML = `<p class="status error">${esc(err.message)}</p>`; q('#dsc-run').disabled = false; }
}

async function trackDiscovery(jobId) {
  dsc.running = true;
  const run = q('#dsc-run'), cancel = q('#dsc-cancel');
  run.disabled = true; cancel.hidden = false;
  cancel.onclick = () => api(`/api/jobs/${jobId}/cancel`, {method: 'POST'}).catch(() => {});
  try {
    const j = await pollJob(jobId, j => { q('#dsc-job').innerHTML = jobBar(j); });
    q('#dsc-job').innerHTML = jobBar(j) + (j.error ? `<p class="status error">${esc(j.error)}</p>` : '');
    if (j.result) renderDiscoveryResult(j.result);
  } catch (err) { q('#dsc-job').innerHTML = `<p class="status error">${esc(err.message)}</p>`; }
  finally { dsc.running = false; run.disabled = false; cancel.hidden = true; if (q('#dsc-ledger-acc')?.open) renderLedger().catch(() => {}); }
}

const numCell = (v, d = 2) => `<td class="num ${sign(v)}">${v === null || v === undefined ? '—' : fmt(v, d)}</td>`;
function renderDiscoveryResult(res) {
  const top = (res.top || []).slice().sort((a, c) => (c.score ?? -1e9) - (a.score ?? -1e9));
  dsc.top = top;
  const box = q('#dsc-results'); if (!box) return;
  if (!top.length) { box.innerHTML = `<div class="card" style="margin-top:16px"><div class="empty">Geen varianten gevonden die de criteria halen.${res.summary_nl ? info(res.summary_nl) : ''}</div></div>`; return; }
  const m4 = m => m ? [numCell(m.net_profit), `<td class="num">${fmt(m.profit_factor)}</td>`, `<td class="num">${pct(m.max_drawdown_pct)}</td>`, `<td class="num">${esc(m.trades ?? m.n_trades ?? '—')}</td>`].join('') : '<td class="num sep">—</td><td class="num">—</td><td class="num">—</td><td class="num">—</td>';
  const statusChip = e => {
    const o = e.oos_metrics;
    if (e.status === 'REJECTED') return '<span class="chip bad">afgewezen</span>';
    if (!o) return '<span class="chip idle">geen OOS</span>';
    return o.net_profit > 0 && (o.profit_factor ?? 0) > 1 ? '<span class="chip good">OOS positief</span>' : '<span class="chip warn">OOS zwak</span>';
  };
  box.innerHTML = `<div class="card" style="margin-top:16px"><h3><span>Beste varianten ${info('Gerangschikt op score. De score is geen kans op winst: kies je de beste uit veel hypotheses, dan overschat je de werkelijke prestatie. Klik een rij voor details en vervolgstappen.' + (res.summary_nl ? '\n' + res.summary_nl : ''))}</span><small>${esc(res.summary_nl ? (res.tested ?? '') + ' hypotheses getest' : '')}</small></h3>
    <div class="scroll"><table class="tbl top-table"><thead><tr><th rowspan="2">Markt</th><th rowspan="2">Familie</th><th rowspan="2">TF</th><th class="grp" colspan="4">In-sample</th><th class="grp" colspan="4">Out-of-sample (historisch) ${info(OOS_TIP)}</th><th rowspan="2" class="num">Score</th><th rowspan="2">Bewijs ${info('Evidence ladder: alleen poort A (Research) is hier getoetst. Robuustheid, forward, paper en live-review volgen pas na Valideren en een forward test.')}</th><th rowspan="2">Status</th></tr>
    <tr><th class="num sep">Netto</th><th class="num">PF</th><th class="num">DD</th><th class="num">Trades</th><th class="num sep">Netto</th><th class="num">PF</th><th class="num">DD</th><th class="num">Trades</th></tr></thead>
    <tbody>${top.map(e => `<tr class="clickable" data-hyp="${esc(e.hypothesis_id)}" tabindex="0"><td><b>${esc(MARKET_LABEL[e.symbol] || e.symbol)}</b></td><td>${esc(e.family)}</td><td>${esc(e.tf ?? e.timeframe ?? '')}</td>${m4(e.metrics).replace('<td class="num ', '<td class="num sep ')}${m4(e.oos_metrics).replace('<td class="num ', '<td class="num sep ')}<td class="num">${fmt(e.score)}</td><td>${researchLadder(e.status)}</td><td>${statusChip(e)}</td></tr>`).join('')}</tbody></table></div>
</div>`;
  box.onkeydown = e => { if (e.key === 'Enter') { const r = e.target.closest('[data-hyp]'); if (r) openHypothesis(r.dataset.hyp); } };
}

async function openHypothesis(id) {
  const e = (dsc.top || []).find(x => String(x.hypothesis_id) === String(id)); if (!e) return;
  const dr = q('#dsc-drawer');
  dr.innerHTML = `<div class="section-head"><h2 style="font-size:18px;margin:0">${esc(MARKET_LABEL[e.symbol] || e.symbol)} · ${esc(e.family)} · ${esc(e.tf ?? e.timeframe ?? '')}</h2><button class="secondary" id="dsc-close" type="button">Sluiten</button></div>
    <div class="row-gap" style="margin-bottom:12px"><span class="chip ${e.status === 'CANDIDATE' ? 'good' : 'bad'}">${e.status === 'CANDIDATE' ? 'kandidaat' : 'afgewezen'}</span><span class="chip idle">score ${fmt(e.score)}</span>${e.dsr !== undefined && e.dsr !== null ? `<span class="chip idle">DSR ${fmt(e.dsr)}</span>` : ''}${researchLadder(e.status)}</div>
    <div id="dsc-eq" class="eq"></div>
    <table class="tbl" style="margin:12px 0"><thead><tr><th></th><th class="num">In-sample</th><th class="num">Out-of-sample (historisch) ${info(OOS_TIP)}</th></tr></thead><tbody>${[['Netto', 'net_profit', 2], ['Profit factor', 'profit_factor', 2], ['Max drawdown %', 'max_drawdown_pct', 1], ['Trades', 'trades', 0], ['Winrate', 'win_rate', 2], ['Expectancy', 'expectancy', 2]].map(([l, k, d]) => `<tr><td>${l}</td><td class="num">${e.metrics?.[k] === undefined || e.metrics?.[k] === null ? '—' : fmt(e.metrics[k], d)}</td><td class="num">${e.oos_metrics?.[k] === undefined || e.oos_metrics?.[k] === null ? '—' : fmt(e.oos_metrics[k], d)}</td></tr>`).join('')}</tbody></table>
    ${e.rejection_reason ? `<p class="muted">${esc(e.rejection_reason)}</p>` : ''}
    <h3 style="font-size:13px;margin:16px 0 8px">Regels</h3><div id="dsc-rules" class="rules-text">Laden…</div>
    <div class="row-gap"><button id="dsc-open-builder" type="button">Open in Builder</button><button id="dsc-open-validate" class="secondary" type="button" disabled>Valideren</button><button id="dsc-add-forward" class="secondary" type="button" disabled>Toevoegen aan forward</button></div>
    <div id="dsc-fwd"></div>`;
  dr.classList.add('open');
  q('#dsc-close').onclick = () => dr.classList.remove('open');
  drawEquity(e);
  let spec = null;
  try {
    spec = e.spec || (await api(`/api/ledger/${encodeURIComponent(e.hypothesis_id)}`)).entry?.spec;
    if (!spec) throw new Error('Geen regelspecificatie bewaard voor deze variant.');
    const v = await api('/api/builder/validate', {method: 'POST', body: {spec}});
    q('#dsc-rules').textContent = v.description || '(geen beschrijving)';
  } catch (err) { if (q('#dsc-rules')) q('#dsc-rules').textContent = err.message; }
  if (!spec || !q('#dsc-open-builder')) return;
  q('#dsc-open-builder').onclick = () => { sessionStorage.setItem('labBuilderSeed', JSON.stringify(spec)); dr.classList.remove('open'); go('builder'); };
  const val = q('#dsc-open-validate'), fwd = q('#dsc-add-forward');
  val.disabled = false; fwd.disabled = false;
  val.onclick = () => { sessionStorage.setItem('labValidateInline', JSON.stringify(spec)); dr.classList.remove('open'); go('validate'); };
  fwd.onclick = () => forwardForm(spec);
}

function drawEquity(e) {
  const el = q('#dsc-eq'); if (!el) return;
  const isEq = e.equity_is || [], oosEq = e.equity_oos || [];
  if (!window.Plotly || !(isEq.length || oosEq.length)) { el.innerHTML = '<div class="empty">Geen equitycurve beschikbaar.</div>'; return; }
  const dark = !document.body.dataset.theme?.startsWith('light'), grid = dark ? '#1a2735' : '#eef2f5';
  const split = oosEq[0]?.time;
  Plotly.newPlot(el, [
    {x: isEq.map(p => p.time), y: isEq.map(p => p.equity), type: 'scatter', mode: 'lines', name: 'In-sample', line: {color: '#4a90d9', width: 1.5}},
    {x: oosEq.map(p => p.time), y: oosEq.map(p => p.equity), type: 'scatter', mode: 'lines', name: 'Out-of-sample', line: {color: '#e8b04a', width: 1.5}}],
    {margin: {l: 50, r: 10, t: 6, b: 28}, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: {color: dark ? '#9fb1c3' : '#55687a', size: 11}, xaxis: {gridcolor: grid}, yaxis: {gridcolor: grid}, legend: {orientation: 'h', y: 1.12},
      shapes: split ? [{type: 'line', x0: split, x1: split, yref: 'paper', y0: 0, y1: 1, line: {color: dark ? '#9fb1c3' : '#55687a', width: 1, dash: 'dot'}}] : []},
    {displayModeBar: false, responsive: true});
}

function forwardForm(spec) {
  const box = q('#dsc-fwd');
  box.innerHTML = '<div class="muted" style="margin-top:12px">Portfolio\'s laden…</div>';
  api('/api/forward/portfolios').then(({portfolios}) => {
    if (!(portfolios || []).length) { box.innerHTML = '<div class="empty" style="margin-top:12px">Nog geen forward-portfolio.<button class="secondary" type="button" id="dsc-to-forward">Naar Forward test</button></div>'; q('#dsc-to-forward').onclick = () => { q('#dsc-drawer').classList.remove('open'); go('forward'); }; return; }
    box.innerHTML = `<fieldset class="box" style="margin-top:12px"><legend>Bevriezen en toevoegen ${info('De regels worden vastgelegd en vanaf nu alleen op nieuwe, vertraagd gepubliceerde Dukascopy-candles gevolgd (DAILY_DELAYED_FORWARD). Dit is geen realtime papertrading.')}</legend>
      <div class="form-grid"><label>Portfolio${sel(portfolios.map(p => [p.id, p.name]), portfolios[0].id, 'id="dsc-fwd-pf"')}</label><label>Kapitaal<input type="number" id="dsc-fwd-cap" value="10000" min="1" step="any"></label><label>Risico per trade (%)<input type="number" id="dsc-fwd-risk" value="${esc(spec.risk?.percent ?? 0.5)}" min="0.01" max="100" step="any"></label></div>
      <div class="row-gap"><button type="button" id="dsc-fwd-go">Bevries en voeg toe</button><span class="muted" id="dsc-fwd-status"></span></div></fieldset>`;
    q('#dsc-fwd-go').onclick = () => freezeIntoForward(spec);
  }).catch(err => { box.innerHTML = `<p class="status error">${esc(err.message)}</p>`; });
}

async function freezeIntoForward(spec) {
  const st = q('#dsc-fwd-status'), go_ = q('#dsc-fwd-go');
  const pf = q('#dsc-fwd-pf').value, cap = Number(q('#dsc-fwd-cap').value), risk = Number(q('#dsc-fwd-risk').value) / 100;
  if (!(cap > 0) || !(risk > 0)) { st.textContent = 'Vul kapitaal en risico in.'; return; }
  if (!confirm(`Deze strategie bevriezen en toevoegen aan het gekozen forward-portfolio?\nKapitaal ${cap}, risico ${risk * 100}% per trade. Dit legt de regels definitief vast.`)) return;
  go_.disabled = true;
  try {
    st.textContent = 'Strategie opslaan…';
    const name = /^[A-Za-z]/.test(spec.name || '') ? spec.name : `H_${spec.name || Date.now()}`;
    const named = {...spec, name};
    try { await api('/api/builder/save', {method: 'POST', body: {spec: named, overwrite: false}}); }
    catch (err) { if (!/bestaat|exist|409|al /i.test(err.message)) throw err; }
    st.textContent = 'Contractgegevens ophalen…';
    const qs = new URLSearchParams({spec_name: name, starting_capital: cap, trade_risk: risk, portfolio_risk_limit: 0.05});
    const prev = await api(`/api/forward/rule-registrations/preview?${qs}`);
    const contract = prev.configuration?.contract;
    if (!contract) throw new Error('Geen contractgegevens ontvangen van de server.');
    st.textContent = 'Bevriezen…';
    const reg = await api('/api/forward/rule-registrations', {method: 'POST', body: {spec_name: name, contract, starting_capital: cap, trade_risk: risk, portfolio_risk_limit: 0.05, leverage: 1}});
    await api(`/api/forward/portfolios/${pf}/members`, {method: 'POST', body: {registration_id: reg.registration_id}});
    document.dispatchEvent(new CustomEvent('lab:forward', {detail: {registration: reg.registration_id}}));
    st.textContent = 'Bevroren en toegevoegd. De forward test volgt nu nieuwe dagen.';
  } catch (err) { st.textContent = `Fout: ${err.message}`; go_.disabled = false; }
}

async function renderLedger() {
  const f = q('#dsc-filter')?.value || '';
  const r = await api(`/api/ledger?limit=300${f ? `&status=${f}` : ''}`);
  const s = r.summary || {};
  q('#dsc-ledger-note').textContent = `${s.total ?? 0} getest · ${s.candidates ?? 0} kandidaat · ${s.rejected ?? 0} afgewezen`;
  q('#dsc-ledger').innerHTML = (r.entries || []).length ? `<div class="scroll"><table class="tbl"><thead><tr><th>ID</th><th>Familie</th><th>Markt</th><th>Status</th><th class="num">Score</th><th class="num">Trades</th><th class="num">Netto</th><th class="num">PF</th><th class="num">OOS netto</th><th>Reden</th><th></th></tr></thead><tbody>${r.entries.map(e => `<tr><td class="muted" title="${esc(e.id)}">${esc(String(e.id).slice(0, 8))}</td><td>${esc(e.family)}</td><td>${esc(e.symbol)} ${esc(e.timeframe)}</td><td>${e.status === 'CANDIDATE' ? '<span class="chip good">kandidaat</span>' : e.status === 'REJECTED' ? '<span class="chip bad">afgewezen</span>' : `<span class="chip idle">${esc(e.status)}</span>`}</td><td class="num">${fmt(e.score)}</td><td class="num">${esc(e.trades ?? '—')}</td><td class="num ${sign(e.net_profit)}">${fmt(e.net_profit)}</td><td class="num">${fmt(e.profit_factor)}</td><td class="num ${sign(e.oos_net_profit)}">${fmt(e.oos_net_profit)}</td><td style="white-space:normal;max-width:260px" class="muted">${esc(e.rejection_reason || '')}</td><td>${e.spec ? `<button class="link-btn" data-ledger-open="${esc(e.id)}">Builder</button>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Nog geen hypotheses getest.</div>';
  q('#dsc-ledger').onclick = async e => { const t = e.target.closest('[data-ledger-open]'); if (!t) return; const x = await api(`/api/ledger/${t.dataset.ledgerOpen}`); sessionStorage.setItem('labBuilderSeed', JSON.stringify(x.entry.spec)); go('builder'); };
}

/* ---------------- Forward / Portfolio / Data ---------------- */
async function loadForward() {
  const b = body('forward');
  q('#forward-selection-note') && (q('#forward-selection-note').textContent = `Huidige selectie in de Strategy Tester · ${q('#strategy')?.value} · ${q('#symbol')?.value} · ${q('#timeframe')?.value}`);
  q('#forward-refresh')?.click();
}
const CORR_TABS = [['historical', 'Historisch'], ['oos', 'Out-of-sample'], ['prospective', 'Prospectief (forward)']];
async function loadForwardCorrelations(box, ok) {
  const sets = (await Promise.all(ok.filter(r => (r.members || []).length > 1).map(async r => {
    try { const c = await api(`/api/forward/portfolios/${encodeURIComponent(r.portfolio.id)}/correlations`); return {r, c}; } catch { return null; }
  }))).filter(Boolean);
  if (!sets.length) return;
  const label = (blk, id, r) => (blk.labels && blk.labels[id]) || r.members.find(m => String(m.registration_id) === String(id))?.configuration?.strategy || id;
  const block = (blk, r) => {
    if (!blk || typeof blk !== 'object') return '<p class="muted">Niet beschikbaar.</p>';
    const ids = blk.ids || blk.members || [], mat = blk.matrix || [];
    if (/INSUFFICIENT/.test(String(blk.status || '')) || !mat.length) return `<div class="empty"><span class="chip warn">Onvoldoende bewijs</span> ${esc(blk.note_nl || blk.reason_nl || 'Te weinig overlappende handelsdagen om een betrouwbare correlatie te berekenen.')}</div>`;
    const high = (blk.high_pairs || []).map(h => `<li>${esc(label(blk, h.a, r))} en ${esc(label(blk, h.b, r))}: ${fmt(h.corr, 2)}</li>`).join('');
    return `<div class="mv-hmap" style="grid-template-columns:auto repeat(${ids.length},1fr)"><span></span>${ids.map(i => `<span><b>${esc(label(blk, i, r))}</b></span>`).join('')}${mat.map((row, n) => `<span><b>${esc(label(blk, ids[n], r))}</b></span>${row.map(v => `<span style="background:color-mix(in srgb,var(--accent) ${Math.round(Math.min(1, Math.abs(Number(v) || 0)) * 70)}%,transparent)">${fmt(v, 2)}</span>`).join('')}`).join('')}</div>
      <p class="muted">${blk.n_days ? `${esc(blk.n_days)} dagen · ` : ''}${esc(blk.note_nl || 'Pearson-correlatie van dagelijkse rendementen.')}</p>${high ? `<p><b>Sterk gecorreleerd (≥ 0,70)</b></p><ul class="fx-list">${high}</ul>` : ''}`;
  };
  box.hidden = false;
  box.innerHTML = `<h3>Correlaties tussen strategieën ${info('Hoe gelijk de strategieën bewegen. Historisch en out-of-sample komen uit eerder onderzoek; prospectief alleen uit forwarddata en heeft eerst tijd nodig.')}</h3>` + sets.map(({r, c}, n) => `<div class="pf-corr-set" data-corr="${n}">${sets.length > 1 ? `<h4>${esc(r.portfolio.name)}</h4>` : ''}
    <div class="chip-select" role="tablist">${CORR_TABS.map(([k, l], i) => `<label><input type="radio" name="pfc${n}" value="${k}" ${i === 0 ? 'checked' : ''}><span>${l}</span></label>`).join('')}</div>
    <div class="pf-corr-body">${block(c[CORR_TABS[0][0]], r)}</div></div>`).join('');
  box.querySelectorAll('.pf-corr-set').forEach((el, n) => el.addEventListener('change', e => { el.querySelector('.pf-corr-body').innerHTML = block(sets[n].c[e.target.value], sets[n].r); }));
}
async function loadPortfolio() {
  const host = body('portfolio'); clearError('portfolio');
  let b = q('#portfolio-live', host);
  if (!b) { b = document.createElement('div'); b.id = 'portfolio-live'; host.prepend(b); }
  const combine = document.getElementById('backtest-portfolio');
  if (combine && combine.parentElement !== host) { combine.classList.add('card'); host.appendChild(combine); }
  if (!shell.pfThemeWatch) { shell.pfThemeWatch = true; new MutationObserver(() => { if (shell.legacy === 'portfolio') loadPortfolio().catch(() => {}); }).observe(document.body, {attributes: true, attributeFilter: ['data-theme']}); }
  const [list, alerts] = await Promise.all([api('/api/forward/portfolios'), api('/api/monitoring/alerts').catch(() => ({alerts: []}))]);
  const ports = list.portfolios || [];
  const al = alerts.alerts || [];
  q('[data-badge="portfolio"]') && (q('[data-badge="portfolio"]').hidden = !al.length, q('[data-badge="portfolio"]').textContent = al.length);
  const alertHtml = al.length ? `<details class="pf-alerts"><summary><span class="chip ${al.some(a => a.severity === 'critical') ? 'bad' : 'warn'}">Bewaking</span> ${al.length} waarschuwing${al.length === 1 ? '' : 'en'}</summary><div class="pf-alert-list">${al.map(a => `<div class="pf-alert"><span class="chip ${a.severity === 'critical' ? 'bad' : 'warn'}">${esc(a.kind || a.severity)}</span> ${esc(a.message)}</div>`).join('')}</div></details>` : '';
  if (!ports.length) {
    b.innerHTML = `${alertHtml}<div class="pf-empty"><span class="muted">Nog geen forward-strategieën in een portfolio.</span><button type="button" data-pf-add>Voeg je eerste gevalideerde strategie toe</button></div>`;
    b.onclick = e => e.target.closest('[data-pf-add]') && window.labGo('onderzoeken/validatie');
    return;
  }
  const reports = await Promise.all(ports.map(p => api(`/api/forward/portfolios/${p.id}`).catch(err => ({error: err.message, portfolio: p}))));
  const ok = reports.filter(r => !r.error && r.performance);
  const cur = (ok[0]?.portfolio || ports[0]).currency || '';
  const same = ok.filter(r => (r.portfolio.currency || '') === cur);
  const equity = same.reduce((s, r) => s + Number(r.performance.portfolio.equity ?? r.portfolio.balance ?? 0), 0);
  const start = same.reduce((s, r) => s + Number(r.portfolio.starting_capital || 0), 0);
  const ret = start > 0 ? (equity - start) / start * 100 : null;
  const dds = same.map(r => r.performance.portfolio.max_drawdown_percent).filter(v => v != null);
  const rows = ok.flatMap(r => r.members.map(m => ({r, m, p: r.performance.members?.[m.registration_id] || {}})));
  const active = rows.filter(x => x.p.status === 'FORWARD_TESTING').length;
  const waiting = rows.filter(x => x.p.status === 'AWAITING_FIRST_CANDLE').length;
  const failed = reports.filter(r => r.error);
  const mixed = ok.length > same.length;
  const tile = (l, v, cls, sub) => `<div class="card pf-tile"><div class="kpi-label">${esc(l)}</div><div class="kpi-value ${cls || ''}">${esc(v)}</div><div class="kpi-sub">${esc(sub || '')}</div></div>`;
  const status = x => x.p.status === 'MODEL_MISMATCH' ? '<span class="chip bad">model afwijkend</span>' : x.p.status === 'AWAITING_FIRST_CANDLE' ? '<span class="chip idle">wacht op data</span>' : (x.p.trade_count ?? 0) < 30 ? '<span class="chip warn">onvoldoende bewijs</span>' : '<span class="chip info">volgen</span>';
  const strat = rows.map(x => {
    const alloc = Number(x.m.allocation) || 0, np = x.p.net_profit;
    return `<tr><td>${esc(x.m.configuration?.strategy ?? x.m.registration_id)}${ok.length > 1 ? ` <span class="muted">· ${esc(x.r.portfolio.name)}</span>` : ''}</td><td>${esc(x.m.configuration?.symbol ?? '—')} ${esc(x.m.configuration?.timeframe ?? '')}</td><td class="num ${sign(np)}">${np == null || !alloc ? '—' : pct(np / alloc * 100, 2)}</td><td class="num">${x.p.max_drawdown_percent == null ? '—' : pct(x.p.max_drawdown_percent)}</td><td>${status(x)}</td></tr>`;
  }).join('');
  b.innerHTML = `${alertHtml}
  <div class="pf-tiles">
    ${tile('Totale equity', fmt(equity), '', `${cur} · start ${fmt(start)}`)}
    ${tile('Netto rendement', ret == null ? '—' : pct(ret, 2), sign(ret), 'sinds start forwardportfolio')}
    ${tile('Max drawdown', dds.length ? pct(Math.max(...dds)) : '—', dds.length ? 'neg' : '', 'dagelijkse equity, slechtste portfolio')}
    ${tile('Actieve strategieën', String(active), '', waiting ? `${waiting} wacht op eerste candle` : `van ${rows.length} bevroren`)}
  </div>
  ${mixed ? '<p class="hint">Portfolio\'s met een andere valuta zijn niet in de totalen opgeteld.</p>' : ''}
  ${failed.map(r => `<p class="status error">${esc(r.portfolio?.name)}: ${esc(r.error)}</p>`).join('')}
  <div class="card pf-chart"><h3><span>Equity-verloop ${info('Dagelijkse equity van het gezamenlijk gesimuleerde forwardportfolio, inclusief open posities. De data komt één dag later binnen; dit is geen realtime resultaat.')}</span><small>dagelijks vertraagd</small></h3><div id="pf-equity" class="pf-plot"></div></div>
  <div class="card"><h3>Strategieën <small>${rows.length} bevroren</small></h3>
    <div class="scroll"><table class="tbl"><thead><tr><th>Naam</th><th>Markt</th><th class="num">Rendement</th><th class="num">Drawdown</th><th>Status</th></tr></thead><tbody>${strat || '<tr><td colspan="5" class="muted">Nog geen strategieën toegevoegd aan een portfolio.</td></tr>'}</tbody></table></div></div>
  <div class="card" id="pf-corr" hidden></div>`;
  const plot = q('#pf-equity', b);
  const traces = ok.filter(r => (r.performance.portfolio.curve || []).length).map(r => ({type: 'scatter', mode: 'lines', name: r.portfolio.name, x: r.performance.portfolio.curve.map(p => p.day), y: r.performance.portfolio.curve.map(p => p.equity), line: {width: 2}, hovertemplate: `${esc(r.portfolio.name)}<br>%{x}<br>Equity %{y:.2f} ${esc(r.portfolio.currency || '')}<extra></extra>`}));
  if (window.Plotly && traces.length) {
    const cs = getComputedStyle(document.body), ink = cs.getPropertyValue('--ink').trim() || cs.color, edge = cs.getPropertyValue('--edge').trim() || '#233140';
    Plotly.newPlot(plot, traces, {height: 340, margin: {l: 64, r: 16, t: 8, b: 36}, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: {color: ink}, showlegend: traces.length > 1, legend: {orientation: 'h'}, xaxis: {type: 'date', gridcolor: edge}, yaxis: {title: {text: cur}, gridcolor: edge}}, {displayModeBar: false, responsive: true});
  } else plot.innerHTML = '<p class="muted pf-note">Nog geen equity-verloop: dit verschijnt zodra de eerste forwarddag is verwerkt.</p>';
  loadForwardCorrelations(q('#pf-corr', b), ok);
}
async function loadData() {
  const b = body('data');
  if (!q('[data-cap]', b)) {
    b.insertAdjacentHTML('beforeend', `<div class="cards two" style="margin-top:14px"><div class="card" data-cap><h3><span>Beschikbare databronnen ${info('Wat het platform eerlijk wel en niet kan.')}</span></h3><div id="cap-table"></div></div>
    <div class="card"><h3><span>Automatisch aanvullen ${info('Zolang de app open staat haalt hij rustig één ontbrekende dag per keer op via de Dukascopy-datafeed (BID én ASK, 1-minuutcandles), om de beurt per markt. Elke dag wordt gecontroleerd en pas daarna toegevoegd. Bij storingen wacht hij langer. Reserve: dagen uit tickbestanden, of zelf geëxporteerde CSV-bestanden in de inbox-map.')}</span><button class="secondary" id="trickle-nudge" type="button">Nu proberen</button></h3><div id="trickle-status" class="muted">…</div></div></div>`);
  }
  q('#data-monitor-refresh')?.click();
  const renderTrickle = async () => {
    const t = await api('/api/data/trickle').catch(() => null);
    const el = q('#trickle-status'); if (!el) return;
    if (!t || !t.running) { el.textContent = 'Uit (de app draait niet als server).'; return; }
    const label = {FILLING: 'Bezig met aanvullen', 'UP TO DATE': 'Alles bijgewerkt', BACKOFF: 'Even wachten (Dukascopy reageert niet)', WAITING: 'Even wachten', BUSY: 'Dagelijkse taak draait', NOT_CONFIGURED: 'Niet ingesteld', ERROR: 'Fout', STARTING: 'Start…'}[t.state] || t.state;
    const last = t.last && t.last.symbol ? `${t.last.symbol} ${t.last.day}: ${t.last.status}` : '';
    el.innerHTML = `<div class="list"><div class="list-row"><span class="grow">${feedChip(t.state === 'FILLING' || t.state === 'UP TO DATE' ? 'UP TO DATE' : t.state === 'ERROR' ? 'ERROR' : 'DELAYED')} ${esc(label)}</span><span class="muted">${esc(t.filled ?? 0)} dagen toegevoegd</span></div>${last ? `<div class="list-row"><span class="grow muted">Laatste: ${esc(last)}</span></div>` : ''}${t.last_error ? `<div class="list-row"><span class="grow muted" title="${esc(t.last_error)}">Laatste probleem: ${esc(String(t.last_error).slice(0, 120))}</span></div>` : ''}<div class="list-row"><span class="grow muted">Volgende poging over ${esc(Math.round((t.next_check_s || 0) / 60) || '<1')} min</span></div></div>`;
  };
  await renderTrickle();
  const nudge = q('#trickle-nudge'); if (nudge) nudge.onclick = async () => { await api('/api/data/trickle/nudge', {method: 'POST'}); setTimeout(renderTrickle, 4000); };
  try {
    const caps = await api('/api/capabilities');
    q('#cap-table').innerHTML = `<table class="tbl"><thead><tr><th>Databron</th><th>Status</th><th>Toelichting</th></tr></thead><tbody>${(caps.capabilities || []).map(c => `<tr><td>${esc(c.name)}</td><td>${feedChip(c.status)}</td><td style="white-space:normal" class="muted">${esc(c.note)}</td></tr>`).join('')}</tbody></table>`;
  } catch (err) { q('#cap-table').textContent = err.message; }
}

const LOADERS = {dashboard: loadDashboard, strategies: loadStrategies, builder: loadBuilder, library: loadLibrary, validate: loadValidate, discover: loadDiscover, forward: loadForward, portfolio: loadPortfolio, data: loadData};

/* ---------------- Tooltip (shared bubble for every [data-tip]) ---------------- */
function initTips() {
  const tip = document.createElement('div');
  tip.id = 'lab-tip'; tip.setAttribute('role', 'tooltip'); tip.hidden = true;
  document.body.appendChild(tip);
  let cur = null;
  const place = () => {
    if (!cur) return;
    const r = cur.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight, m = 8;
    let top = r.bottom + 8;
    if (top + h > innerHeight - m) top = r.top - h - 8;
    top = Math.max(m, Math.min(innerHeight - h - m, top));
    const left = Math.max(m, Math.min(innerWidth - w - m, r.left + r.width / 2 - w / 2));
    tip.style.left = `${left}px`; tip.style.top = `${top}px`;
  };
  const show = el => {
    cur = el; tip.textContent = el.dataset.tip || ''; tip.hidden = false;
    el.setAttribute('aria-describedby', 'lab-tip'); place();
  };
  const hide = () => {
    if (!cur) return;
    cur.removeAttribute('aria-describedby'); cur = null; tip.hidden = true;
  };
  const host = e => e.target.closest?.('[data-tip]');
  document.addEventListener('mouseover', e => { const t = host(e); if (t && t !== cur && t.dataset.tip) show(t); });
  document.addEventListener('mouseout', e => { const t = host(e); if (t && t === cur && !t.contains(e.relatedTarget)) hide(); });
  document.addEventListener('focusin', e => { const t = host(e); if (t && t.dataset.tip) show(t); });
  document.addEventListener('focusout', e => { if (host(e) === cur) hide(); });
  document.addEventListener('click', e => { const t = host(e); if (t && t.classList.contains('info')) { e.preventDefault(); cur === t ? hide() : show(t); } else if (!t) hide(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && cur) { hide(); } });
  window.addEventListener('scroll', hide, true);
  window.addEventListener('resize', place);
  shell.tip = {show, hide};
}

/* ---------------- Header: status dot ---------------- */
function watchStatus() {
  const health = q('#health'), dot = q('#api-status'); if (!health || !dot) return;
  const sync = () => {
    const t = health.textContent.trim();
    dot.dataset.tip = t;
    dot.classList.toggle('good', /gereed/i.test(t));
    dot.classList.toggle('bad', /niet gevonden|fout|error/i.test(t));
  };
  new MutationObserver(sync).observe(health, {childList: true, characterData: true, subtree: true});
  sync();
}

/* ---------------- Rondleiding (guided tour) ---------------- */
const TOUR = [
  {view: 'dashboard', target: '#dash-kpi', title: 'Dashboard: de stand van zaken', text: 'Deze tegels laten in één oogopslag zien of de data actueel is, hoeveel forwardstrategieën lopen en of er waarschuwingen zijn. Klik in de zijbalk om naar een scherm te gaan.'},
  {view: 'dashboard', target: '#dash-data-line', title: 'Datastatus', text: 'Per symbool zie je de laatste candle en of de feed actueel is. Staat er "niet ingesteld", dan regel je dat in de Data Manager.'},
  {view: 'data', target: '#data-sync-now', title: 'Data Manager: dagelijkse update', text: 'Een geplande taak haalt elke dag automatisch nieuwe Dukascopy-data binnen. Met Nu synchroniseren start je die update meteen. Alle tijden zijn UTC.'},
  {view: 'data', target: '[data-cap]', title: 'Databronnen', text: 'Hier staat eerlijk welke bronnen beschikbaar zijn en welke niet. Zo weet je waar een backtest wel en niet op kan rekenen.'},
  {view: 'library', target: '[data-view="library"] .searchbar', title: 'Researchbibliotheek', text: 'Zoek en filter de IQ Capital-dossiers. Graad A, B of C zegt hoe volledig de regels bekend zijn, niet of de strategie winst maakt.'},
  {view: 'library', target: '#lib-grid .lib-card', title: 'Van dossier naar variant', text: 'Open een dossier en kies "Maak RESEARCH_VARIANT in Builder". Je krijgt dan een eigen, expliciete vertaling die nog gevalideerd moet worden.'},
  {view: 'builder', target: '#bld-template', title: 'Strategy Builder: begin met een sjabloon', text: 'Kies een sjabloon of een eerder opgeslagen strategie. Alles hieronder stel je samen met keuzelijsten, zonder code.'},
  {view: 'builder', target: '#bld-form fieldset.box:nth-of-type(2)', title: 'Voorwaarden', text: 'Bouw de instapregels uit vergelijkingen en patronen. Groepen met EN of OF maken ze zo complex als je wilt.'},
  {view: 'builder', target: '#bld-form fieldset.box:nth-last-of-type(2)', title: 'Risico en beheer', text: 'Bepaal hoeveel je per trade riskeert en hoe stops, trailing en break-even werken. Dit bepaalt de uitkomst vaak meer dan het signaal.'},
  {view: 'builder', target: () => q('#bld-rules')?.closest('.card'), title: 'Regeltekst en gegenereerde code', text: 'De regeltekst vertelt in gewone taal wat de strategie doet. De Jesse-code gebruikt dezelfde regel-engine als de researchbacktest, dus wat je leest is wat er draait.'},
  {view: 'builder', target: '#bld-run', title: 'Researchbacktest', text: 'Een snelle test op Dukascopy Bid/Ask-data om te zien of de regels iets doen. Het is een eerste indruk, geen bewijs.'},
  {view: 'builder', target: '#bld-save', title: 'Opslaan als strategie', text: 'Opslaan maakt er een Jesse-strategie van die je in Backtest, Optimaliseren en Valideren terugvindt.'},
  {view: 'backtest', target: '[data-view="backtest"] .toolbar', title: 'Backtest: de Jesse-tester', text: 'Kies data, tijdframe, strategie en periode en start de backtest. Resultaten, trades en grafieken verschijnen eronder.'},
  {view: 'backtest', target: '[data-view="backtest"] .dock', title: 'Trades en beslislogboek', text: 'Klik een trade om entry, exit, stop loss en take profit in de grafiek te zien. Via "Beslislogboek" in de tradedetails zie je welke checks op dat moment PASS, FAIL of SKIP waren.'},
  {view: 'backtest', target: '[data-view="backtest"] .replay-panel', title: 'Candle replay', text: 'Speel de candles stap voor stap af. De replay gebruikt alleen informatie tot het gekozen moment, zonder vooruitkijken.'},
  {view: 'optimize', target: '#top-optimize', title: 'Optimaliseren', text: 'Zoek parameters met Random, Grid of TPE. De instellingen staan links; de laatste candles vormen de holdout, zodat je niet op dezelfde data beoordeelt.'},
  {view: 'validate', target: '[data-view="validate"] .split > .card', title: 'Analyse: probeer de strategie kapot te maken', text: 'Walk-forward test op ongeziene vensters, de holdout blijft tot het eind onaangeroerd en Monte Carlo herschudt de trades om het risico te tonen.'},
  {view: 'validate', target: '#val-result', title: 'Wat betekent het oordeel?', text: 'AFWIJZEN, MEER DATA NODIG, RESEARCHKANDIDAAT, OOS GESLAAGD of GESCHIKT VOOR FORWARD. Het oordeel is geen voorspelling van winst; het vat samen welke controles slaagden.'},
  {view: 'discover', target: '#fx-start', title: 'Automatisch ontdekken', text: 'Kies markten en perioden en start met één klik. Het lab zoekt op de backtestperiode en toetst de beste varianten op de out-of-sample periode; het budget bewaakt de looptijd. Uitgebreide instellingen staan onder Geavanceerd.'},
  {view: 'discover', target: '#dsc-ledger-acc > summary', title: 'Het logboek bewaart verliezers', text: 'Iedere variant, ook de verliezers, komt in het logboek. Zo telt het aantal geteste hypotheses mee en voorkom je cherry-picking. Klap het open om alles te bekijken.'},
  {view: 'strategies', target: '[data-view="strategies"] .tbl', title: 'Strategieën en levenscyclus', text: 'Elke strategie heeft een status. Promoveren kan alleen met bewijs uit een validatie of forwardtest; LIVE_ELIGIBLE betekent alleen klaar voor menselijke beoordeling.'},
  {view: 'strategies', target: '[data-lib-act="multi"]', title: 'Test op andere markten', text: 'Test dezelfde strategie op meerdere markten en timeframes en kijk of de edge generaliseert. Alleen zelfgebouwde regelstrategieën en fabriekskandidaten kunnen dit.'},
  {view: 'forward', target: '#forward-advance', title: 'Forward test', text: 'Bevries een strategie en laat haar alleen nieuwe candles verwerken. Dit is een dagelijkse, vertraagde simulatie (DAILY_DELAYED_FORWARD), geen realtime papertrading.'},
  {view: 'portfolio', target: '[data-view="portfolio"] .view-body > *', title: 'Portfolio', text: 'Strategieën delen één kapitaal en een risicobudget. Onder Bewaking verschijnen waarschuwingen over data, risicolimieten en forwardprestaties.'},
  {view: null, target: null, title: 'Klaar', text: 'Je hebt de hele route gezien: data, research, bouwen, testen, valideren en volgen. Via de knop met het vraagteken start je de rondleiding opnieuw of krijg je uitleg bij het huidige scherm.'},
];
const tour = {steps: [], i: 0, open: false, token: 0, els: null, kind: 'all'};

function tourEls() {
  if (tour.els) return tour.els;
  const layer = document.createElement('div');
  layer.id = 'tour-layer'; layer.hidden = true;
  layer.innerHTML = `<div class="tour-block"></div><div class="tour-spot"></div>
    <div class="tour-card" role="dialog" aria-live="polite" aria-labelledby="tour-title"><div class="tour-progress"><i></i></div>
      <div class="tour-count"></div><h3 id="tour-title"></h3><p></p>
      <div class="tour-nav"><button type="button" class="secondary" data-t="close">Sluiten</button><span class="grow"></span><button type="button" class="secondary" data-t="prev">Vorige</button><button type="button" data-t="next">Volgende</button></div></div>`;
  document.body.appendChild(layer);
  tour.els = {layer, spot: q('.tour-spot', layer), card: q('.tour-card', layer), title: q('h3', layer), text: q('p', layer), count: q('.tour-count', layer), bar: q('.tour-progress i', layer), prev: q('[data-t=prev]', layer), next: q('[data-t=next]', layer)};
  layer.addEventListener('click', e => {
    const t = e.target.closest('[data-t]')?.dataset.t;
    if (t === 'close') closeTour(); else if (t === 'prev') tourStep(tour.i - 1); else if (t === 'next') tourStep(tour.i + 1);
  });
  return tour.els;
}

const resolveTarget = t => !t ? null : typeof t === 'function' ? t() : q(t);
const visible = el => el && el.getClientRects().length > 0;
async function waitTarget(step, token) {
  for (let n = 0; n < 40; n++) {
    if (token !== tour.token) return null;
    const el = resolveTarget(step.target);
    if (visible(el)) return el;
    await new Promise(r => setTimeout(r, 100));
  }
  return null;
}

function tourPlace() {
  if (!tour.open) return;
  const {spot, card} = tourEls(); const el = tour.target && visible(tour.target) ? tour.target : null;
  const W = innerWidth, H = innerHeight, cw = card.offsetWidth, ch = card.offsetHeight, m = 12;
  if (!el) {
    spot.style.cssText = `left:${W / 2}px;top:${H / 2}px;width:0;height:0`;
    card.style.left = `${Math.max(m, (W - cw) / 2)}px`; card.style.top = `${Math.max(m, (H - ch) / 2)}px`;
    return;
  }
  const r = el.getBoundingClientRect(), pad = 6;
  const x0 = Math.max(4, r.left - pad), y0 = Math.max(4, r.top - pad), x1 = Math.min(W - 4, r.right + pad), y1 = Math.min(H - 4, r.bottom + pad);
  spot.style.cssText = `left:${x0}px;top:${y0}px;width:${Math.max(0, x1 - x0)}px;height:${Math.max(0, y1 - y0)}px`;
  let top, left = Math.max(m, Math.min(W - cw - m, r.left + r.width / 2 - cw / 2));
  if (y1 + 14 + ch <= H - m) top = y1 + 14;
  else if (y0 - 14 - ch >= m) top = y0 - 14 - ch;
  else if (x1 + 14 + cw <= W - m) { left = x1 + 14; top = Math.max(m, Math.min(H - ch - m, y0)); }
  else if (x0 - 14 - cw >= m) { left = x0 - 14 - cw; top = Math.max(m, Math.min(H - ch - m, y0)); }
  else { left = W - cw - m; top = H - ch - m; }
  card.style.left = `${left}px`; card.style.top = `${top}px`;
}
let tourRaf = 0;
const tourSchedule = () => { cancelAnimationFrame(tourRaf); tourRaf = requestAnimationFrame(tourPlace); };

async function tourStep(i) {
  if (i < 0) i = 0;
  if (i >= tour.steps.length) { closeTour(); return; }
  const token = ++tour.token, step = tour.steps[i]; tour.i = i;
  const {layer, card, title, text, count, bar, prev, next} = tourEls();
  layer.hidden = false; tour.open = true; document.body.classList.add('touring');
  title.textContent = step.title; text.textContent = step.text;
  count.textContent = `Stap ${i + 1} van ${tour.steps.length}`;
  bar.style.width = `${((i + 1) / tour.steps.length) * 100}%`;
  prev.disabled = i === 0; next.textContent = i === tour.steps.length - 1 ? 'Afronden' : 'Volgende';
  card.classList.add('loading');
  if (step.view && shell.legacy !== step.view) go(step.view, {silent: true});
  const el = await waitTarget(step, token); if (token !== tour.token) return;
  tour.target = el; card.classList.remove('loading');
  if (el) {
    const r = el.getBoundingClientRect();
    el.scrollIntoView({block: r.height > innerHeight * 0.7 ? 'start' : 'center', inline: 'nearest'});
  }
  tourPlace(); setTimeout(tourPlace, 120);
  next.focus({preventScroll: true});
}

function startTour(kind = 'all') {
  hideHelp();
  shell.tip?.hide();
  let steps = TOUR;
  if (kind === 'view') {
    steps = TOUR.filter(s => s.view === shell.legacy);
    if (!steps.length) { const [t, tx] = TITLES[shell.legacy] || ['Dit scherm', '']; steps = [{view: shell.legacy, target: null, title: t, text: tx.split('\n')[0]}]; }
  }
  tour.steps = steps; tour.kind = kind;
  localStorage.setItem('labTourSeen', '1'); dismissOffer();
  tourStep(0);
}
function closeTour() {
  tour.token++; tour.open = false; tour.target = null;
  if (tour.els) tour.els.layer.hidden = true;
  document.body.classList.remove('touring');
}

function tourKeys(e) {
  if (!tour.open) return;
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeTour(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); tourStep(tour.i + 1); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); tourStep(tour.i - 1); }
}

/* ---------------- Header: help menu + first-visit offer ---------------- */
function hideHelp() { const p = q('.help-pop'); if (p) p.hidden = true; q('#tour-btn')?.setAttribute('aria-expanded', 'false'); }
function dismissOffer() { q('#tour-offer')?.remove(); }
function buildHelp() {
  const slot = q('#help-slot'); if (!slot) return;
  slot.className = 'help-menu';
  slot.innerHTML = `<button type="button" id="tour-btn" class="round-btn" aria-haspopup="true" aria-expanded="false" aria-label="Hulp en rondleiding" data-tip="Hulp en rondleiding">?</button>
    <div class="help-pop" hidden><button type="button" data-onboard="1">Interactieve introductie (echte data)</button><button type="button" data-tour="view">Uitleg bij dit scherm</button><button type="button" data-tour="all">Rondleiding langs alle schermen</button></div>`;
  q('#tour-btn').onclick = e => {
    e.stopPropagation(); shell.tip?.hide();
    const pop = q('.help-pop'); pop.hidden = !pop.hidden; e.currentTarget.setAttribute('aria-expanded', String(!pop.hidden));
  };
  q('.help-pop').addEventListener('click', e => { if (e.target.closest('[data-onboard]')) { hideHelp(); window.labOnboarding?.start({restart: true}); return; } const t = e.target.closest('[data-tour]'); if (t) { hideHelp(); startTour(t.dataset.tour); } });
  document.addEventListener('click', e => { if (!e.target.closest('.help-menu')) hideHelp(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hideHelp(); });
  document.addEventListener('keydown', tourKeys, true);
  window.addEventListener('resize', tourSchedule);
  window.addEventListener('scroll', tourSchedule, true);
  if (!localStorage.getItem('labTourSeen')) setTimeout(offerTour, 1200);
}
function offerTour() {
  if (localStorage.getItem('labTourSeen') || tour.open) return;
  const o = document.createElement('div');
  o.id = 'tour-offer'; o.setAttribute('role', 'dialog'); o.setAttribute('aria-label', 'Rondleiding');
  if (window.labOnboarding?.isDone()) { localStorage.setItem('labTourSeen', '1'); return; }
  o.innerHTML = `<b>Nieuw hier?</b><span>De introductie laat je met je eigen Dukascopy-data een voorbeeldstrategie testen, inspecteren, optimaliseren en valideren. Alle cijfers worden echt berekend. Je kunt hem altijd later starten via de knop ?.</span><div class="tour-nav"><button type="button" class="secondary" data-o="no">Niet nu</button><button type="button" data-o="go">Introductie starten</button></div>`;
  o.addEventListener('click', e => {
    const a = e.target.closest('[data-o]')?.dataset.o; if (!a) return;
    localStorage.setItem('labTourSeen', '1'); dismissOffer();
    if (a === 'go') { go('onderzoeken/testen'); window.labOnboarding?.start({restart: true}); }
  });
  document.body.appendChild(o);
}

/* ---------------- Silent update: reload when frontend or backend changed ---------------- */
function toast(msg, ms = 2000) {
  let t = q('#lab-toast');
  if (!t) { t = document.createElement('div'); t.id = 'lab-toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, ms);
}
function watchBuild() {
  if (sessionStorage.getItem('labUpdated')) { sessionStorage.removeItem('labUpdated'); toast('Bijgewerkt'); }
  const expected = q('meta[name="strategy-lab-frontend-build"]')?.content || '';
  let base = null;
  const busy = async v => {
    const a = document.activeElement;
    if ((a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) || tour.open || v.active_runs > 0) return true;
    try {
      const r = await fetch('/api/jobs', {cache: 'no-store'});
      return (await r.json()).jobs?.some(j => j.status === 'running' || j.status === 'queued') ?? false;
    } catch { return true; }
  };
  const check = async () => {
    if (document.hidden) return;
    let v;
    try { const r = await fetch(`/api/version?frontend_build_id=${encodeURIComponent(expected)}`, {cache: 'no-store'}); if (!r.ok) return; v = await r.json(); } catch { return; }
    base = base || {pid: v.process_id, started: v.started_at};
    const changed = v.frontend_matches === false || v.process_id !== base.pid || v.started_at !== base.started;
    if (changed && !(await busy(v))) { sessionStorage.setItem('labUpdated', '1'); location.reload(); }
  };
  setInterval(check, 15000);
  setTimeout(check, 3000);
}

function start() {
  document.body.classList.add('shell');
  buildRail(); buildViews(); buildSubnav(); initTips(); buildHelp(); watchStatus(); watchBuild();
  const initial = location.hash.slice(1) || localStorage.getItem('labLastView') || 'home';
  go(initial, {silent: true});
  if (lsGet(VAL_JOB_KEY, null)?.id) setTimeout(() => loadValidate().catch(() => {}), 800);
  window.addEventListener('hashchange', () => go(location.hash.slice(1), {silent: true}));
  window.addEventListener('beforeunload', () => shell.view && localStorage.setItem('labLastView', shell.view));
  setInterval(() => { if (shell.legacy === 'dashboard' && !document.hidden) loadDashboard().catch(() => {}); }, 60000);
}
window.labShell = {go, api, startTour, closeTour, discovery: {render: renderDiscoveryResult, open: openHypothesis},
  LOADERS, body, esc, fmt, pct, shortDate, info, tipChip, ladder, gateStates, lifecycleChip, verdictChip, feedChip, openLifecycle, openPlugin, showError, clearError,
  renderValidation, loadValidate, pollJob, jobBar, MARKET_LABEL, resolveRoute, toast, cache: shell.cache};
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
