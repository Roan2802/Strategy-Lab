/* Strategy Factory UI: plan, run, funnel, candidate detail. Mounted into Strategieën > Automatisch ontdekken. */
(function () {
'use strict';
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const num = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
const fmt = (v, d = 2) => num(v) ? Number(v).toLocaleString('nl-NL', {minimumFractionDigits: d, maximumFractionDigits: d}) : '—';
const int = v => num(v) ? Math.round(Number(v)).toLocaleString('nl-NL') : '—';
const pc = (v, d = 1) => num(v) ? `${fmt(v, d)}%` : '—';
const tip = t => `<button class="info" type="button" aria-label="Uitleg" data-tip="${esc(t)}">i</button>`;
const first = (o, ...ks) => { for (const k of ks) if (o && o[k] !== undefined && o[k] !== null) return o[k]; return undefined; };
const LS_RUN = 'labFactoryRun';
const DEFAULT_MARKETS = ['XAUUSD', 'US30', 'USA30IDXUSD', 'DE40'];
const MARKET_LABEL = {USATECHIDXUSD: 'USTEC', USA30IDXUSD: 'US30', DEUIDXEUR: 'DE40'};
const mlabel = m => MARKET_LABEL[m] || m;
const PROFILE_FALLBACK = {snel: 'Snelle verkenning', uitgebreid: 'Uitgebreid onderzoek', diepgaand: 'Diepgaand onderzoek'};
const STAGE_NL = {generated: 'Gegenereerd', technically_valid: 'Technisch geldig', rejected: 'Afgewezen', quick_passed: 'Snelle test gehaald', dev_validated: 'Ontwikkeling gevalideerd', wf_passed: 'Walk-forward gehaald', frozen: 'Bevroren', final_tested: 'Eindtest gedaan', oos_approved: 'OOS goedgekeurd', forward_ready: 'Klaar voor forward'};
const stageNl = s => STAGE_NL[s] || (s ? String(s).replace(/_/g, ' ') : '—');
const EVID_NL = {development: 'Ontwikkelperiode (in-sample)', validation: 'Validatieperiode', sealed: 'Historische eindtest (afgeschermd)', final: 'Historische eindtest', oos: 'Historische out-of-sample', forward: 'Forward'};
const evidNl = s => EVID_NL[s] || (s ? String(s).replace(/_/g, ' ') : 'Onbekend');

async function api(path, opts = {}) {
  const init = {...opts, headers: {'Content-Type': 'application/json', ...(opts.headers || {})}};
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const res = await (window.labNet ? window.labNet.fetch(path, init) : fetch(path, init));
  const text = await res.text();
  let data; try { data = text ? JSON.parse(text) : {}; } catch { data = {detail: text}; }
  if (!res.ok) {
    const d = data.detail;
    const err = new Error(typeof d === 'string' ? d : d ? JSON.stringify(d) : `HTTP ${res.status}`);
    err.status = res.status; err.data = data; throw err;
  }
  return data;
}

function themeColors() {
  const cs = getComputedStyle(document.body);
  const v = n => cs.getPropertyValue(n).trim();
  return {ink: v('--ink') || '#e6edf5', ink2: v('--ink-2') || '#9fb1c3', edge: v('--edge') || '#233140', accent: v('--accent') || '#4a90d9', good: v('--good') || '#3fc488', bad: v('--bad') || '#ef6f6f', warn: v('--warn') || '#e8b04a', info: v('--info') || '#6E9BD1'};
}
function plotLayout(extra = {}) {
  const c = themeColors();
  return Object.assign({margin: {l: 52, r: 14, t: 10, b: 34}, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: {color: c.ink2, size: 11},
    xaxis: {gridcolor: c.edge, zerolinecolor: c.edge}, yaxis: {gridcolor: c.edge, zerolinecolor: c.edge}, legend: {orientation: 'h', y: 1.12, x: 0}, hoverlabel: {font: {size: 11}}}, extra);
}
function plot(el, data, layout) {
  if (!el) return;
  if (!window.Plotly) { el.innerHTML = '<div class="fx-muted">Grafiekbibliotheek niet beschikbaar.</div>'; return; }
  window.Plotly.react(el, data, plotLayout(layout), {displayModeBar: false, responsive: true});
}

/* Normalise unknown series shapes into {x:[], y:[]}. */
function series(raw) {
  if (!raw) return null;
  let x = [], y = [];
  if (Array.isArray(raw)) {
    raw.forEach((p, i) => {
      if (Array.isArray(p)) { x.push(p[0]); y.push(p[1]); }
      else if (p && typeof p === 'object') { x.push(first(p, 't', 'time', 'date', 'ts', 'timestamp', 'x') ?? i); y.push(first(p, 'v', 'value', 'equity', 'y', 'pct', 'return_pct')); }
      else { x.push(i); y.push(p); }
    });
  } else if (typeof raw === 'object') {
    const yy = first(raw, 'y', 'values', 'equity', 'v'); const xx = first(raw, 'x', 'dates', 't', 'time', 'timestamps');
    if (!Array.isArray(yy)) return null;
    y = yy; x = Array.isArray(xx) ? xx : yy.map((_, i) => i);
  }
  const ok = y.map(Number).every(Number.isFinite) && y.length > 1;
  return ok ? {x, y: y.map(Number)} : null;
}

const state = {cat: null, sel: null, el: null, runId: null, timer: null, run: null, planTimer: null, planSeq: 0, runs: [], showRejected: false, detailId: null, cands: [], sort: null, noApi: false};

function defaultSel(cat) {
  const syms = (cat.markets || []).filter(m => m.supported !== false).map(m => m.symbol);
  let mk = DEFAULT_MARKETS.filter(m => syms.includes(m));
  if (mk.includes('US30') && mk.includes('USA30IDXUSD')) mk = mk.filter(m => m !== 'USA30IDXUSD');
  if (!mk.length) mk = syms.slice(0, 1);
  const tfs = cat.timeframes || [];
  const pref = ['M15', 'H1'].filter(t => tfs.includes(t));
  return {markets: new Set(mk), tfs: new Set(pref.length ? pref : tfs.slice(0, 1)), groups: new Set(), all: true, profile: 'snel', quality: 'regelmatig', minTpw: '', maxDd: '', risk: '',
    adv: {max_candidates: '', trials: '', workers: '', cpu_cap: '', ram_cap: '', seed: ''}};
}

function groupsOf(cat) { const g = []; (cat.families || []).forEach(f => { if (f.group && !g.includes(f.group)) g.push(f.group); }); return g; }

function planBody() {
  const s = state.sel; const b = {markets: [...s.markets], timeframes: [...s.tfs], profile: s.profile, quality: s.quality};
  b.families = s.all || !s.groups.size ? 'all' : state.cat.families.filter(f => s.groups.has(f.group)).map(f => f.id);
  if (s.quality === 'custom' && num(s.minTpw)) b.min_trades_per_week = Number(s.minTpw);
  if (num(s.maxDd)) b.max_dd_pct = Number(s.maxDd);
  if (num(s.risk)) b.risk_pct = Number(s.risk);
  const adv = {}; Object.entries(s.adv).forEach(([k, v]) => { if (num(v)) adv[k] = Number(v); });
  if (Object.keys(adv).length) b.advanced = adv;
  return b;
}

/* ---------------- skeleton ---------------- */
function mount(el) {
  state.el = el;
  el.classList.add('fx');
  el.innerHTML = `<div class="fx-head"><div><h3 class="fx-title">Automatisch ontdekken</h3><p class="fx-sub">Het lab bouwt, test en wijst strategieën af; een afgeschermde eindtest beslist.</p></div>
    <div class="fx-head-actions"><label class="fx-runs-pick" id="fx-runs-wrap" hidden><span class="fx-muted">Eerdere onderzoeken</span><select id="fx-runs" aria-label="Eerdere onderzoeken"></select></label><button type="button" class="secondary" id="fx-new" hidden>Nieuw onderzoek</button></div></div>
    <div id="fx-notice"></div><div id="fx-setup"></div><div id="fx-run"></div>
    <aside class="fx-drawer" id="fx-drawer" aria-label="Kandidaat detail" tabindex="-1"></aside><div class="fx-scrim" id="fx-scrim"></div>`;
  el.querySelector('#fx-scrim').onclick = closeDetail;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && state.el?.isConnected) closeDetail(); });
  el.querySelector('#fx-new').onclick = () => { stopPoll(); state.runId = null; state.run = null; localStorage.removeItem(LS_RUN); renderRun(); showSetup(true); };
  el.querySelector('#fx-runs').onchange = e => { if (e.target.value) attach(e.target.value); };
  init();
}

async function init() {
  try {
    state.cat = await api('/api/factory/catalog');
  } catch (err) {
    state.noApi = true;
    state.el.querySelector('#fx-notice').innerHTML = err.status === 404
      ? '<div class="notice info">De strategiefabriek is nog niet beschikbaar op deze server. Gebruik zolang de klassieke snelle verkenning hieronder.</div>'
      : `<div class="notice warn">Catalogus laden mislukt: ${esc(err.message)}</div>`;
    return;
  }
  state.sel = defaultSel(state.cat);
  renderSetup();
  schedulePlan();
  await loadRuns();
  const saved = localStorage.getItem(LS_RUN);
  if (saved) attach(saved, true);
}

async function loadRuns() {
  try {
    const r = await api('/api/factory/runs');
    state.runs = Array.isArray(r) ? r : (r.runs || []);
  } catch { state.runs = []; }
  const sel = state.el.querySelector('#fx-runs'); if (!sel) return;
  sel.innerHTML = '<option value="">Kies een onderzoek…</option>' + state.runs.map(r => {
    const id = first(r, 'run_id', 'id'); const when = String(first(r, 'created_at', 'started_at', 'created') || '').replace('T', ' ').slice(0, 16);
    return `<option value="${esc(id)}" ${id === state.runId ? 'selected' : ''}>${esc(when || id.slice(0, 8))} · ${esc(statusNl(r.status))}${r.config?.markets ? ' · ' + esc((r.config.markets || []).map(mlabel).join(', ')) : ''}</option>`;
  }).join('');
  state.el.querySelector('#fx-runs-wrap').hidden = !state.runs.length;
}

