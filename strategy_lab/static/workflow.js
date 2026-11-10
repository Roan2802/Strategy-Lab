/* Research workflow stepper (#workflow-slot) + interactive onboarding (window.labOnboarding).
   Pure add-on: feature-detects labGo, state, execute and the lab:* events; all numbers come from API responses. */
(() => {
'use strict';
if (window.__labWorkflowLoaded) return;
window.__labWorkflowLoaded = true;

const $id = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf = (v, d = 2) => v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('nl-NL', {minimumFractionDigits: d, maximumFractionDigits: d});
const ls = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const labState = () => (typeof state !== 'undefined' && state) || {};
const val = id => ($id(id)?.value || '').trim();

async function api(path, opts = {}) {
  const init = {...opts, headers: {'Content-Type': 'application/json', ...(opts.headers || {})}};
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const res = await (window.labNet ? window.labNet.fetch(path, init) : fetch(path, init));
  const text = await res.text();
  let data; try { data = text ? JSON.parse(text) : {}; } catch { data = {detail: text}; }
  if (!res.ok) {
    const d = data.detail;
    const err = new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ') : `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}
const soft = (p, fallback) => p.catch(() => fallback);

function go(route) {
  if (typeof window.labGo === 'function') return window.labGo(route);
  try { window.labShell?.go?.(route.split('/')[0]); } catch { /* shell not ready */ }
}
const fire = (name, detail) => document.dispatchEvent(new CustomEvent(name, {detail}));
const isoDay = d => d.toISOString().slice(0, 10);

/* ------------------------------------------------------------------ BUILD 1: stepper */
const VERDICT_NL = {REJECT: 'Afwijzen', NEEDS_MORE_DATA: 'Meer data nodig', RESEARCH_CANDIDATE: 'Researchkandidaat',
  OOS_PASSED: 'Out-of-sample geslaagd', FORWARD_ELIGIBLE: 'Geschikt voor forward test'};
const FORWARD_LIFE = ['FORWARD_TESTING', 'PAPER_ELIGIBLE', 'LIVE_REVIEW_REQUIRED', 'LIVE_ELIGIBLE'];
const STATE_NL = {done: 'Klaar', next: 'Volgende', avail: 'Beschikbaar', blocked: 'Geblokkeerd'};

const STEPS = [
  {key: 'strategie', title: 'Strategie kiezen of maken', label: 'Strategie',
   what: 'Je kiest welke handelsregels je onderzoekt: een bestaande Jesse-strategie, of een nieuwe die je in de Builder uit regels samenstelt.',
   settings: [['Strategie', 'De Jesse-strategie die getest wordt. Alleen strategieën met een regelspecificatie kunnen later statistisch gevalideerd worden.']],
   where: 'Veld Strategie in de testerbalk, en onder Strategieën → Overzicht / Bouwen.',
   next: {label: 'Naar data kiezen', route: 'onderzoeken/backtest', step: 1}},
  {key: 'data', title: 'Data kiezen', label: 'Data',
   what: 'Je bepaalt op welke Dukascopy-data de test draait: instrument, tijdframe en periode.',
   settings: [['Data', 'Het instrument (bijv. XAUUSD) uit je lokale Dukascopy-bestanden.'], ['Tijdframe', 'De candlegrootte (M15 = kaarsen van 15 minuten).'], ['Van / Tot en met', 'De testperiode. Moet binnen de beschikbare data liggen.']],
   where: 'Velden Data, Tijdframe, Van en Tot en met in de testerbalk; beschikbare periode onder Data.',
   next: {label: 'Naar backtest', route: 'onderzoeken/backtest', step: 2}},
  {key: 'backtest', title: 'Backtest draaien', label: 'Backtest',
   what: 'Je laat de strategie de gekozen periode historisch doorlopen. Dit is een historische simulatie, geen bewijs van toekomstige winst.',
   settings: [['Startkapitaal / kosten', 'Balans, commissie en leverage in het instellingenpaneel bepalen de uitkomst.'], ['Strategieparameters', 'De waarden in het paneel Parameters.']],
   where: 'Onderzoeken → Backtest (samenvatting) en → Grafiek.',
   next: {label: 'Naar trades inspecteren', route: 'onderzoeken/trades', step: 3}},
  {key: 'trades', title: 'Trades inspecteren', label: 'Trades',
   what: 'Je controleert of de trades doen wat je bedoelde: klik een trade om entry, exit, stoploss en take-profit in de grafiek te zien.',
   settings: [['Geselecteerde trade', 'Klik een rij in de tradelijst; de grafiek springt naar die trade.']],
   where: 'Onderzoeken → Trades en → Inspector (beslischecks per candle).',
   next: {label: 'Naar optimaliseren', route: 'onderzoeken/optimalisatie', step: 4}},
  {key: 'optimaliseren', title: 'Optimaliseren', label: 'Optimaliseren',
   what: 'Je laat de optimizer parametercombinaties proberen. Let op: de beste combinatie uitkiezen geeft selectiebias; de uitkomst moet daarna nog gevalideerd worden.',
   settings: [['Trials', 'Aantal geprobeerde parametercombinaties.'], ['Doelfunctie / train-%', 'Waarop gescoord wordt en welk deel van de data voor training dient.']],
   where: 'Onderzoeken → Optimalisatie.',
   next: {label: 'Naar valideren', route: 'onderzoeken/validatie', step: 5}},
  {key: 'valideren', title: 'Statistisch valideren', label: 'Valideren',
   what: 'Walk-forward, Monte Carlo, kostenstress en holdout testen of het resultaat buiten de trainingsdata standhoudt. Resultaat is een verdict, geen voorspelling.',
   settings: [['Regelspecificatie', 'Alleen Builder-strategieën en plugins kunnen gevalideerd worden.'], ['Periode & holdout', 'Een langere periode; de laatste procenten blijven onaangeroerd tot de eenmalige holdouttest.']],
   where: 'Onderzoeken → Validatie, en het onderzoekslogboek (Strategieën → Overzicht).',
   next: {label: 'Naar forwardportfolio', route: 'portfolio/forward', step: 6}},
  {key: 'forward', title: 'Forwardportfolio', label: 'Forward',
   what: 'Alleen kandidaten met verdict FORWARD_ELIGIBLE mogen bevroren worden en dagelijks vertraagd gevolgd worden op nieuwe data. Dit is geen realtime papertrading.',
   settings: [['Verdict FORWARD_ELIGIBLE', 'Alle kernchecks van de validatie moeten geslaagd zijn.'], ['Portfolio', 'Een forwardportfolio waaraan de bevroren registratie wordt toegevoegd.']],
   where: 'Portfolio → Forward.',
   next: {label: 'Naar forwardportfolio', route: 'portfolio/forward', step: 6}},
];

const NAME_RE = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const wf = {root: null, steps: null, guide: null, res: [], sel: ls.get('labWfStep', 0), open: ls.get('labWfGuideOpen', false),
  seq: 0, timer: 0, cache: {specs: null, plugins: null, at: 0, meta: {}, runs: null, runsAt: 0}};

async function getMeta(symbol) {
  if (!symbol) return null;
  const hit = wf.cache.meta[symbol];
  if (hit && Date.now() - hit.at < 120000) return hit.v;
  let v;
  try { v = (await api(`/api/data/meta?symbol=${encodeURIComponent(symbol)}`)).manifest; }
  catch (e) { v = {error: e.message}; }
  wf.cache.meta[symbol] = {v, at: Date.now()};
  return v;
}

// /api/runs reads every saved run and can take many seconds: never block the stepper on it.
function getRuns() {
  const c = wf.cache;
  if ((!c.runs || Date.now() - c.runsAt > 60000) && !c.runsBusy) {
    c.runsBusy = true;
    soft(api('/api/runs'), null).then(d => { if (d) { c.runs = d.runs || []; c.runsAt = Date.now(); refresh(0); } }).finally(() => { c.runsBusy = false; });
  }
  return c.runs;
}

async function getCatalog() {
  const c = wf.cache;
  if (!c.specs || Date.now() - c.at > 20000) {
    const [s, p] = await Promise.all([soft(api('/api/builder/specs'), {specs: []}), soft(api('/api/plugins'), {plugins: []})]);
    c.specs = s.specs || []; c.plugins = p.plugins || []; c.at = Date.now();
  }
  return c;
}

async function forwardRegistered(name) {
  const {portfolios = []} = await soft(api('/api/forward/portfolios'), {portfolios: []});
  for (const p of portfolios.filter(x => Number(x.members) > 0).slice(0, 6)) {
    const rep = await soft(api(`/api/forward/portfolios/${encodeURIComponent(p.id)}`), null);
    if ((rep?.members || []).some(m => m.configuration?.strategy === name)) return p.name || p.id;
  }
  return null;
}

function tradeMarks() { return ls.get('labWfTradesV1', {}); }
function markTrade(runId) { if (!runId) return; const m = tradeMarks(); m[runId] = Date.now(); ls.set('labWfTradesV1', m); }

async function compute() {
  const strategy = val('strategy'), symbol = val('symbol'), tf = val('timeframe'), start = val('start'), end = val('end');
  const st = labState();
  const out = STEPS.map(() => ({state: 'avail', warn: '', facts: '', reason: '', note: ''}));
  const runsKnown = getRuns();
  const runs = runsKnown || [];
  const [cat, meta] = await Promise.all([getCatalog(), getMeta(symbol)]);
  const mine = runs.filter(r => r.strategy === strategy);
  const backtests = mine.filter(r => r.action === 'backtest');
  const optimizes = mine.filter(r => r.action === 'optimize');

  // 1 strategy
  out[0].state = strategy ? 'done' : 'avail';
  out[0].facts = strategy ? `Gekozen: ${strategy}.` : 'Nog geen strategie gekozen in de testerbalk.';

  // 2 data
  const d = out[1];
  if (!symbol || !tf || !start || !end) { d.reason = ''; d.note = 'Kies instrument, tijdframe en periode in de testerbalk.'; }
  else if (meta?.error) { d.warn = 'Geen data'; d.note = `Voor ${symbol} kon geen data worden geladen: ${meta.error}`; }
  else if (meta?.first_utc && meta?.last_utc) {
    const first = meta.first_utc.slice(0, 10), last = meta.last_utc.slice(0, 10);
    d.facts = `${symbol} ${tf} · ${start} t/m ${end}. Beschikbaar: ${first} t/m ${last}.`;
    if (end < first || start > last) d.warn = 'Buiten data';
    else if (start < first || end > last) d.warn = 'Deels buiten data';
    if (d.warn) d.note = `De gekozen periode valt (deels) buiten de beschikbare ${symbol}-data (${first} t/m ${last}). Kies een periode binnen dit bereik.`;
    else d.state = 'done';
  } else d.state = 'done';
  if (end < start && start && end) { d.warn = 'Einddatum voor start'; d.note = 'De einddatum ligt voor de startdatum.'; d.state = 'avail'; }

  // 3 backtest
  const b = out[2];
  const active = st.runManifest;
  const activeMatches = active && active.strategy === strategy && active.action === 'backtest';
  if (!strategy) { b.state = 'blocked'; b.reason = 'Kies eerst een strategie.'; }
  else if (activeMatches || backtests.length) {
    b.state = 'done';
    const last = activeMatches ? active : backtests[0];
    const row = backtests.find(r => r.run_id === last.run_id) || backtests[0];
    b.facts = `${backtests.length || 1} backtest(s) voor ${strategy}. Laatste: ${(last.created_at || '').slice(0, 16).replace('T', ' ')}${row ? `, ${row.trade_count ?? '?'} trades` : ''}.`;
  } else b.facts = runsKnown ? `Nog geen backtest voor ${strategy}.` : 'Eerdere runs worden geladen…';

  // 4 trades
  const t = out[3];
  const marks = tradeMarks();
  const currentRun = activeMatches ? active.run_id : backtests[0]?.run_id;
  if (b.state !== 'done' && runsKnown) { t.state = 'blocked'; t.reason = 'Er is nog geen backtest, dus er zijn geen trades om te inspecteren. Draai eerst een backtest.'; }
  else if (currentRun && marks[currentRun]) { t.state = 'done'; t.facts = 'Je hebt in deze run een trade geselecteerd.'; }
  else t.facts = 'Klik een trade in de tradelijst of grafiek; daarmee wordt deze stap als klaar gemarkeerd.';

  // 5 optimize
  const o = out[4];
  if (!strategy) { o.state = 'blocked'; o.reason = 'Kies eerst een strategie.'; }
  else if (optimizes.length) { o.state = 'done'; o.facts = `${optimizes.length} optimalisatierun(s) voor ${strategy}. Laatste: ${(optimizes[0].created_at || '').slice(0, 16).replace('T', ' ')}.`; }
  else o.facts = runsKnown ? `Nog geen optimalisatie voor ${strategy}.` : 'Eerdere runs worden geladen…';

  // 6 validate
  const v = out[5];
  const spec = cat.specs.find(s => String(s.name).toLowerCase() === strategy.toLowerCase());
  const plugin = cat.plugins.find(p => [p.id, p.name].some(n => String(n).toLowerCase() === strategy.toLowerCase()));
  let life = null;
  if (strategy && NAME_RE.test(strategy)) life = await soft(api(`/api/lifecycle/${encodeURIComponent(strategy)}`), null);
  let entry = null;
  const lastLedger = life?.last_ledger_id;
  if (lastLedger) entry = (await soft(api(`/api/ledger/${encodeURIComponent(lastLedger)}`), {})).entry || null;
  if (!strategy) { v.state = 'blocked'; v.reason = 'Kies eerst een strategie.'; }
  else if (!spec && !plugin) {
    v.state = 'blocked';
    v.reason = 'Deze Python-strategie heeft geen regelspecificatie; walk-forward/Monte Carlo werkt voor Builder-strategieën en plugins. Bouw of importeer de regels in Strategieën → Bouwen.';
  } else if (life?.last_verdict) {
    v.state = 'done';
    v.facts = `Laatste verdict: ${VERDICT_NL[life.last_verdict] || life.last_verdict} (${life.last_verdict})${entry?.created_at ? `, ${entry.created_at.slice(0, 10)}` : ''}.`;
  } else v.facts = 'Regelspecificatie gevonden; nog geen validatie in het onderzoekslogboek.';
  v.verdict = life?.last_verdict || null;

  // 7 forward
  const f = out[6];
  const registeredIn = strategy && NAME_RE.test(strategy) ? await forwardRegistered(strategy) : null;
  const registeredByLife = life && (FORWARD_LIFE.includes(life.status) || (life.history || []).some(h => FORWARD_LIFE.includes(h.to_status)));
  if (registeredIn || registeredByLife) {
    f.state = 'done';
    f.facts = registeredIn ? `Geregistreerd in forwardportfolio "${registeredIn}".` : `Levenscyclusstatus: ${life.status}.`;
  } else if (!strategy) { f.state = 'blocked'; f.reason = 'Kies eerst een strategie.'; }
  else if (v.state === 'blocked') { f.state = 'blocked'; f.reason = 'Zonder regelspecificatie kan de strategie niet gevalideerd of bevroren worden. Bouw eerst de regels in Strategieën → Bouwen.'; }
  else if (life?.last_verdict !== 'FORWARD_ELIGIBLE') {
    f.state = 'blocked';
    if (!life?.last_verdict) f.reason = 'Ontbrekende check: er is nog geen statistische validatie. Alleen verdict FORWARD_ELIGIBLE mag naar het forwardportfolio.';
    else {
      const failed = (entry?.rejection_reason || '').split(';').map(x => x.trim()).filter(Boolean);
      f.reason = `Laatste verdict is ${VERDICT_NL[life.last_verdict] || life.last_verdict}; FORWARD_ELIGIBLE is vereist.${failed.length ? ` Gefaalde controles: ${failed.join(', ')}.` : ''}`;
    }
  } else f.facts = 'Verdict FORWARD_ELIGIBLE: je kunt de regels bevriezen en toevoegen aan een forwardportfolio.';

  // exactly one "next": first step that is neither done nor blocked
  const nextIdx = out.findIndex(r => r.state !== 'done' && r.state !== 'blocked');
  if (nextIdx >= 0) out[nextIdx].state = 'next';
  return out;
}

function buildStepper(slot) {
  slot.innerHTML = '';
  const root = document.createElement('section');
  root.className = 'wf'; root.setAttribute('aria-label', 'Onderzoeksworkflow');
  root.innerHTML = `<ol class="wf-steps"></ol><div class="wf-guide" id="wf-guide" hidden></div>`;
  slot.append(root);
  wf.root = root; wf.steps = root.querySelector('.wf-steps'); wf.guide = root.querySelector('.wf-guide');
  STEPS.forEach((s, i) => {
    const li = document.createElement('li');
    li.innerHTML = `<button type="button" class="wf-step" data-i="${i}" aria-controls="wf-guide"><span class="wf-step-top"><span class="wf-num">${i + 1}</span><span class="wf-label">${esc(s.label)}</span></span><span class="wf-state"></span></button>`;
    wf.steps.append(li);
  });
  wf.steps.addEventListener('click', e => {
    const btn = e.target.closest('.wf-step'); if (!btn) return;
    const i = Number(btn.dataset.i);
    if (wf.sel === i && wf.open) wf.open = false; else { wf.sel = i; wf.open = true; }
    ls.set('labWfStep', wf.sel); ls.set('labWfGuideOpen', wf.open);
    paint();
  });
}

function paint() {
  if (!wf.root) return;
  const res = wf.res;
  wf.steps.querySelectorAll('.wf-step').forEach((btn, i) => {
    const r = res[i] || {state: 'avail'};
    btn.dataset.state = r.state;
    btn.setAttribute('aria-current', wf.open && wf.sel === i ? 'step' : 'false');
    btn.setAttribute('aria-expanded', String(wf.open && wf.sel === i));
    btn.title = r.reason || r.note || r.facts || (res.length ? '' : 'Status wordt geladen…');
    btn.querySelector('.wf-state').innerHTML = `${esc(STATE_NL[r.state])}${r.warn ? ` <span class="wf-warn" aria-label="Waarschuwing: ${esc(r.warn)}">· ${esc(r.warn)}</span>` : ''}`;
  });
  wf.guide.hidden = !wf.open;
  if (!wf.open) return;
  const s = STEPS[wf.sel], r = res[wf.sel] || {state: 'avail'};
  wf.guide.innerHTML = `
    <div class="wf-guide-head"><h3>Stap ${wf.sel + 1}: ${esc(s.title)} <span class="chip ${r.state === 'done' ? 'good' : r.state === 'blocked' ? 'bad' : r.state === 'next' ? 'info' : 'idle'}">${esc(STATE_NL[r.state])}</span></h3>
      <button type="button" class="wf-linkbtn" data-act="close">Sluiten</button></div>
    <div class="wf-guide-body">
      ${r.state === 'blocked' && r.reason ? `<div class="wf-reason" role="note"><h4>Waarom dit nog niet kan</h4><p>${esc(r.reason)}</p></div>` : ''}
      ${r.note ? `<div class="wf-note" role="note">${esc(r.note)}</div>` : ''}
      <div><h4>Wat je hier doet</h4><p>${esc(s.what)}</p>${r.facts ? `<p style="margin-top:6px"><b>Nu:</b> ${esc(r.facts)}</p>` : ''}</div>
      <div><h4>Noodzakelijke instellingen</h4><dl>${s.settings.map(([k, m]) => `<dt>${esc(k)}</dt><dd>${esc(m)}</dd>`).join('')}</dl></div>
      <div><h4>Waar je het resultaat vindt</h4><p>${esc(s.where)}</p></div>
      <div class="wf-actions"><button type="button" class="wf-btn" data-act="next">${esc(s.next.label)} →</button>
        ${wf.sel > 0 ? '<button type="button" class="wf-btn secondary" data-act="prev">← Vorige stap</button>' : ''}
        ${window.labOnboarding ? '<button type="button" class="wf-linkbtn" data-act="tour">Interactieve rondleiding starten</button>' : ''}</div>
    </div>`;
  wf.guide.onclick = e => {
    const act = e.target.closest('[data-act]')?.dataset.act; if (!act) return;
    if (act === 'close') { wf.open = false; ls.set('labWfGuideOpen', false); paint(); wf.steps.querySelectorAll('.wf-step')[wf.sel]?.focus(); }
    else if (act === 'next') { go(s.next.route); wf.sel = Math.min(STEPS.length - 1, s.next.step); ls.set('labWfStep', wf.sel); paint(); }
    else if (act === 'prev') { wf.sel = Math.max(0, wf.sel - 1); ls.set('labWfStep', wf.sel); paint(); }
    else if (act === 'tour') window.labOnboarding?.start();
  };
}

function refresh(delay = 120) {
  clearTimeout(wf.timer);
  wf.timer = setTimeout(async () => {
    const seq = ++wf.seq;
    try {
      const res = await compute();
      if (seq !== wf.seq) return;
      wf.res = res;
    } catch (e) {
      if (seq !== wf.seq) return;
      wf.res = STEPS.map(() => ({state: 'avail', warn: '', facts: '', reason: '', note: `Status kon niet worden bepaald: ${e.message}`}));
    }
    paint();
  }, delay);
}

function mountStepper() {
  const slot = $id('workflow-slot');
  if (!slot || slot.dataset.wfMounted) return !!slot;
  slot.dataset.wfMounted = '1';
  buildStepper(slot);
  paint(); refresh(0);
  return true;
}

document.addEventListener('lab:run', () => { wf.cache.at = 0; wf.cache.runsAt = 0; refresh(200); });
document.addEventListener('lab:trade-selected', () => { markTrade(labState().activeRun); refresh(100); });
document.addEventListener('lab:validation', () => { wf.cache.at = 0; refresh(300); });
document.addEventListener('lab:forward', () => { refresh(300); });
document.addEventListener('lab:route', () => refresh(150));
document.addEventListener('change', e => { if (['strategy', 'symbol', 'timeframe', 'start', 'end'].includes(e.target?.id)) refresh(200); });

/* ------------------------------------------------------------------ BUILD 2: onboarding */
const OB_KEY = 'labOnboardV1';
const ob = {el: null, s: ls.get(OB_KEY, null) || {step: 0, done: false, skipped: false, ctx: {}}, busy: false, token: 0, abort: null, tpl: null};
const OB_STEPS = ['Welkom', 'Voorbeeldstrategie', 'Data', 'Backtest', 'Optimaliseren', 'Valideren', 'Forwardportfolio', 'Klaar'];
const saveOb = () => ls.set(OB_KEY, ob.s);
const ctx = () => ob.s.ctx;

function obShell() {
  if (ob.el) return ob.el;
  const el = document.createElement('section');
  el.className = 'wfo'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'false'); el.setAttribute('aria-labelledby', 'wfo-title'); el.hidden = true;
  el.innerHTML = `<div class="wfo-head"><h2 id="wfo-title" tabindex="-1"></h2><span class="wfo-count"></span>
      <button type="button" class="wfo-iconbtn" data-act="min" aria-label="Rondleiding inklappen" title="Inklappen">&#8211;</button>
      <button type="button" class="wfo-iconbtn" data-act="later" aria-label="Later verdergaan (voortgang bewaren)" title="Later verdergaan">&#10005;</button></div>
    <div class="wfo-bar" aria-hidden="true"><i></i></div><div class="wfo-body" aria-live="polite"></div>
    <div class="wfo-foot"><button type="button" class="wf-btn secondary" data-act="prev">Vorige</button>
      <button type="button" class="wf-btn secondary" data-act="skipstep">Overslaan</button><span class="grow"></span>
      <button type="button" class="wf-linkbtn" data-act="later">Later verdergaan</button>
      <button type="button" class="wf-btn" data-act="next">Volgende</button></div>`;
  document.body.append(el);
  el.addEventListener('click', e => {
    const a = e.target.closest('[data-act]')?.dataset.act; if (!a) return;
    if (a === 'min') { el.classList.toggle('min'); const m = el.classList.contains('min'); e.target.closest('[data-act]').setAttribute('aria-label', m ? 'Rondleiding uitklappen' : 'Rondleiding inklappen'); }
    else if (a === 'later') obClose(false);
    else if (a === 'prev') obGo(ob.s.step - 1);
    else if (a === 'next' || a === 'skipstep') obGo(ob.s.step + 1, a === 'skipstep');
  });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || el.hidden || e.defaultPrevented) return;
    if (e.target.closest?.('dialog[open], .drawer.open')) return;
    obClose(true);
  });
  ob.el = el;
  return el;
}

