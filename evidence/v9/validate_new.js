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
        <label><span class="lbl">Strategie${info('Kies één strategie. Builder-strategieën zijn regelspecificaties, Jesse-strategieën zijn Python-klassen in Jesse en Python-plugins zijn losse plugins. Wat er gevalideerd kan worden verschilt per type; zie hieronder.')}</span><select id="val-spec"><option value="">— kies —</option>${groups}${val.missing ? '' : ''}${tplOpts}<option value="inline" hidden>Huidige builder-strategie</option></select></label>
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
    const fs = q(fsId); fs.disabled = !ok; fs.classList.toggle('is-off', !ok);
    q(reasonId).textContent = ok ? '' : `Uitgeschakeld: ${reason}`;
    if (!ok) fs.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = false; });
  };
  gate('#val-fs-wf', '#val-reason-wf', caps.walk_forward, 'deze strategie ondersteunt geen walk-forward.');
  gate('#val-fs-stab', '#val-reason-stab', caps.stability && !legacy, legacy ? 'de server levert (nog) geen parameterstabiliteit via de nieuwe validatie-API.' : 'deze strategie heeft geen parameters die gevarieerd kunnen worden.');
  gate('#val-fs-pbo', '#val-reason-pbo', caps.optimize && !legacy, legacy ? 'de server levert (nog) geen PBO via de nieuwe validatie-API.' : 'PBO heeft een zoekruimte met meerdere combinaties nodig; deze strategie ondersteunt geen optimalisatie.');
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
    const name = esc(p.name), label = esc(p.label || p.name), tip = p.description || p.tip || `Waarde van parameter ${p.name}.`;
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
  const ta = ev.trial_accounting, wf = ev.walk_forward, mc = ev.monte_carlo, ps = ev.parameter_stability, pbo = ev.pbo_result, ho = ev.holdout;
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

  const wfRows = (wf?.windows || []).map(w => ({train: Array.isArray(w.train) ? `${w.train[0]} – ${w.train[1]}` : w.train, test: Array.isArray(w.test) ? `${w.test[0]} – ${w.test[1]}` : w.test, params: flat(w.best_params), is_np: w.train_metrics?.net_profit, oos_np: w.test_metrics?.net_profit, oos_tr: w.test_metrics?.n_trades ?? w.test_metrics?.trades, status: w.status}));
  const oosEq = ev.oos_equity || wf?.oos_equity;
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
    <h4 class="val-sub">Zonder de beste trades ${info('Resultaat nadat de beste trades zijn weggelaten. Blijft het positief, dan hangt de winst niet van een paar uitschieters af.')}</h4>${autoTable(ev.drop_best, 'Niet geleverd door de API.')}
    <h4 class="val-sub">Consistentie per jaar ${info('Resultaat per kalenderjaar. Winst in slechts één jaar is een zwak teken; liever een redelijke spreiding.')}</h4>${autoTable(ev.yearly, 'Niet geleverd door de API.')}`);

  const hoSec = sec('holdout', 'Holdout / OOS', 'oos', 'De laatste periode die buiten alle optimalisatie is gehouden en één keer is getest. Alleen een onaangeroerde holdout telt als echte historische out-of-sample test.',
    ho ? `<div class="val-kpis">${kpi('Periode', `<span style="font-size:15px">${ho.start || ho.end ? `${esc(ho.start ?? '—')} → ${esc(ho.end ?? '—')}` : na()}</span>`)}${kpi('Trades', nv(scalarOrCount(ho.trades), 0))}${kpi('Onaangeroerd', ho.untouched === true ? '<span class="chip good">Ja</span>' : ho.untouched === false ? '<span class="chip bad">Nee</span>' : na())}${kpi('Eerdere inzage', ho.previous_exposures === undefined ? na() : nv(scalarOrCount(ho.previous_exposures), 0), 'keren eerder bekeken')}</div>
      ${ho.untouched === false ? '<div class="notice warn">Deze holdout is eerder ingezien en telt daarom niet als onaangeroerde out-of-sample test.</div>' : ''}${autoTable(ho.metrics, 'Geen holdout-metrics geleverd.')}` : `<p class="hint">${na()} Er is geen holdout uitgevoerd (holdout 0% of niet ondersteund).</p>`);

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