const statusNl = s => ({queued: 'In wachtrij', running: 'Bezig', paused: 'Gepauzeerd', stopped: 'Gestopt', done: 'Klaar', completed: 'Klaar', finished: 'Klaar', failed: 'Mislukt', error: 'Fout', stopping: 'Stoppen…'}[String(s || '').toLowerCase()] || s || '—');
const isLive = s => ['queued', 'running', 'paused', 'stopping'].includes(String(s || '').toLowerCase());

/* ---------------- setup ---------------- */
function chips(name, items, selected, opts = {}) {
  return `<div class="chip-select" data-group="${name}" role="group">${items.map(i => `<label><input type="${opts.radio ? 'radio' : 'checkbox'}" name="fx-${name}" value="${esc(i.value)}" ${selected(i.value) ? 'checked' : ''} ${i.disabled ? 'disabled' : ''}><span ${i.tip ? `data-tip="${esc(i.tip)}"` : ''}>${esc(i.label)}</span></label>`).join('')}</div>`;
}

function showSetup(open) { const d = state.el.querySelector('#fx-setup details.fx-setup-acc'); if (d) d.open = open; state.el.querySelector('#fx-setup').hidden = false; }

function renderSetup() {
  const c = state.cat, s = state.sel, box = state.el.querySelector('#fx-setup');
  const markets = (c.markets || []).filter(m => m.supported !== false);
  const profiles = Object.entries(c.profiles || {});
  const quals = Object.entries(c.quality_profiles || {});
  const groups = groupsOf(c);
  box.innerHTML = `<div class="card fx-card"><div class="fx-grid">
    <div class="fx-field"><div class="fx-label">Markten ${tip('Alleen markten met voldoende data. Elke markt heeft een eigen afgeschermde eindtest.')}</div>
      ${chips('markets', markets.map(m => ({value: m.symbol, label: mlabel(m.symbol), tip: [m.first && `Data vanaf ${String(m.first).slice(0, 10)}`, m.notes].filter(Boolean).join('. ')})), v => s.markets.has(v))}</div>
    <div class="fx-field"><div class="fx-label">Timeframes</div>${chips('tfs', (c.timeframes || []).map(t => ({value: t, label: t})), v => s.tfs.has(v))}</div>
    <div class="fx-field fx-wide"><div class="fx-label">Soort strategieën ${tip('Kies groepen of onderzoek alles. Meer groepen betekent meer testen en een strengere multiple-testing correctie.')}</div>
      <div class="chip-select" data-group="groups"><label><input type="checkbox" value="__all" ${s.all ? 'checked' : ''}><span>Alles onderzoeken</span></label>${groups.map(g => `<label><input type="checkbox" value="${esc(g)}" ${!s.all && s.groups.has(g) ? 'checked' : ''}><span data-tip="${esc((c.families || []).filter(f => f.group === g).map(f => f.label_nl).join(', '))}">${esc(({trend: 'Trend', breakout: 'Uitbraak', mean_reversion: 'Terugkeer naar gemiddelde', price_action: 'Price action', statistical: 'Statistisch', vwap_volume: 'VWAP / volume', hybrid: 'Hybride'})[g] || g)}</span></label>`).join('')}</div></div>
    <div class="fx-field"><div class="fx-label">Onderzoeksdiepte</div>
      <div class="fx-seg" role="radiogroup" aria-label="Onderzoeksdiepte" id="fx-profile">${profiles.map(([k, p]) => `<button type="button" role="radio" class="fx-seg-btn ${s.profile === k ? 'on' : ''}" aria-checked="${s.profile === k}" data-v="${esc(k)}" data-tip="${esc(p.description_nl || '')}">${esc(p.label_nl || PROFILE_FALLBACK[k] || k)}</button>`).join('')}</div></div>
  </div>
  <div class="fx-plan" id="fx-plan" aria-live="polite"><span class="fx-muted">Plan berekenen…</span></div>
  <div class="fx-start"><button type="button" id="fx-start">Onderzoek starten</button><span class="fx-muted" id="fx-start-note"></span></div>
  <details class="fx-adv"><summary>Geavanceerd</summary><div class="fx-grid fx-adv-top">
    <div class="fx-field"><div class="fx-label">Hoe vaak minimaal handelen ${tip('Strategieën die te weinig handelen zijn statistisch niet te beoordelen en vallen af.')}</div>
      <select id="fx-quality" aria-label="Minimale handelsfrequentie">${quals.map(([k, q]) => `<option value="${esc(k)}" ${s.quality === k ? 'selected' : ''}>${esc(q.label_nl || k)}</option>`).join('')}</select>
      <input type="number" id="fx-mintpw" class="fx-mini" min="0" step="0.1" placeholder="trades per week" value="${esc(s.minTpw)}" ${s.quality === 'custom' ? '' : 'hidden'} aria-label="Minimum trades per week"></div>
    <div class="fx-field fx-pair"><label>Max risico per trade<span class="fx-inp"><input type="number" id="fx-risk" min="0" step="0.1" placeholder="auto" value="${esc(s.risk)}"><i>%</i></span></label>
      <label>Max drawdown<span class="fx-inp"><input type="number" id="fx-dd" min="0" step="1" placeholder="auto" value="${esc(s.maxDd)}"><i>%</i></span></label></div>
  </div><div class="fx-adv-grid">
    ${[['max_candidates', 'Max aantal strategieën', 'Harde bovengrens op het aantal gegenereerde varianten.'], ['trials', 'Optimalisatiepogingen per strategie', 'Meer pogingen vinden vaker een mooie, maar niet per se robuuste parameterset.'], ['workers', 'Workers', 'Aantal parallelle processen.'], ['cpu_cap', 'CPU-limiet %', 'De run vertraagt als dit gebruik wordt overschreden.'], ['ram_cap', 'RAM-limiet (MB)', 'De run pauzeert als het geheugen dit overschrijdt.'], ['seed', 'Seed', 'Zelfde seed geeft dezelfde varianten.']].map(([k, l, t]) => `<label>${l} ${tip(t)}<input type="number" data-adv="${k}" min="0" placeholder="auto" value="${esc(s.adv[k])}"></label>`).join('')}
    </div><div class="fx-dates" id="fx-dates"></div></details></div>`;
  wireSetup(box);
}