function obClose(skip) {
  ob.token++;
  ob.abort?.();
  if (skip) { ob.s.done = true; ob.s.skipped = true; }
  saveOb();
  if (ob.el) ob.el.hidden = true;
}

function obGo(n, skipped) {
  if (n < 0) return;
  if (n >= OB_STEPS.length) { ob.s.done = true; saveOb(); obClose(false); return; }
  ob.token++;
  ob.abort?.(); ob.abort = null;
  ob.s.step = n; ob.s.done = false; saveOb();
  obRender();
}

const body = () => ob.el.querySelector('.wfo-body');
const nextBtn = () => ob.el.querySelector('[data-act="next"]');
function setNextEnabled(on, label) { const b = nextBtn(); b.disabled = !on; if (label) b.textContent = label; }
const box = (cls, html) => `<div class="wfo-box ${cls || ''}">${html}</div>`;
const kv = items => `<div class="wfo-kv">${items.map(([k, v, c]) => `<div><span>${esc(k)}</span><strong class="${c || ''}">${esc(v)}</strong></div>`).join('')}</div>`;
const sgn = v => Number(v) > 0 ? 'pos' : Number(v) < 0 ? 'neg' : '';

function obRender() {
  const el = obShell(), n = ob.s.step, tok = ob.token;
  el.hidden = false;
  el.querySelector('#wfo-title').textContent = OB_STEPS[n];
  el.querySelector('.wfo-count').textContent = `${n + 1}/${OB_STEPS.length}`;
  el.querySelector('.wfo-bar i').style.width = `${((n + 1) / OB_STEPS.length) * 100}%`;
  el.querySelector('[data-act="prev"]').disabled = n === 0;
  el.querySelector('[data-act="skipstep"]').hidden = n === 0 || n === OB_STEPS.length - 1;
  setNextEnabled(true, n === OB_STEPS.length - 1 ? 'Afronden' : 'Volgende');
  body().innerHTML = '';
  const r = [obIntro, obStrategy, obData, obBacktest, obOptimize, obValidate, obForward, obDone][n];
  Promise.resolve(r(tok)).catch(err => { if (tok === ob.token) body().insertAdjacentHTML('beforeend', box('bad', `Er ging iets mis: ${esc(err.message)}`)); });
  el.querySelector('#wfo-title').focus({preventScroll: true});
}
const alive = tok => tok === ob.token;

/* --- shared helpers */
async function templates() {
  if (!ob.tpl) ob.tpl = (await api('/api/builder/templates')).templates || {};
  return ob.tpl;
}
async function refreshStrategies(select) {
  const {strategies = []} = await api('/api/strategies');
  const st = labState();
  if (Array.isArray(st.strategies)) { st.strategies.length = 0; st.strategies.push(...strategies); }
  const sel = $id('strategy');
  if (sel) {
    const have = new Set([...sel.options].map(o => o.value));
    strategies.forEach(s => { if (!have.has(s.name)) { const o = document.createElement('option'); o.value = s.name; o.textContent = s.name + (s.managed_by_lab ? ' · Lab' : ''); sel.append(o); } });
    if (select && strategies.some(s => s.name === select)) { sel.value = select; sel.dispatchEvent(new Event('change', {bubbles: true})); }
  }
  return strategies;
}
function setField(id, value) {
  const f = $id(id); if (!f) return false;
  f.value = value;
  f.dispatchEvent(new Event('input', {bubbles: true}));
  f.dispatchEvent(new Event('change', {bubbles: true}));
  return f.value === String(value);
}
function ensureSelected() {
  // the tester reads these inputs at run time: re-apply the example choices if the user touched them
  const c = ctx();
  if (c.strategy && val('strategy') !== c.strategy) setField('strategy', c.strategy);
  for (const [id, k] of [['symbol', 'symbol'], ['timeframe', 'tf'], ['start', 'start'], ['end', 'end']]) if (c[k] && val(id) !== c[k]) setField(id, c[k]);
}
function progress(indet) { return `<div class="wfo-progress ${indet ? 'indet' : ''}" role="progressbar" aria-label="Voortgang"><i></i></div>`; }
function routeBtns(list) { return `<div class="wfo-routes">${list.map(([l, r]) => `<button type="button" class="wf-btn secondary" data-route="${esc(r)}">${esc(l)}</button>`).join('')}</div>`; }
function wireRoutes(root) { root.querySelectorAll('[data-route]').forEach(b => b.onclick = () => go(b.dataset.route)); }

/* Runs a tester action (backtest/optimize) and resolves with the new run's real {manifest,result}. */
async function runTester(action, tok, host) {
  if (typeof execute !== 'function') throw new Error('De tester (execute) is niet beschikbaar op deze pagina.');
  const before = labState().activeRun || null;
  const t0 = Date.now();
  host.innerHTML = `${progress(true)}<p class="muted" id="wfo-elapsed">Bezig… 0 s. Deze run kan niet worden geannuleerd; je kunt wel doorgaan via Overslaan.</p>`;
  const tick = setInterval(() => { const e = $id('wfo-elapsed'); if (e) e.textContent = `Bezig… ${Math.round((Date.now() - t0) / 1000)} s. Deze run kan niet worden geannuleerd; je kunt wel doorgaan via Overslaan.`; }, 1000);
  try { await execute(action); } finally { clearInterval(tick); }
  const id = labState().activeRun;
  if (!id || id === before) {
    const msg = $id('run-status')?.textContent?.trim();
    throw new Error(msg || 'De run leverde geen nieuw resultaat op.');
  }
  const data = await api(`/api/runs/${encodeURIComponent(id)}`);
  if (!data.manifest || data.manifest.action !== action) throw new Error('De run leverde geen nieuw resultaat op.');
  return {runId: id, ...data};
}

/* --- 1 intro */
function obIntro() {
  body().innerHTML = `<p>Dit lab onderzoekt handelsideeën <b>op je eigen Dukascopy-data</b>. In deze rondleiding doe je een echt mini-onderzoek: je ziet echte resultaten, geen voorbeeldcijfers.</p>
    <p><b>Vier soorten bewijs</b> worden strikt gescheiden:</p>
    <ul><li><b>Historische backtest</b>: de strategie op bekende data. Zwakste bewijs; kan toeval of overfitting zijn.</li>
      <li><b>Historische out-of-sample (OOS)</b>: getest op data die niet voor het kiezen van regels is gebruikt.</li>
      <li><b>Dagelijks vertraagde forwardtest</b>: bevroren regels, gevolgd op nieuwe data die dagelijks met vertraging binnenkomt.</li>
      <li><b>Realtime papertrading</b>: <b>niet beschikbaar</b>. Dit lab plaatst nooit echte of live orders.</li></ul>
    ${box('', 'Je kunt elke stap overslaan, met Esc de rondleiding sluiten, of via Later verdergaan hier later hervatten.')}`;
}

/* --- 2 example strategy */
async function obStrategy(tok) {
  const host = body();
  host.innerHTML = '<p class="muted">Sjabloon laden…</p>';
  const tpl = (await templates()).ema_rsi_pullback;
  if (!alive(tok)) return;
  if (!tpl) { host.innerHTML = box('bad', 'Sjabloon ema_rsi_pullback is niet beschikbaar op de server.'); return; }
  const name = tpl.name;
  const draw = async () => {
    const [strategies, {specs = []}] = await Promise.all([api('/api/strategies').then(r => r.strategies || []), soft(api('/api/builder/specs'), {specs: []})]);
    if (!alive(tok)) return;
    const hasStrat = strategies.some(s => s.name === name), hasSpec = specs.some(s => s.name === name);
    const ready = hasStrat && hasSpec;
    host.innerHTML = `<p>We gebruiken het Builder-sjabloon <b>${esc(name)}</b>: ${esc(tpl.description || '')}</p>
      <p>Opslaan maakt twee dingen: een <b>Jesse-strategie</b> (die je in de tester kunt backtesten) en een <b>regelspecificatie</b> (waarmee statistische validatie mogelijk is). Er worden geen orders geplaatst.</p>
      ${ready ? box('good', `${esc(name)} bestaat al en wordt hergebruikt.`) : hasStrat ? box('warn', `Er bestaat al een strategie met de naam ${esc(name)}, maar zonder regelspecificatie. Validatie is dan niet mogelijk; kies Overslaan of verwijder die strategie eerst.`) : '<div id="wfo-act"><button type="button" class="wf-btn" id="wfo-save">Maak voorbeeldstrategie aan</button></div>'}
      <div id="wfo-msg" role="status"></div>`;
    if (ready) { await refreshStrategies(name); ctx().strategy = name; saveOb(); setNextEnabled(true); return; }
    setNextEnabled(false);
    if (hasStrat) { setNextEnabled(true); return; }
    $id('wfo-save').onclick = async e => {
      e.target.disabled = true;
      try {
        await api('/api/builder/save', {method: 'POST', body: {spec: tpl, overwrite: false}});
        await refreshStrategies(name);
        refresh(100);
        await draw();
      } catch (err) {
        e.target.disabled = false;
        $id('wfo-msg').innerHTML = box('bad', `Opslaan mislukt: ${esc(err.message)}`);
      }
    };
  };
  await draw();
}

/* --- 3 data */
async function obData(tok) {
  const host = body();
  if (!ctx().strategy) { host.innerHTML = box('warn', 'Er is nog geen voorbeeldstrategie klaargezet. Ga terug naar de vorige stap, of kies zelf een strategie in de testerbalk.'); }
  host.insertAdjacentHTML('beforeend', '<p class="muted" id="wfo-load">Beschikbare data controleren…</p>');
  const symbol = 'XAUUSD', tf = 'M15';
  const meta = await getMeta(symbol);
  if (!alive(tok)) return;
  $id('wfo-load')?.remove();
  if (!meta || meta.error || !meta.last_utc) { host.insertAdjacentHTML('beforeend', box('bad', `Geen ${symbol}-data beschikbaar${meta?.error ? `: ${esc(meta.error)}` : ''}. Kies zelf een instrument onder Data.`)); setNextEnabled(true); return; }
  const first = new Date(meta.first_utc), last = new Date(meta.last_utc);
  const end = isoDay(last);
  const startD = new Date(last.getTime() - 90 * 86400000);
  const start = isoDay(startD < first ? first : startD);
  const vStart = isoDay(new Date(Math.max(first.getTime(), last.getTime() - 3 * 365 * 86400000)));
  Object.assign(ctx(), {symbol, tf, start, end, valStart: vStart, valEnd: end}); saveOb();
  const hasSym = [...($id('symbol')?.options || [])].some(o => o.value === symbol), hasTf = [...($id('timeframe')?.options || [])].some(o => o.value === tf);
  host.insertAdjacentHTML('beforeend', `<p>Je Dukascopy-data voor <b>${symbol}</b> loopt van <b>${esc(isoDay(first))}</b> tot <b>${esc(end)}</b>. We testen op de meest recente ~3 maanden met <b>${tf}</b>-candles.</p>
    ${kv([['Instrument', symbol], ['Tijdframe', tf], ['Van', start], ['Tot en met', end], ['Rekenmodel', 'Futures (de voorbeeldstrategie gaat ook short)']])}
    ${hasSym && hasTf ? '<div><button type="button" class="wf-btn" id="wfo-apply">Instellingen toepassen</button></div>' : box('bad', `${!hasSym ? `${symbol} staat niet in de datalijst. ` : ''}${!hasTf ? `${tf} staat niet in de tijdframelijst.` : ''}`)}
    <div id="wfo-msg" role="status"></div>`);
  const apply = $id('wfo-apply');
  if (!apply) return;
  const doApply = () => {
    const ok = [setField('symbol', symbol), setField('timeframe', tf), setField('start', start), setField('end', end), setField('account-type', 'futures')];
    $id('wfo-msg').innerHTML = ok.every(Boolean) ? box('good', 'Ingesteld in de testerbalk.') : box('warn', 'Niet alle velden namen de waarde over; controleer de testerbalk.');
    refresh(100);
  };
  apply.onclick = doApply;
  doApply();
}

/* --- 4 backtest */
function metricsView(res) {
  const m = res.result?.metrics || {};
  const trades = m.total ?? (res.result?.trades || []).length;
  return kv([['Netto resultaat', nf(m.net_profit), sgn(m.net_profit)], ['Trades', nf(trades, 0)],
    ['Winstpercentage', m.win_rate === undefined || m.win_rate === null ? '—' : `${nf(Number(m.win_rate) * 100, 1)}%`], ['Max. drawdown', m.max_drawdown === undefined || m.max_drawdown === null ? '—' : `${nf(m.max_drawdown)}%`, sgn(m.max_drawdown)]]);
}
async function obBacktest(tok) {
  const host = body();
  const c = ctx();
  host.innerHTML = `<p>Nu laten we de strategie de gekozen periode historisch doorlopen met Jesse. ${c.strategy ? `Strategie: <b>${esc(c.strategy)}</b>.` : ''}</p><div id="wfo-out"></div>`;
  const out = $id('wfo-out');
  const showResult = async res => {
    let interp = null;
    try { interp = await api(`/api/runs/${encodeURIComponent(res.runId)}/interpretation`); } catch { /* optional endpoint */ }
    if (!alive(tok)) return;
    out.innerHTML = `${metricsView(res)}
      ${interp?.headline_nl ? box(interp.evidence_class === 'HISTORICAL_BACKTEST' ? '' : 'warn', `<b>${esc(interp.evidence_label_nl || 'Duiding')}</b><br>${esc(interp.headline_nl)}`) : ''}
      <p>Open nu <b>Trades</b> en klik op een trade: in de grafiek zie je dan entry, exit, stoploss en take-profit van precies die trade.</p>
      <div class="wfo-routes"><button type="button" class="wf-btn secondary" data-route="onderzoeken/trades">Open Trades</button><button type="button" class="wf-btn secondary" data-route="onderzoeken/grafiek">Open Grafiek</button></div>`;
    wireRoutes(out);
    setNextEnabled(true);
    go('onderzoeken/trades');
  };
  if (c.backtestRun) {
    try { const data = await api(`/api/runs/${encodeURIComponent(c.backtestRun)}`); await showResult({runId: c.backtestRun, ...data}); return; } catch { delete c.backtestRun; }
  }
  out.innerHTML = '<button type="button" class="wf-btn" id="wfo-run">Backtest starten</button>';
  $id('wfo-run').onclick = async () => {
    ensureSelected();
    setNextEnabled(false);
    try {
      const res = await runTester('backtest', tok, out);
      if (!alive(tok)) return;
      c.backtestRun = res.runId; saveOb();
      await showResult(res);
    } catch (err) {
      if (!alive(tok)) return;
      out.innerHTML = `${box('bad', `Backtest mislukt: ${esc(err.message)}`)}<button type="button" class="wf-btn" id="wfo-retry">Opnieuw proberen</button>`;
      $id('wfo-retry').onclick = () => obBacktest(tok);
      setNextEnabled(true);
    }
  };
}