function wireSetup(box) {
  const s = state.sel;
  const changed = () => { schedulePlan(); };
  box.querySelectorAll('[data-group="markets"] input').forEach(i => i.onchange = () => { i.checked ? s.markets.add(i.value) : s.markets.delete(i.value); changed(); });
  box.querySelectorAll('[data-group="tfs"] input').forEach(i => i.onchange = () => { i.checked ? s.tfs.add(i.value) : s.tfs.delete(i.value); changed(); });
  box.querySelectorAll('[data-group="groups"] input').forEach(i => i.onchange = () => {
    if (i.value === '__all') { s.all = i.checked; if (s.all) s.groups.clear(); }
    else { i.checked ? s.groups.add(i.value) : s.groups.delete(i.value); s.all = !s.groups.size; }
    box.querySelectorAll('[data-group="groups"] input').forEach(j => { j.checked = j.value === '__all' ? s.all : s.groups.has(j.value); });
    changed();
  });
  box.querySelectorAll('#fx-profile .fx-seg-btn').forEach(b => b.onclick = () => { s.profile = b.dataset.v; box.querySelectorAll('#fx-profile .fx-seg-btn').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-checked', x === b); }); changed(); });
  const qsel = box.querySelector('#fx-quality'); qsel.onchange = () => { s.quality = qsel.value; box.querySelector('#fx-mintpw').hidden = s.quality !== 'custom'; changed(); };
  const bind = (id, key) => { const e = box.querySelector(id); e.oninput = () => { s[key] = e.value; changed(); }; };
  bind('#fx-mintpw', 'minTpw'); bind('#fx-risk', 'risk'); bind('#fx-dd', 'maxDd');
  box.querySelectorAll('[data-adv]').forEach(e => e.oninput = () => { s.adv[e.dataset.adv] = e.value; changed(); });
  box.querySelector('#fx-start').onclick = start;
}

function schedulePlan() { clearTimeout(state.planTimer); state.planTimer = setTimeout(refreshPlan, 400); }

async function refreshPlan() {
  const box = state.el?.querySelector('#fx-plan'); if (!box) return;
  const btn = state.el.querySelector('#fx-start');
  const s = state.sel;
  if (!s.markets.size || !s.tfs.size) { box.innerHTML = '<span class="fx-warn">Kies minstens één markt en één timeframe.</span>'; btn.disabled = true; return; }
  btn.disabled = false;
  const seq = ++state.planSeq;
  try {
    const p = await api('/api/factory/plan', {method: 'POST', body: planBody()});
    if (seq !== state.planSeq) return;
    state.plan = p;
    const sealed = Object.entries(p.ranges || {});
    const sealedLine = sealed.length ? (() => { const r = sealed[0][1].sealed; const a = r && (Array.isArray(r) ? r[0] : r.start || r.from); return a ? `Eindtest ${String(a).slice(0, 4)}–${new Date().getFullYear()} blijft afgeschermd tot het einde.` : 'De eindtest blijft afgeschermd tot het einde.'; })() : '';
    box.innerHTML = `<div class="fx-plan-nums">
      <span><b>± ${int(p.candidates)}</b> strategieën</span><span><b>± ${int(p.backtests_estimate)}</b> backtests</span><span><b>± ${int(p.trials_estimate)}</b> optimalisatiepogingen</span><span><b>± ${int(p.est_minutes)}</b> min</span><span><b>${int(p.workers)}</b> workers</span></div>
      ${(p.warnings || []).map(w => `<div class="fx-plan-warn">${esc(typeof w === 'string' ? w : w.text_nl || w.message || JSON.stringify(w))}</div>`).join('')}
      ${p.cpu_note_nl ? `<div class="fx-muted">${esc(p.cpu_note_nl)}</div>` : ''}
      ${sealedLine ? `<div class="fx-sealed"><svg class="fx-lock" viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2"/></svg>${esc(sealedLine)}</div>` : ''}`;
    const dates = state.el.querySelector('#fx-dates');
    if (dates) dates.innerHTML = sealed.length ? `<div class="fx-label">Periodes per markt ${tip('Alleen-lezen. De grenzen volgen uit de beschikbare data; de eindtest wordt nooit gebruikt om te zoeken of te kiezen.')}</div><table class="tbl"><thead><tr><th>Markt</th><th>Ontwikkeling</th><th>Validatie</th><th>Eindtest (afgeschermd)</th></tr></thead><tbody>${sealed.map(([m, r]) => `<tr><td>${esc(mlabel(m))}</td>${['dev', 'validation', 'sealed'].map(k => `<td>${esc(rangeTxt(r[k]))}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '';
  } catch (err) {
    if (seq !== state.planSeq) return;
    box.innerHTML = `<span class="fx-warn">${esc(err.status === 404 ? 'Planning niet beschikbaar op deze server.' : 'Plan mislukt: ' + err.message)}</span>`;
  }
}
const rangeTxt = r => !r ? '—' : Array.isArray(r) ? `${String(r[0]).slice(0, 10)} → ${String(r[1]).slice(0, 10)}` : `${String(r.start || r.from || '').slice(0, 10)} → ${String(r.end || r.to || '').slice(0, 10)}`;

async function start() {
  const btn = state.el.querySelector('#fx-start'), note = state.el.querySelector('#fx-start-note');
  btn.disabled = true; note.textContent = 'Starten…';
  try {
    const r = await api('/api/factory/runs', {method: 'POST', body: planBody()});
    const id = first(r, 'run_id', 'id');
    note.textContent = '';
    await attach(id); loadRuns();
  } catch (err) { note.textContent = ''; state.el.querySelector('#fx-notice').innerHTML = `<div class="notice bad">Starten mislukt: ${esc(err.message)}</div>`; }
  finally { btn.disabled = false; }
}

/* ---------------- run ---------------- */
function stopPoll() { clearInterval(state.timer); state.timer = null; }

async function attach(id, quiet) {
  stopPoll(); state.runId = id; state.cands = []; state.run = null;
  localStorage.setItem(LS_RUN, id);
  state.el.querySelector('#fx-notice').innerHTML = '';
  const ok = await pollOnce(quiet);
  if (!ok) return;
  state.el.querySelector('#fx-new').hidden = false;
  const acc = state.el.querySelector('#fx-setup'); acc.hidden = true;
  if (isLive(state.run?.status)) state.timer = setInterval(pollOnce, 3000);
}

async function pollOnce(quiet) {
  if (!state.el?.isConnected) { stopPoll(); return false; }
  const id = state.runId; if (!id) return false;
  try {
    const was = state.run?.status;
    const r = await api(`/api/factory/runs/${encodeURIComponent(id)}`);
    if (id !== state.runId) return false;
    state.run = r;
    if (!isLive(r.status)) { stopPoll(); if (was === undefined || isLive(was)) await loadCands(); loadRuns(); }
    renderRun();
    return true;
  } catch (err) {
    stopPoll();
    if (err.status === 404) { localStorage.removeItem(LS_RUN); state.runId = null; state.el.querySelector('#fx-setup').hidden = false; if (!quiet) state.el.querySelector('#fx-notice').innerHTML = '<div class="notice warn">Dit onderzoek bestaat niet meer.</div>'; }
    else state.el.querySelector('#fx-notice').innerHTML = `<div class="notice warn">Voortgang laden mislukt: ${esc(err.message)}</div>`;
    return false;
  }
}

async function loadCands() {
  try { const r = await api(`/api/factory/runs/${encodeURIComponent(state.runId)}/candidates`); state.cands = Array.isArray(r) ? r : (r.candidates || []); }
  catch { state.cands = []; }
}

function renderRun() {
  const box = state.el.querySelector('#fx-run');
  const r = state.run;
  if (!r) { box.innerHTML = ''; return; }
  const live = isLive(r.status);
  const cn = r.counts || {};
  const prog = Math.max(0, Math.min(1, Number(r.progress) || 0));
  const res = r.resources || {};
  const rejected = num(cn.rejected) ? cn.rejected : null;
  const candN = first(cn, 'dev_validated', 'quick_passed');
  let html = '';
  if (live) {
    html += `<div class="card fx-card fx-live"><div class="fx-live-head"><div><span class="fx-state ${r.status === 'paused' ? 'warn' : 'live'}">${esc(statusNl(r.status))}</span> <span class="fx-phase">${esc(r.phase_nl || r.phase || '')}</span></div>
      <div class="fx-ctrl">${r.status === 'paused' ? '<button type="button" class="secondary" data-act="resume">Hervatten</button>' : '<button type="button" class="secondary" data-act="pause">Pauzeren</button>'}<button type="button" class="secondary fx-danger" data-act="stop">Stoppen</button></div></div>
      <div class="fx-counters"><div><b>${int(cn.generated)}</b><span>onderzocht</span></div><div><b>${int(rejected)}</b><span>afgewezen</span></div><div><b>${int(candN)}</b><span>kandidaten</span></div></div>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(prog * 100)}"><i style="width:${(prog * 100).toFixed(1)}%"></i></div>
      <div class="fx-live-foot"><span class="fx-muted">${Math.round(prog * 100)}%</span><span class="fx-res"><span data-tip="Processorgebruik">CPU ${num(res.cpu_pct) ? fmt(res.cpu_pct, 0) + '%' : '—'}</span><span data-tip="Geheugengebruik">RAM ${num(res.ram_mb) ? int(res.ram_mb) + ' MB' : '—'}</span><span data-tip="Parallelle processen">${int(res.workers)} workers</span></span>
      <button type="button" class="link-btn fx-logtoggle" data-act="log">${state.showLog ? 'Verberg log' : 'Toon log'}</button></div>
      ${state.showLog ? `<pre class="fx-log">${esc((r.log_tail || []).map(l => typeof l === 'string' ? l : l.text || JSON.stringify(l)).join('\n') || 'Nog geen logregels.')}</pre>` : ''}</div>`;
  } else {
    const st = String(r.status).toLowerCase();
    if (['failed', 'error'].includes(st)) html += `<div class="notice bad">Onderzoek mislukt. ${esc(r.error_nl || r.error || '')}</div>`;
    else if (st === 'stopped') html += '<div class="notice warn">Onderzoek is gestopt. Onderstaande resultaten zijn gedeeltelijk.</div>';
    html += candidatesHtml() + `<details class="fx-adv fx-howmore" id="fx-howmore"><summary>Hoe is dit gefilterd? Trechter, families en instellingen</summary>${funnelHtml(cn, r)}${familiesHtml(r)}${moreHtml(r)}</details>`;
  }
  box.innerHTML = html;
  wireRun(box);
  if (!live) { const hm = box.querySelector('#fx-howmore'); if (hm) hm.addEventListener('toggle', () => { if (hm.open) drawFamilies(r); }); }
}

function wireRun(box) {
  box.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
    const act = b.dataset.act;
    if (act === 'log') { state.showLog = !state.showLog; renderRun(); return; }
    if (act === 'stop' && !confirm('Onderzoek stoppen? Reeds gevonden resultaten blijven bewaard.')) return;
    b.disabled = true;
    try { await api(`/api/factory/runs/${encodeURIComponent(state.runId)}/${act}`, {method: 'POST'}); await pollOnce(); }
    catch (err) { state.el.querySelector('#fx-notice').innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
  });
  const sw = box.querySelector('#fx-showrej'); if (sw) sw.onchange = () => { state.showRejected = sw.checked; renderRun(); };
  box.querySelectorAll('[data-sort]').forEach(th => th.onclick = () => { const k = th.dataset.sort; state.sort = state.sort?.k === k ? {k, d: -state.sort.d} : {k, d: -1}; renderRun(); });
  box.querySelectorAll('[data-mv]').forEach(b => b.onclick = e => { e.stopPropagation(); multimarketFor(b.dataset.mv); });
  box.querySelectorAll('tr[data-cand]').forEach(tr => { tr.onclick = () => openDetail(tr.dataset.cand); tr.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(tr.dataset.cand); } }; });
}

function funnelHtml(cn, r) {
  const steps = [['generated', 'Totaal onderzocht', 'Alle gegenereerde strategieën, ook de afgewezen.'], ['technically_valid', 'Technisch geldig', 'Strategieën die zonder fouten of onmogelijke regels konden draaien.'], ['dev_validated', 'Ontwikkeling gevalideerd', 'Voldoen aan de eisen op de ontwikkel- en validatieperiode, inclusief walk-forward.'], ['oos_approved', 'Historische OOS goedgekeurd', 'Goedgekeurd op de afgeschermde historische eindtest. Dit is nog geen forward test.'], ['forward_ready', 'Klaar voor forward', 'Mogen vooruit worden getest op nieuwe data.']];
  const top = Number(cn.generated) || 0;
  const mt = r.multiple_testing;
  return `<div class="card fx-card"><h3 class="fx-h">Van idee naar kandidaat ${tip('Elke stap is strenger. Weinig overlevenden is normaal en gewenst.')}${mt ? `<small class="fx-muted">${esc(typeof mt === 'string' ? mt : mt.note_nl || '')}</small>` : ''}</h3>
    <div class="fx-funnel">${steps.map(([k, l, t], i) => { const v = num(cn[k]) ? Number(cn[k]) : null; const w = top && v !== null ? Math.max(6, Math.round(v / top * 100)) : 6; const prev = i && num(cn[steps[i - 1][0]]) && Number(cn[steps[i - 1][0]]) ? v / Number(cn[steps[i - 1][0]]) : null;
      return `<div class="fx-fstep" data-tip="${esc(t)}"><div class="fx-fbar"><i style="width:${w}%"></i></div><b>${int(v)}</b><span>${esc(l)}</span>${prev !== null ? `<em>${fmt(prev * 100, 0)}% blijft over</em>` : ''}</div>`; }).join('')}</div></div>`;
}

const evChip = c => { const e = c.evidence_class; return e ? `<span class="chip info" data-tip="Soort bewijs">${esc(evidNl(e))}</span>` : ''; };
function statusChip(c) {
  const t = `${c.status || ''} ${c.stage || ''}`.toLowerCase();
  const cls = /reject|afgew|fail/.test(t) ? 'bad' : /forward_ready|approved|goedgekeurd/.test(t) ? 'good' : /warn|uncertain|twijfel/.test(t) ? 'warn' : 'info';
  const label = /reject|afgew/.test(String(c.status || '').toLowerCase()) ? 'Afgewezen' : stageNl(c.status && STAGE_NL[c.status] ? c.status : c.stage || c.status);
  return `<span class="chip ${cls}" ${c.reasons_nl?.length ? `data-tip="${esc([].concat(c.reasons_nl).slice(0, 3).join('\n'))}"` : ''}>${esc(label)}</span>`;
}
const isRejected = c => /reject|afgew|fail/.test(`${c.status || ''} ${c.stage || ''}`.toLowerCase());
const posNum = v => num(v) && Number(v) > 0;
function isKandidaat(c) {
  if (!posNum(c.net_return_pct)) return false;
  const v = first(c, 'validation_positive', 'validation_ok');
  if (typeof v === 'boolean') return v;
  const vr = first(c, 'validation_net_return_pct', 'validation_return_pct', 'wf_oos_return_pct') ?? first(c.validation || {}, 'net_return_pct');
  if (num(vr)) return Number(vr) > 0;
  const hp = String(c.highest_promotion || c.promotion_status || '').toUpperCase();
  return /DEVELOPMENT_PASS|WALK_FORWARD|ROBUSTNESS|HISTORICAL_OOS|FROZEN|FORWARD|PROSPECTIVE/.test(hp) || /forward_ready|oos_approved|dev_validated|wf_passed|frozen/i.test(`${c.status} ${c.stage}`);
}

function candidatesHtml() {
  const all = state.cands || [];
  const kand = all.filter(isKandidaat);
  const rows = state.showRejected ? all.slice() : kand;
  const cols = [['name', 'Naam'], ['symbol', 'Markt / TF'], ['family', 'Familie'], ['trades', 'Trades', 1], ['net_return_pct', 'Netto %', 1], ['max_dd_pct', 'Drawdown', 1], ['profit_factor', 'PF', 1], ['recovery_factor', 'RF', 1], ['status', 'Status']];
  if (state.sort) rows.sort((a, b) => { const x = a[state.sort.k], y = b[state.sort.k]; return (num(x) && num(y) ? Number(x) - Number(y) : String(x ?? '').localeCompare(String(y ?? ''))) * state.sort.d; });
  const rejN = all.length - kand.length;
  return `<div class="card fx-card"><h3 class="fx-h">Kandidaten<small class="fx-muted">${int(kand.length)} kandidaten · ${int(rows.length)} getoond</small>
    <label class="fx-toggle"><input type="checkbox" id="fx-showrej" ${state.showRejected ? 'checked' : ''}><span>Toon afgewezen / onvoldoende bewijs (${int(rejN)})</span></label></h3>
    ${rows.length ? `<div class="fx-scroll"><table class="tbl fx-tbl"><thead><tr>${cols.map(([k, l, n]) => `<th class="${n ? 'num' : ''}" data-sort="${k}" tabindex="0" aria-sort="${state.sort?.k === k ? (state.sort.d > 0 ? 'ascending' : 'descending') : 'none'}">${l}</th>`).join('')}<th></th></tr></thead><tbody>
    ${rows.map(c => `<tr class="clickable" data-cand="${esc(c.id)}" tabindex="0"><td><b>${esc(c.name || c.id)}</b></td><td>${esc(mlabel(c.symbol))} <span class="fx-muted">${esc(c.timeframe || '')}</span></td><td>${esc(c.family || '—')}</td><td class="num">${int(c.trades)}</td><td class="num ${num(c.net_return_pct) ? (c.net_return_pct >= 0 ? 'fx-pos' : 'fx-neg') : ''}">${pc(c.net_return_pct)}</td><td class="num">${pc(c.max_dd_pct)}</td><td class="num">${fmt(c.profit_factor)}</td><td class="num">${fmt(c.recovery_factor)}</td><td>${statusChip(c)}</td><td class="act"><button type="button" class="secondary fx-mini" data-mv="${esc(c.id)}">Andere markten</button></td></tr>`).join('')}</tbody></table></div>`
      : `<div class="empty"><strong>${all.length ? 'Nog geen kandidaten met positieve backtest en validatie' : 'Nog geen kandidaten'}</strong>${all.length ? 'Zet "Toon afgewezen / onvoldoende bewijs" aan om te zien waarom strategieën zijn afgevallen.' : ''}</div>`}</div>`;
}

function familiesHtml(r) {
  return (r.families || []).length ? `<div class="card fx-card"><h3 class="fx-h">Per familie ${tip('Veel geteste varianten met weinig overlevenden wijst op toeval, niet op een bruikbare edge.')}</h3><div id="fx-fam" class="fx-chart"></div></div>` : '';
}
function drawFamilies(r) {
  const f = (r.families || []).slice().sort((a, b) => (b.tested || 0) - (a.tested || 0)); const c = themeColors();
  if (!f.length) return;
  const el = state.el.querySelector('#fx-fam'); if (el) el.style.height = Math.max(180, 44 + f.length * 34) + 'px';
  plot(el, [{type: 'bar', orientation: 'h', name: 'Getest', y: f.map(x => x.family), x: f.map(x => x.tested), marker: {color: c.ink2}, opacity: .5, hovertemplate: '%{y}: %{x} getest<extra></extra>'},
    {type: 'bar', orientation: 'h', name: 'Overleefd', y: f.map(x => x.family), x: f.map(x => x.survived), marker: {color: c.good}, hovertemplate: '%{y}: %{x} overleefd<extra></extra>'}],
    {barmode: 'group', margin: {l: 150, r: 14, t: 24, b: 30}, yaxis: {autorange: 'reversed', gridcolor: 'rgba(0,0,0,0)'}});
}

function moreHtml(r) {
  const cfg = r.config, proto = r.protocol, mt = r.multiple_testing;
  if (!cfg && !proto && !mt) return '';
  const block = (t, o) => o ? `<div><div class="fx-label">${t}</div>${kv(o)}</div>` : '';
  return `<details class="fx-adv fx-more"><summary>Meer over dit onderzoek</summary><div class="fx-more-grid">${block('Protocol', proto)}${block('Multiple testing', mt)}${block('Instellingen', cfg)}</div></details>`;
}
function kv(o, depth = 0) {
  if (o === null || o === undefined) return '—';
  if (typeof o !== 'object') return esc(num(o) && !Number.isInteger(Number(o)) ? fmt(o, 3) : o);
  if (Array.isArray(o)) {
    if (o.length > 8) return `<span class="fx-muted">${o.length} waarden (reeks)</span>`;
    return o.length ? o.map(x => typeof x === 'object' && x ? kv(x, depth + 1) : esc(x)).join(', ') : '—';
  }
  if (depth >= 1) return `<span class="fx-inline">${Object.entries(o).map(([k, v]) => `${esc(k.replace(/_/g, ' '))}: ${typeof v === 'object' && v ? (Array.isArray(v) && v.length > 8 ? `${v.length} waarden` : depth >= 2 ? '…' : kv(v, depth + 1)) : esc(num(v) && !Number.isInteger(Number(v)) ? fmt(v, 3) : v)}`).join(' · ')}</span>`;
  return `<dl class="fx-kv">${Object.entries(o).map(([k, v]) => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd>${kv(v, depth + 1)}</dd>`).join('')}</dl>`;
}

/* ---------------- detail ---------------- */
let lastFocus = null;
function closeDetail() { const d = state.el?.querySelector('#fx-drawer'); if (!d) return; d.classList.remove('open'); state.el.querySelector('#fx-scrim').classList.remove('open'); state.detailId = null; lastFocus?.focus?.(); }

async function openDetail(id) {
  const d = state.el.querySelector('#fx-drawer'); lastFocus = document.activeElement; state.detailId = id;
  d.innerHTML = '<div class="fx-muted" style="padding:20px">Laden…</div>'; d.classList.add('open'); state.el.querySelector('#fx-scrim').classList.add('open'); d.focus();
  try {
    const x = await api(`/api/factory/candidates/${encodeURIComponent(id)}`);
    if (state.detailId !== id) return;
    renderDetail(d, id, x);
  } catch (err) { d.innerHTML = `<button type="button" class="secondary fx-close" aria-label="Sluiten">Sluiten</button><div class="notice bad" style="margin-top:12px">Detail laden mislukt: ${esc(err.message)}</div>`; d.querySelector('.fx-close').onclick = closeDetail; }
}


/* ---------------- samenvatting ---------------- */
const REPORT_ST = {
  PASSED: ['good', 'Goedgekeurd'], REJECTED: ['bad', 'Afgewezen'],
  INSUFFICIENT_EVIDENCE: ['warn', 'Onvoldoende bewijs'], TECHNICAL_BLOCKED: ['idle', 'Technisch geblokkeerd'],
};
function reportStatus(x, row, eligible) {
  const given = String(first(x, 'report_status', 'verdict_status') ?? first(row, 'report_status') ?? '').toUpperCase();
  if (REPORT_ST[given]) return given;
  const raw = `${x.status || row.status || ''} ${x.promotion_status || ''} ${x.evidence_verdict || ''}`.toUpperCase();
  const tech = [].concat(x.technical || []);
  if (/TECHNICAL|BLOCKED/.test(raw) || (String(x.stage || row.stage) === 'L1' && /REJECT/.test(raw)) || tech.some(t => t.ok === false)) return 'TECHNICAL_BLOCKED';
  if (/INSUFFICIENT/.test(raw)) return 'INSUFFICIENT_EVIDENCE';
  if (eligible === true || /PASS|FORWARD_ELIGIBLE|APPROVED/.test(String(x.evidence_verdict || '').toUpperCase())) return 'PASSED';
  if (/REJECT|FAIL/.test(raw)) return 'REJECTED';
  return null;
}
const stChip = k => k ? `<span class="chip fx-st fx-st-${k.toLowerCase().replace(/_/g, '-')} ${REPORT_ST[k][0]}">${REPORT_ST[k][1]}</span>` : '';
const KIND_NL = {statistisch: 'Statistische reden', technisch: 'Technische reden', 'onvoldoende bewijs': 'Onvoldoende bewijs'};
const dRange = (a, b) => a && b ? `${esc(a)} t/m ${esc(b)}` : null;
function periodsOf(x) {
  const p = first(x, 'periods', 'periodes');
  if (p && typeof p === 'object') {
    const o = [['Ontwikkeling', first(p, 'dev', 'development', 'ontwikkeling')], ['Validatie', first(p, 'validation', 'validatie')], ['Eindtest', first(p, 'final', 'sealed', 'eindtest')]];
    const out = o.map(([l, v]) => { const r = v && typeof v === 'object' ? dRange(first(v, 'start', 'from'), first(v, 'end', 'to')) : typeof v === 'string' ? esc(v) : null; return r ? [l, r] : null; }).filter(Boolean);
    if (out.length) return out;
  }
  const pr = state.run?.protocol; if (!pr) return [];
  return [['Ontwikkeling', dRange(first(pr, 'dev_start'), first(pr, 'development_end'))],
    ['Validatie', dRange(first(pr, 'development_end'), first(pr, 'sealed_start'))],
    ['Eindtest', first(pr, 'sealed_start') ? `vanaf ${esc(pr.sealed_start)} (afgeschermd${x.final_test ? '' : ', nog niet gebruikt'})` : null]].filter(a => a[1]);
}
function costsOf(x) {
  const o = first(x, 'costs', 'cost_model', 'costs_assumed') || deep(x, 'costs') || deep(x, 'cost_model') || deep(x, 'cost_assumptions');
  if (!o || typeof o !== 'object') return null;
  const parts = [['Spread', first(o, 'spread', 'spread_pips', 'spread_points', 'spread_mode')], ['Commissie', first(o, 'commission', 'commissie', 'commission_per_lot')], ['Slippage', first(o, 'slippage', 'slippage_pips', 'slippage_points')]]
    .filter(([, v]) => v !== undefined && v !== null).map(([l, v]) => `${l}: ${esc(typeof v === 'object' ? JSON.stringify(v) : num(v) ? fmt(v, Number.isInteger(Number(v)) ? 0 : 2) : v)}`);
  return parts.length ? parts.join(' · ') : null;
}
function dsrVariants(x) {
  const st = deep(x, 'statistics') || {}; const out = []; const seen = new Set();
  const walk = (o, d) => { if (!o || typeof o !== 'object' || d > 3) return; for (const [k, v] of Object.entries(o)) {
    if (/dsr/i.test(k) && !/n_trials|note|scope|(^|_)n$/i.test(k)) { const val = v && typeof v === 'object' ? first(v, 'value', 'dsr') : v; const name = (v && typeof v === 'object' && first(v, 'label', 'variant', 'name')) || k; if (num(val) && !seen.has(name)) { seen.add(name); out.push([String(name), Number(val)]); } }
    else if (v && typeof v === 'object' && !Array.isArray(v)) walk(v, d + 1); } };
  walk(st, 0); if (!out.length) walk(x.final_test, 0); return out;
}
function summaryHtml(x, row, reasons, eligible) {
  const c = x.candidate || {}, m = x.dev?.metrics || {}, ft = x.final_test;
  const k = reportStatus(x, row, eligible);
  const kind = String(first(x, 'rejection_kind') ?? first(row, 'rejection_kind') ?? '').toLowerCase();
  const kindChip = kind ? `<span class="chip idle" data-tip="Soort reden voor afwijzing">${esc(KIND_NL[kind] || kind)}</span>` : '';
  const sm = x.summary_nl && typeof x.summary_nl === 'object' ? x.summary_nl : {};
  const st = deep(x, 'statistics') || {}; const dsr = first(st, 'dsr'); const dd = dsr && typeof dsr === 'object' ? dsr : {};
  const mt = x.multiple_testing_final || x.multiple_testing || state.run?.multiple_testing || {};
  const raw = first(sm, 'trials_raw') ?? first(dd, 'n_trials_raw', 'raw_n', 'n_trials') ?? first(st, 'n_trials') ?? first(mt, 'search_trials', 'dsr_n_trials');
  const eff = first(sm, 'trials_effective') ?? first(dd, 'n_eff', 'effective_n', 'n_trials_effective', 'n_effective');
  const trades = first(sm, 'trades') ?? first(m, 'trades', 'n_trades') ?? row.trades;
  const ftTr = ft && first(ft.metrics || ft, 'trades', 'n_trades');
  const per = periodsOf(x), costs = first(sm, 'costs_nl') || costsOf(x);
  const variants = dsrVariants(x);
  const missing = [].concat(first(x, 'missing_nl') ?? first(sm, 'missing_nl') ?? []);
  const nextT = [].concat(first(x, 'next_test_nl') ?? first(sm, 'next_test_nl') ?? []);
  const why = first(sm, 'why_nl') || reasons[0];
  const li = (q, a) => `<div class="fx-sq"><dt>${q}</dt><dd>${a}</dd></div>`;
  const na = '<span class="fx-muted">niet beschikbaar</span>';
  return `<section class="fx-summary" aria-label="Samenvatting"><div class="fx-sum-head"><h4>Samenvatting</h4>${stChip(k)}${kindChip}</div><dl class="fx-sumdl">
    ${li('Wat is getest?', `${esc(c.name || row.name || '')} · ${esc(mlabel(row.symbol || c.symbol))} ${esc(row.timeframe || c.timeframe || '')}${c.family || row.family ? ` · ${esc(c.family || row.family)}` : ''}`)}
    ${li('Welke periode?', per.length ? per.map(([l, v]) => `<span class="fx-per"><b>${l}</b> ${v}</span>`).join('') : na)}
    ${li('Hoeveel trials?', raw !== undefined || eff !== undefined ? `${raw !== undefined ? `${int(raw)} ruw` : ''}${raw !== undefined && eff !== undefined ? ' · ' : ''}${eff !== undefined ? `${fmt(eff, 1)} effectief (N)` : ''}` : na)}
    ${li('Welke kosten?', costs || na)}
    ${li('Hoeveel trades?', num(trades) || num(ftTr) ? `${num(trades) ? `${int(trades)} in ontwikkeling` : ''}${num(trades) && num(ftTr) ? ' · ' : ''}${num(ftTr) ? `${int(ftTr)} in eindtest` : ''}` : na)}
    ${li(k === 'PASSED' ? 'Waarom goedgekeurd?' : 'Waarom afgewezen?', why ? esc(why) : na)}
    ${li('Hoe zeker?', variants.length ? variants.map(([n, v]) => `<span class="fx-per"><b>${esc(n)}</b> ${fmt(v, 3)}</span>`).join('') : '<span class="fx-muted">Geen DSR beschikbaar (nog geen eindtest).</span>')}
    ${li('Nog niet bewezen', missing.length ? `<ul class="fx-list">${missing.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : (first(x, 'missing_nl') !== undefined ? '<span class="fx-muted">Geen openstaande punten vastgelegd.</span>' : na))}
    ${nextT.length ? li('Mogelijke volgende test', `<ul class="fx-list">${nextT.map(t => `<li>${esc(t)}</li>`).join('')}</ul>`) : ''}
  </dl><p class="fx-muted fx-sum-note">Technische details staan hieronder en onder Meer analyse.</p></section>`;
}

const metricRow = (l, v, t) => `<div class="fx-metric"><span>${l}${t ? ' ' + tip(t) : ''}</span><b>${v}</b></div>`;
const provChip = s => { const t = String(s?.provenance || s?.type || s?.bron || '').toLowerCase(); const lbl = /proxy/.test(t) ? 'Proxy' : /own|eigen|variant/.test(t) ? 'Eigen variant' : 'Bron'; return `<span class="chip ${lbl === 'Proxy' ? 'warn' : lbl === 'Bron' ? 'info' : 'idle'}">${lbl}</span>`; };

function renderDetail(d, id, x) {
  const c = x.candidate || {}, dev = x.dev || {}, m = dev.metrics || {}, ft = x.final_test;
  const row = state.cands.find(r => String(r.id) === String(id)) || {};
  const spec = c.spec || {};
  const name = row.name || spec.name || id;
  const warns = [].concat(x.warnings_nl || []);
  const reasons = [].concat(x.reasons_nl || row.reasons_nl || []);
  const eligible = x.forward_eligible ?? x.eligible_for_forward ?? (row.stage === 'forward_ready' || row.status === 'forward_ready' || row.status === 'oos_approved' ? true : undefined);
  const rules = c.rules_nl;
  const rulesHtml = rules ? (Array.isArray(rules) ? `<ul class="fx-list">${rules.map(r => `<li>${esc(typeof r === 'string' ? r : r.text_nl || JSON.stringify(r))}</li>`).join('')}</ul>` : typeof rules === 'object' ? Object.entries(rules).map(([k, v]) => `<div class="fx-rule"><b>${esc(k.replace(/_/g, ' '))}</b><ul class="fx-list">${[].concat(v).map(r => `<li>${esc(typeof r === 'string' ? r : JSON.stringify(r))}</li>`).join('')}</ul></div>`).join('') : `<p>${esc(rules)}</p>`) : '<span class="fx-muted">Geen regels beschikbaar.</span>';
  const ps = c.params && typeof c.params === 'object' ? Object.entries(c.params) : [];
  const iq = [].concat(c.iq_sources || []);
  const proxies = [].concat(c.proxies_nl || []);
  const pass = c.passport;
  const yearly = dev.yearly;
  const cs = x.robustness?.cost_stress;
  const wfs = x.walk_forward;
  const dist = x.distribution;
  d.innerHTML = `<div class="fx-dhead"><div><h3>${esc(name)}</h3><div class="fx-dsub">${esc(mlabel(row.symbol || spec.symbol))} ${esc(row.timeframe || spec.timeframe || '')} · ${esc(row.family || spec.family || '')} ${row.id ? statusChip(row) : ''}</div></div><button type="button" class="secondary fx-close" aria-label="Sluiten">Sluiten</button></div>
    ${warns.length ? `<div class="fx-warnbox" role="alert">${warns.map(w => `<div>${esc(w)}</div>`).join('')}</div>` : ''}
    ${summaryHtml(x, row, reasons, eligible)}
    ${c.hypothesis_nl ? `<p class="fx-hyp">${esc(c.hypothesis_nl)}</p>` : ''}
    <div class="fx-kpis">${metricRow('Netto', pc(first(m, 'net_return_pct', 'net_pct') ?? row.net_return_pct))}${metricRow('Drawdown', pc(first(m, 'max_dd_pct', 'max_drawdown_pct') ?? row.max_dd_pct))}${metricRow('Profit factor', fmt(first(m, 'profit_factor') ?? row.profit_factor))}${metricRow('Trades', int(first(m, 'trades', 'n_trades') ?? row.trades))}${metricRow('Recovery factor', fmt(first(m, 'recovery_factor') ?? row.recovery_factor))}</div>
    <nav class="fx-jump" aria-label="Rapportonderdelen"><a href="#fx-g-prest">Prestaties</a><a href="#fx-g-stab">Stabiliteit</a><a href="#fx-g-stat">Statistische betrouwbaarheid</a><a href="#fx-g-final">Eindbeoordeling</a></nav>
    <div class="fx-grp" id="fx-g-prest"><h3>Prestaties</h3>
    <section><h4>Equitycurve ${tip('Ontwikkelperiode en historische eindtest apart gekleurd. Alleen de eindtest telt als onafhankelijk bewijs.')}</h4><div class="fx-evrow">${evChip(row.evidence_class ? row : {evidence_class: x.final_test ? 'sealed' : 'development'})}</div><div id="fx-eq" class="fx-chart fx-chart-sm"></div></section>
    <section><h4>Trades op prijs</h4><div id="fx-trades" class="fx-chart fx-chart-sm"><span class="fx-muted">Laden…</span></div></section>
    <div class="fx-2col"><section><h4>Regels</h4>${rulesHtml}</section><section><h4>Parameters</h4>${ps.length ? `<dl class="fx-kv">${ps.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === 'number' ? fmt(v, Number.isInteger(v) ? 0 : 3) : typeof v === 'object' ? JSON.stringify(v) : v)}</dd>`).join('')}</dl>` : '<span class="fx-muted">Geen parameters.</span>'}</section></div>
    <section><h4>Herkomst ${tip('Bron: bekende publieke methode. Eigen variant: afgeleid door het lab. Proxy: benadering omdat de oorspronkelijke data ontbreekt.')}</h4>
      ${iq.length ? `<div class="fx-prov">${iq.map(s => `<div>${provChip(s)}<span>${esc(typeof s === 'string' ? s : s.label_nl || s.name || s.title || s.id || JSON.stringify(s))}</span></div>`).join('')}</div>` : ''}
      ${proxies.length ? `<ul class="fx-list">${proxies.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}${pass ? `<div class="fx-passport">${kv(pass)}</div>` : ''}${!iq.length && !proxies.length && !pass ? '<span class="fx-muted">Geen herkomst vastgelegd.</span>' : ''}</section>
    <div class="fx-2col"><section><h4>Resultaat per jaar</h4><div id="fx-yearly" class="fx-chart fx-chart-xs">${yearly ? '' : '<span class="fx-muted">Niet beschikbaar.</span>'}</div></section>
      <section><h4>Winst- en verliesreeksen ${tip('Clusters van opeenvolgende winst- of verliestrades. Lange verliesreeksen zijn psychologisch en financieel zwaar.')}</h4><div id="fx-dist">${distHtml(dist)}</div></section></div>
    </div><div class="fx-grp" id="fx-g-stab"><h3>Stabiliteit</h3>
    ${cs ? `<section><h4>Kostenstress ${tip('Resultaat bij hogere spread, slippage en commissie.')}</h4>${costHtml(cs)}</section>` : ''}
    ${wfs ? `<section><h4>Walk-forward</h4>${wfHtml(wfs)}</section>` : ''}
    <section><h4>Ontwikkeling vs eindtest</h4>${cmpHtml(m, ft, x.baseline_comparison)}</section>
    ${stabExtraHtml(x)}</div>
    <div class="fx-grp" id="fx-g-stat"><h3>Statistische betrouwbaarheid</h3>${statHtml(x)}</div>
    <div class="fx-grp" id="fx-g-final"><h3>Eindbeoordeling</h3>${finalHtml(x, row, reasons, eligible)}</div>
    <details class="fx-adv fx-more"><summary>Meer analyse</summary><div class="fx-more-grid">${[['Scorecard', x.scorecard], ['Monte Carlo', x.robustness?.monte_carlo], ['Parameter-perturbatie', x.robustness?.perturbation], ['Optimalisatie', x.optimisation], ['Multiple testing', x.multiple_testing || state.run?.multiple_testing], ['Sessies', dev.sessions], ['Regimes', dev.regimes], ['Maandresultaat', dev.monthly]].filter(a => a[1]).map(([t, o]) => `<div><div class="fx-label">${t}</div>${scorecardOrKv(t, o)}</div>`).join('') || '<span class="fx-muted">Geen extra analyses.</span>'}</div></details>
    <div class="fx-dfoot"><button type="button" id="fx-fwd">Naar forwardtest</button><button type="button" class="secondary" id="fx-mv">Test op andere markten</button><span class="fx-muted" id="fx-fwd-note"></span></div>`;
  d.querySelector('.fx-close').onclick = closeDetail;
  d.querySelector('#fx-mv').onclick = () => multimarketFor(id);
  d.querySelectorAll('.fx-jump a').forEach(a => a.onclick = e => { e.preventDefault(); d.querySelector(a.getAttribute('href'))?.scrollIntoView({behavior: 'smooth', block: 'start'}); });
  const fb = d.querySelector('#fx-fwd'), fn = d.querySelector('#fx-fwd-note');
  if (eligible === false) { fb.disabled = true; fn.textContent = reasons[0] || 'Nog niet geschikt voor forward: de eindtest is niet gehaald.'; }
  else if (eligible === undefined && !/forward_ready/.test(`${row.status} ${row.stage}`)) { fb.disabled = true; fn.textContent = 'Alleen strategieën die de historische eindtest haalden kunnen naar forward.'; }
  fb.onclick = async () => {
    fb.disabled = true; fn.textContent = 'Bezig…';
    try { await api(`/api/factory/candidates/${encodeURIComponent(id)}/forward`, {method: 'POST'}); fn.textContent = 'Toegevoegd aan forwardtest.'; if (window.labGo) setTimeout(() => window.labGo('forward'), 700); }
    catch (err) { fn.textContent = err.status === 409 ? (err.data?.reason_nl || err.data?.reason || err.data?.detail?.reason_nl || err.data?.detail?.reason || err.message) : err.message; if (err.status !== 409) fb.disabled = false; }
  };
  const c2 = themeColors();
  const dv = series(dev.equity), fs = ft ? series(ft.equity || ft.equity_curve || ft.metrics?.equity) : null;
  const traces = [];
  if (dv) traces.push({x: dv.x, y: dv.y, mode: 'lines', name: 'Ontwikkeling', line: {color: c2.info, width: 1.6}});
  const wo = series(x.walk_forward?.oos_equity);
  if (wo) traces.push({x: wo.x, y: wo.y, mode: 'lines', name: 'Walk-forward OOS (validatie)', line: {color: c2.good || '#3fc488', width: 1.6}});
  if (fs) {
    const offset = typeof dv?.x[0] === 'number' && typeof fs.x[0] === 'number' && fs.x[0] === 0 ? dv.x.length : 0;
    traces.push({x: offset ? fs.x.map(v => v + offset) : fs.x, y: fs.y, mode: 'lines', name: 'Eindtest', line: {color: c2.warn, width: 1.8}});
  }
  const eqEl = d.querySelector('#fx-eq');
  if (traces.length) plot(eqEl, traces, {yaxis: {gridcolor: c2.edge, title: {text: 'Equity %', font: {size: 10}}}}); else if (Array.isArray(x.quick) && x.quick.length) eqEl.innerHTML = `<p class="fx-muted">Afgewezen in de snelle test; er is daarom geen volledige ontwikkelbacktest. Resultaat per representatief venster:</p><table class="tbl"><thead><tr><th>Venster</th><th>Regime</th><th class="num">Trades</th><th class="num">Netto</th><th class="num">PF</th></tr></thead><tbody>${x.quick.map(w => `<tr><td>${esc(w.start)} – ${esc(w.end)}</td><td>${esc(w.regime_nl || '')}</td><td class="num">${esc(w.metrics?.n_trades ?? w.metrics?.trades ?? '—')}</td><td class="num">${w.metrics?.net_profit == null ? '—' : Number(w.metrics.net_profit).toFixed(0)}</td><td class="num">${w.metrics?.profit_factor == null ? '—' : Number(w.metrics.profit_factor).toFixed(2)}</td></tr>`).join('')}</tbody></table>`;
  else eqEl.innerHTML = '<span class="fx-muted">Geen equitycurve beschikbaar.</span>';
  if (yearly) drawYearly(d.querySelector('#fx-yearly'), yearly);
  drawTrades(d.querySelector('#fx-trades'), x, row, spec);
}

function multimarketFor(id) {
  const row = (state.cands || []).find(r => String(r.id) === String(id)) || {};
  window.labV10?.multimarket({kind: 'candidate', candidate_id: id, name: row.name || id, symbol: row.symbol, timeframe: row.timeframe});
}

const deep = (o, key, depth = 0) => {
  if (!o || typeof o !== 'object' || depth > 4) return undefined;
  if (o[key] !== undefined && o[key] !== null) return o[key];
  for (const v of Object.values(o)) { const r = deep(v, key, depth + 1); if (r !== undefined) return r; }
  return undefined;
};
const srow = (l, v, t) => v === undefined || v === null || v === '' ? '' : `<div class="fx-metric"><span>${l}${t ? ' ' + tip(t) : ''}</span><b>${v}</b></div>`;
const rangeOf = v => Array.isArray(v) ? v : v && typeof v === 'object' ? [first(v, 'lo', 'low', 'lower', 'ci_low', 'p05', 'p2_5'), first(v, 'hi', 'high', 'upper', 'ci_high', 'p95', 'p97_5')] : null;

function statHtml(x) {
  const stats = deep(x, 'statistics') || {};
  const dsr = first(stats, 'dsr') || deep(x, 'dsr');
  const psr = first(stats, 'psr') || deep(x, 'psr');
  let pbo = deep(x, 'pbo_result') ?? x.robustness?.pbo ?? deep(x, 'pbo');
  if (pbo && typeof pbo === 'object') pbo = first(pbo, 'pbo');
  const ci = first(stats, 'trade_pnl_ci') || deep(x, 'trade_pnl_ci') || deep(x, 'confidence_intervals');
  const mt = x.multiple_testing || state.run?.multiple_testing;
  const d = dsr && typeof dsr === 'object' ? dsr : null;
  const dsrVal = d ? first(d, 'value', 'dsr') : (num(dsr) ? Number(dsr) : undefined);
  const rows = [];
  const pick = (o, ...ks) => { const v = first(o, ...ks); return num(v) ? v : undefined; };
  if (d || dsrVal !== undefined) {
    rows.push(srow('Deflated Sharpe Ratio (DSR)', num(dsrVal) ? fmt(dsrVal, 3) : '—', 'Kans dat de gemeten Sharpe boven het toeval-maximum uitkomt na correctie voor het aantal geprobeerde varianten. Dicht bij 1 is sterk, rond 0,5 of lager is zwak.'));
    if (d) {
      const v = {
        sr: pick(d, 'sr', 'observed_sr'), sr0: pick(d, 'sr0'),
        raw: pick(d, 'n_trials_raw', 'raw_n', 'n_trials') ?? (num(stats.n_trials) ? stats.n_trials : undefined),
        eff: pick(d, 'n_eff', 'effective_n', 'n_trials_effective', 'n_effective'),
        V: pick(d, 'sr_variance_across_trials', 'v', 'V', 'sr_variance') ?? (num(stats.sr_variance) ? stats.sr_variance : undefined),
        T: pick(d, 'n', 'T', 't_obs'), skew: pick(d, 'skew', 'skewness'), kurt: pick(d, 'kurt', 'kurtosis'),
      };
      rows.push(srow('Gemeten Sharpe (SR)', v.sr !== undefined ? fmt(v.sr, 4) : undefined, 'Sharpe-ratio per waarneming (dag).'));
      rows.push(srow('Toeval-drempel (SR0)', v.sr0 !== undefined ? fmt(v.sr0, 4) : undefined, 'Het Sharpe-maximum dat je door puur toeval verwacht bij zoveel pogingen.'));
      rows.push(srow('Ruwe N (alle pogingen)', v.raw !== undefined ? int(v.raw) : undefined, 'Aantal geteste varianten volgens het onderzoekslogboek, ook de verliezers.'));
      rows.push(srow('Effectieve N', v.eff !== undefined ? fmt(v.eff, 1) : undefined, 'Aantal onafhankelijke pogingen: varianten die op elkaar lijken tellen minder mee.'));
      rows.push(srow('V (variantie van SR)', v.V !== undefined ? fmt(v.V, 6) : undefined, 'Spreiding van de Sharpe-ratio over alle geprobeerde varianten.'));
      rows.push(srow('T (waarnemingen)', v.T !== undefined ? int(v.T) : undefined, 'Aantal waarnemingen (dagen) in de eindtest.'));
      rows.push(srow('Skewness', v.skew !== undefined ? fmt(v.skew, 2) : undefined));
      rows.push(srow('Kurtosis', v.kurt !== undefined ? fmt(v.kurt, 2) : undefined));
    }
  }
  const pv = psr && typeof psr === 'object' ? first(psr, 'value') : (num(psr) ? psr : undefined);
  rows.push(srow('Probabilistic Sharpe (PSR)', num(pv) ? fmt(pv, 3) : undefined, 'Kans dat de echte Sharpe positief is, rekening houdend met scheefheid en dikke staarten.'));
  rows.push(srow('Kans op overfit (PBO)', num(pbo) ? `${fmt(Number(pbo) * 100, 0)}%` : undefined, 'Probability of Backtest Overfitting: hoe vaak de beste in-sample variant buiten de steekproef uitvalt. Boven 50% is een waarschuwing.'));
  let ciHtml = '';
  if (ci && typeof ci === 'object') {
    const items = Object.entries(ci).map(([k, v]) => { const r = rangeOf(v); return r && (num(r[0]) || num(r[1])) ? `<div class="fx-metric"><span>${esc(k.replace(/_/g, ' '))}</span><b>${fmt(r[0], 2)} tot ${fmt(r[1], 2)}</b></div>` : ''; }).join('');
    ciHtml = `<h5 class="fx-sub">Betrouwbaarheidsintervallen</h5>${items ? `<div class="fx-two">${items}</div>` : kv(ci)}`;
  }
  const mtHtml = mt ? `<h5 class="fx-sub">Multiple testing</h5>${typeof mt === 'string' ? `<p>${esc(mt)}</p>` : mt.note_nl ? `<p>${esc(mt.note_nl)}</p>` : ''}<details><summary>Alle cijfers</summary>${kv(mt)}</details>` : '';
  const body = rows.join('');
  if (!body && !ciHtml && !mtHtml) return '<div class="fx-muted">Voor deze kandidaat zijn nog geen statistische betrouwbaarheidscijfers beschikbaar: ze worden berekend voor strategieën die de eerdere stappen halen.</div>';
  return `<div class="fx-two">${body}</div>${ciHtml}${mtHtml}<p class="fx-stat-note">Cijfers komen rechtstreeks uit het onderzoek; ontbrekende regels zijn niet beschikbaar.</p>`;
}

function stabExtraHtml(x) {
  const pert = x.robustness?.perturbation, mc = x.robustness?.monte_carlo;
  return `${pert ? `<section><h4>Parameterstabiliteit ${tip('Wat gebeurt er met het resultaat als de parameters een beetje veranderen. Een scherpe piek wijst op overfitting.')}</h4>${kv(pert)}</section>` : ''}${mc ? `<section><h4>Monte Carlo ${tip('Het resultaat met willekeurig herschudde trades: laat zien hoeveel geluk er in de volgorde zat.')}</h4>${kv(mc)}</section>` : ''}`;
}

function finalHtml(x, row, reasons, eligible) {
  const checks = [].concat(deep(x, 'quality_checks') || x.scorecard?.checks || []);
  const st = c => String(c.status || ((c.passed ?? c.ok) ? 'PASS' : 'FAIL')).toUpperCase();
  const li = c => `<li>${esc(c.explanation_nl || c.label_nl || c.name || c.id || '')}</li>`;
  const strong = checks.filter(c => st(c) === 'PASS'), weak = checks.filter(c => st(c) === 'FAIL'), missing = checks.filter(c => /INSUFF/.test(st(c)));
  const list = (cls, title, items) => items.length ? `<div class="fx-verdict ${cls}"><h5>${title}</h5><ul class="fx-list">${items.join('')}</ul></div>` : '';
  const sc = x.scorecard?.verdict;
  const why = eligible === true ? 'Geschikt voor forwardtest: de historische eindtest is gehaald en er zijn geen harde bezwaren vastgelegd.'
    : eligible === false ? `Nog niet geschikt voor forwardtest. ${reasons[0] || 'De eindtest is niet gehaald.'}`
    : 'Nog niet beoordeeld voor forwardtest: de eindtest is nog niet uitgevoerd.';
  const fallback = !checks.length && reasons.length ? list('warn', 'Redenen en onzekerheden', reasons.map(r => `<li>${esc(r)}</li>`)) : '';
  return `${sc ? `<p>Oordeel scorecard: <b>${esc(String(sc).replace(/_/g, ' '))}</b></p>` : ''}${list('good', 'Sterk', strong.map(li))}${list('bad', 'Zwak', weak.map(li))}${list('warn', 'Ontbrekend bewijs', missing.map(li))}${fallback}
    <div class="fx-verdict ${eligible === true ? 'good' : eligible === false ? 'bad' : 'warn'}"><h5>${eligible === true ? 'Waarom wel naar forward' : 'Waarom (nog) niet naar forward'}</h5><p>${esc(why)}</p></div>`;
}

async function openFromLibrary(runId, id) {
  if (!state.el) return false;
  if (state.runId !== runId) await attach(runId, true);
  openDetail(id);
  return true;
}

function scorecardOrKv(t, o) {
  if (t === 'Scorecard') {
    const arr = Array.isArray(o) ? o : Array.isArray(o.checks) ? o.checks : null;
    if (arr) return `<ul class="fx-list">${arr.map(k => { const ok = k.passed ?? k.ok ?? /pass|ok/i.test(k.status || ''); return `<li><span class="chip ${ok ? 'good' : 'bad'}">${ok ? 'Goed' : 'Niet goed'}</span> ${esc(k.label_nl || k.name || k.id || '')}${k.detail_nl ? ` <span class="fx-muted">${esc(k.detail_nl)}</span>` : ''}</li>`; }).join('')}</ul>`;
  }
  return kv(o);
}

function distHtml(d) {
  if (!d || typeof d !== 'object') return '<span class="fx-muted">Niet beschikbaar.</span>';
  const pct = v => v == null ? '—' : fmt(Number(v) * (Math.abs(v) <= 1.5 ? 100 : 1), 0) + '%';
  const rows = [
    ['Winstgevende dagen', d.winning_days != null && d.trading_days ? `${d.winning_days} van ${d.trading_days} (${pct(d.winning_day_share)})` : '—'],
    ['Gem. dag (winst / verlies)', `${fmt(d.avg_winning_day, 0)} / ${fmt(d.avg_losing_day, 0)}`],
    ['Winst uit beste 3 dagen', pct(d.profit_share_best_3_days)],
    ['Langste winst- / verliesreeks (trades)', `${esc(d.max_consecutive_winning_trades ?? '—')} / ${esc(d.max_consecutive_losing_trades ?? '—')}`],
    ['Langste winst- / verliesreeks (dagen)', `${esc(d.max_consecutive_winning_days ?? '—')} / ${esc(d.max_consecutive_losing_days ?? '—')}`],
    ['Langste drawdown', d.longest_drawdown_days == null ? '—' : `${d.longest_drawdown_days} dagen`],
    ['Langst onder vorige top', d.longest_time_under_high_days == null ? '—' : `${d.longest_time_under_high_days} dagen`],
    ['Langst zonder trades', d.longest_inactive_days == null ? '—' : `${d.longest_inactive_days} dagen`],
  ];
  const wt = d.without_top_days && typeof d.without_top_days === 'object' ? Object.entries(d.without_top_days).map(([k, v]) => `zonder beste ${k}: ${fmt(v?.net_after, 0)}${v?.still_positive === false ? ' (negatief)' : ''}`).join(' · ') : '';
  const warn = [...(d.warnings_nl || []), ...((d.flags || []).map?.(f => typeof f === 'string' ? f : f?.text_nl || '') || [])].filter(Boolean);
  return `${warn.length ? `<div class="fx-warn-box">${warn.map(esc).join('<br>')}</div>` : ''}<dl class="fx-kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>${wt ? `<p class="fx-muted">${esc(wt)}</p>` : ''}<details><summary>Alle verdelingscijfers</summary>${kv(d)}</details>`;
}

function costHtml(cs) {
  const sc = cs?.scenarios && typeof cs.scenarios === 'object' ? cs.scenarios : null;
  if (!sc) return kv(cs);
  const nm = {baseline: 'Basis', 'spread_+25%': 'Spread +25%', 'spread_+50%': 'Spread +50%', commission_x2: 'Commissie ×2', 'slippage_+1pt': 'Slippage +1 punt', entry_delay_1bar: 'Entry 1 candle later', exit_delay_1bar: 'Exit 1 candle later'};
  return `<table class="tbl"><thead><tr><th>Scenario</th><th class="num">Netto</th><th class="num">PF</th><th class="num">Max DD %</th><th class="num">Trades</th></tr></thead><tbody>${Object.entries(sc).map(([k, m]) => `<tr><td>${esc(nm[k] || k)}</td><td class="num">${m.net_profit == null ? '—' : fmt(m.net_profit, 0)}</td><td class="num">${m.profit_factor == null ? '—' : fmt(m.profit_factor, 2)}</td><td class="num">${m.max_drawdown_pct == null ? '—' : fmt(m.max_drawdown_pct, 1)}</td><td class="num">${esc(m.n_trades ?? '—')}</td></tr>`).join('')}</tbody></table><p class="fx-muted">${cs.survives_spread_50 ? 'Blijft positief bij +50% spread.' : 'Niet positief bij +50% spread.'} ${esc(cs.n_scenarios_positive ?? '—')} van ${Object.keys(sc).length} scenario's positief.</p>`;
}
function wfHtml(wf) {
  const one = (label, w) => {
    if (!w || typeof w !== 'object' || !Array.isArray(w.windows)) return '';
    const m = w.oos_metrics || {};
    return `<h5 class="fx-sub">${esc(label)} · WFE ${w.wfe == null ? '—' : fmt(w.wfe, 2)} · OOS netto ${m.net_profit == null ? '—' : fmt(m.net_profit, 0)} · PF ${m.profit_factor == null ? '—' : fmt(m.profit_factor, 2)} · ${esc(m.n_trades ?? '—')} trades · ${w.oos_profitable_windows_frac == null ? '' : fmt(w.oos_profitable_windows_frac * 100, 0) + '% vensters winstgevend'}</h5>
      <table class="tbl"><thead><tr><th>Testvenster</th><th class="num">IS netto</th><th class="num">OOS netto</th><th class="num">OOS trades</th><th>Status</th></tr></thead><tbody>${w.windows.map(x => `<tr><td>${esc(Array.isArray(x.test) ? x.test.join(' – ') : x.test ?? '')}</td><td class="num">${x.train_metrics?.net_profit == null ? '—' : fmt(x.train_metrics.net_profit, 0)}</td><td class="num">${x.test_metrics?.net_profit == null ? '—' : fmt(x.test_metrics.net_profit, 0)}</td><td class="num">${esc(x.test_metrics?.n_trades ?? x.test_metrics?.trades ?? '—')}</td><td>${esc(x.status ?? '')}</td></tr>`).join('')}</tbody></table>`;
  };
  const html = one('Rollend', wf.rolling) + one('Uitbreidend (expanding)', wf.expanding);
  return html || kv(wf);
}

function cmpHtml(m, ft, base) {
  const f = ft?.metrics || ft || null;
  const keys = [['Netto %', ['net_return_pct', 'net_pct'], pc], ['Profit factor', ['profit_factor'], fmt], ['Drawdown', ['max_dd_pct', 'max_drawdown_pct'], pc], ['Trades', ['trades', 'n_trades'], int]];
  const b = base && typeof base === 'object' ? base : null;
  const bv = (ks) => b ? first(b.metrics || b, ...ks) : undefined;
  if (!f && !b) return '<div class="fx-muted">De eindtest is nog niet uitgevoerd. Dit gebeurt pas voor kandidaten die alle eerdere stappen halen.</div>';
  return `<table class="tbl"><thead><tr><th></th><th class="num">Ontwikkeling</th><th class="num">Eindtest</th>${b ? '<th class="num">Baseline</th>' : ''}</tr></thead><tbody>${keys.map(([l, ks, fn]) => `<tr><td>${l}</td><td class="num">${fn(first(m, ...ks))}</td><td class="num">${f ? fn(first(f, ...ks)) : '—'}</td>${b ? `<td class="num">${fn(bv(ks))}</td>` : ''}</tr>`).join('')}</tbody></table>`;
}
function drawYearly(el, y) {
  const c = themeColors();
  const rows = Array.isArray(y) ? y.map(r => [first(r, 'year', 'jaar', 'label'), first(r, 'net_return_pct', 'return_pct', 'net_pct', 'value')]) : Object.entries(y).map(([k, v]) => [k, typeof v === 'object' ? first(v, 'net_return_pct', 'return_pct', 'net_pct') : v]);
  const ok = rows.filter(r => num(r[1]));
  if (!ok.length) { el.innerHTML = '<span class="fx-muted">Niet beschikbaar.</span>'; return; }
  plot(el, [{type: 'bar', x: ok.map(r => String(r[0])), y: ok.map(r => Number(r[1])), marker: {color: ok.map(r => r[1] >= 0 ? c.good : c.bad)}, hovertemplate: '%{x}: %{y:.1f}%<extra></extra>'}], {margin: {l: 40, r: 8, t: 6, b: 28}, xaxis: {type: 'category', gridcolor: 'rgba(0,0,0,0)'}});
}

async function drawTrades(el, x, row, spec) {
  const raw = [].concat(x.trades_sample || []);
  const tt = raw.map(t => ({et: first(t, 'entry_time', 'open_time', 'entry_ts', 'time', 't0'), xt: first(t, 'exit_time', 'close_time', 'exit_ts', 't1'), ep: first(t, 'entry_price', 'entry', 'open_price'), xp: first(t, 'exit_price', 'exit', 'close_price'), pnl: first(t, 'pnl', 'pnl_pct', 'net_pct', 'return_pct', 'profit'), side: String(first(t, 'side', 'direction') || '').toLowerCase()})).filter(t => t.et);
  if (!tt.length) { el.innerHTML = '<span class="fx-muted">Geen voorbeeldtrades beschikbaar.</span>'; return; }
  const c = themeColors();
  const sym = row.symbol || spec.symbol, tf = row.timeframe || spec.timeframe;
  const ts = tt.map(t => new Date(t.et).getTime()).filter(Number.isFinite), te = tt.map(t => new Date(t.xt || t.et).getTime()).filter(Number.isFinite);
  let candles = null;
  if (sym && tf && ts.length && tt.every(t => num(t.ep))) {
    try {
      const pad = 3 * 86400000, a = new Date(Math.min(...ts) - pad), b = new Date(Math.max(...te, ...ts) + pad);
      if (b - a < 40 * 86400000) {
        const r = await api(`/api/candles?symbol=${encodeURIComponent(sym)}&timeframe=${encodeURIComponent(tf)}&start=${a.toISOString().slice(0, 10)}&end=${b.toISOString().slice(0, 10)}&limit=2500`);
        candles = r.candles?.length ? r.candles.map(k => ({timestamp: k.timestamp, open: first(k, 'open', 'bid_open'), high: first(k, 'high', 'bid_high'), low: first(k, 'low', 'bid_low'), close: first(k, 'close', 'bid_close')})) : null;
      }
    } catch { candles = null; }
  }
  if (!el.isConnected) return;
  el.innerHTML = '';
  const long = t => /long|buy|koop/.test(t.side);
  const traces = [];
  if (candles) traces.push({type: 'candlestick', x: candles.map(k => k.timestamp), open: candles.map(k => k.open), high: candles.map(k => k.high), low: candles.map(k => k.low), close: candles.map(k => k.close), name: 'Prijs', increasing: {line: {color: c.good}}, decreasing: {line: {color: c.bad}}});
  if (candles) {
    traces.push({type: 'scatter', mode: 'markers', x: tt.map(t => t.et), y: tt.map(t => t.ep), name: 'Instap', marker: {symbol: tt.map(t => long(t) ? 'triangle-up' : 'triangle-down'), size: 9, color: c.accent, line: {color: c.ink, width: 1}}});
    const ex = tt.filter(t => t.xt && num(t.xp));
    traces.push({type: 'scatter', mode: 'markers', x: ex.map(t => t.xt), y: ex.map(t => t.xp), name: 'Uitstap', marker: {symbol: 'x', size: 8, color: ex.map(t => num(t.pnl) ? (t.pnl >= 0 ? c.good : c.bad) : c.warn)}});
    plot(el, traces, {xaxis: {rangeslider: {visible: false}, gridcolor: c.edge}, yaxis: {gridcolor: c.edge, tickformat: ',.2f'}});
  } else {
    const p = tt.filter(t => num(t.pnl)); let cum = 0;
    if (!p.length) { el.innerHTML = '<span class="fx-muted">Geen prijsdata of resultaat per trade beschikbaar.</span>'; return; }
    const cumv = p.map(t => (cum += Number(t.pnl)));
    plot(el, [{type: 'bar', x: p.map(t => t.et), y: p.map(t => Number(t.pnl)), name: 'Resultaat per trade', marker: {color: p.map(t => t.pnl >= 0 ? c.good : c.bad)}, opacity: .7}, {type: 'scatter', mode: 'lines', x: p.map(t => t.et), y: cumv, name: 'Cumulatief', yaxis: 'y2', line: {color: c.accent}}],
      {yaxis2: {overlaying: 'y', side: 'right', showgrid: false}, margin: {l: 48, r: 48, t: 10, b: 34}});
    el.insertAdjacentHTML('beforeend', '<div class="fx-muted fx-note">Prijsdata niet beschikbaar voor dit venster; getoond zijn de voorbeeldtrades.</div>');
  }
}

window.labFactory = {mount, open: openFromLibrary, _state: state};
// shell.js may already have routed to this view before this script was parsed
document.addEventListener('DOMContentLoaded', () => {
  const v = document.querySelector('[data-view="discover"]');
  if (v && v.classList.contains('active') && window.labGo && !v.querySelector('#fx-root')) { const body = v.querySelector('.view-body'); if (body) { delete body.dataset.ready; body.innerHTML = ''; } window.labShell?.go?.('discover'); }
});
})();