/* --- 5 optimize */
async function obOptimize(tok) {
  const host = body(), c = ctx(), TRIALS = 8;
  host.innerHTML = `<p>De optimizer probeert <b>${TRIALS} parametercombinaties</b> (bewust klein, zodat het snel klaar is) en scoort ze op het trainingsdeel van de data.</p><div id="wfo-out"></div>`;
  const out = $id('wfo-out');
  const show = res => {
    const r = res.result || {}, best = (r.best_trials || [])[0], opt = r.optimization || {};
    const counts = Object.entries(opt.outcome_counts || {}).map(([k, v]) => `${k}: ${v}`).join(', ');
    const tm = best?.training_metrics || {}, te = best?.testing_metrics || {};
    out.innerHTML = best ? `${kv([['Beste trial', `#${best.trial}`], [`Score (${best.objective || opt.objective_function || 'doel'})`, nf(best.score, 3)], ['Train netto', nf(tm.net_profit), sgn(tm.net_profit)], ['Train trades', nf(tm.total, 0)]])}
        <p class="muted">${opt.completed_trials ?? '?'}/${opt.total_trials ?? '?'} trials afgerond${counts ? ` · uitkomsten: ${esc(counts)}` : ''}. Holdout-trades in deze trial: ${nf(te.total, 0)}.</p>`
      : box('warn', `Geen enkele trial gaf een geldig resultaat${counts ? ` (${esc(counts)})` : ''}. Dat is normaal bij zo weinig trials; probeer later meer trials in Optimalisatie.`);
    out.insertAdjacentHTML('beforeend', box('warn', '<b>Let op, selectiebias:</b> de beste van meerdere pogingen ziet er altijd beter uit dan hij is. Zo\'n winnaar is pas iets waard na validatie op data die de optimizer nooit zag.') +
      routeBtns([['Open Optimalisatie', 'onderzoeken/optimalisatie']]));
    wireRoutes(out);
    setNextEnabled(true);
    go('onderzoeken/optimalisatie');
  };
  if (c.optimizeRun) {
    try { const data = await api(`/api/runs/${encodeURIComponent(c.optimizeRun)}`); show({runId: c.optimizeRun, ...data}); return; } catch { delete c.optimizeRun; }
  }
  out.innerHTML = '<button type="button" class="wf-btn" id="wfo-run">Optimalisatie starten</button>';
  $id('wfo-run').onclick = async () => {
    ensureSelected();
    setField('trials', TRIALS);
    setNextEnabled(false);
    try {
      const res = await runTester('optimize', tok, out);
      if (!alive(tok)) return;
      c.optimizeRun = res.runId; saveOb();
      show(res);
    } catch (err) {
      if (!alive(tok)) return;
      out.innerHTML = `${box('bad', `Optimalisatie mislukt: ${esc(err.message)}`)}<button type="button" class="wf-btn" id="wfo-retry">Opnieuw proberen</button>`;
      $id('wfo-retry').onclick = () => obOptimize(tok);
      setNextEnabled(true);
    }
  };
}

/* --- 6 validate */
const VERDICT_TEXT = {
  REJECT: 'Afwijzen: een of meer harde controles faalden. Het resultaat is niet houdbaar bewezen.',
  NEEDS_MORE_DATA: 'Meer data nodig: er is te weinig bewijs (bijvoorbeeld te weinig trades) om iets te concluderen.',
  RESEARCH_CANDIDATE: 'Researchkandidaat: geslaagd op historische data, nog zonder voldoende out-of-sample bewijs.',
  OOS_PASSED: 'Out-of-sample geslaagd: houdt stand op ongeziene historische data.',
  FORWARD_ELIGIBLE: 'Geschikt voor forward test: alle kernchecks geslaagd.'};
function validationView(j) {
  const r = j.result || {}, sc = r.scorecard || {}, checks = sc.checks || [];
  const failed = checks.filter(c => c.status === 'FAIL'), unknown = checks.filter(c => c.status !== 'PASS' && c.status !== 'FAIL');
  return `${box(sc.verdict === 'FORWARD_ELIGIBLE' || sc.verdict === 'OOS_PASSED' ? 'good' : sc.verdict === 'REJECT' ? 'bad' : 'warn',
      `<b>${esc(VERDICT_NL[sc.verdict] || sc.verdict || 'Onbekend')}</b> <span class="muted">(${esc(sc.verdict || '?')})</span><br>${esc(VERDICT_TEXT[sc.verdict] || '')}`)}
    <p class="muted">${checks.length - failed.length - unknown.length} van ${checks.length} controles geslaagd · ${esc(r.n_trials ?? '?')} hypotheses meegeteld${r.ledger_id ? ` · logboek ${esc(String(r.ledger_id).slice(0, 8))}` : ''}.</p>
    ${failed.length ? `<p><b>Gefaalde controles:</b></p><ul class="wfo-checks">${failed.map(c => `<li><span class="tag">FAIL</span><span><b>${esc(c.name)}</b>${c.explanation_nl ? `: ${esc(c.explanation_nl)}` : ''}</span></li>`).join('')}</ul>` : ''}
    ${unknown.length ? `<p class="muted">Niet te toetsen: ${esc(unknown.map(c => c.name).join(', '))}.</p>` : ''}
    <p class="muted">Het verdict is geen voorspelling of kans op winst, maar een samenvatting van welke controles slaagden.</p>`;
}
async function pollJob(id, tok, onUpdate) {
  for (;;) {
    const j = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (!alive(tok)) return null;
    onUpdate(j);
    if (['done', 'error', 'cancelled'].includes(j.status)) return j;
    await sleep(1200);
  }
}
async function obValidate(tok) {
  const host = body(), c = ctx();
  host.innerHTML = `<p>Nu de echte toets: <b>walk-forward</b>, <b>Monte Carlo</b>, kostenstress en een eenmalige <b>holdout</b> op een langere periode${c.valStart ? ` (${esc(c.valStart)} t/m ${esc(c.valEnd)})` : ''}. Dit kan enkele minuten duren. De uitkomst wordt vastgelegd in het onderzoekslogboek (alleen toevoegen, nooit wijzigen).</p><div id="wfo-out"></div>`;
  const out = $id('wfo-out');
  const finish = j => {
    if (j.status === 'done') {
      out.innerHTML = `${validationView(j)}${routeBtns([['Open Validatie', 'onderzoeken/validatie']])}`;
      wireRoutes(out);
      c.verdict = j.result?.scorecard?.verdict || null; c.failedChecks = (j.result?.scorecard?.checks || []).filter(x => x.status === 'FAIL').map(x => x.name); saveOb();
      fire('lab:validation', {result: j.result, specName: c.strategy, source: 'onboarding'});
      setNextEnabled(true);
    } else {
      out.innerHTML = `${box(j.status === 'cancelled' ? 'warn' : 'bad', j.status === 'cancelled' ? 'Validatie geannuleerd.' : `Validatie mislukt: ${esc(j.error || j.status)}`)}<button type="button" class="wf-btn" id="wfo-retry">Opnieuw starten</button>`;
      $id('wfo-retry').onclick = () => { delete c.valJob; saveOb(); obValidate(tok); };
      setNextEnabled(true);
    }
  };
  const follow = async jobId => {
    out.innerHTML = `${progress(false)}<p class="muted" id="wfo-jobmsg">Taak gestart…</p><button type="button" class="wf-btn secondary" id="wfo-cancel">Annuleren</button>`;
    ob.abort = () => { /* polling stops via token */ };
    $id('wfo-cancel').onclick = async e => { e.target.disabled = true; try { await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {method: 'POST'}); } catch (err) { $id('wfo-jobmsg').textContent = `Annuleren mislukt: ${err.message}`; } };
    setNextEnabled(false);
    const j = await pollJob(jobId, tok, job => {
      const p = Math.round((job.progress || 0) * 100), bar = out.querySelector('.wfo-progress i'), m = $id('wfo-jobmsg');
      if (bar) bar.style.width = `${p}%`;
      if (m) m.textContent = `${job.status} · ${p}% · ${job.message || ''}`;
    });
    if (j && alive(tok)) { delete c.valJob; saveOb(); finish(j); }
  };
  if (c.valJob) {
    const existing = await soft(api(`/api/jobs/${encodeURIComponent(c.valJob)}`), null);
    if (!alive(tok)) return;
    if (existing) { if (['done', 'error', 'cancelled'].includes(existing.status)) finish(existing); else await follow(c.valJob); return; }
    delete c.valJob;
  }
  if (!c.strategy) { out.innerHTML = box('warn', 'Geen voorbeeldstrategie klaargezet; vorige stappen overgeslagen. Je kunt zelf valideren via Onderzoeken → Validatie.') + routeBtns([['Open Validatie', 'onderzoeken/validatie']]); wireRoutes(out); return; }
  out.innerHTML = `<div class="wfo-routes"><button type="button" class="wf-btn" id="wfo-run">Validatie starten</button><button type="button" class="wf-btn secondary" data-route="onderzoeken/validatie">Zelf starten in Validatie</button></div><div id="wfo-msg" role="status"></div>`;
  wireRoutes(out);
  $id('wfo-run').onclick = async e => {
    e.target.disabled = true;
    try {
      const {spec} = await api(`/api/builder/specs/${encodeURIComponent(c.strategy)}`);
      const payload = {spec: {...spec, symbol: c.symbol || spec.symbol, timeframe: c.tf || spec.timeframe}, start: c.valStart, end: c.valEnd, holdout_percent: 20,
        walk_forward: {train_days: 120, test_days: 30, step_days: 30, mode: 'rolling', max_combos: 12, min_trades: 10}, monte_carlo: {n: 1000, method: 'block_bootstrap'}};
      const {job_id} = await api('/api/validate', {method: 'POST', body: payload});
      c.valJob = job_id; saveOb();
      await follow(job_id);
    } catch (err) {
      e.target.disabled = false;
      $id('wfo-msg').innerHTML = box('bad', `Validatie starten mislukt: ${esc(err.message)}`);
      setNextEnabled(true);
    }
  };
}

/* --- 7 forward */
async function obForward(tok) {
  const host = body(), c = ctx();
  host.innerHTML = `<p>Alleen kandidaten met verdict <b>FORWARD_ELIGIBLE</b> mogen worden bevroren en dagelijks vertraagd op nieuwe data gevolgd. Dat is <b>geen realtime papertrading</b>; er worden geen orders geplaatst.</p><p class="muted" id="wfo-load">Status controleren…</p>`;
  let life = null, entry = null;
  if (c.strategy) life = await soft(api(`/api/lifecycle/${encodeURIComponent(c.strategy)}`), null);
  if (life?.last_ledger_id) entry = (await soft(api(`/api/ledger/${encodeURIComponent(life.last_ledger_id)}`), {})).entry || null;
  if (!alive(tok)) return;
  $id('wfo-load')?.remove();
  const verdict = life?.last_verdict || c.verdict || null;
  if (!c.strategy) host.insertAdjacentHTML('beforeend', box('warn', 'Er is geen voorbeeldstrategie gebruikt, dus er is hier niets te beoordelen.'));
  else if (!verdict) host.insertAdjacentHTML('beforeend', box('warn', 'Er is nog geen validatie uitgevoerd, dus het voorbeeld kwalificeert nog niet. Ontbrekende check: statistische validatie.'));
  else if (verdict === 'FORWARD_ELIGIBLE') host.insertAdjacentHTML('beforeend', box('good', `<b>${esc(c.strategy)}</b> heeft verdict FORWARD_ELIGIBLE en kan in Portfolio → Forward worden bevroren en toegevoegd.`));
  else {
    const failed = (entry?.rejection_reason || '').split(';').map(x => x.trim()).filter(Boolean);
    const list = failed.length ? failed : c.failedChecks || [];
    host.insertAdjacentHTML('beforeend', box('bad', `<b>${esc(c.strategy)}</b> kwalificeert <b>niet</b>: verdict ${esc(VERDICT_NL[verdict] || verdict)} (${esc(verdict)}).${list.length ? ` Gefaalde controles: ${esc(list.join(', '))}.` : ''} Dat is een normale uitkomst; de meeste ideeën halen dit niet, en dat is precies waarvoor de toets bestaat.`));
  }
  host.insertAdjacentHTML('beforeend', routeBtns([['Open Forwardportfolio', 'portfolio/forward']]));
  wireRoutes(host);
}

/* --- 8 done */
function obDone() {
  body().innerHTML = `<p>Klaar. Zo is het lab ingedeeld:</p>
    <ul><li><b>Home</b>: overzicht en status van je onderzoek.</li>
      <li><b>Strategieën</b>: overzicht, Bouwen (regels zonder code) en Bibliotheek.</li>
      <li><b>Onderzoeken</b>: backtest, grafiek, trades, inspector, optimalisatie, validatie, journaal en automatisch zoeken.</li>
      <li><b>Portfolio</b>: forward volgen en risico.</li>
      <li><b>Data</b>: welke Dukascopy-data beschikbaar is en hoe vers.</li></ul>
    ${routeBtns([['Home', 'home'], ['Strategieën', 'strategieen/overzicht'], ['Onderzoeken', 'onderzoeken/backtest'], ['Portfolio', 'portfolio/forward'], ['Data', 'data']])}
    <p class="muted">Je kunt deze rondleiding opnieuw starten via het Help-menu.</p>`;
  wireRoutes(body());
}

window.labOnboarding = {
  start(opts) {
    ob.s = ls.get(OB_KEY, null) || {step: 0, done: false, skipped: false, ctx: {}};
    if (ob.s.done || (opts && opts.restart)) ob.s = {step: 0, done: false, skipped: false, ctx: {}};
    saveOb();
    obShell().classList.remove('min');
    obRender();
  },
  isDone() { return !!ls.get(OB_KEY, null)?.done; },
};

/* ------------------------------------------------------------------ boot */
function boot() {
  if (!mountStepper()) {
    const obs = new MutationObserver(() => { if (mountStepper()) obs.disconnect(); });
    obs.observe(document.body, {childList: true, subtree: true});
  }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();

/* V10 UI redesign add-on: Mijn strategieën library, multi-market drawer, Analyse view, data status.
   Pure client code: every number shown comes from an API response; missing endpoints give a calm Dutch notice. */
(() => {
'use strict';
const S = window.labShell;
if (!S) return;
const {api, esc, fmt, shortDate} = S;
const q = (sel, root = document) => root.querySelector(sel);
const qa = (sel, root = document) => [...root.querySelectorAll(sel)];
const num = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
const pc = (v, d = 1) => num(v) ? `${fmt(v, d)}%` : '—';
const first = (o, ...ks) => { for (const k of ks) if (o && o[k] !== undefined && o[k] !== null) return o[k]; return undefined; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const mlabel = s => S.MARKET_LABEL[s] || s;
const tip = t => S.info(t);
const chip = (cls, text, t) => `<span class="chip ${cls}" ${t ? `tabindex="0" data-tip="${esc(t)}"` : ''}>${esc(text)}</span>`;
const css = name => getComputedStyle(document.body).getPropertyValue(name).trim();
const unavailable = what => `<div class="v10-notice">${esc(what)} is nog niet beschikbaar op deze server. Probeer het later opnieuw of werk de server bij.</div>`;
const SERIES = ['#4a90d9', '#3fc488', '#e8b04a', '#ef6f6f', '#a77bd9', '#4fc3c3', '#d9884a', '#8aa1b5'];
const plotBase = (extra = {}) => ({
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: {color: css('--ink-2') || '#9fb1c3', size: 11},
  margin: {l: 52, r: 12, t: 10, b: 32}, legend: {orientation: 'h', y: -0.2}, hovermode: 'x unified',
  xaxis: {gridcolor: css('--edge') || '#233140'}, yaxis: {gridcolor: css('--edge') || '#233140'}, ...extra,
});
/* Plotly work per element is serialised: a purge never overtakes a draw that is still finishing (avoids stale rehover errors). */
const queue = (el, fn) => { el._pq = (el._pq || Promise.resolve()).then(fn).catch(() => {}); return el._pq; };
const purge = el => { if (el && window.Plotly) queue(el, () => { try { Plotly.purge(el); } catch { /* not a plot */ } }); };
const clearPlot = (el, html = '') => queue(el, () => { try { Plotly.purge(el); } catch { /* not a plot */ } el.innerHTML = html; });
const plot = (el, data, layout) => queue(el, () => { if (!el._fullLayout) el.innerHTML = ''; if (el.isConnected) return Plotly.react(el, data, plotBase(layout), {displayModeBar: false, responsive: true}); });
const resizePlots = () => requestAnimationFrame(() => qa('.js-plotly-plot').forEach(el => { if (el._fullLayout && el.offsetParent) { try { Promise.resolve(Plotly.Plots.resize(el)).catch(() => {}); } catch { /* ignore */ } } }));

/* =============================================================== data status */
const kindOf = s => {
  const t = String(s || '');
  if (/closed|dicht/i.test(t)) return 'idle';
  if (/err|fout|fail|stuck/i.test(t)) return 'bad';
  if (/partial|behind|achter|delay|lag|gedeeltelijk|backoff|wacht|pending|stale|catch/i.test(t)) return 'warn';
  if (/ok|actueel|up.?to.?date|current|synced|bijgewerkt|fresh|good|ready/i.test(t)) return 'good';
  return 'idle';
};
const STATUS_NL = {good: 'bijgewerkt', warn: 'gedeeltelijk achter', bad: 'fout', idle: 'onbekend'};

async function dataStatus() {
  try {
    const d = await api('/api/data/supervisor');
    const syms = d.symbols || [];
    const lagging = syms.filter(x => !x.market_closed && (/BEHIND|DELAY|BACKOFF|CATCH|PARTIAL|PENDING|ERROR|FAIL/i.test(x.status || '') || Number(x.lag_days) > 0));
    let kind = kindOf(d.overall);
    if (kind === 'good' && lagging.length) kind = 'warn';
    if (kind === 'idle' && lagging.length) kind = 'warn';
    const detail = lagging.length ? `${lagging.length} van ${syms.length} instrumenten lopen achter${d.overall_nl && !/lopen achter/.test(d.overall_nl) ? ` · ${d.overall_nl}` : ''}` : (d.overall_nl || '');
    return {source: 'supervisor', raw: d, kind, text: kind === 'good' ? 'bijgewerkt' : STATUS_NL[kind], detail, checked: d.last_check};
  } catch (err) {
    if (err.status && err.status !== 404) throw err;
  }
  const m = await api('/api/data/monitor').catch(() => ({configured: false, symbols: []}));
  const syms = m.symbols || [];
  if (!m.configured) return {source: 'monitor', raw: m, kind: 'idle', text: 'niet ingesteld', detail: 'De dagelijkse update is nog niet ingesteld.'};
  const bad = syms.filter(s => /ERROR|FAIL/i.test(s.status || '')).length;
  const ok = syms.filter(s => /UP.?TO.?DATE|MARKET.?CLOSED/i.test(s.status || '')).length;
  const kind = bad ? 'bad' : ok === syms.length ? 'good' : 'warn';
  return {source: 'monitor', raw: m, kind, text: STATUS_NL[kind], detail: `${ok} van ${syms.length} symbolen actueel`};
}

async function dataLine(el) {
  if (!el) return;
  try {
    const d = await dataStatus();
    el.innerHTML = `<b>Marktdata:</b> ${chip(d.kind, d.text)}<span class="grow">${esc(d.detail || '')}${d.checked ? ` · laatste controle ${esc(shortDate(d.checked))} UTC` : ''}</span><button type="button" class="secondary" data-go="data">Details</button>`;
  } catch (err) {
    el.innerHTML = `<b>Marktdata:</b> ${chip('bad', 'fout')}<span class="grow">${esc(err.message)}</span><button type="button" class="secondary" data-go="data">Details</button>`;
  }
}

const SYM_STATUS_NL = {UP_TO_DATE: 'Actueel', MARKET_CLOSED: 'Markt dicht', BEHIND: 'Loopt achter', DELAYED: 'Loopt achter', ERROR: 'Fout', BACKOFF: 'Wacht (backoff)', CATCHING_UP: 'Haalt achterstand in', PARTIAL: 'Gedeeltelijk', PENDING: 'Wacht', UNKNOWN: 'Onbekend', NOT_CONFIGURED: 'Niet ingesteld'};
const symStatus = s => { const k = String(s.market_closed && !/ERR/i.test(s.status || '') && !/UP_TO/i.test(s.status || '') ? 'MARKET_CLOSED' : s.status || 'UNKNOWN').toUpperCase().replace(/ /g, '_'); return chip(kindOf(k), SYM_STATUS_NL[k] || k.replace(/_/g, ' ').toLowerCase()); };
function qualityCell(qv) {
  if (qv === null || qv === undefined) return '—';
  if (typeof qv !== 'object') return esc(qv);
  const problems = [];
  if (qv.bid_ask === false) problems.push('Bid/Ask ontbreekt');
  if (qv.scale_ok === false) problems.push('prijsschaal wijkt af');
  if (qv.utc === false) problems.push('tijdzone niet UTC');
  if (qv.duplicates) problems.push(`${qv.duplicates} dubbele candles`);
  if (qv.negative_spread) problems.push(`${qv.negative_spread} negatieve spreads`);
  if (qv.incomplete_days) problems.push(`${qv.incomplete_days} onvolledige dagen`);
  if (qv.gaps) problems.push(`${qv.gaps} gaten`);
  if (qv.revisions) problems.push(`${qv.revisions} herzieningen`);
  return problems.length ? chip('warn', `${problems.length} aandachtspunt${problems.length > 1 ? 'en' : ''}`, problems.join('\n')) : chip('good', 'OK');
}
const when = v => v ? `${esc(shortDate(v))}` : '—';
const textOf = (o, ...ks) => { if (o === null || o === undefined) return '—'; if (typeof o !== 'object') return esc(o); return esc(first(o, ...ks) ?? '—'); };

async function loadData() {
  const root = S.body('data'); S.clearError('data');
  if (!root.dataset.v10) {
    root.dataset.v10 = '1';
    const hosted = q('[data-hosted="data-monitor-dialog"]', root);
    root.insertAdjacentHTML('afterbegin', `<div id="dsup" class="dsup">
      <div class="data-head"><div class="grow"><span id="dsup-badge"></span> <span id="dsup-text" class="muted"></span></div><button type="button" id="dsup-run">Nu controleren en synchroniseren</button></div>
      <div id="dsup-status" class="status" role="status" aria-live="polite"></div>
      <div class="data-meta" id="dsup-meta"></div>
      <div class="card"><div class="scroll"><table class="tbl data-tbl"><thead><tr><th>Symbool</th><th>Laatste candle (UTC)</th><th>Verwacht t/m</th><th>Achterstand</th><th>Status</th><th>Toelichting</th><th>Volgende poging</th><th>Kwaliteit</th><th>Snapshot</th></tr></thead><tbody id="dsup-rows"></tbody></table></div></div>
      <details class="accordion data-sub" id="dsup-caps"><summary>Beschikbare databronnen</summary><div class="acc-body" id="dsup-caps-body"></div></details></div>`);
    if (hosted) { hosted.hidden = true; hosted.id = 'dsup-legacy'; }
    q('#dsup-run').onclick = async () => {
      const b = q('#dsup-run'), st = q('#dsup-status'); b.disabled = true; st.textContent = 'Controle en synchronisatie gestart…';
      try { await api('/api/data/supervisor/check-sync', {method: 'POST'}); st.textContent = 'Gestart. De tabel ververst automatisch.'; }
      catch (err) { st.textContent = err.status === 404 ? 'Deze server ondersteunt nog geen gecombineerde controle.' : err.message; }
      b.disabled = false; setTimeout(loadData, 3500); setTimeout(loadData, 12000);
    };
    q('#dsup-caps').addEventListener('toggle', async () => {
      const body = q('#dsup-caps-body'); if (!q('#dsup-caps').open || body.dataset.ready) return; body.dataset.ready = '1';
      try { const caps = await api('/api/capabilities'); body.innerHTML = `<table class="tbl"><thead><tr><th>Databron</th><th>Status</th><th>Toelichting</th></tr></thead><tbody>${(caps.capabilities || []).map(c => `<tr><td>${esc(c.name)}</td><td>${S.feedChip(c.status)}</td><td style="white-space:normal">${esc(c.note_nl || c.detail || c.explanation_nl || '')}</td></tr>`).join('')}</tbody></table>`; }
      catch (err) { body.textContent = err.message; }
    });
  }
  const d = await dataStatus();
  const legacy = q('#dsup-legacy'), mine = q('#dsup');
  if (d.source !== 'supervisor') {
    // supervisor not available on this server: keep the previous monitor page
    mine.hidden = true; if (legacy) legacy.hidden = false;
    await legacyLoadData(); return;
  }
  mine.hidden = false; if (legacy) legacy.hidden = true;
  const r = d.raw;
  q('#dsup-badge').innerHTML = chip(d.kind, d.text);
  q('#dsup-text').textContent = d.detail || '';
  const sch = r.scheduler || {}, wk = r.worker || {};
  const schText = sch.installed === false ? 'niet geïnstalleerd' : `${sch.enabled === false ? 'uitgeschakeld' : (sch.state || 'actief')}${sch.last_result_nl ? `, laatste run ${sch.last_result_nl}` : ''}`;
  const wkText = wk.alive === false ? 'niet actief' : `${wk.stuck ? 'vastgelopen' : (wk.state || 'actief')}${wk.last_error ? `, fout: ${wk.last_error}` : ''}`;
  q('#dsup-meta').innerHTML = [
    ['Laatste controle', `${when(r.last_check)}`], ['Laatste succes', `${when(r.last_success)}`],
    ['Planner', `${esc(schText)}${sch.next_run ? ` · volgende ${esc(shortDate(sch.next_run))}` : ''}${sch.missed_runs ? ` · ${sch.missed_runs} gemist` : ''}`],
    ['Achtergrondproces', `${esc(wkText)}${num(wk.next_check_s) ? ` · volgende controle over ${Math.round(wk.next_check_s)} s` : ''}`],
  ].map(([k, v]) => `<span>${esc(k)}: <b>${v}</b></span>`).join('');
  q('#dsup-rows').innerHTML = (r.symbols || []).map(s => `<tr><td><b title="${esc(s.symbol)}">${esc(mlabel(s.symbol))}</b></td><td>${when(s.last_candle)}</td><td>${esc(s.expected_day || '—')}</td><td>${num(s.lag_days) ? (s.lag_days === 0 ? '0 dagen' : `${s.lag_days} ${s.lag_days === 1 ? 'dag' : 'dagen'}`) : '—'}</td><td>${symStatus(s)}</td><td class="why">${esc(s.reason_nl || '')}</td><td>${s.backoff_until ? `wacht tot ${when(s.backoff_until)}` : when(s.next_attempt)}</td><td>${qualityCell(s.quality)}</td><td class="sha" title="${esc(s.snapshot_sha || '')}">${esc((s.snapshot_sha || '').slice(0, 8)) || '—'}</td></tr>`).join('') || '<tr><td colspan="9" class="muted">Geen symbolen gevonden.</td></tr>';
}
let legacyLoadData = async () => {};

/* =============================================================== library (Mijn strategieën) */
const lib = {rows: [], sort: {k: 'name', d: 1}, filter: {text: '', type: '', status: ''}, builderNames: new Set()};
const TYPE_NL = {jesse: 'Jesse-strategie', builder: 'Zelf gebouwd', plugin: 'Python-plugin', candidate: 'Automatisch ontdekt'};
const VERDICT_NL = {REJECT: 'Afgewezen', NEEDS_MORE_DATA: 'Meer data nodig', RESEARCH_CANDIDATE: 'Researchkandidaat', OOS_PASSED: 'Out-of-sample geslaagd', FORWARD_ELIGIBLE: 'Geschikt voor forward'};
const LIFE_NL = {IDEA: 'Idee', PROTOTYPE: 'Niet onderzocht', BACKTESTING: 'Backtest', OPTIMIZING: 'Optimaliseren', VALIDATION_FAILED: 'Validatie gefaald', BACKTEST_VALIDATED: 'Backtest gevalideerd', OOS_VALIDATED: 'OOS gevalideerd', FORWARD_TESTING: 'Forward test', FORWARD_FAILED: 'Forward gefaald', PAPER_ELIGIBLE: 'Paper', LIVE_REVIEW_REQUIRED: 'Review nodig', LIVE_ELIGIBLE: 'Review gereed', RETIRED: 'Gepensioneerd'};
const CAND_NL = {forward_ready: 'Klaar voor forward', frozen: 'Bevroren', oos_approved: 'OOS goedgekeurd', dev_validated: 'Ontwikkeling gevalideerd', wf_passed: 'Walk-forward gehaald', quick_passed: 'Snelle test gehaald'};

function statusOf(r) {
  if (r.kind === 'candidate') {
    const s = String(r.raw.status || '').toLowerCase(), st = String(r.raw.stage || '').toLowerCase();
    const key = r.raw.frozen_id ? 'frozen' : (CAND_NL[s] ? s : CAND_NL[st] ? st : s);
    return {group: key === 'frozen' || key === 'forward_ready' ? 'frozen' : 'candidate', cls: key === 'frozen' || key === 'forward_ready' ? 'good' : 'info', text: CAND_NL[key] || key.replace(/_/g, ' ') || 'Kandidaat'};
  }
  if (r.kind === 'plugin') return r.raw.error ? {group: 'untested', cls: 'bad', text: 'Fout in plugin', tip: r.raw.error} : {group: 'untested', cls: 'idle', text: 'Niet onderzocht'};
  const v = r.raw.last_verdict;
  if (v) return {group: v === 'REJECT' ? 'rejected' : v === 'NEEDS_MORE_DATA' ? 'untested' : 'candidate', cls: v === 'REJECT' ? 'bad' : v === 'NEEDS_MORE_DATA' ? 'warn' : v === 'RESEARCH_CANDIDATE' ? 'info' : 'good', text: VERDICT_NL[v] || v};
  const st = r.raw.status || 'PROTOTYPE';
  return {group: /FORWARD/.test(st) ? 'frozen' : /VALIDATED|ELIGIBLE|PAPER|REVIEW/.test(st) ? 'candidate' : /FAILED|RETIRED/.test(st) ? 'rejected' : 'untested', cls: /FAILED|RETIRED/.test(st) ? 'bad' : /VALIDATED|ELIGIBLE|FORWARD|PAPER|REVIEW/.test(st) ? 'good' : 'idle', text: LIFE_NL[st] || st};
}

async function fetchLibrary() {
  const soft = (p, d) => p.catch(() => d);
  const [life, jesse, specs, plug, runs] = await Promise.all([
    soft(api('/api/lifecycle'), {strategies: []}), soft(api('/api/strategies'), {strategies: []}), soft(api('/api/builder/specs'), {specs: []}),
    soft(api('/api/plugins'), {plugins: []}), soft(api('/api/factory/runs'), {runs: []}),
  ]);
  const rows = new Map();
  const jmap = new Map((jesse.strategies || []).map(s => [s.name, s]));
  const smap = new Map((specs.specs || []).map(s => [s.name, s]));
  lib.builderNames = new Set(smap.keys());
  const add = (name, data) => {
    const spec = smap.get(name), j = jmap.get(name), l = data || {};
    const builder = !!spec || l.origin === 'builder' || l.spec === true;
    rows.set(`s:${name}`, {key: `s:${name}`, kind: builder ? 'builder' : 'jesse', name, sub: l.description || '', family: builder ? 'Regelstrategie' : (j?.managed_by_lab ? 'Lab-strategie' : 'Python'),
      market: spec?.symbol || '', tf: spec?.timeframe || '', version: j?.sha256 ? j.sha256.slice(0, 8) : '', sha: j?.sha256 || '', updated: l.updated_at || '', raw: l, parameters: j?.parameters || []});
  };
  (life.strategies || []).forEach(l => add(l.name, l));
  jmap.forEach((_, n) => { if (!rows.has(`s:${n}`)) add(n, null); });
  smap.forEach((_, n) => { if (!rows.has(`s:${n}`)) add(n, null); });
  (plug.plugins || []).forEach(p => rows.set(`p:${p.id}`, {key: `p:${p.id}`, kind: 'plugin', name: p.name, sub: p.description || '', family: 'Python-plugin', market: (p.symbols || []).map(mlabel).join(', '), tf: p.base_timeframe || '', version: p.version || '', id: p.id, updated: '', raw: p}));
  // factory candidates: only runs that produced non-rejected or frozen candidates
  const runList = (runs.runs || []).filter(r => ['dev_validated', 'oos_approved', 'forward_ready', 'frozen', 'wf_passed', 'quick_passed'].some(k => Number(r.counts?.[k]) > 0)).slice(0, 12);
  const lists = await Promise.all(runList.map(r => soft(api(`/api/factory/runs/${encodeURIComponent(r.run_id)}/candidates`), [])));
  lists.forEach((l, i) => (Array.isArray(l) ? l : l.candidates || []).forEach(c => {
    const rej = /reject|afgew|fail/.test(`${c.status || ''}`.toLowerCase());
    if (rej && !c.frozen_id) return;
    const key = `c:${c.id}`; if (rows.has(key)) return;
    rows.set(key, {key, kind: 'candidate', name: c.name || c.id, sub: '', family: c.family || '', market: c.symbol || '', tf: c.timeframe || '', version: c.sha ? String(c.sha).slice(0, 8) : String(c.id).slice(0, 8), id: c.id, runId: runList[i].run_id, updated: runList[i].created_at || '', raw: c});
  }));
  lib.rows = [...rows.values()].map(r => ({...r, st: statusOf(r)}));
}

function libFiltered() {
  const {text, type, status} = lib.filter, t = text.trim().toLowerCase();
  const rows = lib.rows.filter(r => (!type || r.kind === type) && (!status || r.st.group === status) && (!t || `${r.name} ${r.family} ${r.market} ${r.tf} ${r.version} ${TYPE_NL[r.kind]}`.toLowerCase().includes(t)));
  const {k, d} = lib.sort;
  const val = r => k === 'type' ? TYPE_NL[r.kind] : k === 'market' ? `${r.market} ${r.tf}` : k === 'status' ? r.st.text : k === 'version' ? r.version : (r[k] || '');
  return rows.sort((a, b) => String(val(a)).localeCompare(String(val(b)), 'nl', {numeric: true}) * d);
}

const multiOk = r => ['builder', 'candidate', 'jesse', 'plugin'].includes(r.kind);
function renderLibraryRows() {
  const rows = libFiltered();
  q('#lib-count').textContent = `${rows.length} van ${lib.rows.length}`;
  qa('#lib-table th[data-sort]').forEach(th => th.setAttribute('aria-sort', th.dataset.sort === lib.sort.k ? (lib.sort.d > 0 ? 'ascending' : 'descending') : 'none'));
  q('#lib-rows').innerHTML = rows.map(r => `<tr data-key="${esc(r.key)}"><td class="name"><b>${esc(r.name)}</b>${r.sub ? `<span class="sub">${esc(r.sub)}</span>` : ''}</td>
    <td>${esc(TYPE_NL[r.kind])}${r.family && r.kind !== 'plugin' ? `<span class="sub">${esc(r.family)}</span>` : ''}</td>
    <td ${r.market.includes(',') ? `title="${esc(r.market)}"` : ''}>${r.market || r.tf ? `${esc(r.market.split(', ').length > 2 ? `${r.market.split(', ').length} markten` : r.market)} ${esc(r.tf)}` : '—'}</td>
    <td class="sha" ${r.sha ? `title="${esc(r.sha)}"` : ''}>${esc(r.version || '—')}</td>
    <td>${chip(r.st.cls, r.st.text, r.st.tip)}</td>
    <td class="act"><div class="act-main"><button type="button" class="secondary strong" data-lib-act="test">Onderzoeken</button><button type="button" class="secondary" data-lib-act="multi" ${multiOk(r) ? '' : 'disabled'} data-tip="${esc(multiOk(r) ? 'Dezelfde strategie op meerdere markten en timeframes testen.' : 'Dit type strategie kan niet op andere markten worden getest.')}">Test op andere markten</button></div>${r.kind === 'builder' || r.kind === 'jesse' ? `<div class="act-sub">${r.kind === 'builder' ? '<button type="button" class="link-btn" data-lib-act="edit">Bewerken</button>' : ''}<button type="button" class="link-btn" data-lib-act="history">Historie</button></div>` : ''}</td></tr>`).join('')
    || `<tr><td colspan="6"><div class="empty">Geen strategieën gevonden voor deze selectie.</div></td></tr>`;
}

async function loadLibrary() {
  const b = S.body('strategies'); S.clearError('strategies');
  if (!b.dataset.v10) {
    b.dataset.v10 = '1';
    b.innerHTML = `<div class="lib-bar"><input type="search" id="lib-search" placeholder="Zoek op naam, familie of markt" aria-label="Zoeken">
        <select id="lib-type" aria-label="Type"><option value="">Alle typen</option>${Object.entries(TYPE_NL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
        <select id="lib-status" aria-label="Onderzoeksstatus"><option value="">Alle statussen</option><option value="untested">Niet onderzocht</option><option value="rejected">Afgewezen</option><option value="candidate">Kandidaat of gevalideerd</option><option value="frozen">Bevroren of forward</option></select>
        <span class="count" id="lib-count"></span></div>
      <div class="card lib-wrap"><table class="tbl lib-tbl" id="lib-table"><thead><tr>${[['name', 'Naam'], ['type', 'Type / familie'], ['market', 'Markt / TF'], ['version', 'Versie'], ['status', 'Onderzoeksstatus']].map(([k, l]) => `<th data-sort="${k}" tabindex="0">${l}</th>`).join('')}<th></th></tr></thead><tbody id="lib-rows"><tr><td colspan="6" class="muted">Laden…</td></tr></tbody></table></div>
      <div id="plugin-panel"></div><div id="lifecycle-history"></div>
      <p class="muted lib-more">${tip('Eigen Python-strategie als plugin: kopieer strategy_lab/plugins/voorbeeld_ma_cross, pas PLUGIN en backtest() aan. Het lab ontdekt de map automatisch en toont de plugin hier.')} Eigen Python-strategie toevoegen</p>`;
    q('#lib-search').oninput = e => { lib.filter.text = e.target.value; renderLibraryRows(); };
    q('#lib-type').onchange = e => { lib.filter.type = e.target.value; renderLibraryRows(); };
    q('#lib-status').onchange = e => { lib.filter.status = e.target.value; renderLibraryRows(); };
    const sortBy = th => { const k = th.dataset.sort; lib.sort = {k, d: lib.sort.k === k ? -lib.sort.d : 1}; renderLibraryRows(); };
    q('#lib-table thead').addEventListener('click', e => { const th = e.target.closest('th[data-sort]'); if (th) sortBy(th); });
    q('#lib-table thead').addEventListener('keydown', e => { const th = e.target.closest('th[data-sort]'); if (th && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); sortBy(th); } });
    q('#lib-rows').addEventListener('click', e => { const btn = e.target.closest('[data-lib-act]'); if (!btn || btn.disabled) return; libAction(btn.dataset.libAct, lib.rows.find(r => r.key === btn.closest('tr').dataset.key)); });
  }
  await fetchLibrary();
  renderLibraryRows();
}

async function libAction(act, r) {
  if (!r) return;
  if (act === 'edit') { sessionStorage.setItem('labOpenSpec', r.name); S.go('strategieen/bouwen'); return; }
  if (act === 'history') { await S.openLifecycle(r.name, q('#lifecycle-history')); q('#lifecycle-history').scrollIntoView({behavior: 'smooth'}); return; }
  if (act === 'multi') { multimarket(strategyRef(r)); return; }
  if (r.kind === 'plugin') { S.openPlugin(r.id, 'backtest'); q('#plugin-panel')?.scrollIntoView({behavior: 'smooth'}); return; }
  if (r.kind === 'candidate') {
    S.go('strategieen/automatisch');
    for (let i = 0; i < 40 && !window.labFactory?._state?.cat; i++) await sleep(150);
    window.labFactory?.open?.(r.runId, r.id); return;
  }
  await preselectTester(r);
}
const strategyRef = r => r.kind === 'candidate' ? {kind: 'factory', candidate_id: r.id, name: r.name, symbol: r.market, timeframe: r.tf}
  : r.kind === 'jesse' || r.kind === 'plugin' ? {kind: r.kind, name: r.name, id: r.id, symbol: r.market, timeframe: r.tf}
  : {kind: 'builder', name: r.name, symbol: r.market, timeframe: r.tf};

async function preselectTester(r) {
  S.go('onderzoeken/testen');
  const sel = document.getElementById('strategy');
  for (let i = 0; i < 60 && ![...(sel?.options || [])].some(o => o.value === r.name); i++) await sleep(100);
  if (![...(sel?.options || [])].some(o => o.value === r.name)) { S.toast('Deze strategie staat nog niet als Jesse-strategie in de tester.', 3500); return; }
  sel.value = r.name; sel.dispatchEvent(new Event('change', {bubbles: true}));
  for (const [id, v] of [['symbol', r.market], ['timeframe', r.tf]]) {
    const el = document.getElementById(id);
    if (v && el && [...el.options].some(o => o.value === v)) { el.value = v; el.dispatchEvent(new Event('change', {bubbles: true})); }
  }
}

/* =============================================================== multi-market drawer */
const mv = {ref: null, markets: [], poll: null, runId: null};
const FLAG_NL = {FEW_TRADES: 'Weinig trades', FAILS_SPREAD_50: 'Faalt bij spread +50%', UNSTABLE_PARAMS: 'Instabiele parameters', NO_DATA: 'Geen data', SHORT_HISTORY: 'Korte historie', PROVISIONAL_CURRENCY: 'Valuta voorlopig', PROVISIONAL_CONTRACT: 'Contract voorlopig', ERROR: 'Fout', NEW_HYPOTHESIS: 'Nieuwe hypothese'};
const VAL_NL = {PASS: ['good', 'Voldoende'], WEAK: ['warn', 'Zwak'], FAIL: ['bad', 'Onvoldoende'], INSUFFICIENT_TRADES: ['idle', 'Te weinig trades'], INSUFFICIENT_DATA: ['idle', 'Te weinig data']};
const GEN_NL = {NO_EDGE: ['bad', 'Geen edge'], MARKET_SPECIFIC: ['warn', 'Marktspecifiek'], PARTIAL_GENERALISATION: ['warn', 'Gedeeltelijk generaliserend'], BROAD_GENERALISATION: ['good', 'Breed generaliserend'], NOT_COMPARABLE: ['idle', 'Niet vergelijkbaar']};
const TFS = ['M5', 'M15', 'M30', 'H1', 'H4'];
const mvIdle = s => !['queued', 'running', 'pending', 'stopping'].includes(String(s || '').toLowerCase());

function ensureMvDrawer() {
  if (q('#mv-drawer')) return;
  const d = document.createElement('aside');
  d.className = 'side-drawer mv-drawer'; d.id = 'mv-drawer'; d.setAttribute('aria-label', 'Test op andere markten');
  d.innerHTML = `<div class="section-head"><h2>Test op andere markten</h2><button type="button" class="secondary drawer-close" aria-label="Sluiten">Sluiten</button></div>
    <div class="mv-form"><div class="mv-strat" id="mv-strat"></div>
      <div><div class="field-label">Markten ${tip('Dezelfde, bevroren strategie wordt op elke gekozen markt en elk timeframe getest. Er wordt niets opnieuw geoptimaliseerd.')}<span class="mv-quick"><button type="button" class="secondary" data-mv-q="all">Alle</button><button type="button" class="secondary" data-mv-q="none">Geen</button></span></div><div class="chip-select" id="mv-markets"></div></div>
      <div><div class="field-label">Timeframes</div><div class="chip-select" id="mv-tfs">${TFS.map(t => `<label><input type="checkbox" value="${t}" ${t === 'M15' || t === 'H1' ? 'checked' : ''}><span>${t}</span></label>`).join('')}</div></div>
      <details class="accordion"><summary>Geavanceerd</summary><div class="acc-body"><div class="field-label">Periode ${tip('Alleen ontwikkeldata wordt gebruikt: de afgeschermde eindperiode blijft dicht. Leeg laten gebruikt de standaardperiode van de server.')}</div><div class="form-grid"><label>Van<input type="date" id="mv-start"></label><label>Tot en met<input type="date" id="mv-end"></label></div>
        <label class="check-row"><input type="checkbox" id="mv-reopt"> Parameters per markt opnieuw optimaliseren ${tip('Standaard blijft de strategie ongewijzigd. Opnieuw optimaliseren maakt er een nieuwe hypothese van; de generalisatieklasse is dan niet vergelijkbaar.')}</label></div></details>
      <div class="mv-actions"><button type="button" id="mv-run">Test uitvoeren</button><span class="muted" id="mv-note"></span></div></div>
    <div id="mv-progress"></div><div class="mv-res" id="mv-result"></div>`;
  (q('main') || document.body).append(d);
  q('.drawer-close', d).onclick = () => window.labDrawer.close();
  d.addEventListener('click', e => { const b = e.target.closest('[data-mv-q]'); if (!b) return; qa('#mv-markets input:not(:disabled)').forEach(i => { i.checked = b.dataset.mvQ === 'all'; }); });
  q('#mv-run').onclick = mvStart;
}

async function multimarket(ref) {
  ensureMvDrawer();
  mv.ref = ref;
  const sup = ['builder', 'factory', 'jesse', 'plugin'].includes(ref.kind);
  const kindNl = {factory: TYPE_NL.candidate, jesse: TYPE_NL.jesse, plugin: TYPE_NL.plugin}[ref.kind] || TYPE_NL.builder;
  q('#mv-strat').innerHTML = `<b>${esc(ref.name || ref.candidate_id || '')}</b> <span class="muted">· ${esc(kindNl)}${ref.symbol ? ` · eerder getest op ${esc(mlabel(ref.symbol))} ${esc(ref.timeframe || '')}` : ''}</span>`;
  if (ref.timeframe && TFS.includes(ref.timeframe)) qa('#mv-tfs input').forEach(i => { i.checked = i.value === ref.timeframe || (!qa('#mv-tfs input:checked').length && i.value === 'M15'); });
  window.labDrawer.open('mv-drawer');
  const box = q('#mv-markets'); box.innerHTML = '<span class="muted">Markten laden…</span>';
  q('#mv-note').textContent = '';
  try {
    const m = await api('/api/multimarket/markets');
    mv.markets = (m.markets || m || []).map(x => typeof x === 'string' ? {symbol: x, data_available: true} : x);
    box.innerHTML = mv.markets.map(x => {
      const ok = x.data_available !== false;
      return `<label><input type="checkbox" value="${esc(x.symbol)}" ${ok ? 'checked' : 'disabled'}><span ${x.money_status_note_nl || !ok ? `data-tip="${esc(!ok ? 'Geen lokale data voor deze markt.' : x.money_status_note_nl)}"` : ''}>${esc(mlabel(x.symbol))}</span></label>`;
    }).join('');
    q('#mv-run').disabled = !sup;
    if (!sup) q('#mv-note').textContent = 'Dit type strategie kan niet op andere markten worden getest.';
    else if (ref.kind === 'jesse' || ref.kind === 'plugin') q('#mv-note').textContent = 'Markten die deze strategie niet ondersteunt of waar de strategie aan één instrument is gebonden, krijgen een reden in het resultaat.';
  } catch (err) {
    box.innerHTML = err.status === 404 ? unavailable('De multi-markttest') : `<div class="notice bad">${esc(err.message)}</div>`;
    q('#mv-run').disabled = true;
  }
}

async function mvStart() {
  const markets = qa('#mv-markets input:checked').map(i => i.value), tfs = qa('#mv-tfs input:checked').map(i => i.value);
  const note = q('#mv-note');
  if (!markets.length || !tfs.length) { note.textContent = 'Kies minstens één markt en één timeframe.'; return; }
  const all = mv.markets.filter(x => x.data_available !== false).length === markets.length;
  const ref = mv.ref, strategy = ref.kind === 'factory' ? {kind: 'factory', candidate_id: ref.candidate_id} : ref.kind === 'plugin' ? {kind: 'plugin', name: ref.name, ...(ref.id ? {id: ref.id} : {})} : {kind: ref.kind, name: ref.name};
  const body = {strategy, markets: all ? 'all' : markets, timeframes: tfs, reoptimise: q('#mv-reopt').checked};
  if (q('#mv-start').value) body.start = q('#mv-start').value;
  if (q('#mv-end').value) body.end = q('#mv-end').value;
  q('#mv-run').disabled = true; note.textContent = 'Starten…'; q('#mv-result').innerHTML = '';
  try {
    const r = await api('/api/multimarket/runs', {method: 'POST', body});
    mv.runId = first(r, 'run_id', 'job_id', 'id'); localStorage.setItem('labMvLast', mv.runId); note.textContent = '';
    mvPoll(mv.runId);
  } catch (err) {
    note.textContent = err.status === 404 ? 'De multi-markttest is nog niet beschikbaar op deze server.' : err.message; q('#mv-run').disabled = false;
  }
}

function mvPoll(id) {
  clearTimeout(mv.poll);
  const tick = async () => {
    let run;
    try { run = await api(`/api/multimarket/runs/${encodeURIComponent(id)}`); }
    catch (err) { q('#mv-progress').innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; q('#mv-run').disabled = false; return; }
    const p = Math.round((Number(run.progress) || 0) * 100);
    const done = mvIdle(run.status);
    q('#mv-progress').innerHTML = done ? '' : `<div class="progress"><i style="width:${p}%"></i></div><div class="muted">${esc(run.message || run.status || '')} · ${p}%</div>`;
    renderMvResult(q('#mv-result'), run);
    if (done) { q('#mv-run').disabled = false; renderAnalyseMv(); return; }
    mv.poll = setTimeout(tick, 2000);
  };
  tick();
}

const MV_UNSUP = {NOT_SUPPORTED: 'Niet ondersteund', INSTRUMENT_BOUND: 'Aan instrument gebonden'};
function mvUnsupported(c) {
  const code = [c.status, c.validation_status, c.support_status, ...(c.flags || [])].map(v => String(v || '').toUpperCase()).find(v => MV_UNSUP[v]);
  if (!code) return null;
  return {label: MV_UNSUP[code], reason: first(c, 'reason_nl', 'reason', 'message_nl', 'note_nl') || (typeof c.error === 'string' ? c.error.replace(/^[A-Z_]+:\s*/, '') : '')};
}
function cellVal(c, ...ks) { return first(c, ...ks) ?? first(c.metrics || {}, ...ks); }
function costCell(c) {
  const cs = c.cost_stress; if (!cs || typeof cs !== 'object') return '—';
  const e = Object.entries(cs)[0]; if (!e) return '—';
  const v = e[1] || {}; return `${pc(v.net_return_pct)} <span class="muted">PF ${fmt(v.profit_factor)}</span>`;
}
function renderMvResult(el, run) {
  if (!el) return;
  const rows = run.results || [];
  if (run.status === 'error' || run.status === 'failed') { el.innerHTML = `<div class="notice bad">${esc(run.error || 'De multi-markttest is mislukt.')}</div>`; return; }
  if (!rows.length) { el.innerHTML = mvIdle(run.status) ? '<div class="empty">Geen resultaten.</div>' : ''; return; }
  qa('.mv-eq', el).forEach(purge);
  const gen = run.generalisation, prov = rows.some(r => r.provisional) || run.provisional_note_nl;
  const [gcls, glabel] = gen ? (GEN_NL[gen.class] || ['idle', gen.class]) : ['idle', ''];
  const corr = gen?.correlation_matrix;
  const corrHtml = corr?.markets?.length > 1 ? `<div><div class="field-label">Correlatie tussen markten ${tip(corr.basis || 'Dagelijkse rendementen van de strategie per markt.')}</div><div class="mv-hmap" style="grid-template-columns:auto repeat(${corr.markets.length},1fr)"><span></span>${corr.markets.map(m => `<span><b>${esc(mlabel(m))}</b></span>`).join('')}${corr.matrix.map((row, i) => `<span><b>${esc(mlabel(corr.markets[i]))}</b></span>${row.map(v => `<span style="background:color-mix(in srgb,var(--accent) ${Math.round(Math.abs(v) * 70)}%,transparent)">${fmt(v, 2)}</span>`).join('')}`).join('')}</div></div>` : '';
  el.innerHTML = `${gen ? `<div class="mv-verdict ${gcls}"><b>${chip(gcls, glabel)}</b>${esc(gen.explanation_nl || '')}<div class="muted">${gen.n_markets_positive ?? '—'} van ${gen.n_markets_tested ?? '—'} markten positief${num(gen.effective_markets) ? ` · effectief ${fmt(gen.effective_markets, 1)} onafhankelijke markten` : ''}${gen.selection?.note_nl ? `<br>${esc(gen.selection.note_nl)}` : ''}</div></div>` : ''}
    ${prov ? `<div class="mv-note"><b>Voorlopig.</b> ${esc(run.provisional_note_nl || 'Geldbedragen zijn indicatief: contractgegevens en valuta zijn niet door een broker bevestigd.')}</div>` : ''}
    <div class="scroll"><table class="tbl mv-tbl"><thead><tr><th>Markt</th><th>TF</th><th class="num">Trades/week</th><th class="num">Netto %</th><th class="num">PF</th><th class="num">RF</th><th class="num">Max DD %</th><th class="num">Sharpe</th><th class="num">Sortino</th><th class="num">Trades</th><th class="num">Param.stabiliteit</th><th>Kostenstress</th><th>Oordeel</th></tr></thead><tbody>
    ${rows.map(c => { const unsup = mvUnsupported(c); const [vc, vl] = unsup ? ['idle', unsup.label] : VAL_NL[c.validation_status] || ['idle', c.validation_status || (c.status && c.status !== 'OK' ? c.status : '—')]; const net = cellVal(c, 'net_return_pct');
      const stab = c.param_stability ?? c.stability?.frac_positive;
      return `<tr><td><b>${esc(mlabel(c.symbol))}</b></td><td>${esc(c.timeframe)}</td><td class="num">${fmt(cellVal(c, 'trades_per_week'))}</td><td class="num ${net > 0 ? 'fx-pos' : net < 0 ? 'fx-neg' : ''}">${pc(net)}</td><td class="num">${fmt(cellVal(c, 'profit_factor'))}</td><td class="num">${fmt(cellVal(c, 'recovery_factor'))}</td><td class="num">${pc(cellVal(c, 'max_dd_pct', 'max_drawdown_pct'))}</td><td class="num">${fmt(cellVal(c, 'sharpe', 'sharpe_daily'))}</td><td class="num">${fmt(cellVal(c, 'sortino', 'sortino_daily'))}</td><td class="num">${num(cellVal(c, 'trades')) ? Math.round(cellVal(c, 'trades')) : '—'}</td><td class="num">${num(stab) ? pc(Number(stab) * (Number(stab) <= 1 ? 100 : 1), 0) : '—'}</td><td>${costCell(c)}</td>
        <td>${chip(vc, vl)}${unsup?.reason ? `<div class="muted mv-reason">${esc(unsup.reason)}</div>` : ''}${c.provisional ? chip('warn', 'voorlopig') : ''}${(c.flags || []).filter(f => !/^PROVISIONAL/.test(f)).map(f => `<span class="flag">${chip('idle', FLAG_NL[f] || f)}</span>`).join('')}</td></tr>`; }).join('')}</tbody></table></div>
    <div><div class="field-label">Equity per markt en gecombineerd ${tip(run.combined?.method || 'Gelijk gewogen: kapitaal gelijk verdeeld over alle cellen.')}</div><div class="mv-eq" data-mv-eq></div></div>${corrHtml}`;
  const traces = [];
  const toXY = pts => ({x: pts.map(p => Array.isArray(p) ? p[0] : first(p, 'time', 't', 'x')), y: pts.map(p => Array.isArray(p) ? p[1] : first(p, 'equity', 'value', 'y'))});
  Object.entries(run.equities || {}).forEach(([k, pts], i) => { if (pts?.length) traces.push({...toXY(pts), mode: 'lines', name: k.replace('|', ' ').replace(/^([A-Z0-9]+)/, (m, s) => mlabel(s)), line: {width: 1.2, color: SERIES[i % SERIES.length]}}); });
  if (run.combined?.equity?.length) traces.push({...toXY(run.combined.equity), mode: 'lines', name: 'Gecombineerd', line: {width: 2.6, color: css('--ink') || '#e6edf5'}});
  const eq = q('[data-mv-eq]', el);
  if (traces.length) plot(eq, traces, {yaxis: {gridcolor: css('--edge'), title: {text: 'Equity', font: {size: 10}}}}); else eq.textContent = 'Geen equitycurves beschikbaar.';
}

/* =============================================================== Analyse */
const an = {built: false, valLoaded: false};
function mountInto(slotId, selector, climb) {
  const el = q(selector), slot = q(slotId); if (!el || !slot) return;
  slot.append(climb ? el.closest(climb) || el : el);
}
function buildAnalyse() {
  if (an.built) return true;
  const root = S.body('analyse'); if (!root) return false;
  an.built = true;
  root.innerHTML = `<div class="an-head"><label>Run<select id="an-run" aria-label="Run kiezen"></select></label><button type="button" class="secondary icon-btn" id="an-refresh" aria-label="Runs verversen" data-tip="Lijst met runs verversen">↻</button><span class="grow"></span><button type="button" class="secondary" id="an-multi">Test op andere markten</button><button type="button" class="secondary" id="an-test">Terug naar Testen</button></div>
    <p class="an-run-note" id="an-note"></p>
    <div class="an-kpis" id="an-kpis"></div>
    <details class="an-sec" id="an-prestaties" open><summary>Prestaties</summary><div class="an-body"><div id="an-slot-interp"></div><div class="an-two"><div id="an-slot-winloss"><h3 class="widget-subheading">Winst- en verliesverdeling ${tip('Hoe de winst of het verlies per trade is verdeeld. Eén grote winnaar tussen veel kleine verliezen wijst op een fragiele strategie.')}</h3><div id="an-winloss" class="an-plot"></div></div></div><div id="an-slot-analytics"></div></div></details>
    <details class="an-sec" id="an-validatie"><summary>Validatie en robuustheid ${tip('Walk-forward, kostenstress, Monte Carlo, overfitting-correcties en parameterstabiliteit. Oordelen: AFWIJZEN = faalt een harde controle. MEER DATA NODIG = te weinig trades of data. RESEARCHKANDIDAAT = doorstaat de controles, nog zonder out-of-sample bewijs. OOS GESLAAGD = ook de holdout slaagt. GESCHIKT VOOR FORWARD = klaar om te bevriezen en te volgen.')}</summary><div class="an-body"><div data-view="validate" class="val-host"><div class="view-body"></div></div></div></details>
    <details class="an-sec" id="an-vergelijken"><summary>Vergelijken</summary><div class="an-body"><div id="an-slot-compare"></div><h3 class="widget-subheading">Equity naast elkaar</h3><div id="an-overlay" class="an-plot tall"><div class="an-empty">Kies hierboven 2 tot 4 runs en klik op Vergelijk.</div></div><h3 class="widget-subheading">Resultaten op andere markten</h3><div id="an-mv"></div></div></details>
    <details class="an-sec" id="an-details"><summary>Details</summary><div class="an-body"><div id="an-slot-logs"></div></div></details>`;
  mountInto('#an-slot-interp', '#interpretation');
  mountInto('#an-slot-analytics', '#analytics-options', '.an-page');
  mountInto('#an-slot-compare', '#compare-a', '.an-page');
  mountInto('#an-slot-logs', '#result-logs', '.an-page');
  q('#analyse-stash')?.remove();
  syncRunSelect();
  const rs = document.getElementById('run-select');
  if (rs) new MutationObserver(syncRunSelect).observe(rs, {childList: true});
  q('#an-run').onchange = e => { rs.value = e.target.value; rs.dispatchEvent(new Event('change', {bubbles: true})); };
  q('#an-refresh').onclick = () => document.getElementById('refresh-runs')?.click();
  q('#an-test').onclick = () => S.go('onderzoeken/testen');
  q('#an-multi').onclick = () => { const r = activeStrategyRef(); if (r) multimarket(r); else S.toast('Kies eerst een run of strategie.', 2500); };
  qa('.an-sec', root).forEach(d => d.addEventListener('toggle', () => { if (d.open) { resizePlots(); if (d.id === 'an-validatie') openValidation(); if (d.id === 'an-vergelijken') renderAnalyseMv(); } }));
  paintKpis(); return true;
}
function syncRunSelect() {
  const rs = document.getElementById('run-select'), sel = q('#an-run'); if (!rs || !sel) return;
  sel.innerHTML = rs.innerHTML.replace('Geen backtestrun', 'Kies een run'); sel.value = rs.value;
}
function activeStrategyRef() {
  const name = (typeof state !== 'undefined' && state.runManifest?.strategy) || document.getElementById('strategy')?.value;
  if (!name) return null;
  const row = lib.rows.find(r => r.key === `s:${name}`);
  const isBuilder = lib.builderNames.has(name) || row?.kind === 'builder';
  const kind = isBuilder ? 'builder' : row?.kind === 'plugin' ? 'plugin' : 'jesse';
  return {kind, name, id: row?.id, symbol: state.runManifest?.symbol, timeframe: state.runManifest?.timeframe, _supported: true};
}

function kpiTile(label, value, cls, t) { return `<div class="an-kpi"><div class="l">${esc(label)} ${t ? tip(t) : ''}</div><div class="v ${cls || ''}">${value}</div></div>`; }
function paintKpis(result, manifest) {
  const el = q('#an-kpis'); if (!el) return;
  const st = typeof state !== 'undefined' ? state : {};
  result = result || st.runData; manifest = manifest || st.runManifest;
  const m = result?.metrics;
  const note = q('#an-note');
  if (!m) {
    el.innerHTML = [['Netto rendement', 'Nettoresultaat gedeeld door het startsaldo.'], ['Max drawdown', 'Grootste piek-tot-dalverlies.'], ['Profit Factor', 'Brutowinst gedeeld door brutoverlies. 1,0 is break-even.'], ['Recovery Factor', 'Nettowinst gedeeld door maximale drawdown.']].map(([l, t]) => kpiTile(l, '—', '', t)).join('');
    if (note) note.textContent = result?.all_trials ? 'Dit is een optimalisatierun. Kies een backtestrun om de analyse te zien.' : 'Kies een run om de analyse te zien, of voer een backtest uit bij Testen.';
    drawWinLoss(null); return;
  }
  const bal = Number(m.starting_balance || manifest?.starting_balance), net = Number(m.net_profit);
  const ret = bal > 0 && Number.isFinite(net) ? net / bal * 100 : null;
  let pf = m.profit_factor; if (pf == null && Number.isFinite(Number(m.gross_profit)) && Number(m.gross_loss) < 0) pf = Number(m.gross_profit) / Math.abs(Number(m.gross_loss));
  const dd = num(m.max_drawdown) ? -Math.abs(Number(m.max_drawdown)) : null;
  el.innerHTML = kpiTile('Netto rendement', ret === null ? '—' : pc(ret), ret > 0 ? 'pos' : ret < 0 ? 'neg' : '', 'Nettoresultaat gedeeld door het startsaldo · voorlopig: volgens de simulatie-instellingen, geen gevalideerd brokerbedrag.')
    + kpiTile('Max drawdown', dd === null ? '—' : pc(dd), dd < 0 ? 'neg' : '', 'Grootste piek-tot-dalverlies volgens Jesse · voorlopig.')
    + kpiTile('Profit Factor', num(pf) ? fmt(pf) : '—', num(pf) ? (pf >= 1 ? 'pos' : 'neg') : '', 'Brutowinst gedeeld door brutoverlies. 1,0 is break-even vóór niet-gemodelleerde kosten.')
    + kpiTile('Recovery Factor', num(m.recovery_factor) ? fmt(m.recovery_factor) : '—', '', 'Nettowinst gedeeld door maximale drawdown in geld.');
  if (note && manifest) note.textContent = `${manifest.strategy || ''} · ${mlabel(manifest.symbol || '')} ${manifest.timeframe || ''} · ${manifest.start_date || ''} t/m ${manifest.end_date || ''} · ${m.total ?? (result.trades || []).length} trades`;
  drawWinLoss(result.trades || []);
}
function drawWinLoss(trades) {
  const el = q('#an-winloss'); if (!el) return;
  const pnl = (trades || []).map(t => Number(t.PNL ?? t.pnl)).filter(Number.isFinite);
  el.classList.toggle('empty', !pnl.length);
  if (!pnl.length) { clearPlot(el, '<div class="an-empty">Geen gesloten trades in deze run.</div>'); return; }
  const lo = Math.min(...pnl), hi = Math.max(...pnl), size = (hi - lo) / 30 || 1;
  const bins = {start: lo, end: hi + size, size};
  plot(el, [{type: 'histogram', x: pnl.filter(v => v > 0), name: 'Winst', marker: {color: css('--good') || '#3fc488'}, xbins: bins}, {type: 'histogram', x: pnl.filter(v => v <= 0), name: 'Verlies', marker: {color: css('--bad') || '#ef6f6f'}, xbins: bins}],
    {barmode: 'overlay', bargap: 0.05, xaxis: {title: {text: 'Winst/verlies per trade', font: {size: 10}}, gridcolor: css('--edge')}, yaxis: {title: {text: 'Aantal', font: {size: 10}}, gridcolor: css('--edge')}, hovermode: 'closest'});
}

async function openValidation(force) {
  if (an.valLoaded && !force) return;
  an.valLoaded = true;
  try { await S.loadValidate(); } catch (err) { S.showError('validate', err); }
}

async function renderAnalyseMv() {
  const el = q('#an-mv'); if (!el) return;
  const id = localStorage.getItem('labMvLast');
  if (!id) { el.innerHTML = '<div class="an-empty">Nog geen test op andere markten uitgevoerd. Gebruik de knop bovenaan.</div>'; return; }
  try { const run = await api(`/api/multimarket/runs/${encodeURIComponent(id)}`); qa('.mv-eq', el).forEach(purge); el.innerHTML = ''; const inner = document.createElement('div'); inner.className = 'mv-res'; el.append(inner); renderMvResult(inner, run); }
  catch (err) { el.innerHTML = err.status === 404 && id ? '<div class="an-empty">De laatste multi-markttest is niet meer beschikbaar.</div>' : unavailable('De multi-markttest'); }
}

function drawOverlay(runs) {
  const el = q('#an-overlay'); if (!el) return;
  const traces = runs.map((r, i) => {
    const bal = Number(r.metrics?.starting_balance || r.manifest?.starting_balance);
    const ts = (r.trades || []).map(t => [Number(t.closed_at ?? t.closed_at_ms) || Date.parse(t.closed_at), Number(t.PNL ?? t.pnl)]).filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1])).sort((a, b) => a[0] - b[0]);
    if (!ts.length || !(bal > 0)) return null;
    let cum = 0; return {x: ts.map(p => new Date(p[0])), y: ts.map(p => (cum += p[1]) / bal * 100), mode: 'lines', line: {shape: 'hv', width: 1.6, color: SERIES[i % SERIES.length]}, name: `${String.fromCharCode(65 + i)}: ${r.manifest?.strategy || ''} ${mlabel(r.manifest?.symbol || '')}`};
  }).filter(Boolean);
  if (!traces.length) { clearPlot(el, '<div class="an-empty">Deze runs hebben geen gesloten trades om te vergelijken.</div>'); return; }
  plot(el, traces, {yaxis: {title: {text: 'Rendement op startsaldo (%)', font: {size: 10}}, gridcolor: css('--edge')}});
}

async function loadAnalyse(act) {
  if (!buildAnalyse()) return;
  syncRunSelect(); paintKpis();
  if (typeof renderPerformanceAnalytics === 'function') try { renderPerformanceAnalytics(); } catch { /* ignore */ }
  resizePlots(); setTimeout(resizePlots, 250);
  if (act === 'validatie') { const d = q('#an-validatie'); d.open = true; openValidation(true); setTimeout(() => d.scrollIntoView({behavior: 'smooth', block: 'start'}), 150); }
  else if (act === 'details') { const d = q('#an-details'); d.open = true; setTimeout(() => d.scrollIntoView({behavior: 'smooth', block: 'start'}), 150); }
  if (q('#an-vergelijken').open) renderAnalyseMv();
}

/* =============================================================== Testen additions */
function wireTester() {
  const side = q('.results-side'); if (!side) return;
  const strip = document.createElement('div');
  strip.className = 'result-more inline';
  strip.innerHTML = '<button type="button" class="link-btn" id="to-analyse">Volledige analyse</button><button type="button" class="secondary" id="tester-multi" data-tip="Dezelfde strategie op andere markten en timeframes testen.">Test op andere markten</button>';
  (q('#evidence-banner') || side.firstElementChild).after(strip);
  q('#to-analyse').onclick = () => S.go('onderzoeken/analyse');
  q('#tester-multi').onclick = async () => {
    let r = activeStrategyRef(); if (!r) { S.toast('Kies eerst een strategie.', 2500); return; }
    if (!lib.rows.length) { try { await fetchLibrary(); } catch { /* ignore */ } r = activeStrategyRef(); }
    multimarket(r);
  };
}

document.addEventListener('lab:run', e => { const d = e.detail || {}; paintKpis(d.result, d.manifest); const n = q('#trade-count'); if (n) n.textContent = d.result?.trades ? `(${d.result.trades.length})` : ''; });
document.addEventListener('lab:compare', e => drawOverlay(e.detail.runs || []));
document.addEventListener('lab:route', e => {
  if (e.detail.sub === 'analyse') { setTimeout(resizePlots, 100); }
  document.getElementById('open-settings')?.blur();
});

/* =============================================================== boot */
legacyLoadData = S.LOADERS.data;
S.LOADERS.strategies = loadLibrary;
S.LOADERS.analyse = loadAnalyse;
S.LOADERS.data = loadData;
window.labV10 = {dataLine, multimarket, loadLibrary, reloadLibrary: loadLibrary, dataStatus};
document.addEventListener('DOMContentLoaded', () => { buildAnalyse(); wireTester(); });
})();
