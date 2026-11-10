const $ = id => document.getElementById(id);
function purgePlots(root){if(!root||!window.Plotly)return;root.querySelectorAll('.js-plotly-plot').forEach(el=>{try{Plotly.Fx.unhover(el);el._hoverdata=undefined;Plotly.purge(el)}catch{}})}
function labResize(target){const el=typeof target==='string'?document.getElementById(target):target;if(!el||!el.data||!el.offsetParent)return;try{Promise.resolve(Plotly.Plots.resize(el)).catch(()=>{})}catch{}}
const state = {strategies:[], candles:[], manifest:null, runManifest:null, activeRun:null, runData:null, replay:false, replaySession:null, causalReplayBusy:false, optimizerSpaceByStrategy:{}, optimizerSort:{key:'trial',direction:1}};
const colors = {PASS:'#42cf8f',FAIL:'#ff7878',SKIP:'#91a5ba'};
const replaySpeeds=[1,3,5,25,50,100];
const candleHalfWidths={M1:30000,M5:150000,M15:450000,M30:900000,H1:1800000,H4:7200000,D1:43200000};
let tooltipSequence=0;

const labNet=(()=>{
  let pill=null;
  const show=()=>{if(!pill){pill=document.createElement('div');pill.id='lab-conn';pill.setAttribute('role','status');pill.textContent='Verbinding herstellen…';document.body.appendChild(pill)}pill.hidden=false};
  const hide=()=>{if(pill)pill.hidden=true};
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const delays=[400,800,1500,3000,3000,3000,5000,5000,5000,5000];
  async function retryFetch(url,options={}){
    const retry=['GET','HEAD'].includes((options.method||'GET').toUpperCase());
    for(let i=0;;i++){
      try{
        const res=await fetch(url,options);
        if(retry&&[502,503,504].includes(res.status)&&i<delays.length){show();await sleep(delays[i]);continue}
        hide();return res;
      }catch(err){
        if(!retry||i>=delays.length){hide();throw err}
        show();await sleep(delays[i]);
      }
    }
  }
  return {fetch:retryFetch,show,hide};
})();
window.labNet=labNet;
async function api(url, options={}) {
  const response = await labNet.fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json().catch(()=>({detail:response.statusText}));
  if (!response.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}
function status(message, kind='') { $('run-status').textContent=message; $('run-status').className=`status ${kind}`; }
async function checkRuntimeBuild(){
  const warning=$('build-warning');if(!warning)return;
  const expected=document.querySelector('meta[name="strategy-lab-frontend-build"]')?.content||'';
  try{
    const build=await api(`/api/version?frontend_build_id=${encodeURIComponent(expected)}`),messages=[];
    if(!build.backend_current)messages.push(build.active_runs?`Servercode is verouderd; update wacht op ${build.active_runs} actieve run(s).`:'Servercode is verouderd; start de app via de bureaubladsnelkoppeling voor een veilige update.');
    if(build.frontend_matches===false)messages.push('De geopende frontend is verouderd; herlaad de applicatie.');
    warning.textContent=messages.join(' ');warning.hidden=messages.length===0;
    warning.dataset.backendBuild=build.backend_build_id;warning.dataset.frontendBuild=build.frontend_build_id;
  }catch{
    warning.textContent='De actieve server biedt geen buildcontrole; open de app via de bureaubladsnelkoppeling om de actuele versie te controleren.';warning.hidden=false;
  }
}
function nice(value, digits=2) { const n=Number(value); return Number.isFinite(n)?n.toLocaleString('nl-NL',{maximumFractionDigits:digits}):'—'; }
function isoDate(ms) { return new Date(ms).toISOString().slice(0,10); }
function selectedStrategy() { return state.strategies.find(s=>s.name===$('strategy').value); }
function makeInfo(text,label='Uitleg',dynamic=false) {
  const wrap=document.createElement('span');wrap.className='help-wrap';
  const button=document.createElement('button');button.type='button';button.className='param-help';button.textContent='i';button.setAttribute('aria-label',`${label} tonen`);button.setAttribute('aria-haspopup','true');
  const bubble=document.createElement('span');bubble.className=`parameter-description${dynamic?' parameter-tooltip':''}`;bubble.setAttribute('role','tooltip');bubble.textContent=text;
  button.setAttribute('aria-describedby',`help-${++tooltipSequence}`);bubble.id=button.getAttribute('aria-describedby');document.body.appendChild(bubble);wrap.append(button);
  const show=()=>{bubble.classList.add('visible');const r=button.getBoundingClientRect();requestAnimationFrame(()=>{const w=bubble.offsetWidth,h=bubble.offsetHeight,left=r.right+8+w<innerWidth?r.right+8:Math.max(8,r.left-w-8);bubble.style.left=`${left}px`;bubble.style.top=`${Math.max(8,Math.min(innerHeight-h-8,r.top))}px`})};
  const hide=()=>bubble.classList.remove('visible');wrap.addEventListener('pointerenter',show);wrap.addEventListener('pointerleave',hide);button.addEventListener('focus',show);button.addEventListener('blur',hide);return {wrap,bubble};
}
function currentParams() {
  const out={}; document.querySelectorAll('[data-param]').forEach(el=>{
    const type=el.dataset.type, raw=el.value.trim().replace(',','.'); out[el.dataset.param]=type==='int'?parseInt(raw,10):type==='float'?Number(raw):type==='bool'?el.checked:el.value;
  }); return out;
}
function buildParams(defs, selected={}) {
  document.querySelectorAll('.parameter-tooltip').forEach(node=>node.remove());
  const primary=$('parameters-primary'),additional=$('parameters-additional'),details=$('additional-parameters');
  primary.innerHTML='';additional.innerHTML='';const advancedRows=[];
  for (let p of defs||[]) {
    const label=document.createElement('label');label.className='param-label';
    const meta=window.paramLabel?window.paramLabel(p.name,p,$('strategy').value):{label:p.label||p.name};p={...p,label:meta.label||p.label||p.name,description:p.description||[meta.help,meta.suggest?`Voorstel: ${meta.suggest}.`:'',meta.example?`Effect: ${meta.example}`:''].filter(Boolean).join(' '),unit:p.unit||meta.unit,simple:p.simple??meta.simple};if(!p.option_labels&&window.PARAM_OPTION_LABELS){const ol=window.PARAM_OPTION_LABELS[`${$('strategy').value}.${p.name}`]||window.PARAM_OPTION_LABELS[p.name];if(ol)p.option_labels=ol}
    const title=document.createElement('span');title.className='param-title';title.textContent=p.label+(p.unit&&!/\(/.test(p.label)?` (${p.unit})`:'');label.appendChild(title);const key=document.createElement('code');key.className='param-key';key.textContent=p.name;title.appendChild(key);
    const input=document.createElement(p.type==='bool'?'input':'input'); input.dataset.param=p.name;input.dataset.type=p.type;
    let optionSelect=null;
    if(p.type==='bool'){input.type='checkbox';input.checked=selected[p.name]??Boolean(p.default);label.prepend(input)}
    else if(p.type==='categorical'){optionSelect=document.createElement('select');optionSelect.dataset.param=p.name;optionSelect.dataset.type=p.type;(p.options||[]).forEach(v=>{const o=document.createElement('option');o.value=String(v);o.textContent=p.option_labels?.[String(v)]||String(v);optionSelect.appendChild(o)});optionSelect.value=String(selected[p.name]??p.default);label.appendChild(optionSelect)}
    else {input.type='text';input.inputMode=p.type==='int'?'numeric':p.type==='float'?'decimal':'text';input.value=selected[p.name]??p.default??'';if(p.min!==undefined)input.dataset.min=p.min;if(p.max!==undefined)input.dataset.max=p.max;label.appendChild(input)}
    if(p.description||p.option_details){
      const tooltipText=()=>{const facts=[];if(p.description)facts.push(p.description);if(p.unit)facts.push(`Eenheid: ${p.unit}.`);if(p.min!==undefined||p.max!==undefined)facts.push(`Bereik: ${p.min??'—'}–${p.max??'—'}.`);if(p.default!==undefined)facts.push(`Standaard: ${p.type==='categorical'?(p.option_labels?.[String(p.default)]||String(p.default)):(p.type==='bool'?(p.default?'Ja':'Nee'):String(p.default))}.`);const detail=p.option_details?.[optionSelect?.value];if(detail)facts.push(`Wanneer: ${detail.when} Gedrag: ${detail.behavior} Beperking: ${detail.limitations}`);return facts.join(' ')};
      const info=makeInfo(tooltipText(),p.label||p.name,true);title.appendChild(info.wrap);if(optionSelect)optionSelect.addEventListener('change',()=>info.bubble.textContent=tooltipText());
    }
    const target=p.simple===false?additional:primary;target.appendChild(label);if(p.simple===false)advancedRows.push({parameter:p,label});
  }
  const summary=$('additional-parameters-summary');details.hidden=advancedRows.length===0;
  summary.textContent=advancedRows.length?`Overige parameters (${advancedRows.length})`:'Overige strategieparameters';
}
function currentForm(action='backtest') {
  return {action,symbol:$('symbol').value,timeframe:$('timeframe').value,strategy:$('strategy').value,
    start_date:$('start').value,end_date:$('end').value,parameters:currentParams(),
    starting_balance:Number($('balance').value),fee:Number($('fee').value),account_type:$('account-type').value,
    leverage:Number($('leverage').value),warmup_candles:Number($('warmup').value),
    trials:Number($('trials').value),train_percent:Number($('train-percent').value),objective:$('objective').value,
    optimizer_method:$('optimizer-method').value,seed:Number($('optimizer-seed').value),
    resume_run_id:$('resume-run').value||null,optimizer_space:currentOptimizerSpace()};
}

function currentOptimizerSpace() {
  const strategy=selectedStrategy(); if(!strategy)return [];
  const rows=[...$('optimizer-space-controls').querySelectorAll('[data-optimizer-param]')];
  if(!rows.length)return state.optimizerSpaceByStrategy[strategy.name]||strategy.parameters.map(p=>({name:p.name,enabled:!/position_size|qty|lot/i.test(p.name)}));
  const controls=rows.map(row=>{const item={name:row.dataset.optimizerParam,enabled:row.querySelector('[data-space-enabled]').checked};
    if(!item.enabled)return item;
    if(row.dataset.type==='int'||row.dataset.type==='float'){item.min=row.querySelector('[data-space-min]').value;item.max=row.querySelector('[data-space-max]').value;item.step=row.querySelector('[data-space-step]').value}
    else if(row.dataset.type==='categorical')item.options=[...row.querySelectorAll('[data-space-option]:checked')].map(x=>x.value);
    return item;
  });state.optimizerSpaceByStrategy[strategy.name]=controls;return controls;
}

function renderOptimizerSpace() {
  const root=$('optimizer-space-controls');if(!root)return;const strategy=selectedStrategy(),params=strategy?.parameters||[];
  const previous=new Map((state.optimizerSpaceByStrategy[strategy?.name]||[]).map(x=>[x.name,x]));root.replaceChildren();
  for(const p of params){const row=document.createElement('div');row.className='optimizer-control-row';row.dataset.optimizerParam=p.name;row.dataset.type=p.type;
    const title=document.createElement('label');title.className='optimizer-enable';const enabled=document.createElement('input');enabled.type='checkbox';enabled.dataset.spaceEnabled='';enabled.checked=previous.has(p.name)?previous.get(p.name).enabled!==false:!/position_size|qty|lot/i.test(p.name);
    const name=document.createElement('span');name.textContent=(window.paramLabel?window.paramLabel(p.name,p,strategy?.name).label:p.label)||p.name;if(/position_size|qty|lot/i.test(p.name))name.title='Standaard uit: positiegrootte optimaliseren zoekt vooral naar meer hefboom en loopt snel tegen het saldo aan.';title.append(enabled,name);row.append(title);
    if(p.type==='int'||p.type==='float')for(const [key,label,initial] of [['min','Start',p.min??0],['max','Stop',p.max??p.min??0],['step','Stap',p.step??(p.type==='int'?1:'')]]){const wrap=document.createElement('label');wrap.className='optimizer-bound';wrap.append(document.createTextNode(label));const input=document.createElement('input');input.type='number';input.dataset[`space${key[0].toUpperCase()}${key.slice(1)}`]='';input.value=previous.get(p.name)?.[key]??initial;input.step=p.type==='int'?'1':'any';if(key!=='step'){input.min=p.min??'';input.max=p.max??''}wrap.append(input);row.append(wrap)}
    else if(p.type==='categorical'){const choices=document.createElement('div');choices.className='optimizer-categories';const allowed=optimizerOptions(p),selected=previous.get(p.name)?.options||allowed.map(String);for(const value of allowed){const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.dataset.spaceOption='';check.value=String(value);check.checked=selected.some(x=>String(x)===String(value));label.append(check,document.createTextNode(p.option_labels?.[String(value)]||String(value)));choices.append(label)}row.append(choices)}
    row.addEventListener('input',()=>{currentOptimizerSpace();renderOptimizerPlan()});row.addEventListener('change',()=>{currentOptimizerSpace();renderOptimizerPlan()});root.append(row);
  }
}

function optimizerCardinality(parameter, method) {
  if(parameter.type==='categorical')return Math.max(0,(parameter.options||[]).length);
  if(!['int','float'].includes(parameter.type))return null;
  const min=Number(parameter.min),max=Number(parameter.max),step=Number(parameter.step);
  if(!Number.isFinite(min)||!Number.isFinite(max)||max<min)return null;
  if(!step)return method==='grid'?2:(parameter.type==='int'?Math.floor(max-min)+1:Infinity);
  const stride=Number.isFinite(step)&&step>0?step:null;
  return stride?Math.floor((max-min)/stride+1e-9)+1:null;
}
function optimizerOptions(parameter) {
  const options=parameter.options||[],allowed=parameter.account_options?.[$('account-type')?.value];
  return Array.isArray(allowed)?options.filter(value=>allowed.some(x=>String(x)===String(value))):options;
}
function renderOptimizerPlan() {
  const root=$('optimizer-plan');if(!root)return;
  const strategy=selectedStrategy(),params=strategy?.parameters||[],method=$('optimizer-method').value;
  const space=new Map(currentOptimizerSpace().map(x=>[x.name,x]));
  const counts=params.map(p=>{const c=space.get(p.name)||{};if(c.enabled===false)return 1;if(p.type==='categorical')return (c.options||optimizerOptions(p)).length;return optimizerCardinality({...p,min:c.min??p.min,max:c.max??p.max,step:c.step??p.step},method)});
  const total=counts.some(n=>n===null)?null:counts.some(n=>n===Infinity)?Infinity:counts.reduce((a,n)=>a*n,1);
  const trials=Number($('trials').value),trainPercent=Number($('train-percent').value),holdoutPercent=100-trainPercent,start=$('start').value,end=$('end').value;
  const methodLabel=$('optimizer-method').selectedOptions[0]?.textContent||method;
  const trainText=`${start} – ${end}; ${trainPercent}% training / ${holdoutPercent}% holdout op candle-volgorde`;
  const totalText=total===Infinity?'Onbegrensd voor continue parameters':total===null?'Niet berekenbaar voor deze parametertypen':String(total);
  const note=method==='grid'?(Number.isFinite(total)&&trials>total?`Grid kan maximaal ${total} combinaties uitvoeren; ${trials} trials aangevraagd.`:`Grid voert de eerste ${trials} van ${totalText} combinaties uit in vaste volgorde.`):`${trials} aangevraagde trials zijn evaluaties; Random/TPE kunnen dezelfde combinatie meer dan één keer trekken.`;
  root.replaceChildren();
  const summary=document.createElement('p');summary.textContent=`${strategy?.name||'Geen strategie'} · ${params.length} parameters · ${trials} trials · ${methodLabel} · doel ${$('objective').value} · seed ${$('optimizer-seed').value} · ${trainText} · combinaties: ${totalText}. Holdout is het einde van de geselecteerde reeks; dit is geen aparte kalenderperiode.`;root.appendChild(summary);
  const list=document.createElement('div');list.className='optimizer-space-list';
  for(const p of params){const row=document.createElement('div'),name=document.createElement('strong'),detail=document.createElement('span');row.className='optimizer-space-item';name.textContent=p.label||p.name;
    const c=space.get(p.name)||{};if(c.enabled===false){detail.textContent=`Vast op huidige waarde: ${currentParams()[p.name]}`;row.append(name,detail);list.appendChild(row);continue;}
    const options=c.options||optimizerOptions(p),excluded=(p.options||[]).length-options.length;
    const range=p.type==='categorical'?`Opties: ${options.map(v=>p.option_labels?.[String(v)]||String(v)).join(', ')}`:`Min ${c.min??p.min??'—'} · max ${c.max??p.max??'—'}`;
    const step=p.type==='categorical'?`${options.length} haalbare keuzes${excluded?` (${excluded} uitgesloten voor ${$('account-type').value})`:''}`:p.type==='int'?(p.step?String(p.step):method==='grid'?'eindpunten in Grid':'1 (default in Random/TPE)'):p.type==='float'?(p.step?String(p.step):method==='grid'?'eindpunten in Grid':'continu, geen stap'):'niet ondersteund';
    detail.textContent=`${range} · stap: ${c.step??p.step??step}${p.unit?` · ${p.unit}`:''}`;row.append(name,detail);list.appendChild(row)}
  root.appendChild(list);
  const foot=document.createElement('p');foot.className='muted';foot.textContent=note;root.appendChild(foot);
}

async function init() {
  await checkRuntimeBuild();
  window.setInterval(checkRuntimeBuild,30000);
  const settingsMode=localStorage.getItem('strategyLabSettingsMode')||'simple';
  document.body.classList.toggle('simple-mode',settingsMode==='simple');
  $('settings-mode').textContent=settingsMode==='simple'?'Geavanceerd':'Eenvoudig';
  document.querySelectorAll('details[data-setting-group]').forEach(group=>{
    const saved=localStorage.getItem(`strategyLabGroup:${group.id}`);
    if(saved!==null)group.open=saved==='open';
    group.addEventListener('toggle',()=>localStorage.setItem(`strategyLabGroup:${group.id}`,group.open?'open':'closed'));
  });
  const h=await api('/api/health'); $('health').textContent=h.source_exists?'Lokale Dukascopy-data gereed':'Dukascopy-data niet gevonden';
  const ds=await api('/api/datasets');
  for(const item of ds.datasets.filter(x=>x.available)){const o=document.createElement('option');o.value=item.symbol;o.textContent=item.symbol;$('symbol').appendChild(o)}
  for(const tf of ds.timeframes){const o=document.createElement('option');o.value=tf;o.textContent=tf;$('timeframe').appendChild(o)}
  const st=await api('/api/strategies');state.strategies=st.strategies;
  st.strategies.forEach(s=>{const o=document.createElement('option');o.value=s.name;o.textContent=s.name+ (s.managed_by_lab?' · Lab':'');$('strategy').appendChild(o)});
  if(st.strategies.some(s=>s.name==='LabBreakout'))$('strategy').value='LabBreakout';
  if(!st.strategies.length) status('Geen Jesse-strategieën gevonden.','error');
  $('strategy').addEventListener('change',()=>{currentOptimizerSpace();buildParams(selectedStrategy()?.parameters||[]);renderOptimizerSpace();renderOptimizerPlan()});
  document.querySelectorAll('#analytics-options input').forEach(box=>box.onchange=()=>{localStorage.setItem('strategyLabAnalyticsSelectionV3',JSON.stringify([...document.querySelectorAll('#analytics-options input:checked')].map(x=>x.value)));renderPerformanceAnalytics()});
  document.querySelectorAll('[data-inspector-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-inspector-tab]').forEach(x=>{const active=x===b;x.classList.toggle('active',active);x.setAttribute('aria-selected',String(active))});document.querySelectorAll('[data-inspector-page]').forEach(x=>x.classList.toggle('hidden',x.dataset.inspectorPage!==b.dataset.inspectorTab))});
  wireMenus();setTheme(localStorage.getItem('strategyLabTheme')||'dark');wireThemeEditor();renderCustomThemeOptions();
  wireForwardPortfolio();
  wireDataMonitor();
  prepareDashboardWidgets();wireLayout();
  document.body.removeAttribute('data-tester-view');
  $('inspector-help').append(makeInfo('Selecteer een candle of een trade om de op dat moment opgeslagen beslischecks en lifecycle-events te bekijken. De replay gebruikt alleen candles en events tot de gekozen tijd.','Entry Inspector').wrap);
  $('ledger-help').append(makeInfo('Het Jesse-resultaat blijft voorlopig. De Bid/Ask-ledger speelt opgeslagen orderintenties opnieuw af op Dukascopy Bid/Ask OHLC, maar herberekent geen willekeurige strategiebeslissingen. Account- en contractinstellingen zijn door de gebruiker opgegeven, niet door een broker bevestigd.','Bid/Ask ledger').wrap);
  $('mfe-help').append(makeInfo('Gunstigste ruwe prijsbeweging vanaf de entry over Dukascopy Bid-candles na de fill en vóór de exitcandle. De entrycandle telt alleen mee bij een marketfill exact op candle-open; pending-fill- en exitcandles zijn uitgesloten. Dit is geen accountvaluta-P&L.','MFE').wrap);
  $('mae-help').append(makeInfo('Ongunstigste ruwe prijsbeweging vanaf de entry over Dukascopy Bid-candles na de fill en vóór de exitcandle. De entrycandle telt alleen mee bij een marketfill exact op candle-open; pending-fill- en exitcandles zijn uitgesloten. Dit is geen accountvaluta-P&L.','MAE').wrap);
  $('chart-focus').onclick=toggleChartFocus;
  $('chart-expand').onclick=toggleChartFocus;document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('chart-focus-mode'))toggleChartFocus();});
  buildParams(selectedStrategy()?.parameters||[]);renderOptimizerSpace();
  for(const id of ['start','end','trials','train-percent','objective','optimizer-method','optimizer-seed'])$(id).addEventListener('input',renderOptimizerPlan);
  $('account-type').addEventListener('change',()=>{currentOptimizerSpace();renderOptimizerSpace();renderOptimizerPlan()});
  for(const id of ['objective','optimizer-method'])$(id).addEventListener('change',renderOptimizerPlan);
  renderOptimizerPlan();
  $('symbol').addEventListener('change',async()=>{await loadMeta();loadChart()}); $('timeframe').addEventListener('change',()=>loadChart());
  for(const id of ['start','end'])$(id).addEventListener('change',()=>{clearTimeout(state.periodTimer);state.periodTimer=setTimeout(loadChart,350)});
  applySettingLabels();
  $('load-chart').onclick=()=>loadChart();
  $('top-run').onclick=()=>execute('backtest'); $('top-optimize').onclick=()=>execute('optimize');
  $('replay-toggle').onclick=toggleReplay; $('replay-next').onclick=()=>stepReplay(1); $('replay-back').onclick=()=>stepReplay(-1);
  const savedReplaySpeed=Number(localStorage.getItem('strategyLabReplaySpeedV2')||1),savedReplayIndex=Math.max(0,replaySpeeds.indexOf(savedReplaySpeed));$('replay-speed').value=String(savedReplayIndex);updateReplaySpeed();
  $('replay-speed').addEventListener('input',()=>{
    localStorage.setItem('strategyLabReplaySpeedV2',String(updateReplaySpeed()));
    if(state.replaySession?.candles)restartReplayClock(state.replaySession);
  });
  const limitSelect=$('chart-limit'),savedLimit=localStorage.getItem('strategyLabChartLimitV2');
  limitSelect.value=[...limitSelect.options].some(option=>option.value===savedLimit)?savedLimit:'0';
  limitSelect.onchange=()=>{
    localStorage.setItem('strategyLabChartLimitV2',limitSelect.value);
    if(state.replaySession?.candles)queueReplayDraw(state.replaySession);
    else if(!state.replaySession)loadChart();
  };
  $('replay-slider').addEventListener('input',sliderReplay); $('refresh-runs').onclick=loadRuns; $('run-select').onchange=loadSelectedRun;
  $('causal-replay-run').onclick=runCausalReplayAtSelection;
  $('save-preset').onclick=savePreset; $('presets').onchange=loadPreset; $('compare').onclick=compareRuns;
  $('load-source').onclick=loadSource; $('save-strategy').onclick=saveStrategy; $('run-ledger').onclick=runLedger; $('portfolio-analyze').onclick=analyzePortfolio;
  document.querySelectorAll('#decision-filters input').forEach(el=>el.onchange=renderEvents);
  await loadMeta(); await loadChart(); await Promise.all([loadRuns(),loadPresets()]);
  await restoreSession();
  document.querySelector('.workspace').addEventListener('change',queueSaveSession);document.querySelector('.workspace').addEventListener('input',queueSaveSession);
}
const SESSION_KEY='strategyLabSessionV1',SESSION_FIELDS=['symbol','timeframe','start','end','balance','fee','account-type','leverage','warmup','trials','train-percent','objective','optimizer-method','optimizer-seed'];
function queueSaveSession(){clearTimeout(state.saveTimer);state.saveTimer=setTimeout(saveSession,300)}
function saveSession(){try{const v={strategy:$('strategy').value,params:currentParams(),activeRun:state.activeRun||null,fields:{}};for(const id of SESSION_FIELDS)v.fields[id]=$(id)?.value;localStorage.setItem(SESSION_KEY,JSON.stringify(v))}catch{}}
async function restoreSession(){
  let v;try{v=JSON.parse(localStorage.getItem(SESSION_KEY)||'null')}catch{}if(!v)return;
  const setSel=(id,val)=>{const el=$(id);if(el&&val!=null&&(el.tagName!=='SELECT'||[...el.options].some(o=>o.value===String(val))))el.value=val};
  const applyForm=()=>{setSel('strategy',v.strategy);buildParams(selectedStrategy()?.parameters||[],v.params||{});for(const [id,val] of Object.entries(v.fields||{}))setSel(id,val);renderOptimizerSpace();renderOptimizerPlan()};
  if(v.activeRun&&(state.runs||[]).some(r=>r.run_id===v.activeRun)){$('run-select').value=v.activeRun;await loadSelectedRun()}
  const symbolBefore=$('symbol').value;applyForm();if($('symbol').value!==symbolBefore)await loadMeta();applyForm();
  await loadChart();status('Vorige sessie hersteld: instellingen'+(v.activeRun&&state.activeRun===v.activeRun?' en laatste run':'')+'.');
}

const baseLayout={panels:['market-controls','settings','chart','results','dock','optimization-settings'],sizes:{'market-controls':{columns:12,rows:2},settings:{columns:3,rows:9},chart:{columns:6,rows:9},results:{columns:3,rows:9},dock:{columns:12,rows:7},'optimization-settings':{columns:4,rows:9}},positions:{'market-controls':{column:1,row:1},settings:{column:1,row:3},chart:{column:4,row:3},results:{column:10,row:3},dock:{column:1,row:12},'optimization-settings':{column:4,row:3}},hidden:['optimization-settings']};
const MAX_GRID_ROW=40;
const LAYOUT_KEY='strategyLabLayoutV4:';
const layoutPresets={
  default:baseLayout,
  grafiek:{...baseLayout,sizes:{...baseLayout.sizes,chart:{columns:9,rows:11},results:{columns:3,rows:11}},positions:{...baseLayout.positions,chart:{column:1,row:3},results:{column:10,row:3},dock:{column:1,row:14}},hidden:['settings','optimization-settings']},
  optimization:{...baseLayout,sizes:{...baseLayout.sizes,settings:{columns:3,rows:9},'optimization-settings':{columns:5,rows:9},results:{columns:4,rows:9}},positions:{...baseLayout.positions,settings:{column:1,row:3},'optimization-settings':{column:4,row:3},results:{column:9,row:3},dock:{column:1,row:12}},hidden:['chart']}
};
function migrateToolbarHeight(layout){
  const toolbar=layout.sizes?.['market-controls'],position=layout.positions?.['market-controls'];
  if(toolbar?.rows!==1||toolbar.columns!==12||position?.row!==1)return layout;
  toolbar.rows=2;
  for(const [id,slot] of Object.entries(layout.positions)){
    if(id!=='market-controls')slot.row=Math.min(MAX_GRID_ROW,Math.max(3,slot.row+1));
  }
  return layout;
}
function prepareDashboardWidgets(){
  const ws=document.querySelector('.workspace');
  const toolbar=document.querySelector('.toolbar');if(toolbar){toolbar.classList.add('panel','dashboard-widget');toolbar.dataset.panel='market-controls';toolbar.setAttribute('aria-label','Strategie, markt en periode');ws.prepend(toolbar)}
  document.querySelectorAll('[data-setting-page]').forEach(x=>x.classList.remove('hidden'));
  const optimization=$('optimization-controls');if(optimization){const widget=document.createElement('section');widget.className='panel dashboard-widget';widget.dataset.panel='optimization-settings';const heading=document.createElement('div');heading.className='section-head';const h=document.createElement('h2');h.textContent='Optimalisatie-instellingen';heading.append(h);const run=document.createElement('button');run.type='button';run.textContent='Optimalisatie starten';run.onclick=()=>execute('optimize');heading.append(run);widget.append(heading,optimization);optimization.classList.remove('advanced-setting');optimization.open=true;ws.append(widget)}
  document.querySelectorAll('[data-dock-tab]').forEach(b=>b.onclick=()=>showDockTab(b.dataset.dockTab));
}
function showDockTab(name){
  if(!document.querySelector(`[data-dock-page="${name}"]`))return;
  document.querySelectorAll('[data-dock-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.dockTab===name)));
  document.querySelectorAll('[data-dock-page]').forEach(p=>p.hidden=p.dataset.dockPage!==name);
  if(name==='analytics')requestAnimationFrame(()=>document.querySelectorAll('.analytics-plot').forEach(x=>{try{labResize(x)}catch{}}));
}
const widgetNames={'market-controls':'Strategie, markt en periode',settings:'Instellingen',chart:'Grafiek',results:'Resultaten',dock:'Trades, beslislogboek en journaal','optimization-settings':'Optimalisatie-instellingen'};
function wireLayout(){
  const ws=document.querySelector('.workspace'),windowMenu=$('window-menu');
  const resizeCharts=()=>requestAnimationFrame(()=>{try{labResize('chart');labResize('equity-chart');document.querySelectorAll('.analytics-plot').forEach(x=>labResize(x))}catch{}});
  if(window.ResizeObserver)new ResizeObserver(resizeCharts).observe(ws);
  window.addEventListener('resize',resizeCharts);
  $('reset-params').onclick=()=>buildParams(selectedStrategy()?.parameters||[]);
  $('settings-mode').onclick=()=>{document.body.classList.toggle('simple-mode');const mode=document.body.classList.contains('simple-mode')?'simple':'advanced';localStorage.setItem('strategyLabSettingsMode',mode);$('settings-mode').textContent=mode==='simple'?'Geavanceerd':'Eenvoudig';};
  for(const [id,title] of Object.entries(widgetNames)){
    const panel=ws.querySelector(`[data-panel="${id}"]`);if(!panel)continue;
    const label=document.createElement('label'),box=document.createElement('input');label.className='window-widget-toggle';box.type='checkbox';box.dataset.widgetToggle=id;box.checked=true;box.onchange=()=>{panel.classList.toggle('panel-hidden',!box.checked);state.layout=readLayout();if(box.checked)placeWithoutOverlap(state.layout,id);applyLayout(state.layout);persistLayout()};label.append(box,document.createTextNode(title));windowMenu.append(label);
    const drag=document.createElement('button');
    drag.type='button';
    drag.className='panel-drag-handle';
    drag.innerHTML='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3h2v2H5zm4 0h2v2H9zM5 7h2v2H5zm4 0h2v2H9zm-4 4h2v2H5zm4 0h2v2H9z"/></svg>';
    drag.setAttribute('aria-label',`Verplaats paneel ${title}`);
    drag.title=`Sleep om ${title} te verplaatsen`;
    panel.prepend(drag);
    const grip=document.createElement('button');grip.type='button';grip.className='widget-resize-handle';grip.setAttribute('aria-label',`Formaat van ${title} aanpassen`);grip.title='Sleep de hoekgreep om de widget breder, smaller, hoger of lager te maken';panel.append(grip);
    const reorder=document.createElement('span');reorder.className='layout-reorder';reorder.innerHTML=`<span>${title}</span><button type="button" class="secondary" aria-label="${title} naar links">←</button><button type="button" class="secondary" aria-label="${title} naar rechts">→</button>`;const buttons=reorder.querySelectorAll('button');buttons[0].onclick=()=>movePanel(id,-1);buttons[1].onclick=()=>movePanel(id,1);$('layout-editor').append(reorder);
  }
  $('menu-edit-layout').onclick=()=>{state.layoutBefore=readLayout();state.layoutBeforePreset=localStorage.getItem('strategyLabLayoutPresetV1')||'default';document.body.classList.add('editing');$('layout-editor').classList.remove('hidden');$('menu-edit-layout').closest('.menu-item').classList.remove('open')};
  $('cancel-layout').onclick=()=>{document.body.classList.remove('editing');$('layout-editor').classList.add('hidden');applyLayout(state.layoutBefore||layoutPresets.default);localStorage.setItem('strategyLabLayoutPresetV1',state.layoutBeforePreset||'default')};
  $('restore-layout').onclick=()=>activateLayoutProfile(layoutPresets[localStorage.getItem('strategyLabLayoutPresetV1')]?localStorage.getItem('strategyLabLayoutPresetV1'):'default',{reset:true});
  $('save-layout').onclick=async()=>{try{const layout=readLayout(),name=$('layout-name').value.trim();await api('/api/layouts',{method:'POST',body:JSON.stringify({name,layout})});localStorage.setItem(LAYOUT_KEY+name,JSON.stringify(layout));localStorage.setItem('strategyLabLayoutPresetV1',name);await refreshProfiles(name);status('Layout opgeslagen.');document.body.classList.remove('editing');$('layout-editor').classList.add('hidden')}catch(e){status(e.message,'error')}};
  $('layout-profile').onchange=()=>activateLayoutProfile($('layout-profile').value);
  windowMenu._sync=()=>windowMenu.querySelectorAll('[data-widget-toggle]').forEach(box=>{box.checked=!ws.querySelector(`[data-panel="${box.dataset.widgetToggle}"]`)?.classList.contains('panel-hidden')});
  $('restore-layout').addEventListener('click',syncWindowMenu);
  let dragState=null;
  const dropTarget=document.createElement('div');
  dropTarget.className='layout-drop-target';
  dropTarget.setAttribute('aria-hidden','true');
  dropTarget.hidden=true;
  document.body.append(dropTarget);
  const clearDrag=(commit=false)=>{
    if(!dragState)return;
    const {active,original,preview,panel,panels,minHeight,handle,pointerId}=dragState;
    dragState=null;
    if(handle.hasPointerCapture(pointerId))handle.releasePointerCapture(pointerId);
    if(!active)return;
    for(const item of panels)item.style.order='';
    panel.classList.remove('dragging-panel');
    panel.style.translate='';
    ws.style.minHeight=minHeight;
    dropTarget.hidden=true;
    if(commit&&preview){applyLayout(preview);persistLayout()}
    else setWidgetSpans(original);
    resizeCharts();
  };
  const previewDrag=(x,y)=>{
    const drag=dragState;
    const left=x-drag.offsetX,top=y-drag.offsetY;
    if(drag.stacked){
      const next=drag.stackTargets.find(target=>y+window.scrollY<target.midpoint);
      const key=next?.id||'end';
      if(drag.previewKey!==key){
        const order=drag.original.panels.filter(id=>id!==drag.id);
        order.splice(next?order.indexOf(next.id):order.length,0,drag.id);
        for(const item of drag.panels)item.style.order=order.indexOf(item.dataset.panel);
        drag.preview={...drag.original,panels:order};
        drag.previewKey=key;
      }
    }else{
      const point=gridPointAt(left,top,ws);
      const column=Math.min(13-drag.original.sizes[drag.id].columns,point.column);
      const key=`${column}:${point.row}`;
      if(drag.previewKey!==key){
        const positions=Object.fromEntries(Object.entries(drag.original.positions)
          .map(([id,position])=>[id,{...position}]));
        positions[drag.id]={column,row:point.row};
        drag.preview={...drag.original,positions};
        placeWithoutOverlap(drag.preview,drag.id);
        compactLayoutRows(drag.preview);
        setWidgetSpans(drag.preview);
        drag.previewKey=key;
        resizeCharts();
      }
    }
    const rect=drag.panel.getBoundingClientRect();
    const slotLeft=rect.left-drag.translateX,slotTop=rect.top-drag.translateY;
    Object.assign(dropTarget.style,{
      left:`${slotLeft}px`,top:`${slotTop}px`,width:`${rect.width}px`,height:`${rect.height}px`
    });
    dropTarget.hidden=false;
    drag.translateX=left-slotLeft;
    drag.translateY=top-slotTop;
    drag.panel.style.translate=`${drag.translateX}px ${drag.translateY}px`;
  };
  window.addEventListener('scroll',()=>{if(dragState?.active)previewDrag(dragState.x,dragState.y)},{passive:true});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&dragState)clearDrag()});
  ws.querySelectorAll('.panel-drag-handle').forEach(handle=>{
    handle.addEventListener('pointerdown',e=>{
      if(e.button!==0||dragState)return;
      const panel=handle.closest('[data-panel]');
      const rect=panel.getBoundingClientRect();
      const panels=[...ws.querySelectorAll(':scope > [data-panel]:not(.panel-hidden)')];
      const stacked=window.matchMedia('(max-width:1100px)').matches;
      const stackTargets=stacked?panels.filter(item=>item!==panel).map(item=>{
        const bounds=item.getBoundingClientRect();
        return {id:item.dataset.panel,midpoint:window.scrollY+bounds.top+bounds.height/2};
      }):[];
      dragState={id:panel.dataset.panel,panel,handle,pointerId:e.pointerId,x:e.clientX,y:e.clientY,active:false,
        offsetX:e.clientX-rect.left,offsetY:e.clientY-rect.top,translateX:0,translateY:0,
        original:readLayout(),panels,stacked,stackTargets,minHeight:ws.style.minHeight};
      handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener('pointermove',e=>{
      if(dragState?.handle!==handle||!handle.hasPointerCapture(e.pointerId))return;
      const drag=dragState;
      if(!drag.active){
        if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<8)return;
        drag.active=true;
        ws.style.minHeight=`${ws.offsetHeight}px`;
        drag.panel.classList.add('dragging-panel');
      }
      drag.x=e.clientX;
      drag.y=e.clientY;
      previewDrag(drag.x,drag.y);
    });
    handle.addEventListener('pointerup',e=>{
      if(dragState?.handle!==handle||!handle.hasPointerCapture(e.pointerId))return;
      if(dragState.active)previewDrag(e.clientX,e.clientY);
      clearDrag(true);
    });
    const cancel=e=>{if(dragState?.handle===handle&&dragState.pointerId===e.pointerId)clearDrag()};
    handle.addEventListener('pointercancel',cancel);
    handle.addEventListener('lostpointercapture',cancel);
  });
  ws.querySelectorAll('.widget-resize-handle').forEach(grip=>{
    let start=null;grip.addEventListener('pointerdown',e=>{e.preventDefault();const panel=grip.closest('[data-panel]'),layout=readLayout(),size=layout.sizes[panel.dataset.panel]||{columns:1,rows:3};start={id:panel.dataset.panel,x:e.clientX,y:e.clientY,columns:size.columns,rows:size.rows};grip.setPointerCapture(e.pointerId)});
    grip.addEventListener('pointermove',e=>{if(!start||!grip.hasPointerCapture(e.pointerId))return;const columns=Math.max(1,Math.round(getComputedStyle(ws).gridTemplateColumns.split(/\s+/).length)),gap=parseFloat(getComputedStyle(ws).columnGap)||0,unit=(ws.clientWidth-gap*(columns-1))/columns,layout=readLayout(),position=layout.positions[start.id]||{column:1,row:1},width=Math.max(1,Math.min(columns-position.column+1,start.columns+Math.round((e.clientX-start.x)/unit)));layout.sizes[start.id]={columns:width,rows:Math.max(1,Math.min(16,start.rows+Math.round((e.clientY-start.y)/64)))};state.layout=layout;setWidgetSpans(layout);resizeCharts()});
    grip.addEventListener('pointerup',e=>{if(!start||!grip.hasPointerCapture(e.pointerId))return;start=null;state.layout=readLayout();placeWithoutOverlap(state.layout);applyLayout(state.layout);persistLayout()});
  });
  const activeProfile=localStorage.getItem('strategyLabLayoutPresetV1')||'default';
  const saved=JSON.parse(localStorage.getItem(LAYOUT_KEY+activeProfile)||'null');
  applyLayout(saved||layoutPresets[activeProfile]||layoutPresets.default);
  localStorage.setItem('strategyLabLayoutPresetV1',activeProfile);syncWindowMenu();refreshProfiles(activeProfile);
  state.layoutReady=true;if(state.pendingFocus){const f=state.pendingFocus;state.pendingFocus=null;window.labTester.focus(...f)}
}
function syncWindowMenu(){document.querySelector('#window-menu')?._sync?.()}
function readLayout(){const sizes={},positions={};for(const[id,size]of Object.entries(baseLayout.sizes)){const el=document.querySelector(`[data-panel="${id}"]`);if(!el)continue;const col=Number(el.style.gridColumn.match(/span\s+(\d+)/)?.[1]),row=Number(el.style.gridRow.match(/span\s+(\d+)/)?.[1]);sizes[id]={columns:Math.max(1,col||size.columns),rows:Math.max(1,row||size.rows)};positions[id]={column:Number(el.style.gridColumnStart)||baseLayout.positions[id]?.column||1,row:Math.max(1,Math.min(MAX_GRID_ROW,Number(el.style.gridRowStart)||baseLayout.positions[id]?.row||1))}}return{panels:[...document.querySelectorAll('.workspace [data-panel]')].map(x=>x.dataset.panel),sizes,positions,hidden:[...document.querySelectorAll('.workspace [data-panel].panel-hidden')].map(x=>x.dataset.panel)}}
function setWidgetSpans(layout){layout.positions={...baseLayout.positions,...(layout.positions||{})};for(const[id,size]of Object.entries(layout.sizes||baseLayout.sizes)){const el=document.querySelector(`[data-panel="${id}"]`);if(!el)continue;const columns=Math.max(1,Math.min(12,Number(size.columns)||1)),rows=Math.max(1,Math.min(16,Number(size.rows)||3)),position=layout.positions[id]||{column:1,row:1};el.style.gridColumn=`${Math.min(13-columns,Math.max(1,position.column||1))} / span ${columns}`;el.style.gridRow=`${Math.max(1,Math.min(MAX_GRID_ROW,position.row||1))} / span ${rows}`}}
function persistLayout(){const layout=readLayout();localStorage.setItem(LAYOUT_KEY+(localStorage.getItem('strategyLabLayoutPresetV1')||'default'),JSON.stringify(layout));return layout}
async function activateLayoutProfile(key,{reset=false,scroll=true}={}){if(layoutPresets[key]){const own=!reset&&JSON.parse(localStorage.getItem(LAYOUT_KEY+key)||'null');applyLayout(own||layoutPresets[key]);localStorage.setItem('strategyLabLayoutPresetV1',key);persistLayout();if(scroll)window.scrollTo({top:0,behavior:'smooth'});return}try{const data=await api('/api/layouts');if(data.layouts?.[key]){applyLayout(data.layouts[key]);localStorage.setItem('strategyLabLayoutPresetV1',key);persistLayout();window.scrollTo({top:0,behavior:'smooth'});return}}catch{}$('layout-profile').value='default';await activateLayoutProfile('default')}
function movePanel(id,direction){const layout=readLayout(),size=layout.sizes[id],position=layout.positions[id]||{column:1,row:1};layout.positions[id]={...position,column:Math.max(1,Math.min(13-size.columns,position.column+direction))};placeWithoutOverlap(layout,id);applyLayout(layout);persistLayout()}
function applyLayout(layout){const ws=document.querySelector('.workspace');state.layout={...baseLayout,...layout,panels:[...(layout.panels||[]),...baseLayout.panels.filter(x=>!(layout.panels||[]).includes(x))],sizes:{...baseLayout.sizes,...(layout.sizes||{})},positions:{...baseLayout.positions,...(layout.positions||{})}};compactLayoutRows(state.layout);for(const id of state.layout.panels){const el=ws.querySelector(`[data-panel="${id}"]`);if(el)ws.append(el)}for(const el of ws.querySelectorAll('[data-panel]'))el.classList.toggle('panel-hidden',(state.layout.hidden||[]).includes(el.dataset.panel));setWidgetSpans(state.layout);reflowPanels();syncWindowMenu();setTimeout(()=>{try{labResize('chart');labResize('equity-chart')}catch{}},80)}
function compactLayoutRows(layout){
  const visible=layout.panels.filter(id=>!layout.hidden.includes(id));
  const occupied=new Set();
  for(const id of visible){
    const start=Math.max(1,Math.min(MAX_GRID_ROW,layout.positions[id].row));
    for(let row=start;row<start+layout.sizes[id].rows;row++)occupied.add(row);
  }
  const compactRows=new Map([...occupied].sort((a,b)=>a-b).map((row,index)=>[row,index+1]));
  for(const id of visible){
    const position=layout.positions[id];
    const start=Math.max(1,Math.min(MAX_GRID_ROW,position.row));
    layout.positions[id]={...position,row:compactRows.get(start)};
  }
}
function gridPointAt(x,y,grid){const rect=grid.getBoundingClientRect(),columns=Math.max(1,Math.round(getComputedStyle(grid).gridTemplateColumns.split(/\s+/).length)),columnGap=parseFloat(getComputedStyle(grid).columnGap)||0,rowGap=parseFloat(getComputedStyle(grid).rowGap)||0,trackWidth=(grid.clientWidth-columnGap*(columns-1))/columns,trackHeight=parseFloat(getComputedStyle(grid).gridAutoRows)||64;return{column:Math.max(1,Math.min(columns,Math.round((x-rect.left+grid.scrollLeft)/(trackWidth+columnGap))+1)),row:Math.max(1,Math.min(MAX_GRID_ROW,Math.round((y-rect.top+grid.scrollTop)/(trackHeight+rowGap))+1))}}
function placeWithoutOverlap(layout,anchorId){const visible=layout.panels.filter(id=>!layout.hidden.includes(id)),placed=anchorId&&visible.includes(anchorId)?[anchorId]:[];for(const id of visible){if(id===anchorId)continue;const size=layout.sizes[id],pos=layout.positions[id]||{column:1,row:1};pos.column=Math.max(1,Math.min(13-size.columns,pos.column));const preferred=Math.max(1,Math.min(MAX_GRID_ROW,pos.row));let row=preferred,found=false;for(;row<=MAX_GRID_ROW;row++){pos.row=row;if(!placed.some(other=>isPanelOverlap(id,other,layout))){found=true;break}}if(!found)for(row=1;row<preferred;row++){pos.row=row;if(!placed.some(other=>isPanelOverlap(id,other,layout))){found=true;break}}if(!found)pos.row=MAX_GRID_ROW;layout.positions[id]=pos;placed.push(id)}}
function isPanelOverlap(a,b,layout){const x=layout.positions[a],y=layout.positions[b],u=layout.sizes[a],v=layout.sizes[b];return x.column<y.column+v.columns&&x.column+u.columns>y.column&&x.row<y.row+v.rows&&x.row+u.rows>y.row}
function reflowPanels(){const ws=document.querySelector('.workspace');ws.classList.remove('responsive-stack');ws.style.display='';ws.style.flexDirection='';ws.style.gridTemplateColumns='';ws.style.gridTemplateAreas='';ws.querySelectorAll('[data-panel]').forEach(x=>{x.style.order=''});requestAnimationFrame(()=>{try{labResize('chart');labResize('equity-chart')}catch{}})}
async function refreshProfiles(select){try{const data=await api('/api/layouts'),s=$('layout-profile');const custom=Object.keys(data.layouts||{});s.innerHTML='<option value="default">Standaard</option><option value="grafiek">Grafiek</option><option value="optimization">Optimalisatie</option>'+custom.map(n=>`<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');if(select&&[...s.options].some(o=>o.value===select))s.value=select;else s.value='default';}catch{}}
function toggleChartFocus(){const active=document.body.classList.toggle('chart-focus-mode');$('chart-expand').textContent=active?'Chartfocus sluiten':'⛶';$('chart-expand').setAttribute('aria-label',active?'Chartfocus sluiten':'Chart vergroten');setTimeout(()=>{try{labResize('chart')}catch{}},50);}
function hexRgb(hex){const n=parseInt(hex.replace('#',''),16);return[(n>>16)&255,(n>>8)&255,n&255]}
function mixHex(hex,white){const rgb=hexRgb(hex).map(v=>Math.round(v*(1-white)+255*white));return '#'+rgb.map(v=>v.toString(16).padStart(2,'0')).join('')}
function buildThemePalette(color){const vars={'--theme-accent':color,'--theme-bg':mixHex(color,.965),'--theme-panel':mixHex(color,.992),'--theme-edge':mixHex(color,.76),'--theme-control':mixHex(color,.975),'--theme-plot':mixHex(color,.94)};return{vars,panel:vars['--theme-panel'],plot:vars['--theme-plot'],edge:vars['--theme-edge']}}
function setTheme(name){if(name==='light')name='light-green';const themes=JSON.parse(localStorage.getItem('strategyLabCustomThemes')||'[]'),custom=themes.find(x=>`custom:${x.id}`===name);if(!['dark','light-green'].includes(name)&&!custom)name='dark';document.body.dataset.theme=custom?'light-custom':name;localStorage.setItem('strategyLabTheme',custom?`custom:${custom.id}`:name);const c=buildThemePalette(custom?.color||'#548b69');for(const[k,v]of Object.entries(c.vars))document.body.style.setProperty(k,v);const p=name==='dark'?{paper:'#131f2e',plot:'#0c1623',text:'#c5d5e5',grid:'#26394d'}:{paper:c.panel,plot:c.plot,text:'#334155',grid:c.edge};for(const id of ['chart','equity-chart',...document.querySelectorAll('.analytics-plot')].map(x=>typeof x==='string'?x:x.id))try{if($(id).data)Plotly.relayout(id,{'paper_bgcolor':p.paper,'plot_bgcolor':p.plot,'font.color':name==='dark'?'#99afc3':'#526779','xaxis.gridcolor':p.grid,'yaxis.gridcolor':p.grid})}catch{};document.querySelectorAll('button[data-theme]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.theme===name)));}
function renderCustomThemeOptions(){const root=$('custom-theme-options');if(!root)return;root.replaceChildren();for(const theme of JSON.parse(localStorage.getItem('strategyLabCustomThemes')||'[]')){const b=document.createElement('button');b.type='button';b.dataset.customTheme=theme.id;b.textContent=theme.name;b.onclick=()=>{setTheme(`custom:${theme.id}`);document.querySelector('.main-menu .menu-item.open')?.classList.remove('open')};root.append(b)}}
function wireThemeEditor(){const dialog=$('theme-dialog');if(!dialog)return;const color=$('theme-color'),hex=$('theme-hex'),preview=$('theme-preview');const update=value=>{if(!/^#[0-9a-fA-F]{6}$/.test(value))return;color.value=value;hex.value=value;const p=buildThemePalette(value);preview.style.background=p.vars['--theme-bg'];preview.style.borderColor=p.vars['--theme-edge'];preview.style.color='#334155';preview.style.setProperty('--theme-accent',value)};color.oninput=()=>update(color.value);hex.oninput=()=>update(hex.value);$('theme-customize').onclick=()=>{document.querySelector('.main-menu .menu-item.open')?.classList.remove('open');dialog.showModal()};$('theme-save').onclick=()=>{if(!/^#[0-9a-fA-F]{6}$/.test(hex.value))return;const themes=JSON.parse(localStorage.getItem('strategyLabCustomThemes')||'[]'),id=crypto.randomUUID(),theme={id,name:$('theme-name').value.trim()||'Mijn thema',color:hex.value};themes.push(theme);localStorage.setItem('strategyLabCustomThemes',JSON.stringify(themes));renderCustomThemeOptions();setTheme(`custom:${id}`);dialog.close()};update(color.value)}
function wireMenus(){
  const topItems=[...document.querySelectorAll('.main-menu > .menu-item')],subItems=[...document.querySelectorAll('.menu-subitem')];
  const closeMenus=()=>{topItems.forEach(x=>{x.classList.remove('open');x.querySelector('.menu-trigger')?.setAttribute('aria-expanded','false')});subItems.forEach(x=>{x.classList.remove('open');x.querySelector('.menu-subtrigger')?.setAttribute('aria-expanded','false')})};
  document.querySelectorAll('.main-menu > .menu-item > .menu-trigger').forEach(trigger=>{const item=trigger.closest('.menu-item');trigger.addEventListener('click',e=>{e.stopPropagation();closeMenus();item.classList.add('open');trigger.setAttribute('aria-expanded','true')});item.addEventListener('pointerenter',()=>{topItems.filter(x=>x!==item).forEach(x=>{x.classList.remove('open');x.querySelector('.menu-trigger')?.setAttribute('aria-expanded','false')});item.classList.add('open');trigger.setAttribute('aria-expanded','true')});item.addEventListener('pointerleave',()=>{item.classList.remove('open');trigger.setAttribute('aria-expanded','false');item.querySelectorAll('.menu-subitem').forEach(x=>{x.classList.remove('open');x.querySelector('.menu-subtrigger')?.setAttribute('aria-expanded','false')})})});
  document.addEventListener('click',e=>{if(!e.target.closest('.main-menu'))closeMenus()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeMenus()});
  document.querySelectorAll('[data-theme]').forEach(b=>b.addEventListener('click',()=>{setTheme(b.dataset.theme);closeMenus()}));
  document.querySelectorAll('.menu-subtrigger').forEach(b=>{const item=b.closest('.menu-subitem');b.addEventListener('click',e=>{e.stopPropagation();item.classList.add('open');b.setAttribute('aria-expanded','true')});item.addEventListener('pointerenter',()=>{item.classList.add('open');b.setAttribute('aria-expanded','true')});item.addEventListener('pointerleave',()=>{item.classList.remove('open');b.setAttribute('aria-expanded','false');item.querySelectorAll('.menu-subitem').forEach(x=>{x.classList.remove('open');x.querySelector('.menu-subtrigger')?.setAttribute('aria-expanded','false')})})});
  document.querySelectorAll('[data-menu-action]').forEach(b=>b.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();const action=b.dataset.menuAction;closeMenus();execute(action)}));
}
function activateResultsTab(name){if(name&&name!=='summary')showDockTab(name)}
function activateTesterTab(){document.body.removeAttribute('data-tester-view');reflowPanels()}

async function loadMeta(){
  try {
    const {manifest}=await api(`/api/data/meta?symbol=${encodeURIComponent($('symbol').value)}`);state.manifest=manifest;
    const first=new Date(manifest.first_utc),last=new Date(manifest.last_utc);
    const fmt=d=>d.toISOString().slice(0,10);
    $('start').min=fmt(first);$('start').max=fmt(last);$('end').min=fmt(first);$('end').max=fmt(last);
    if(!$('start').value||$('start').value<fmt(first)||$('start').value>fmt(last)) $('start').value=fmt(new Date(last.getTime()-6*86400000));
    if(!$('end').value||$('end').value<fmt(first)||$('end').value>fmt(last)) $('end').value=fmt(last);
    const range=$('data-range');if(range){range.textContent=`Data: ${fmt(first)} t/m ${fmt(last)}`;range.dataset.tip='Beschikbare lokale Dukascopy-data voor deze markt (UTC). De periode moet hierbinnen vallen.'}
  } catch(e){const health=$('health');if(health)health.textContent=e.message;}
}

async function loadChart() {
  stopReplay();
  const request=state.chartRequest=(state.chartRequest||0)+1;
  try {
    const limit=Number($('chart-limit')?.value??0);
    const query=new URLSearchParams({symbol:$('symbol').value,timeframe:$('timeframe').value,start:$('start').value,end:$('end').value});
    const data=await api(`/api/candles?${query}&limit=${limit}&tail=true`);
    if(request!==state.chartRequest||state.replaySession)return;
    state.chartQuery=query;state.chartAvailableRows=data.available_rows;
    state.candles=data.candles;state.chartStart=Date.parse($('start').value+'T00:00:00Z');state.chartEnd=Date.parse($('end').value+'T23:59:59Z');
    renderChart(data.candles,undefined,null,{rolling:data.candles.length<data.available_rows,
      padding:candleHalfWidths[query.get('timeframe')]||30000});
  } catch(e){if(request===state.chartRequest&&!state.replaySession)$('chart').textContent=`Chart laden mislukt: ${e.message}`;}
}

function renderChart(rows, events=state.runData?.events||[], replayCursorMs=null, replayWindow=null) {
  state.visibleCandles=rows;
  if(!$('chart').offsetParent&&$('chart').data){state.chartStale=true;return;}
  if(!rows?.length){$('chart').innerHTML='Geen candles in deze periode';return;}
  const x=rows.map(c=>c.timestamp),ema=replayWindow?.ema??[];
  let average=null;const period=Math.max(2,Number(currentParams().ema_period)||20),alpha=2/(period+1);
  if(!replayWindow?.ema)rows.forEach((c,i)=>{average=i===0?c.bid_close:alpha*c.bid_close+(1-alpha)*average;ema.push(average)});
  const traces=[{type:'candlestick',x,open:rows.map(c=>c.bid_open),high:rows.map(c=>c.bid_high),low:rows.map(c=>c.bid_low),close:rows.map(c=>c.bid_close),name:'Dukascopy Bid',increasing:{line:{color:'#42cf8f'}},decreasing:{line:{color:'#ff7878'}},customdata:rows.map(c=>c.timestamp_ms),hovertemplate:'%{x}<br>O %{open}<br>H %{high}<br>L %{low}<br>C %{close}<extra>Bid</extra>'},
    {type:'scatter',mode:'lines',x,y:ema,name:`EMA ${period}`,line:{color:'#49b5ff',width:1.2},hoverinfo:'skip'}];
  const chartTime=value=>{const numeric=Number(value);return Number.isFinite(numeric)&&numeric>0?numeric:Date.parse(value)};
  const lastVisibleCandle=chartTime(rows.at(-1).timestamp_ms??rows.at(-1).timestamp);
  const visibleEnd=state.replay&&replayCursorMs!==null&&replayCursorMs!==undefined&&Number.isFinite(Number(replayCursorMs))?Number(replayCursorMs):lastVisibleCandle;
  const visibleStart=replayWindow?.rolling?rows[0].timestamp_ms:-Infinity;
  const sourceTrades=state.runData?.trades||[],entryPoints={long:[],short:[]},exitPoints={tp:[],sl:[],other:[]};
  const closeEvents=new Map((events||[]).filter(e=>(e.event_type||e.event)==='POSITION_CLOSE'&&e.trade_id&&(!state.replay||e.timestamp_ms==null||Number(e.timestamp_ms)<=visibleEnd)).map(e=>[String(e.trade_id),e]));
  for(const [i,t] of sourceTrades.entries()){
    const opened=chartTime(t.opened_at??t.entry_timestamp_ms),closed=chartTime(t.closed_at??t.closed_at_ms),entry=Number(t.entry_price),exit=Number(t.exit_price),side=String(t.type||t.side||'').toLowerCase(),id=String(t.id||t.trade_id||i+1);
    if(state.replay&&opened>visibleEnd)continue;
    if(!opened||!Number.isFinite(entry)||Math.max(opened,closed||0)<visibleStart)continue;
    const entryName=side.includes('short')||side==='sell'?'short':'long';
    const closeEvent=closeEvents.get(id);
    const closeOrder=(t.orders||[]).find(o=>o.reduce_only===true&&(closed&&Number(o.executed_at||0)===closed))||(t.orders||[]).find(o=>o.reduce_only===true&&(closeEvent?.order_id&&String(o.id)===String(closeEvent.order_id)))||(t.orders||[]).find(o=>o.reduce_only===true);
    const reason=String(closeEvent?.exit_reason||closeEvent?.reason||t.exit_reason||closeOrder?.submitted_via||'').toLowerCase(),exitKind=reason.includes('take-profit')||reason.includes('take_profit')||reason==='tp'?'tp':reason.includes('stop-loss')||reason.includes('stop_loss')||reason==='sl'?'sl':'other';
    if(opened>=visibleStart)entryPoints[entryName].push({x:new Date(opened).toISOString(),y:entry,customdata:opened,text:`${entryName==='long'?'Bullish':'Bearish'} entry · trade ${id}`});
    if(Number.isFinite(closed)&&closed>0&&Number.isFinite(exit)&&(!state.replay||closed<=visibleEnd)){
      const entryTime=new Date(opened).toISOString(),exitTime=new Date(closed).toISOString(),exitLabel=exitKind==='tp'?'TP geraakt':exitKind==='sl'?'SL geraakt':'Trade gesloten';
      const lineColor=`hsl(${(i*137.508)%360}, 72%, 45%)`;
      if(opened>=visibleStart)traces.push({type:'scatter',mode:'lines',x:[entryTime,exitTime],y:[entry,exit],customdata:[[id,'Entry',entryTime],[id,exitLabel,exitTime]],name:`Trade ${id}`,legendgroup:`trade-${id}`,showlegend:false,hovertemplate:`Trade %{customdata[0]} · ${entryName==='long'?'bullish':'bearish'}<br>%{customdata[1]} · %{customdata[2]} · %{y:.5f}<extra></extra>`,line:{color:lineColor,width:3,dash:'dot'}});
      exitPoints[exitKind].push({x:new Date(closed).toISOString(),y:exit,customdata:closed,text:`${exitKind==='tp'?'TP geraakt':exitKind==='sl'?'SL geraakt':'Trade gesloten'} · trade ${id}`});
    }
  }
  for(const [side,points,color,symbol,label] of [['long',entryPoints.long,'#18a66a','triangle-up','Bullish entry'],['short',entryPoints.short,'#c75267','triangle-down','Bearish entry']])if(points.length)traces.push({type:'scatter',mode:'markers',x:points.map(p=>p.x),y:points.map(p=>p.y),customdata:points.map(p=>p.customdata),text:points.map(p=>p.text),name:label,hovertemplate:'%{text}<br>%{x}<br>Prijs %{y}<extra></extra>',marker:{symbol,size:15,color,line:{color:'#ffffff',width:1}}});
  for(const [kind,points,color,symbol,label] of [['tp',exitPoints.tp,'#20a56b','square','TP geraakt'],['sl',exitPoints.sl,'#d94f61','x','SL geraakt'],['other',exitPoints.other,'#738398','diamond','Exit (type onbekend)']])if(points.length)traces.push({type:'scatter',mode:'markers',x:points.map(p=>p.x),y:points.map(p=>p.y),customdata:points.map(p=>p.customdata),text:points.map(p=>p.text),name:label,hovertemplate:'%{text}<br>%{x}<br>Prijs %{y}<extra></extra>',marker:{symbol,size:13,color,line:{color:'#ffffff',width:1}}});
  const light=document.body.dataset.theme?.startsWith('light');
  const style=light?getComputedStyle(document.body):null;
  const grid=style?.getPropertyValue('--theme-edge').trim()||'#26394d';
  const replayRange=replayWindow?.rolling?{range:[new Date(visibleStart-replayWindow.padding).toISOString(),new Date(visibleEnd+replayWindow.padding).toISOString()]}:{};
  const draw=Plotly.react('chart',traces,{paper_bgcolor:style?.getPropertyValue('--theme-panel').trim()||'#131f2e',plot_bgcolor:style?.getPropertyValue('--theme-plot').trim()||'#0c1623',font:{color:light?'#526779':'#c5d5e5',size:11},margin:{l:55,r:20,t:22,b:35},xaxis:{rangeslider:{visible:false},gridcolor:grid,showspikes:true,spikemode:'across',...replayRange},yaxis:{gridcolor:grid,fixedrange:false},legend:{orientation:'h',y:1.08},dragmode:'pan',shapes:selectedTradeShapes(),annotations:selectedTradeAnnotations()}, {responsive:true,scrollZoom:true,displaylogo:false,modeBarButtonsToAdd:['drawline','drawrect','eraseshape']});
  const graph=$('chart');graph.removeAllListeners?.('plotly_click');graph.on('plotly_click',ev=>{const point=ev.points[0];const ms=Number(point.customdata);if(Number.isFinite(ms)){inspect(ms);showDockTab('inspector')}});
  if(!state.replay){renderEquity();setTheme(document.body.dataset.theme||'dark')}
  return draw;
}

function renderEquity(){
  const trades=state.runData?.trades||[];$('equity-chart').hidden=!trades.length;if(!trades.length)return;
  const sorted=[...trades].sort((a,b)=>Number(a.closed_at||a.closed_at_ms||0)-Number(b.closed_at||b.closed_at_ms||0));let equity=0;const x=[],y=[];
  for(const t of sorted){equity+=Number(t.PNL??t.pnl??0);x.push(new Date(Number(t.closed_at||t.closed_at_ms)).toISOString());y.push(equity);}
  Plotly.react('equity-chart',[{type:'scatter',mode:'lines',x,y,name:'Cumulatieve Jesse P&L',line:{color:'#6dc7aa',width:1.5}}],{paper_bgcolor:'#131f2e',plot_bgcolor:'#0c1623',font:{color:'#99afc3',size:10},margin:{l:55,r:20,t:6,b:25},xaxis:{gridcolor:'#26394d'},yaxis:{gridcolor:'#26394d',title:'P&L'}} ,{responsive:true,displaylogo:false});
}

async function inspect(ms,session=state.replaySession){
  if(session&&(!session.candles||ms>session.candles[session.index].timestamp_ms))return;
  const request=state.inspectRequest=(state.inspectRequest||0)+1;
  try{const query=session?.query;const q=new URLSearchParams({symbol:query?.get('symbol')??$('symbol').value,timeframe:query?.get('timeframe')??$('timeframe').value,timestamp_ms:String(ms)});if(state.activeRun)q.set('run_id',state.activeRun);
    const data=await api('/api/inspect?'+q);const c=data.candle;
    if(state.inspectRequest!==request||session!==state.replaySession||(session&&ms>session.candles[session.index].timestamp_ms))return;
    $('selected-candle').innerHTML=c?`<b>${c.timestamp_utc}</b><br>Bid O ${nice(c.bid_open,3)} · H ${nice(c.bid_high,3)} · L ${nice(c.bid_low,3)} · C ${nice(c.bid_close,3)}<br>Ask O ${nice(c.ask_open,3)} · spread ${nice(c.spread_open,4)}`:'Geen candle.';
    state.selectedEvents=data.events||[];state.selectedSnapshot=data.state_snapshot||null;$('causal-replay-run').disabled=!state.activeRun||!state.selectedSnapshot?.timestamp_ms;$('causal-replay-result').textContent='';renderEvents();
  }catch(e){if(state.inspectRequest===request&&session===state.replaySession)$('candle-events').textContent=e.message;}
}

async function runCausalReplayAtSelection(){
  const button=$('causal-replay-run'),root=$('causal-replay-result'),runId=state.activeRun,cursor=Number(state.selectedSnapshot?.timestamp_ms);
  if(!runId||!Number.isFinite(cursor)||state.causalReplayBusy)return;
  state.causalReplayBusy=true;button.disabled=true;root.textContent='Strategieprefix wordt opnieuw uitgevoerd op alleen Dukascopy-candles tot deze candle…';
  try{
    const replay=await api(`/api/runs/${runId}/causal-replay`,{method:'POST',body:JSON.stringify({cursor_ms:cursor})});
    const event=replay.decision_event,match=replay.decision_matches_original_run;
    const heading=replay.status==='FULL_CAUSAL_REPLAY'?'Volledige causale replay':replay.status==='RECORDED_EVENT_REPLAY'?'Opgeslagen events':'Replaycontract ontbreekt';
    const status=match===true?' · beslissing komt overeen':match===false?' · beslissing wijkt af':replay.status==='FULL_CAUSAL_REPLAY'?' · geen opgeslagen beslissing om mee te vergelijken':'';
    const details=event?`${event.decision} · ${event.reason}`:`${replay.reason||'Deze strategie registreerde geen candlebeslissing.'}`;
    root.textContent=`${heading}${status}. ${details} · ${replay.prefix_candles||0} prefixcandles · geen toekomstige candles.`;
    root.dataset.replayStatus=replay.status||'UNKNOWN';
  }catch(e){root.textContent=`Causale replay mislukt: ${e.message}`;root.dataset.replayStatus='ERROR';}
  finally{state.causalReplayBusy=false;button.disabled=!state.activeRun||!state.selectedSnapshot?.timestamp_ms;}
}

function renderEvents(){
  const root=$('candle-events');root.innerHTML='';const allowed=new Set([...document.querySelectorAll('#decision-filters input:checked')].map(x=>x.value));
  const events=(state.selectedEvents||[]).filter(e=>!e.decision||allowed.has(e.decision));
  if(!events.length){const why=state.selectedSnapshot?.reason||'Voer een backtest uit om checks en SKIP/FAIL-events op te slaan.';root.innerHTML=`<div class="muted">Geen Inspector-events op deze candle. ${escapeHtml(why)}</div>`;return;}
  for(const e of events){const card=document.createElement('div');card.className=`event-card ${e.decision||''}`;
    let html=`<div><span class="badge">${e.decision||e.event_type||e.event||'EVENT'}</span><b>${e.reason||e.event_type||e.event||''}</b></div>`;
    if(e.ema!==undefined)html+=`<div class="check">EMA ${e.ema_period||''}<strong>${nice(e.ema,5)}</strong></div>`;
    if(e.close!==undefined)html+=`<div class="check">Close<strong>${nice(e.close,5)}</strong></div>`;
    if(e.range!==undefined)html+=`<div class="check">Bar range ≤ ${nice(e.max_bar_range,3)}<strong>${nice(e.range,5)} · ${e.range_condition||''}</strong></div>`;
    if(e.trend_condition)html+=`<div class="check">EMA trend<strong>${e.trend_condition}</strong></div>`;
    for(const indicator of e.indicators||[]){const units=[indicator.timeframe,indicator.unit].filter(Boolean).join(' · ');html+=`<div class="check">${escapeHtml(indicator.name||'Indicator')}<strong>${escapeHtml(indicator.value===null?'niet beschikbaar':typeof indicator.value==='number'?nice(indicator.value,6):JSON.stringify(indicator.value))}${units?` · ${escapeHtml(units)}`:''}</strong>${indicator.source?`<br>${escapeHtml(indicator.source)}`:''}</div>`}
    if(e.signal){const signal=[e.signal.side,e.signal.order_type,e.signal.price===null||e.signal.price===undefined?'':nice(e.signal.price,5),e.signal.reason].filter(Boolean).join(' · ');html+=`<div class="check">Orderintentie<strong>${escapeHtml(signal||'Geen')}</strong></div>`}
    for(const check of e.checks||[]) html+=`<div class="check">${escapeHtml(check.name||check.condition||'Voorwaarde')}<strong>${escapeHtml(check.status||check.result||'')}</strong><br>${escapeHtml(JSON.stringify(check.value??check.actual??''))} ${check.operator||''} ${escapeHtml(JSON.stringify(check.threshold??''))}</div>`;
    if(e.event_type==='CANDLE_DECISION'){
      const entries=Object.entries(e.state||{});
      html+=`<details class="state-snapshot"><summary>Opgeslagen strategiestatus</summary>${entries.length?entries.map(([key,value])=>`<div class="check">${escapeHtml(key)}<strong>${escapeHtml(typeof value==='object'?JSON.stringify(value):String(value??''))}</strong></div>`).join(''):'<div class="muted">Deze strategie heeft geen status opgeslagen; er is geen toestand achteraf afgeleid.</div>'}</details>`;
      const replay=state.selectedSnapshot?.timestamp_ms===e.timestamp_ms?state.selectedSnapshot?.reexecution:null;
      if(replay){html+=`<details class="state-snapshot"><summary>Causale herberekening ${replay.available?(replay.matches_recorded?'· match':'· afwijking'):'· niet beschikbaar'}</summary>${replay.available?`<div class="check">Beslissing<strong>${escapeHtml(replay.decision)} · ${escapeHtml(replay.reason)}</strong></div><div class="check">EMA ${escapeHtml(replay.adapter)}<strong>${nice(replay.ema,5)} · index ${replay.index}</strong></div><div class="muted">Alleen candles tot deze cursor; positie en pending orders komen uit de exacte opgeslagen snapshot.</div>`:`<div class="muted">${escapeHtml(replay.reason)}</div>`}</details>`;}
    }
    html+=`<details><summary>Alle eventvelden</summary><pre>${escapeHtml(JSON.stringify(e,null,2))}</pre></details>`;card.innerHTML=html;root.appendChild(card);
  }
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

async function execute(action){
  $('top-run').disabled=true;$('top-optimize').disabled=true;status(action==='optimize'?'Jesse optimizer draait…':'Dukascopy-data naar Jesse sturen…','busy');
  try{const payload=currentForm(action);const data=await api('/api/runs',{method:'POST',body:JSON.stringify(payload)});state.activeRun=data.manifest.run_id;
    $('run-select').value=state.activeRun;const trials=data.result.all_trials||[],trialErrors=trials.filter(t=>String(t.status||'').toUpperCase()==='ERROR').length;status(action==='optimize'?`Optimalisatie afgerond · ${data.result.optimization?.completed_trials||0}/${data.result.optimization?.total_trials||payload.trials} trials · ${data.result.best_trials?.length||0} geldige resultaten · ${trialErrors} fouten.`:`Backtest afgerond · ${data.trade_count} trades · ${data.event_count} events.`, '');
    await loadRuns();if(![...$('run-select').options].some(o=>o.value===state.activeRun)){const o=document.createElement('option');o.value=state.activeRun;o.textContent=`Nieuwe run · ${payload.strategy} · ${payload.symbol}`;$('run-select').append(o)}$('run-select').value=state.activeRun;await loadSelectedRun();
    if(action==='optimize')activateResultsTab('optimization')
  }catch(e){status(e.message,'error');if(/shortposities/.test(e.message)&&$('account-type').value!=='futures'){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent='Futures instellen en opnieuw starten';b.onclick=()=>{$('account-type').value='futures';$('account-type').dispatchEvent(new Event('change'));execute(action)};$('run-status').append(' ',b)}}finally{$('top-run').disabled=false;$('top-optimize').disabled=false;}
}

function showOptimization(trials,meta={}){purgePlots($('optimization-results'));
  const root=$('optimization-results');if(window.Plotly){for(const old of root.querySelectorAll('.optimizer-heatmap,.optimizer-sensitivity'))try{Plotly.purge(old)}catch{}}root.innerHTML='';
  const split=Number(meta.train_percent),holdout=Number(meta.holdout_percent);
  const splitLabel=Number.isFinite(split)&&Number.isFinite(holdout)?` · ${split}/${holdout}% train/holdout · ${meta.training_candles||0}/${meta.holdout_candles||0} candles`:'';
  const rows=Array.isArray(trials)?trials:[];
  const summary=document.createElement('div');summary.className='optimizer-summary';
  const summaryText=document.createElement('div');summaryText.className='metric';summaryText.innerHTML=`<span>${escapeHtml(meta.method||'random')} · seed ${escapeHtml(meta.seed??42)}${escapeHtml(splitLabel)}</span><strong>${meta.completed_trials||0}/${meta.total_trials||0} trials · scores zijn trainingsdata</strong>`;
  const summaryOutcomes=rows.reduce((counts,t)=>{const key=optimizerOutcomeKey(t);counts[key]=(counts[key]||0)+1;return counts},{});
  const outcomeSummary=document.createElement('small');outcomeSummary.className='optimizer-outcome-summary';
  outcomeSummary.textContent=Object.entries(summaryOutcomes).map(([key,count])=>`${optimizerOutcomeLabel(key)}: ${count}`).join(' · ');
  summaryText.appendChild(outcomeSummary);
  const exportButton=document.createElement('button');exportButton.type='button';exportButton.className='secondary';exportButton.textContent='Run exporteren';exportButton.onclick=()=>exportOptimization(rows,meta);
  summary.append(summaryText,exportButton);root.appendChild(summary);

  const varyingNumeric=[...new Set(rows.flatMap(t=>Object.entries(t.params||{}).filter(([,v])=>typeof v==='number'&&Number.isFinite(v)).map(([k])=>k)))].filter(key=>new Set(rows.map(t=>t.params?.[key]).filter(v=>typeof v==='number'&&Number.isFinite(v))).size>1);
  const analysis=document.createElement('section');analysis.className='optimizer-analysis';
  const analysisHead=document.createElement('div');analysisHead.className='section-head';const title=document.createElement('h3');title.textContent='Score-analyse · training';
  const selectors=document.createElement('div');selectors.className='inline optimizer-selectors';
  const xSelect=document.createElement('select'),ySelect=document.createElement('select');xSelect.setAttribute('aria-label','Heatmap horizontale parameter');ySelect.setAttribute('aria-label','Heatmap verticale parameter');
  for(const key of varyingNumeric){for(const [select,label] of [[xSelect,`X · ${key}`],[ySelect,`Y · ${key}`]]){const option=document.createElement('option');option.value=key;option.textContent=label;select.append(option)}}
  const heatTitle=document.createElement('h4');heatTitle.textContent='Gemiddelde trainingsscore per combinatie';
  const heatNote=document.createElement('p');heatNote.className='muted';heatNote.textContent='Alleen gescoorde trainingstrials zijn ingekleurd; fouttrials en combinaties zonder trainingstrades blijven in de tabel staan.';
  const heat=document.createElement('div');heat.className='optimizer-heatmap';heat.setAttribute('role','img');heat.setAttribute('aria-label','Heatmap van daadwerkelijk uitgevoerde parameters en trainingsscores');
  const sensitivityHead=document.createElement('div');sensitivityHead.className='section-head';const sensitivityTitle=document.createElement('h4');sensitivityTitle.textContent='Parametergevoeligheid';
  const sensitivitySelect=document.createElement('select');sensitivitySelect.setAttribute('aria-label','Parameter voor gevoeligheidsweergave');
  for(const key of varyingNumeric){const option=document.createElement('option');option.value=key;option.textContent=key;sensitivitySelect.append(option)}
  const sensitivity=document.createElement('div');sensitivity.className='optimizer-sensitivity';sensitivity.setAttribute('role','img');sensitivity.setAttribute('aria-label','Waargenomen trainingsscore per uitgevoerde parameterwaarde');
  if(varyingNumeric.length){xSelect.value=varyingNumeric[0];ySelect.value=varyingNumeric[Math.min(1,varyingNumeric.length-1)];sensitivitySelect.value=varyingNumeric[0];}
  analysisHead.append(title,selectors);selectors.append(xSelect,ySelect);
  sensitivityHead.append(sensitivityTitle,sensitivitySelect);
  analysis.append(analysisHead,heatTitle,heatNote,heat,sensitivityHead,sensitivity);
  if(varyingNumeric.length>=2){xSelect.onchange=()=>renderOptimizerHeatmap(heat,rows,xSelect.value,ySelect.value);ySelect.onchange=()=>renderOptimizerHeatmap(heat,rows,xSelect.value,ySelect.value);renderOptimizerHeatmap(heat,rows,xSelect.value,ySelect.value);}
  else {heatNote.textContent=varyingNumeric.length?'Heatmap vereist twee gevarieerde numerieke parameters.':'Geen twee gevarieerde numerieke parameters in deze run.';heat.textContent='Heatmap niet beschikbaar voor deze zoekruimte.';}
  if(varyingNumeric.length){sensitivitySelect.onchange=()=>renderOptimizerSensitivity(sensitivity,rows,sensitivitySelect.value);renderOptimizerSensitivity(sensitivity,rows,sensitivitySelect.value);}
  else sensitivity.textContent='Geen numerieke parameterwaarden om te vergelijken.';
  root.appendChild(analysis);

  const tableWrap=document.createElement('div');tableWrap.className='optimizer-table-wrap';const table=document.createElement('table');table.className='optimizer-trials-table';
  const thead=document.createElement('thead'),headRow=document.createElement('tr');
  for(const [key,label] of [['trial','Trial'],['status','Status'],['score','Score (train)'],['params','Parameters'],['action','']]){const th=document.createElement('th');if(key==='action'){th.textContent=label;}else{const sort=document.createElement('button');sort.type='button';sort.className='optimizer-sort secondary';sort.dataset.sort=key;sort.textContent=label;sort.setAttribute('aria-sort',state.optimizerSort.key===key?(state.optimizerSort.direction>0?'ascending':'descending'):'none');sort.onclick=()=>{if(state.optimizerSort.key===key)state.optimizerSort.direction*=-1;else state.optimizerSort={key,direction:key==='trial'?1:-1};for(const button of root.querySelectorAll('[data-sort]'))button.setAttribute('aria-sort',state.optimizerSort.key===button.dataset.sort?(state.optimizerSort.direction>0?'ascending':'descending'):'none');renderOptimizerTable(body,rows);};th.append(sort);}headRow.append(th)}
  thead.append(headRow);const body=document.createElement('tbody');table.append(thead,body);tableWrap.append(table);root.append(tableWrap);renderOptimizerTable(body,rows);activateResultsTab('optimization');
}

function exportOptimization(trials,meta){
  const payload={schema_version:1,exported_at:new Date().toISOString(),run_id:state.activeRun,source_root:state.runManifest?.source_root||'C:\\AI\\Dukascopy_data',dataset_sha256:state.runManifest?.dataset_sha256||null,optimization:meta,trials};
  const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`dukascopy-optimization-${state.activeRun||'run'}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function renderOptimizerTable(body,trials){
  const {key,direction}=state.optimizerSort;const sorted=[...trials].sort((a,b)=>{let av,bv;if(key==='trial'){av=Number(a.trial);bv=Number(b.trial)}else if(key==='score'){av=typeof a.score==='number'&&Number.isFinite(a.score)?a.score:-Infinity;bv=typeof b.score==='number'&&Number.isFinite(b.score)?b.score:-Infinity}else if(key==='status'){av=optimizerOutcomeLabel(optimizerOutcomeKey(a));bv=optimizerOutcomeLabel(optimizerOutcomeKey(b))}else{av=JSON.stringify(a.params||{});bv=JSON.stringify(b.params||{})}if(av<bv)return -direction;if(av>bv)return direction;return Number(a.trial)-Number(b.trial)});body.replaceChildren();
  for(const trial of sorted){const tr=document.createElement('tr'),trialCell=document.createElement('td'),statusCell=document.createElement('td'),scoreCell=document.createElement('td'),paramsCell=document.createElement('td'),actionCell=document.createElement('td'),outcome=optimizerOutcomeKey(trial);trialCell.textContent=String(Number(trial.trial??0)+1);statusCell.textContent=optimizerOutcomeLabel(outcome);statusCell.className='optimizer-outcome';statusCell.dataset.outcome=outcome;statusCell.title=[trial.failure_phase?`Fase: ${trial.failure_phase}`:'',trial.reason||''].filter(Boolean).join('\n');scoreCell.textContent=outcome==='NO_TRAINING_TRADES'?'—':typeof trial.score==='number'&&Number.isFinite(trial.score)?nice(trial.score,6):'—';paramsCell.textContent=JSON.stringify(trial.params||{});paramsCell.className='optimizer-params-cell';const use=document.createElement('button');use.type='button';use.className='secondary';use.textContent='Gebruik';use.disabled=!trial.params;use.onclick=()=>buildParams(selectedStrategy()?.parameters||[],trial.params||{});actionCell.append(use);tr.append(trialCell,statusCell,scoreCell,paramsCell,actionCell);body.append(tr);}
}

function optimizerOutcomeKey(trial){
  if(trial.outcome)return String(trial.outcome).toUpperCase();
  const status=String(trial.status||'UNKNOWN').toUpperCase(),reason=String(trial.reason||'');
  if(status==='FILTERED'&&/no training trades/i.test(reason))return 'NO_TRAINING_TRADES';
  if(status==='ERROR'&&reason.startsWith('InsufficientBalance:'))return 'INSUFFICIENT_FUNDS_AT_ORDER';
  const profit=Number(trial.training_metrics?.net_profit);
  if(status==='COMPLETED'&&Number.isFinite(profit))return profit>0?'COMPLETED_POSITIVE':profit<0?'COMPLETED_NEGATIVE':'COMPLETED_BREAKEVEN';
  return status;
}

function optimizerOutcomeLabel(key){return ({
  NO_TRAINING_TRADES:'Geen trainingstrades',COMPLETED_POSITIVE:'Positief resultaat',
  COMPLETED_NEGATIVE:'Negatief resultaat',COMPLETED_BREAKEVEN:'Break-even',
  COMPLETED_SCORE_ONLY:'Voltooid · P&L onbekend',INSUFFICIENT_SPOT_BALANCE_AT_ORDER:'Spot-saldo onvoldoende',
  INSUFFICIENT_FUTURES_MARGIN_AT_ORDER:'Futures-margin onvoldoende',INSUFFICIENT_FUNDS_AT_ORDER:'Saldo/margin onvoldoende',
  INVALID_PARAMETER_COMBINATION:'Ongeldige combinatie',TECHNICAL_ERROR:'Technische fout',
  FILTERED:'Gefilterd',ERROR:'Fout',COMPLETED:'Voltooid'
})[key]||key.replaceAll('_',' ').toLowerCase()}

function renderOptimizerHeatmap(target,trials,xKey,yKey){
  if(!window.Plotly||!xKey||!yKey){target.textContent='Lokale chartbibliotheek niet geladen.';return;}const valid=trials.filter(t=>!['ERROR','FILTERED'].includes(String(t.status||'').toUpperCase())&&typeof t.score==='number'&&Number.isFinite(t.score)&&Number.isFinite(Number(t.params?.[xKey]))&&Number.isFinite(Number(t.params?.[yKey])));if(!valid.length){target.textContent='Geen succesvolle numerieke trials voor deze heatmap.';return;}
  const xs=[...new Set(valid.map(t=>Number(t.params[xKey])))].sort((a,b)=>a-b),ys=[...new Set(valid.map(t=>Number(t.params[yKey])))].sort((a,b)=>a-b);if(xs.length*ys.length>10000){target.textContent='Te veel unieke combinaties voor een leesbare heatmap; beperk de zoekruimte.';return;}
  const cells=new Map();for(const trial of valid){const key=`${trial.params[xKey]}|${trial.params[yKey]}`;const cell=cells.get(key)||{sum:0,count:0};cell.sum+=Number(trial.score);cell.count++;cells.set(key,cell);}const z=ys.map(y=>xs.map(x=>{const c=cells.get(`${x}|${y}`);return c?c.sum/c.count:null})),counts=ys.map(y=>xs.map(x=>cells.get(`${x}|${y}`)?.count||0));
  Plotly.react(target,[{type:'heatmap',x:xs,y:ys,z,customdata:counts,colorscale:'RdYlGn',hovertemplate:`${xKey}: %{x}<br>${yKey}: %{y}<br>Gemiddelde trainingsscore: %{z:.6f}<br>Trials: %{customdata}<extra></extra>`,connectgaps:false}],{height:320,margin:{l:58,r:12,t:8,b:54},xaxis:{title:{text:xKey},automargin:true},yaxis:{title:{text:yKey},automargin:true},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:getComputedStyle(document.body).color},uirevision:`${xKey}:${yKey}`},{displayModeBar:false,responsive:true});
}

function renderOptimizerSensitivity(target,trials,key){
  if(!window.Plotly||!key){target.textContent='Lokale chartbibliotheek niet geladen.';return;}const valid=trials.filter(t=>!['ERROR','FILTERED'].includes(String(t.status||'').toUpperCase())&&typeof t.score==='number'&&Number.isFinite(t.score)&&Number.isFinite(Number(t.params?.[key])));if(!valid.length){target.textContent='Geen succesvolle numerieke trials voor deze parameter.';return;}const errors=trials.filter(t=>String(t.status||'').toUpperCase()==='ERROR').length,noTrades=trials.filter(t=>optimizerOutcomeKey(t)==='NO_TRAINING_TRADES').length,omitted=[errors?`${errors} fout${errors===1?'':'en'}`:'',noTrades?`${noTrades} zonder trainingstrades`: ''].filter(Boolean).join(' · ');
  Plotly.react(target,[{type:'scatter',mode:'markers',x:valid.map(t=>Number(t.params[key])),y:valid.map(t=>Number(t.score)),text:valid.map(t=>`Trial ${Number(t.trial)+1}`),customdata:valid.map(t=>JSON.stringify(t.params||{})),marker:{size:9,color:'#3787d2',opacity:.78},hovertemplate:`${key}: %{x}<br>Trainingsscore: %{y:.6f}<br>%{text}<br>%{customdata}<extra></extra>`}],{height:270,margin:{l:58,r:12,t:8,b:54},xaxis:{title:{text:key},automargin:true},yaxis:{title:{text:'Trainingsscore'},automargin:true},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:getComputedStyle(document.body).color},annotations:omitted?[{text:`${omitted} uitgesloten`,xref:'paper',yref:'paper',x:1,y:1,showarrow:false,font:{size:10}}]:[],uirevision:key},{displayModeBar:false,responsive:true});
}

async function loadRuns(){
  try{
    const data=await api('/api/runs');state.runs=data.runs||[];
    for(const id of ['run-select','compare-a','compare-b']){const current=$(id).value;const first=id==='run-select';$(id).innerHTML=first?'<option value="">Geen backtestrun</option>':'<option value="">'+(id.endsWith('a')?'Run A':'Run B')+'</option>';state.runs.forEach(r=>{const o=document.createElement('option');o.value=r.run_id;o.textContent=`${r.created_at.slice(0,16)} · ${r.strategy} · ${r.symbol}`;$(id).appendChild(o)});if(current)$(id).value=current;}
    const resume=$('resume-run');if(resume){const current=resume.value;resume.innerHTML='<option value="">Nieuwe optimalisatie</option>';state.runs.filter(r=>r.action==='optimize').forEach(r=>{const o=document.createElement('option');o.value=r.run_id;o.textContent=`${r.created_at.slice(0,16)} · ${r.symbol} · ${r.run_id.slice(0,8)}`;resume.appendChild(o)});if(current)resume.value=current;}
    renderPortfolioRuns();
  }catch(e){status('Runs ophalen mislukt: '+e.message,'error')}
}

function renderPortfolioRuns(){
  const root=$('portfolio-runs');if(!root)return;root.replaceChildren();
  const runs=(state.runs||[]).filter(r=>r.action==='backtest'&&r.bidask_available);
  if(!runs.length){root.innerHTML='<p class="muted">Geen opgeslagen Dukascopy Bid/Ask backtestruns beschikbaar.</p>';return;}
  const help=document.createElement('p');help.className='muted';help.textContent='Selecteer 2–12 runs. Startsaldo blijft vast; een andere toewijzing vereist een nieuwe backtest.';root.appendChild(help);
  for(const run of runs){
    const row=document.createElement('label');row.className='portfolio-run-option';
    const pick=document.createElement('input');pick.type='checkbox';pick.dataset.portfolioRun=run.run_id;pick.setAttribute('aria-label',`Selecteer ${run.strategy} ${run.symbol}`);
    const meta=document.createElement('span');meta.className='portfolio-run-meta';meta.textContent=`${String(run.created_at||'').slice(0,16)} · ${run.strategy} · ${run.symbol} ${run.timeframe} · ${run.trade_count??0} trades`;
    const capital=document.createElement('input');capital.type='number';capital.min='0';capital.step='any';capital.value=run.starting_balance??'';capital.dataset.portfolioAllocation=run.run_id;capital.setAttribute('aria-label',`Kapitaal voor ${run.strategy} ${run.symbol}`);
    row.append(pick,meta,capital);root.appendChild(row);
  }
}

function portfolioCell(value){const cell=document.createElement('td');cell.textContent=value==null?'—':String(value);return cell;}
function wireDataMonitor(){
  const dialog=$('data-monitor-dialog'), sync=$('data-sync-now'), message=$('data-monitor-status');
  const report=$('data-monitor-report');
  const stamp=value=>value?value.slice(0,16).replace('T',' '):'—';
  const feedLabels={'UP TO DATE':'Bijgewerkt',DELAYED:'In afwachting',INCOMPLETE:'Onvolledig',
    ERROR:'Fout','MARKET CLOSED':'Markt gesloten'};
  const runLabels={RUNNING:'Bezig',COMPLETE:'Afgerond',RETRY:'Herkansing nodig',ERROR:'Fout',INTERRUPTED:'Onderbroken'};
  let configured=false, busy=false;
  const refresh=async()=>{
    try{
      const data=await api('/api/data/monitor');
      configured=data.configured;
      report.replaceChildren();
      if(!configured){
        report.textContent='De dagelijkse synchronisatie is nog niet ingesteld. Configureer de lokale Dukascopy-invoer en Windows Taakplanner.';
        return;
      }
      renderPortfolioTable(report,data.symbols.map(row=>({
        symbol:row.symbol,status:feedLabels[row.status]??row.status,
        candle:stamp(row.last_candle_at),success:stamp(row.last_success_at),next:stamp(row.next_update_at),
        added:row.last_added_rows,backlog:row.pending_days,
        gaps:row.missing_bid==null?'—':`Bid ${row.missing_bid} · Ask ${row.missing_ask}`,
        pending:row.first_pending_day?`${row.first_pending_day} · ${row.expected_minutes_first_day} min verwacht`:'—',
        forward:row.forward.map(item=>`${item.name} · ${item.last_day??'wacht op verwerking'}`).join(', ')||'—',
      })),[['symbol','Symbool'],['status','Datastatus'],['candle','Nieuwste sync-candle'],['success','Laatste succes'],
        ['next','Volgende geplande tijd'],['added','Nieuw bij laatste poging'],['backlog','Open dagen'],
        ['pending','Eerste open dag'],['gaps','Geconstateerde gaten'],['forward','Forward verwerkt t/m']]);
      if(data.runs.length){
        const title=document.createElement('h3');title.textContent='Synchronisatielogboek';
        report.append(title);
        renderPortfolioTable(report,data.runs.map(row=>({
          started:stamp(row.started_at),finished:stamp(row.finished_at),mode:row.mode==='MANUAL'?'Handmatig':'Gepland',
          status:runLabels[row.status]??row.status,results:row.detail?.intake?.length??'—',
        })),[['started','Start'],['finished','Einde'],['mode','Startwijze'],['status','Resultaat'],['results','Pogingen']]);
      }
      const power=document.createElement('h3');power.textContent='Windows de afgelopen zeven dagen';
      report.append(power);
      const powerNames={STARTED:'Pc opgestart',SHUTDOWN:'Pc afgesloten',UNCLEAN_RESTART:'Onverwachte herstart',
        SLEEP:'Slaapstand gestart',RESUMED:'Uit slaapstand gekomen'};
      if(data.windows_power?.events.length){
        renderPortfolioTable(report,data.windows_power.events.map(event=>({
          at:stamp(event.at),kind:powerNames[event.kind]??event.kind,
        })),[['at','Tijd UTC'],['kind','Gebeurtenis']]);
      }else{
        const note=document.createElement('p');note.className='muted';
        note.textContent=data.windows_power?.available?'Geen relevante gebeurtenissen geregistreerd.':
          'Windows-energiegebeurtenissen zijn niet beschikbaar.';
        report.append(note);
      }
    }catch(error){configured=false;message.textContent=error.message}
    finally{sync.disabled=!configured||busy}
  };
    $('data-monitor-refresh').onclick=refresh;
  sync.onclick=async()=>{
    if(busy)return;
    busy=true;
    sync.disabled=true;
    message.textContent='Dukascopy-invoer controleren…';
    try{
      const result=await api('/api/data/sync',{method:'POST',body:'{}'});
      message.textContent=result.status==='RETRY'?'Synchronisatie vastgelegd. Er wachten nog dagen of portfolio’s.'
        : 'Synchronisatie afgerond.';
    }catch(error){message.textContent=error.message}
    finally{busy=false;await refresh()}
  };
}
function wireForwardPortfolio(){
  const dialog=$('forward-dialog'), select=$('forward-selection'), message=$('forward-status');
  const report=$('forward-report'), enroll=$('forward-enroll'), advance=$('forward-advance');
  const referenceForm=$('forward-reference-form'),referenceSelect=referenceForm.elements.registration;
  let portfolios=[], frozen=null, busy=false, referenceMembers=[];
  const path='/api/forward/portfolios';
  const post=(url,body)=>api(url,{method:'POST',body:JSON.stringify(body)});
  const picked=()=>portfolios.find(item=>item.id===select.value);
  const run=async(action)=>{
    if(busy)return;
    busy=true;
    select.disabled=true;
    referenceForm.querySelector('button').disabled=true;
    message.textContent='';
    try{await action()}catch(error){message.textContent=error.message}
    finally{
      busy=false;
      select.disabled=false;
      advance.disabled=!picked();
      enroll.disabled=!picked()||!frozen;
      referenceForm.querySelector('button').disabled=!referenceMembers.length;
    }
  };
  const loadReport=async()=>{
    const oldChart=report.querySelector('.forward-equity-chart');
    if(oldChart)Plotly.purge(oldChart);
    report.replaceChildren();
    $('forward-reference-panel').hidden=true;
    if(!picked()){
      referenceMembers=[];
      report.textContent='Maak een portfolio aan om te beginnen.';
      return;
    }
    const data=await api(`${path}/${select.value}`);
    const portfolio=data.portfolio, currency=portfolio.currency;
    const performance=data.performance, totals=performance.portfolio;
    const number=value=>value==null?'—':nice(value);
    const percent=value=>value==null?'—':`${nice(value)}%`;
    const utc=value=>value?.slice(0,16).replace('T',' ')??'—';
    const warningText={MODEL_MISMATCH:'Bevroren uitvoermodel wijkt af. Verwerking staat stil.',
      AWAITING_FIRST_CANDLE:'Wacht op de eerste toegelaten candle.',FORWARD_TESTING:'Forwardtest actief'};
    const total=document.createElement('p');
    total.textContent=`Saldo ${nice(portfolio.balance)} ${currency} · Equity ${nice(totals.equity)} ${currency} · Laatste verwerkte UTC-dag ${portfolio.last_day??'—'}`;
    report.append(total);
    const performanceColumns=[['trade_count','Trades'],['net_profit',`Netto (${currency})`],['winrate_percent','Winrate'],
      ['expectancy','Netto per trade'],['profit_factor','Profit factor'],['recovery_factor','Recovery factor'],
      ['sharpe_daily','Sharpe / dag'],['sortino_daily','Sortino / dag'],['max_drawdown_percent','Max. drawdown'],
      ['max_underwater_days','Onder water (dagen)'],['average_reward_to_risk','Gem. gepland rendement/risico'],
      ['average_mae',`Gem. MAE (${currency})`],['average_mfe',`Gem. MFE (${currency})`],['excursion_covered','MAE/MFE-dekking']];
    const formatMetrics=row=>({...Object.fromEntries(performanceColumns.map(([key])=>
      [key,key.endsWith('_percent')?percent(row[key]):number(row[key])])),
      excursion_covered:row.trade_count?`${row.excursion_covered} / ${row.trade_count}`:'—'});
    renderPortfolioTable(report,[formatMetrics(totals)],performanceColumns);
    const risk=document.createElement('p');
    risk.textContent=`Open stoprisico ${nice(totals.open_stop_risk)} / ${nice(totals.portfolio_stop_budget)} ${currency} · Marge ${nice(totals.open_margin)} · Bruto blootstelling ${nice(totals.gross_open_exposure)} · Niet toegewezen startkapitaal ${nice(totals.unallocated_capital)}`;
    report.append(risk);
    if(totals.curve.length){
      const plot=document.createElement('div');
      plot.className='forward-equity-chart';
      report.append(plot);
      const dark=document.body.dataset.theme==='dark',style=getComputedStyle(document.body);
      Plotly.react(plot,[{type:'scatter',mode:'lines+markers',name:'Equity',line:{color:'#5ba6d8'},
        x:totals.curve.map(point=>point.day),y:totals.curve.map(point=>point.equity),
        customdata:totals.curve.map(point=>point.drawdown_percent),
        hovertemplate:'%{x}<br>Equity %{y:.2f}<br>Drawdown %{customdata:.2f}%<extra></extra>'}],
      {height:205,margin:{l:62,r:12,t:12,b:32},paper_bgcolor:'transparent',plot_bgcolor:'transparent',
        font:{color:dark?'#c5d5e5':'#334155'},xaxis:{type:'date'},
        yaxis:{title:currency,gridcolor:dark?'#26394d':style.getPropertyValue('--theme-edge').trim()}},
      {responsive:true,displayModeBar:false});
    }
    const members=data.members.map(item=>{
      const m=performance.members[item.registration_id];
      const warnings=[warningText[m.status]];
      if(m.risk_rejections)warnings.push(`${m.risk_rejections} orders geweigerd op marge of risico`);
      if(m.excursion_unverified)warnings.push(`${m.excursion_unverified} trades met niet-verifieerbare brondata`);
      return {...formatMetrics(m), strategy:item.configuration.strategy, symbol:item.configuration.symbol,
        since:utc(item.joined_at), days:m.sample_days, position:item.state.position?.side??'—',
        signal:utc(m.last_signal), trade:utc(m.last_trade?.at), status:warnings.join(' · ')};
    });
    const basis=document.createElement('p');
    basis.className='muted';
    basis.textContent='Portfolio: UTC-dagequity inclusief open posities. Per strategie: gerealiseerd dagsaldo. Sharpe en Sortino zijn dagratio’s tegenover 0%, zonder annualisering.';
    const excursionHelp=makeInfo('MAE is de grootste ongunstige koersbeweging en MFE de grootste gunstige koersbeweging vanaf de fill, bruto in rekeningvaluta. Gebaseerd op Bid voor longs en Ask voor shorts. De exitcandle en de fillcandle van een stoporder tellen niet mee. De dekking geeft aan hoeveel gesloten trades meetbaar zijn.','MAE en MFE');
    basis.append(' ',excursionHelp.wrap);
    report.append(excursionHelp.bubble);
    report.append(basis);
    renderPortfolioTable(report,members,[['strategy','Strategie'],['symbol','Symbool'],['since','Start UTC'],
      ['days','Verwerkte dagen'],...performanceColumns,['position','Open positie'],['signal','Laatste signaal UTC'],
      ['trade','Laatste fill/exit UTC'],['status','Status']]);
    const symbols=new Map(data.members.map(item=>[item.registration_id,item.configuration.symbol]));
    const previousMember=referenceSelect.value;
    referenceMembers=data.members;
    referenceSelect.replaceChildren(...referenceMembers.map(item=>new Option(
      `${item.configuration.strategy} · ${item.configuration.symbol} · ${item.snapshot_sha256.slice(0,8)}`,
      item.registration_id)));
    if(symbols.has(previousMember))referenceSelect.value=previousMember;
    setReferenceDates();
    const referenceResults=$('forward-reference-results');
    referenceResults.replaceChildren();
    const references=(data.historical_references||[]).map(ref=>{
      const forward=performance.members[ref.registration_id],historical=ref.metrics;
      const observed=forward&&data.members.find(item=>item.registration_id===ref.registration_id)?.state.last_candle_at;
      const pair=(past,current,format=number)=>`${format(past)} / ${format(observed?current:null)}`;
      return {
        symbol:symbols.get(ref.registration_id),period:`${ref.start_day} – ${ref.end_day}`,
        trades:pair(historical.trade_count,forward?.trade_count),
        net:pair(100*historical.net_profit/ref.allocation,100*(forward?.net_profit??0)/ref.allocation,percent),
        perTrade:pair(historical.expectancy,forward?.expectancy),
        drawdown:pair(historical.max_drawdown_percent,forward?.max_drawdown_percent,percent),
        days:pair(historical.sample_days,forward?.sample_days),
        unobserved:nice(ref.minutes_without_quotes),open:ref.open_position_at_end?'Open positie':'—',
      };
    });
    if(references.length){
      const note=document.createElement('p');
      note.className='muted';
      note.textContent='Historisch / forward. Beide kolommen tonen gerealiseerde resultaten. De historische strategie draait zelfstandig met dezelfde risicolimieten. Minuten zonder quotes kunnen marktsluitingen zijn. Deze referentie heeft geen OOS-status.';
      referenceResults.append(note);
      renderPortfolioTable(referenceResults,references,[['symbol','Symbool'],['period','Historische UTC-periode'],
        ['days','Dagen'],['trades','Trades'],['net','Netto / toegewezen kapitaal'],
        ['perTrade',`Netto per trade (${currency})`],['drawdown','Max. drawdown'],
        ['unobserved','Minuten zonder quotes'],['open','Historisch op einddatum']]);
    }
    $('forward-reference-panel').hidden=!referenceMembers.length;
    const names={ORDER_SUBMITTED:'Order aangemaakt',ORDER_FILLED:'Order uitgevoerd',ORDER_REJECTED:'Order afgewezen',
      POSITION_CLOSED:'Positie gesloten',ORDER_CANCELLED:'Order vervallen',SIGNAL_REJECTED:'Signaal afgewezen'};
    const events=data.events.slice(-20).reverse().map(event=>{
      const excursion=performance.trade_excursions[event.id];
      return {symbol:symbols.get(event.registration_id),time:event.candle_at.slice(0,16),
        observed:event.detail.quote_observed_at.slice(0,16),kind:names[event.kind]??event.kind,
        pnl:number(event.detail.net_pnl),mae:number(excursion?.mae),mfe:number(excursion?.mfe)};
    });
    renderPortfolioTable(report,events,[['symbol','Symbool'],['time','Candle UTC'],['observed','Quotes ontvangen UTC'],
      ['kind','Gebeurtenis'],['pnl',`Netto (${currency})`],['mae',`MAE (${currency})`],['mfe',`MFE (${currency})`]]);
  };
  const refresh=async(id=select.value)=>{
    ({portfolios}=await api(path));
    select.replaceChildren(...portfolios.map(item=>new Option(item.name,item.id)));
    if(portfolios.some(item=>item.id===id))select.value=id;
    await loadReport();
  };
  $('forward-refresh').onclick=()=>run(()=>refresh());
  select.onchange=()=>run(loadReport);
  function setReferenceDates(){
    const member=referenceMembers.find(item=>item.registration_id===referenceSelect.value);
    if(!member)return;
    const lastDay=new Date(Date.parse(member.frozen_at.slice(0,10))-86400000).toISOString().slice(0,10);
    for(const [name,source] of [['start_day','start'],['end_day','end']]){
      const input=referenceForm.elements[name];
      input.max=lastDay;
      if(!input.value||input.value>lastDay)input.value=$(`${source}`).value<=lastDay?$(source).value:lastDay;
    }
  }
  referenceSelect.onchange=setReferenceDates;
  referenceForm.onsubmit=event=>{
    event.preventDefault();
    const values=new FormData(referenceForm);
    run(async()=>{
      if(!picked()||!values.get('registration'))return;
      message.textContent='Historische referentie wordt berekend…';
      await post(`${path}/${select.value}/members/${values.get('registration')}/historical-reference`,{
        start_day:values.get('start_day'),end_day:values.get('end_day'),
      });
      await loadReport();
      message.textContent='Historische referentie opgeslagen.';
    });
  };
  advance.onclick=()=>run(async()=>{
    if(!picked())return;
    const result=await post(`${path}/${select.value}/advance`,{});
    await loadReport();
    message.textContent=result.status==='WAITING_FOR_DATA'
      ?`Wacht op ${result.missing_symbols.join(', ')} voor ${result.day} UTC.`
      :`${result.committed_days.length} dag(en) verwerkt.`;
  });
  $('forward-create-form').onsubmit=event=>{
    event.preventDefault();
    const form=event.currentTarget, values=new FormData(form);
    run(async()=>{
      const result=await post(path,{name:values.get('name'),currency:values.get('currency'),
        starting_capital:Number(values.get('starting_capital')),risk_limit:Number(values.get('risk_limit'))/100});
      form.reset();
      await refresh(result.id);
      message.textContent='Portfolio aangemaakt.';
    });
  };
  $('forward-freeze-form').onsubmit=event=>{
    event.preventDefault();
    const values=new FormData(event.currentTarget);
    run(async()=>{
      if(!picked())throw new Error('Selecteer eerst een portfolio.');
      if($('strategy').value!=='LabBreakout'||$('timeframe').value!=='M1'){
        throw new Error('Selecteer LabBreakout op M1 om deze forwardversie te gebruiken.');
      }
      const parameters=currentParams();
      parameters.order_mode=Number(parameters.order_mode);
      frozen=await post('/api/forward/registrations',{
        symbol:$('symbol').value,parameters,leverage:Number($('leverage').value),
        starting_capital:Number(values.get('starting_capital')),trade_risk:Number(values.get('trade_risk'))/100,
        portfolio_risk_limit:picked().risk_limit,contract:{
          account_currency:$('account-currency').value,contract_size:Number($('contract-size').value),
          tick_size:Number($('tick-size').value),quote_to_account:Number($('conversion').value),
          volume_step:Number($('volume-step').value),commission_per_lot_side:Number($('commission').value),
          slippage_points:Number($('slippage').value),
        },
      });
      message.textContent=`Bevroren · ${frozen.configuration.symbol} · versie ${frozen.snapshot_sha256.slice(0,12)}.`;
    });
  };
  enroll.onclick=()=>run(async()=>{setTimeout(()=>document.dispatchEvent(new CustomEvent('lab:forward',{detail:{}})),1500);
    if(!picked()||!frozen)return;
    await post(`${path}/${select.value}/members`,{registration_id:frozen.id});
    frozen=null;
    await loadReport();
    message.textContent='Bevroren strategie toegevoegd.';
  });
}
function renderPortfolioTable(target,rows,columns){
  if(!rows?.length)return;const wrap=document.createElement('div');wrap.className='table-wrap';const table=document.createElement('table'),head=document.createElement('thead'),headRow=document.createElement('tr');
  columns.forEach(([,label])=>{const cell=document.createElement('th');cell.textContent=label;headRow.appendChild(cell)});head.appendChild(headRow);table.appendChild(head);
  const body=document.createElement('tbody');for(const row of rows){const tr=document.createElement('tr');columns.forEach(([key])=>tr.appendChild(portfolioCell(row[key])));body.appendChild(tr)}table.appendChild(body);wrap.appendChild(table);target.appendChild(wrap);
}
function analyzePortfolio(){
  const selected=[...document.querySelectorAll('[data-portfolio-run]:checked')];
  if(selected.length<2||selected.length>12){$('portfolio-result').textContent='Selecteer minimaal 2 en maximaal 12 runs.';return;}
  const runs=selected.map(box=>({run_id:box.dataset.portfolioRun,allocated_capital:Number([...document.querySelectorAll('[data-portfolio-allocation]')].find(input=>input.dataset.portfolioAllocation===box.dataset.portfolioRun)?.value)}));
  $('portfolio-result').textContent='Compatibiliteit en portefeuille berekenen…';
  api('/api/portfolio/analysis',{method:'POST',body:JSON.stringify({runs})}).then(renderPortfolioResult).catch(error=>{$('portfolio-result').textContent=`Analyse mislukt: ${error.message}`});
}
function renderPortfolioResult(data){
  const root=$('portfolio-result');root.replaceChildren();
  const title=document.createElement('h3');title.textContent=data.status==='PROVISIONAL_RESEARCH_PORTFOLIO'?'Voorlopige Duka-portefeuille':'Combinatie geblokkeerd';root.appendChild(title);
  if(data.status==='COMPATIBILITY_BLOCKED'){
    const list=document.createElement('ul');for(const blocker of data.blockers||[]){const item=document.createElement('li');item.textContent=`${blocker.code}: ${blocker.message}`;list.appendChild(item)}root.appendChild(list);
    renderPortfolioTable(root,data.runs,[['strategy','Strategie'],['symbol','Symbool'],['trade_count','Trades'],['allocated_capital','Toegewezen kapitaal'],['net_pnl','Run-P&L · voorlopig']]);
    const note=document.createElement('p');note.className='muted';note.textContent=data.note||'';root.appendChild(note);return;
  }
  const currency=data.account_currency||'';const metrics=[['Accountvaluta',currency],['Startkapitaal',data.allocated_capital],['Netto-P&L · voorlopig',data.net_pnl],['Eindsaldo · voorlopig',data.ending_equity],['Max. drawdown · voorlopig',`${Number(data.max_drawdown_money).toFixed(2)} (${Number(data.max_drawdown_percent).toFixed(2)}%)`],['Max. gelijktijdige verliestrades',data.simultaneous_losing_trades_max],['Aantal trades',data.trade_count]];
  const cards=document.createElement('div');cards.className='metrics';for(const [label,value] of metrics){const card=document.createElement('article');card.className='metric';const name=document.createElement('span');name.textContent=label;const strong=document.createElement('strong');strong.textContent=typeof value==='number'?`${value.toFixed(2)} ${currency}`:String(value);card.append(name,strong);cards.appendChild(card)}root.appendChild(cards);
  const chart=document.createElement('div');chart.id='portfolio-equity';chart.className='portfolio-chart';root.appendChild(chart);
  const curve=(data.equity_curve||[]).filter(point=>point.timestamp_ms!=null&&Number.isFinite(Number(point.timestamp_ms))&&Number.isFinite(Number(point.equity)));
  if(curve.length)Plotly.react(chart,[{type:'scatter',mode:'lines',x:curve.map(p=>new Date(p.timestamp_ms).toISOString()),y:curve.map(p=>p.equity),name:'Portefeuille-equity',line:{color:'#3787d2'},hovertemplate:'%{x}<br>Equity %{y:.2f} '+currency+'<extra></extra>'}],{height:260,margin:{l:58,r:16,t:12,b:50},xaxis:{title:'UTC'},yaxis:{title:`Equity (${currency})`},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:getComputedStyle(document.body).color}},{displayModeBar:false,responsive:true});
  for(const [key,label,columns] of [['runs','Geselecteerde runs',[['strategy','Strategie'],['symbol','Symbool'],['trade_count','Trades'],['allocated_capital','Startkapitaal'],['net_pnl','Netto-P&L']]],['by_strategy','Per strategie',[['key','Strategie'],['trade_count','Trades'],['wins','Winst'],['losses','Verlies'],['net_pnl','Netto-P&L']]],['by_instrument','Per instrument',[['key','Instrument'],['trade_count','Trades'],['wins','Winst'],['losses','Verlies'],['net_pnl','Netto-P&L']]],['by_month','Per maand',[['key','Maand UTC'],['trade_count','Trades'],['wins','Winst'],['losses','Verlies'],['net_pnl','Netto-P&L']]],['correlations','Dagelijkse genormaliseerde rendementscorrelatie',[['first_run_id','Run A'],['second_run_id','Run B'],['daily_normalized_return_correlation','Correlatie'],['observation_days','Dagen'],['reason_unavailable','Toelichting']]]]){
    const heading=document.createElement('h3');heading.textContent=label;root.appendChild(heading);renderPortfolioTable(root,data[key],columns);
  }
  const note=document.createElement('p');note.className='muted';note.textContent=data.note||'';root.appendChild(note);
}
async function loadSelectedRun(){
  stopReplay();
  const id=$('run-select').value;state.activeRun=id||null;if(!id){state.runManifest=null;state.runData=null;await loadChart();$('trades').innerHTML='';$('metrics').innerHTML='';renderPerformanceAnalytics();activateResultsTab('summary');return;}
  try{const data=await api('/api/runs/'+id),manifest=data.manifest;state.runManifest=manifest;state.runData=data.result;state.activeRun=id;$('symbol').value=manifest.symbol;$('timeframe').value=manifest.timeframe;$('strategy').value=manifest.strategy;if(manifest.optimizer_space)state.optimizerSpaceByStrategy[manifest.strategy]=manifest.optimizer_space;buildParams(selectedStrategy()?.parameters||[],manifest.parameters||{});$('balance').value=manifest.starting_balance;$('fee').value=manifest.fee;$('account-type').value=manifest.account_type;$('leverage').value=manifest.leverage;$('warmup').value=manifest.warmup_candles;$('train-percent').value=manifest.train_percent??80;renderOptimizerSpace();await loadMeta();$('start').value=manifest.start_date;$('end').value=manifest.end_date;renderOptimizerPlan();await loadChart();renderMetrics(state.runData.metrics||{},state.runData.trades||[],Date.parse(manifest.start_date)||0);renderTrades(state.runData.trades||[]);renderPerformanceAnalytics();renderInterpretation(id,manifest);queueSaveSession();document.dispatchEvent(new CustomEvent('lab:run',{detail:{runId:id,action:manifest.action||(Array.isArray(state.runData.all_trials)?'optimize':'backtest'),manifest,result:state.runData}}));$('result-logs').textContent=state.runData.logs||state.runData.error||'Geen run- of foutlogs beschikbaar.';if(state.runData.action==='optimize'||Array.isArray(state.runData.all_trials)){showOptimization(state.runData.all_trials||[],state.runData.optimization||{});return;}activateResultsTab('summary');}
  catch(e){status('Run laden mislukt: '+e.message,'error')}
}
const metricCatalog=[['total','Aantal trades','Totaal aantal gesloten trades in deze run.'],['net_profit','Nettoresultaat · voorlopig','Jesse-resultaat volgens de gebruikte simulatie-instellingen; geen gevalideerd brokerbedrag.'],['profit_factor','Profit factor · voorlopig','Brutowinst gedeeld door absolute brutoverlies. 1,0 is break-even vóór niet-gemodelleerde kosten.'],['recovery_factor','Recovery factor · voorlopig','Nettowinst gedeeld door maximale drawdown in geld; alleen betekenisvol binnen hetzelfde simulatiewaardemodel.'],['max_drawdown','Maximale drawdown · voorlopig','Grootste piek-tot-dalverlies als percentage volgens Jesse.'],['win_rate','Winrate','Percentage gesloten trades met positieve P&L.'],['calmar_ratio','Calmar-ratio · voorlopig','Geannualiseerd rendement gedeeld door maximale drawdown. Gebaseerd op Jesse-metrics en runduur.'],['sortino_ratio','Sortino-ratio · voorlopig','Rendement afgezet tegen neerwaartse volatiliteit volgens Jesse.'],['sharpe_ratio','Sharpe-ratio · voorlopig','Rendement per eenheid totale volatiliteit volgens Jesse.'],['annual_return','Geannualiseerd rendement · voorlopig','Jesse annualiseert de runduur; korte periodes kunnen dit sterk vertekenen.'],['average_win','Gemiddelde winnaar','Gemiddelde P&L van winnende gesloten trades.'],['average_loss','Gemiddelde verliezer','Gemiddelde P&L van verliezende gesloten trades.'],['ratio_avg_win_loss','Payoff-ratio','Gemiddelde winst gedeeld door absolute gemiddelde verlies.'],['expectancy','Verwachting per trade','Gemiddelde P&L per gesloten trade, inclusief Jesse-simulatiekosten.'],['largest_winning_trade','Grootste winnaar','Hoogste enkele trade-P&L.'],['largest_losing_trade','Grootste verliezer','Laagste enkele trade-P&L.'],['winning_streak','Winstreeks','Langste aaneengesloten winstgevende reeks volgens Jesse.'],['losing_streak','Verliesreeks','Langste aaneengesloten verliesreeks volgens Jesse.'],['avg_trades_per_month','Trades per maand','Jesse-gemiddelde op basis van de geselecteerde runperiode.'],['max_underwater_period','Langste underwater-periode','Langste drawdownperiode zoals Jesse rapporteert; eenheid volgt de Jesse-engine.'],['positive_months','Winstgevende maanden','Aantal maanden met positief resultaat volgens Jesse.'],['negative_months','Verliesgevende maanden','Aantal maanden met negatief resultaat volgens Jesse.']];
const derivedMetricCatalog=[['ulcer_index_close','Ulcer Index · tradeclose','Wortelgemiddelde van gekwadrateerde piek-tot-dalpercentages op gesloten trades. Floating drawdown ontbreekt.'],['pain_index_close','Pain Index · tradeclose','Gemiddelde piek-tot-dalpercentagediepte op gesloten trades; geen tijdgewogen of brokergevalideerde reeks.'],['time_underwater_close','Tijd onder water · tradeclose','Tijdsfractie onder eerdere equitypiek tussen trade-sluitingen. Floating equity ontbreekt.'],['longest_drawdown_days_close','Langste drawdown · tradeclose','Duur van een piek tot herstel, afgeleid van gesloten trade-P&L.'],['average_drawdown_days_close','Gemiddelde drawdownduur · tradeclose','Gemiddelde piek-tot-herstelduur van afgesloten drawdowns.'],['max_time_to_trough_days_close','Langste tijd naar dal · tradeclose','Langste tijd tussen equitypiek en daaropvolgend dieptepunt op sluitmomenten.'],['drawdown_frequency_close','Drawdownfrequentie · tradeclose','Aantal afzonderlijke dalingen onder een eerdere equitypiek.'],['rolling_12m_return_close','Rolling 12M return · tradeclose','Netto equityrendement over de laatste 365 dagen tussen trade-sluitingen; leeg bij minder dan één jaar data.'],['worst_month_close','Slechtste maand · voorlopig','Slechtste maandresultaat als percentage van startsaldo, gebaseerd op gesloten trades.'],['worst_year_close','Slechtste jaar · voorlopig','Slechtste jaarresultaat als percentage van startsaldo, gebaseerd op gesloten trades.']];
function computeTradeEquityStats(trades,balance,startTime=0){
  const asMs=value=>{const n=Number(value);return Number.isFinite(n)&&n>0?n:Date.parse(value)};
  if(!(balance>0))return null;
  const closes=new Map();
  for(const trade of trades||[]){const time=asMs(trade.closed_at??trade.closed_at_ms),pnl=Number(trade.PNL??trade.pnl);if(!Number.isFinite(time)||!Number.isFinite(pnl))continue;closes.set(time,(closes.get(time)||0)+pnl)}
  const ordered=[...closes.entries()].sort((a,b)=>a[0]-b[0]);if(!ordered.length)return null;
  const start=Number.isFinite(startTime)&&startTime>0&&startTime<=ordered[0][0]?startTime:ordered[0][0],points=[{x:start,y:balance,dd:0}],segments=[];let equity=balance,peak=balance,peakAt=start,drawdown=null,previous=start,underwaterMs=0,ddSquares=0,ddSum=0,ddSamples=0,maxDepth=0,currentDepth=0;
  for(const [time,pnl] of ordered){if(drawdown)underwaterMs+=Math.max(0,time-previous);equity+=pnl;const depth=Math.max(0,(peak-equity)/peak*100);points.push({x:time,y:equity,dd:depth});if(equity>=peak){if(drawdown){drawdown.end=time;segments.push(drawdown);drawdown=null}peak=equity;peakAt=time;currentDepth=0}else{if(!drawdown)drawdown={start:peakAt,trough:time,min:equity,end:null};if(equity<drawdown.min){drawdown.min=equity;drawdown.trough=time}currentDepth=depth;maxDepth=Math.max(maxDepth,depth)}ddSquares+=depth*depth;ddSum+=depth;ddSamples++;previous=time}
  const last=ordered.at(-1)[0];if(drawdown)segments.push({...drawdown,end:last});const durations=segments.map(s=>Math.max(0,s.end-s.start)),troughDurations=segments.map(s=>Math.max(0,s.trough-s.start));
  const startDate=points[0].x,rollingTarget=last-365*86400000;let rolling=null;if(rollingTarget>=startDate){let base=null;for(const p of points){if(p.x>rollingTarget)break;base=p}if(base&&base.y>0)rolling=(equity/base.y-1)*100}
  const bucket=new Map();for(const [time,pnl] of ordered){const d=new Date(time),month=d.toISOString().slice(0,7),year=String(d.getUTCFullYear());for(const key of [month,year])bucket.set(key,(bucket.get(key)||0)+pnl)}
  const months=[...bucket].filter(([key])=>key.length===7).map(([,pnl])=>pnl/balance*100),years=[...bucket].filter(([key])=>key.length===4).map(([,pnl])=>pnl/balance*100);
  return {points,maxDrawdownPct:maxDepth,painIndex:ddSamples?ddSum/ddSamples:null,ulcerIndex:ddSamples?Math.sqrt(ddSquares/ddSamples):null,timeUnderwaterPct:last>start?underwaterMs/(last-start)*100:null,longestDrawdownDays:durations.length?Math.max(...durations)/86400000:null,averageDrawdownDays:durations.length?durations.reduce((a,b)=>a+b,0)/durations.length/86400000:null,maxTimeToTroughDays:troughDurations.length?Math.max(...troughDurations)/86400000:null,drawdownFrequency:segments.length,currentDrawdownDays:currentDepth>0?Math.max(0,last-peakAt)/86400000:0,rolling12mReturn:rolling,worstMonth:months.length?Math.min(...months):null,worstYear:years.length?Math.min(...years):null};
}
function renderMetrics(m,trades=[],startTime=0){
  const root=$('metrics'),options=$('metric-options'),saved=JSON.parse(localStorage.getItem('strategyLabMetricSelection')||'null'),defaults=['total','net_profit','profit_factor','recovery_factor','max_drawdown','win_rate','calmar_ratio','sortino_ratio','expectancy'];
  const values={...m},risk=computeTradeEquityStats(trades,Number(m.starting_balance),startTime);if(risk)Object.assign(values,{ulcer_index_close:risk.ulcerIndex,pain_index_close:risk.painIndex,time_underwater_close:risk.timeUnderwaterPct,longest_drawdown_days_close:risk.longestDrawdownDays,average_drawdown_days_close:risk.averageDrawdownDays,max_time_to_trough_days_close:risk.maxTimeToTroughDays,drawdown_frequency_close:risk.drawdownFrequency,rolling_12m_return_close:risk.rolling12mReturn,worst_month_close:risk.worstMonth,worst_year_close:risk.worstYear});if(values.profit_factor==null&&Number.isFinite(Number(m.gross_profit))&&Number(m.gross_loss)<0)values.profit_factor=Number(m.gross_profit)/Math.abs(Number(m.gross_loss));if(values.recovery_factor==null&&Number.isFinite(Number(m.net_profit))&&Number(m.starting_balance)>0&&Number(m.max_drawdown)<0)values.recovery_factor=Number(m.net_profit)/(Number(m.starting_balance)*Math.abs(Number(m.max_drawdown))/100);
  const selected=new Set(Array.isArray(saved)?saved:defaults);options.replaceChildren();
  for(const [key,label] of [...metricCatalog,...derivedMetricCatalog]){const item=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.value=key;box.checked=selected.has(key);item.append(box,document.createTextNode(label));box.onchange=()=>{localStorage.setItem('strategyLabMetricSelection',JSON.stringify([...options.querySelectorAll('input:checked')].map(x=>x.value)));draw()};options.append(item)}
  const draw=()=>{document.querySelectorAll('.metric-tooltip').forEach(x=>x.remove());root.replaceChildren();for(const key of [...options.querySelectorAll('input:checked')].map(x=>x.value)){const spec=[...metricCatalog,...derivedMetricCatalog].find(x=>x[0]===key);if(!spec)continue;const [_,label,help]=spec,card=document.createElement('article'),title=document.createElement('span'),value=document.createElement('strong');card.className='metric';title.textContent=label;const info=makeInfo(help,label,true);info.bubble.classList.add('metric-tooltip');title.append(info.wrap);const raw=values[key],percent=['win_rate','max_drawdown','annual_return','time_underwater_close','rolling_12m_return_close','worst_month_close','worst_year_close'].includes(key);value.textContent=raw===null||raw===undefined?'—':percent?`${nice(raw,2)}%`:nice(raw,3);card.append(title,value);root.append(card)}};
  draw();
}
function computeTradeOverlap(trades){
  const events=[];let validTrades=0;
  const asMs=value=>{const numeric=Number(value);return Number.isFinite(numeric)&&numeric>0?numeric:Date.parse(value)};
  for(const trade of trades){const opened=asMs(trade.opened_at??trade.entry_timestamp_ms),closed=asMs(trade.closed_at??trade.closed_at_ms);if(!Number.isFinite(opened)||!Number.isFinite(closed)||closed<opened)continue;events.push({time:opened,delta:1},{time:closed,delta:-1});validTrades++}
  events.sort((a,b)=>a.time-b.time||a.delta-b.delta);if(!events.length)return {points:[],maxConcurrent:0,overlapMs:0,validTrades};
  let active=0,maxConcurrent=0,overlapMs=0,previous=null;const points=[{x:events[0].time,y:0}];
  for(let i=0;i<events.length;){const time=events[i].time;if(previous!==null&&active>1)overlapMs+=time-previous;let delta=0;while(i<events.length&&events[i].time===time)delta+=events[i++].delta;active=Math.max(0,active+delta);maxConcurrent=Math.max(maxConcurrent,active);points.push({x:time,y:active});previous=time}
  return {points,maxConcurrent,overlapMs,validTrades};
}
function renderPerformanceAnalytics(){purgePlots($('performance-analytics'));
  const root=$('performance-analytics');if(!root)return;root.querySelectorAll('.analytics-plot').forEach(x=>Plotly.purge(x));document.querySelectorAll('.analytics-tooltip').forEach(x=>x.remove());root.replaceChildren();
  const trades=state.runData?.trades||[],balance=Number(state.runManifest?.starting_balance||0);
  if(!trades.length||!(balance>0)){root.innerHTML='<p class="muted">Geen gesloten trades of startsaldo voor deze run.</p>';return}
  const wanted=new Set([...document.querySelectorAll('#analytics-options input:checked')].map(x=>x.value));
  const selectionKey='strategyLabAnalyticsSelectionV3',savedRaw=localStorage.getItem(selectionKey),legacyRaw=localStorage.getItem('strategyLabAnalyticsSelectionV2')||localStorage.getItem('strategyLabAnalyticsSelection'),saved=JSON.parse(savedRaw||legacyRaw||'null');if(Array.isArray(saved)){wanted.clear();saved.forEach(x=>wanted.add(x));if(!savedRaw){wanted.add('equity');wanted.add('drawdown')}document.querySelectorAll('#analytics-options input').forEach(x=>x.checked=wanted.has(x.value))}
  const groups={monthly:new Map(),yearly:new Map(),weekday:new Map(),session:new Map(),side:new Map()};
  const asMs=value=>{const numeric=Number(value);return Number.isFinite(numeric)&&numeric>0?numeric:Date.parse(value)};
  for(const trade of trades){const t=asMs(trade.closed_at??trade.closed_at_ms),pnl=Number(trade.PNL??trade.pnl);if(!Number.isFinite(t)||!Number.isFinite(pnl))continue;const d=new Date(t),entryMs=asMs(trade.opened_at??trade.entry_timestamp_ms),entry=new Date(Number.isFinite(entryMs)?entryMs:t),pct=pnl/balance*100,month=d.toISOString().slice(0,7),year=String(d.getUTCFullYear()),day=['Zo','Ma','Di','Wo','Do','Vr','Za'][entry.getUTCDay()],hour=entry.getUTCHours(),session=hour<6?'00–06':hour<12?'06–12':hour<18?'12–18':'18–24',side=String(trade.type||trade.side||'Onbekend').toLowerCase();for(const [key,value] of [['monthly',month],['yearly',year],['weekday',day],['session',session],['side',side]]){const map=groups[key],item=map.get(value)||{pnl:0,trades:0,wins:0};item.pnl+=pct;item.trades++;if(pnl>0)item.wins++;map.set(value,item)}}
  const specs=[['monthly','Maandresultaat','Gegroepeerd op sluitmaand (UTC).'],['yearly','Jaarresultaat','Gegroepeerd op sluitjaar (UTC).'],['weekday','Instapweekdag','Gegroepeerd op UTC-instapdag.'],['session','UTC-instapsessie','UTC 00–06, 06–12, 12–18 en 18–24; gegroepeerd op instaptijd.'],['side','Long versus short','Gegroepeerd op Jesse-traderichting.'],['overlap','Gelijktijdige open trades','Actieve posities op basis van opgeslagen open- en sluittijden; trades die exact tegelijk sluiten en openen tellen niet als overlap.'],['equity','Equity curve · tradeclose','Startsaldo plus gesloten trade-P&L op de sluitmomenten. Intratrade en broker-equity ontbreken.'],['drawdown','Underwater / drawdown · tradeclose','Piek-tot-dalpercentage op gesloten trade-momenten; floating drawdown ontbreekt.']];
  const dark=document.body.dataset.theme==='dark',theme=getComputedStyle(document.body),palette=dark?{paper:'#131f2e',plot:'#0c1623',text:'#c5d5e5',muted:'#99afc3',grid:'#26394d'}:{paper:theme.getPropertyValue('--theme-panel').trim(),plot:theme.getPropertyValue('--theme-plot').trim(),text:'#334155',muted:'#526779',grid:theme.getPropertyValue('--theme-edge').trim()};
  const overlap=computeTradeOverlap(trades),equity=computeTradeEquityStats(trades,balance,Date.parse(state.runManifest?.start_date)||0);
  for(const [key,title,help] of specs){if(!wanted.has(key))continue;const card=document.createElement('section');card.className='analytics-card';const head=document.createElement('div');head.className='section-head';const h=document.createElement('h3');h.textContent=title;const explanation=key==='overlap'?`${help} Maximaal gelijktijdig: ${overlap.maxConcurrent}; overlapduur: ${(overlap.overlapMs/60000).toFixed(1)} minuten; complete trades: ${overlap.validTrades}.`:(key==='equity'||key==='drawdown'?help:`${help} P&L is gedeeld door startsaldo en komt uit Jesse; dit is geen brokergevalideerde return.`);const info=makeInfo(explanation,title,true);info.bubble.classList.add('analytics-tooltip');h.append(info.wrap);head.append(h);card.append(head);const chart=document.createElement('div');chart.id=`analytics-${key}`;chart.className='analytics-plot';card.append(chart);root.append(card);if(key==='equity'||key==='drawdown'){const points=equity?.points||[],y=key==='equity'?points.map(p=>p.y):points.map(p=>p.dd);Plotly.react(chart,[{type:'scatter',mode:'lines',x:points.map(p=>new Date(p.x).toISOString()),y,name:key==='equity'?'Jesse-saldo':'Drawdown (%)',line:{color:key==='equity'?'#5ba6d8':'#ed8795',width:2},fill:key==='drawdown'?'tozeroy':'none',hovertemplate:key==='equity'?'%{x|%Y-%m-%d %H:%M UTC}<br>Saldo %{y:.3f}<extra></extra>':'%{x|%Y-%m-%d %H:%M UTC}<br>Drawdown %{y:.2f}%<extra></extra>'}],{paper_bgcolor:palette.paper,plot_bgcolor:palette.plot,font:{color:palette.muted,size:10},margin:{l:56,r:8,t:4,b:36},xaxis:{type:'date',gridcolor:palette.grid,title:'Sluitmoment (UTC)',tickformat:'%d %b\n%Y'},yaxis:{gridcolor:palette.grid,title:key==='equity'?'Jesse-punten':'Drawdown (%)',rangemode:key==='drawdown'?'tozero':'normal',ticksuffix:key==='drawdown'?'%':''},showlegend:false},{responsive:true,displaylogo:false});continue}if(key==='overlap'){Plotly.react(chart,[{type:'scatter',mode:'lines',x:overlap.points.map(p=>new Date(p.x).toISOString()),y:overlap.points.map(p=>p.y),name:'Open trades',line:{color:'#5ba6d8',shape:'hv',width:2},fill:'tozeroy',hovertemplate:'%{x|%Y-%m-%d %H:%M:%S UTC}<br>%{y} gelijktijdige open trades<extra></extra>'}],{paper_bgcolor:palette.paper,plot_bgcolor:palette.plot,font:{color:palette.muted,size:10},margin:{l:48,r:8,t:4,b:36},xaxis:{type:'date',gridcolor:palette.grid,title:'Tijd (UTC)',tickformat:'%d %b\n%H:%M'},yaxis:{gridcolor:palette.grid,title:'Open trades',rangemode:'tozero',dtick:1},showlegend:false},{responsive:true,displaylogo:false});continue}const order=key==='weekday'?['Ma','Di','Wo','Do','Vr','Za','Zo']:key==='session'?['00–06','06–12','12–18','18–24']:null,entries=[...groups[key].entries()].sort((a,b)=>order?order.indexOf(a[0])-order.indexOf(b[0]):a[0].localeCompare(b[0]));Plotly.react(chart,[{type:'bar',x:entries.map(x=>x[0]),y:entries.map(x=>x[1].pnl),name:'Jesse P&L / startsaldo',marker:{color:entries.map(x=>x[1].pnl>=0?'#59c995':'#ed8795')},customdata:entries.map(x=>[x[1].trades,x[1].wins,x[1].trades?100*x[1].wins/x[1].trades:0]),hovertemplate:'%{x}<br>%{y:.3f}% saldo<br>trades %{customdata[0]} · winrate %{customdata[2]:.1f}%<extra></extra>'}],{paper_bgcolor:palette.paper,plot_bgcolor:palette.plot,font:{color:palette.muted,size:10},margin:{l:48,r:8,t:4,b:36},xaxis:{gridcolor:palette.grid},yaxis:{gridcolor:palette.grid,title:'Netto P&L (% startsaldo)'},showlegend:false},{responsive:true,displaylogo:false})}
}
function renderTrades(trades){
  const body=$('trades');body.innerHTML='';trades.forEach((t,i)=>{const tr=document.createElement('tr');tr.className='trade-row';const opened=Number(t.opened_at||t.entry_timestamp_ms||0);tr.innerHTML=`<td>${i+1}</td><td>${/short|sell/i.test(t.type||t.side||'')?'Short':'Long'}</td><td>${opened?new Date(opened).toISOString().slice(0,16).replace('T',' '):''}</td><td>${nice(t.entry_price,4)}</td><td>${nice(t.exit_price,4)}</td><td>${nice(t.PNL??t.pnl,3)}</td><td>${nice(t.mfe_price,4)}</td><td>${nice(t.mae_price,4)}</td><td><button class="secondary">Toon</button></td>`;tr.querySelector('button').onclick=e=>{e.stopPropagation();selectTrade(t,i,tr)};tr.onclick=()=>selectTrade(t,i,tr);tr.tabIndex=0;tr.onkeydown=e=>{if(e.key==='Enter')selectTrade(t,i,tr)};body.appendChild(tr);});
}
async function jumpTrade(trade){
  const ev=state.runData?.events||[];const id=String(trade.id||trade.trade_id||'');
  const openedAt=Number(trade.opened_at||trade.entry_timestamp_ms||0);
  const entryOrders=(trade.orders||[]).filter(o=>o.id&&o.reduce_only!==true&&(!Number(o.executed_at||o.created_at)||Number(o.executed_at||o.created_at)<=openedAt));
  const entryOrder=entryOrders.sort((a,b)=>Number(a.executed_at||a.created_at||0)-Number(b.executed_at||b.created_at||0))[0];
  let event=entryOrder?ev.find(e=>(e.event_type||e.event)==='POSITION_OPEN'&&String(e.order_id||'')===String(entryOrder.id)):null;
  if(!event)event=ev.find(e=>String(e.trade_id||'')===id&&e.signal_timestamp_ms);
  if(!event)event=ev.find(e=>(e.event_type||e.event)==='POSITION_OPEN'&&Number(e.fill_timestamp_ms||e.timestamp_ms)===openedAt)||[...ev].filter(e=>(e.event_type||e.event)==='ORDER_SUBMITTED'&&Number(e.signal_timestamp_ms)<=openedAt).pop();
  const cursor=Number(event?.signal_timestamp_ms||entryOrder?.created_at||openedAt);if(!Number.isFinite(cursor)||cursor<=0)return;
  const session=state.replaySession??await startReplay();
  if(session?.candles&&state.replaySession===session)seekReplay(session,replayIndexAt(session.candles,cursor));
}
function updateReplaySpeed(){
  const slider=$('replay-speed'),index=Math.max(0,Math.min(replaySpeeds.length-1,Number(slider.value)||0)),speed=replaySpeeds[index];
  slider.value=String(index);slider.setAttribute('aria-valuetext',`x${speed}`);slider.style.setProperty('--speed-position',`${index/(replaySpeeds.length-1)*100}%`);$('replay-speed-value').textContent=`x${speed}`;
  return speed;
}
function replayIndexAt(candles,cursor){
  let left=0,right=candles.length;
  while(left<right){
    const middle=Math.floor((left+right)/2);
    if(candles[middle].timestamp_ms<=cursor)left=middle+1;
    else right=middle;
  }
  return Math.max(0,left-1);
}
function clearReplayInspection(){
  state.inspectRequest=(state.inspectRequest||0)+1;
  state.selectedEvents=[];state.selectedSnapshot=null;
  $('selected-candle').textContent='Replaycursor wordt bijgewerkt…';
  $('candle-events').replaceChildren();
  $('causal-replay-result').textContent='';
  $('causal-replay-run').disabled=true;
}
function stopReplay(){
  const session=state.replaySession;
  if(!session)return null;
  clearTimeout(session.timer);
  clearTimeout(session.paintTimer);
  cancelAnimationFrame(session.frame);
  state.replaySession=null;state.replay=false;
  $('chart').style.visibility='';
  $('replay-toggle').textContent='Replay starten';
  $('replay-slider').disabled=true;
  $('replay-time').textContent='Replay staat uit.';
  return session;
}
async function toggleReplay(){
  const previous=stopReplay();
  if(!previous||previous.ended)return startReplay();
  await previous.draw;
  if(!state.replaySession)await loadChart();
}
async function startReplay(index=0){
  if(!state.candles.length){
    $('replay-time').textContent='Laad eerst een periode met candles.';
    return null;
  }
  const session={query:state.chartQuery,run:state.activeRun,index:0,ema:[],lastInspection:-Infinity,dirty:false};
  state.replaySession=session;state.replay=true;
  $('chart').style.visibility='hidden';
  clearReplayInspection();
  $('replay-toggle').textContent='Replay stoppen';
  $('replay-time').textContent='Replay wordt voorbereid…';
  try{
    const [candles,events]=await Promise.all([
      state.candles.length===state.chartAvailableRows?state.candles:api(`/api/candles?${session.query}&limit=0`).then(data=>data.candles),
      session.run?api(`/api/runs/${session.run}/replay-exits`).then(data=>data.events):[],
    ]);
    if(state.replaySession!==session)return null;
    if(!candles.length)throw new Error('Geen candles in deze periode.');
    session.candles=candles;session.events=events;
    session.padding=candleHalfWidths[session.query.get('timeframe')]||30000;
    const slider=$('replay-slider');
    slider.min='0';slider.max=String(candles.length-1);slider.step='1';slider.disabled=false;
    seekReplay(session,index);
    return session;
  }catch(error){
    if(state.replaySession===session){stopReplay();$('replay-time').textContent='Replay fout: '+error.message}
    return null;
  }
}
function sliderReplay(){
  const session=state.replaySession;
  if(session?.candles)seekReplay(session,Number($('replay-slider').value));
}
function stepReplay(direction){
  const session=state.replaySession;
  if(!session)startReplay(Math.max(0,direction));
  else if(session.candles)seekReplay(session,session.index+direction);
}
function seekReplay(session,index){
  if(index<session.index)clearReplayInspection();
  session.index=Math.max(0,Math.min(index,session.candles.length-1));
  session.lastInspection=-Infinity;
  queueReplayDraw(session);
  restartReplayClock(session);
}
function restartReplayClock(session){
  clearTimeout(session.timer);
  if(session.index===session.candles.length-1){
    session.ended=true;
    $('replay-toggle').textContent='Replay herstarten';
    queueReplayDraw(session);
    return;
  }
  session.ended=false;
  $('replay-toggle').textContent='Replay stoppen';
  session.speed=replaySpeeds[Number($('replay-speed').value)]||1;
  const interval=1000/session.speed;
  let nextTick=performance.now()+interval;
  const tick=()=>{
    if(state.replaySession!==session)return;
    session.index++;
    if(session.index===session.candles.length-1){restartReplayClock(session);return}
    queueReplayDraw(session);
    nextTick+=interval;
    session.timer=setTimeout(tick,Math.max(0,nextTick-performance.now()));
  };
  session.timer=setTimeout(tick,interval);
}
function queueReplayDraw(session){
  session.dirty=true;
  if(session.frame||session.draw||session.paintTimer)return;
  // Let the one-candle clock run between expensive Plotly updates at high speeds.
  const wait=session.speed>=50?32-(performance.now()-session.lastPaint):0;
  if(wait>0){
    session.paintTimer=setTimeout(()=>{session.paintTimer=null;queueReplayDraw(session)},wait);
    return;
  }
  session.frame=requestAnimationFrame(()=>{
    session.frame=null;
    if(state.replaySession!==session)return;
    session.dirty=false;
    const end=session.index+1,limit=Number($('chart-limit').value);
    const start=limit?Math.max(0,end-limit):0;
    const period=Math.max(2,Number(currentParams().ema_period)||20);
    if(period!==session.emaPeriod){session.emaPeriod=period;session.ema=[]}
    const alpha=2/(period+1);
    for(let i=session.ema.length;i<end;i++){
      const close=session.candles[i].bid_close;
      session.ema.push(i?alpha*close+(1-alpha)*session.ema[i-1]:close);
    }
    const rows=session.candles.slice(start,end),cursor=rows.at(-1).timestamp_ms;
    const slider=$('replay-slider');
    slider.value=String(end-1);
    slider.setAttribute('aria-valuetext',new Date(cursor).toISOString());
    $('replay-time').textContent=`Cursor ${new Date(cursor).toISOString()} · ${rows.length} candles zichtbaar${session.ended?' · einde bereikt':''}`;
    session.draw=Promise.resolve().then(()=>renderChart(rows,session.events,cursor,{
      ema:session.ema.slice(start,end),rolling:start>0,padding:session.padding,
    })).then(()=>{
      if(state.replaySession===session)$('chart').style.visibility='';
    }).catch(error=>{
      if(state.replaySession===session){stopReplay();$('replay-time').textContent='Replay fout: '+error.message}
    }).finally(()=>{
      session.lastPaint=performance.now();
      session.draw=null;
      if(state.replaySession===session&&session.dirty)queueReplayDraw(session);
    });
    if(!session.inspectPending&&(session.ended||performance.now()-session.lastInspection>=250)){
      session.lastInspection=performance.now();
      session.inspectPending=inspect(cursor,session).finally(()=>{session.inspectPending=null});
    }
  });
}

async function savePreset(){try{const item={name:$('preset-name').value,strategy:$('strategy').value,symbol:$('symbol').value,timeframe:$('timeframe').value,parameters:currentParams(),config:{balance:$('balance').value,fee:$('fee').value,account_type:$('account-type').value,leverage:$('leverage').value,warmup:$('warmup').value}};await api('/api/presets',{method:'POST',body:JSON.stringify(item)});await loadPresets();status('Preset lokaal opgeslagen.');}catch(e){status(e.message,'error')}}
async function loadPresets(){try{const data=await api('/api/presets');$('presets').innerHTML='<option value="">Kies preset</option>';data.presets.forEach(p=>{const o=document.createElement('option');o.value=p.name;o.textContent=p.name;$('presets').appendChild(o)});}catch{}}
async function loadPreset(){const name=$('presets').value;if(!name)return;const p=(await api('/api/presets')).presets.find(x=>x.name===name);if(!p)return;$('strategy').value=p.strategy;$('symbol').value=p.symbol;$('timeframe').value=p.timeframe;buildParams(selectedStrategy()?.parameters||[],p.parameters);if(p.config){$('balance').value=p.config.balance||10000;$('fee').value=p.config.fee||0;$('account-type').value=p.config.account_type||'spot';$('leverage').value=p.config.leverage||1;$('warmup').value=p.config.warmup||210;}await loadMeta();await loadChart();}
async function loadSource(){const name=$('strategy').value;if(!name)return;try{const data=await api(`/api/strategies/${name}/source`);$('new-strategy-name').value=name+'LabCopy';$('strategy-source').value=data.source;}catch(e){status(e.message,'error')}}
async function saveStrategy(){try{const name=$('new-strategy-name').value.trim();const data=await api('/api/strategies',{method:'POST',body:JSON.stringify({name,source:$('strategy-source').value})});status(`Strategie ${data.name} opgeslagen · ${data.sha256.slice(0,12)}…`);state.strategies=(await api('/api/strategies')).strategies;$('strategy').innerHTML='';state.strategies.forEach(s=>{const o=document.createElement('option');o.value=s.name;o.textContent=s.name+(s.managed_by_lab?' · Lab':'');$('strategy').appendChild(o)});$('strategy').value=data.name;buildParams(data.parameters);}catch(e){status(e.message,'error')}}
async function compareRuns(){if(!$('compare-a').value||!$('compare-b').value)return;try{const data=await api(`/api/metrics/compare?first=${$('compare-a').value}&second=${$('compare-b').value}`);renderComparison(data);activateResultsTab('comparison');}catch(e){$('comparison').textContent=e.message;activateResultsTab('comparison')}}
async function runLedger(){if(!state.activeRun)return status('Selecteer eerst een backtest.','error');const body={account_currency:$('account-currency').value,contract_size:Number($('contract-size').value),tick_size:Number($('tick-size').value),quote_to_account:Number($('conversion').value),volume_step:Number($('volume-step').value),commission_per_lot_side:Number($('commission').value),slippage_points:Number($('slippage').value)};if(!body.account_currency||Object.values(body).slice(1,5).some(v=>!Number.isFinite(v)||v<=0))return $('ledger-result').innerHTML='<span class="error-text">Vul expliciete accountvaluta, contractgrootte, tick size, conversie en lot step in.</span>';try{const data=await api(`/api/runs/${state.activeRun}/bidask-ledger`,{method:'POST',body:JSON.stringify(body)});const repr=data.timestamp_reprice||{},ind=data.independent_model||{};const validated=data.status==='EXECUTION_VALIDATED'&&data.contract_spec_status==='BROKER_VERIFIED'&&data.strategy_signals_recomputed===true&&ind.broker_contract_verified===true;const invalidLots=(repr.trades||[]).filter(t=>t.volume_step_valid===false).length;const amount=value=>validated?`${nice(value,2)} ${escapeHtml(body.account_currency)}`:'verborgen · model niet gevalideerd';const details=(repr.trades||[]).slice(0,8).map(t=>{const row={...t};if(!validated)for(const key of ['gross_pnl','commission','net_pnl','tick_value','jesse_pnl_provisional'])delete row[key];return row;});const lotWarning=invalidLots?` ${invalidLots}/${repr.trades?.length||0} volumes schenden lotstap ${body.volume_step}; bedragen verborgen.`:'';$('ledger-result').innerHTML=`<b>${escapeHtml(repr.status||'JESSE_RESEARCH_PROVISIONAL')} · timestamp-herwaardering · netto ${amount(repr.net_pnl)} · ${repr.trades?.length||0} trades</b><br><b>${escapeHtml(ind.status||'DUKASCOPY_BIDASK_REPLAY')} · ${amount(ind.net_profit)} · ${ind.trades?.length||0} trades · ${ind.trades?.filter(t=>t.intrabar_ambiguous).length||0} intrabar-ambiguïteiten</b><p class="muted">${escapeHtml(ind.scope||data.note||'') } Quote-kwaliteit: ${escapeHtml(ind.quote_quality||'onbekend')}. Contract gecontroleerd: ${ind.broker_contract_verified?'ja':'nee'}. Signalen opnieuw berekend: ${ind.strategy_signals_recomputed?'ja':'nee'}.${escapeHtml(lotWarning)}</p><pre>${escapeHtml(JSON.stringify({timestamp_reprice:details,signal_reexecution:{model:ind.signal_model,decision_counts:ind.signal_decision_counts,saved_matches:ind.saved_decision_matches,saved_mismatches:ind.saved_decision_mismatches,mismatch_examples:ind.signal_mismatch_examples||[]},independent_warnings:ind.warnings||[]},null,2))}</pre>`;}catch(e){$('ledger-result').innerHTML=`<span class="error-text">${escapeHtml(e.message)}</span>`}}

function applySettingLabels(){
  const labels=window.SETTING_LABELS||{},opts=window.OPTION_LABELS||{};
  for(const [id,meta] of Object.entries(labels)){
    const el=$(id);if(!el)continue;const label=el.closest('label');if(!label||label.closest('.toolbar'))continue;
    let span=label.querySelector(':scope>span');
    if(!span){span=document.createElement('span');[...label.childNodes].filter(n=>n.nodeType===3&&n.textContent.trim()).forEach(n=>n.remove());label.prepend(span)}
    const tip=[meta.help,meta.suggest?`Voorstel: ${meta.suggest}.`:'',meta.example?`Effect: ${meta.example}`:''].filter(Boolean).join(' ');
    span.className='setting-title';span.innerHTML='';span.append(document.createTextNode((meta.label||id)+(meta.unit&&!/\(/.test(meta.label||'')?` (${meta.unit})`:'')+' '));
    if(tip){const b=document.createElement('button');b.type='button';b.className='info';b.setAttribute('aria-label','Uitleg');b.dataset.tip=tip;b.textContent='i';span.append(b)}
    if(meta.simple===false)label.classList.add('advanced-only');
  }
  for(const [id,map] of Object.entries(opts)){const el=$(id);if(!el)continue;for(const o of el.options)if(map[o.value])o.textContent=map[o.value]}
}
async function renderInterpretation(id,manifest){
  const root=$('interpretation'),banner=$('evidence-banner');if(!root)return;
  root.innerHTML='<p class="muted">Interpretatie berekenen…</p>';
  try{
    const r=await api(`/api/runs/${encodeURIComponent(id)}/interpretation`);if(state.activeRun!==id)return;
    const cls={HISTORICAL_BACKTEST:'Historische backtest',HISTORICAL_OOS:'Historische out-of-sample'}[r.evidence_class]||r.evidence_class;
    banner.hidden=false;banner.dataset.kind=r.evidence_class||'';
    banner.innerHTML=`<b>${escapeHtml(cls)}</b><span>${escapeHtml(r.evidence_label_nl||'')}</span><button class="info" type="button" aria-label="Uitleg" data-tip="${escapeHtml('Vier soorten bewijs, van zwak naar sterk: 1) historische backtest (op data die je kon zien), 2) historische out-of-sample (oude data die bij het kiezen niet is gebruikt), 3) dagelijks vertraagde forwardtest (alleen nieuwe dagen na bevriezen), 4) realtime papertrading (niet beschikbaar in dit lab). '+(r.warning_nl||''))}">i</button>`;
    const icon={good:'✓',warn:'!',bad:'✕',info:'i'};
    root.innerHTML=`<p class="interp-headline">${escapeHtml(r.headline_nl||'')}</p>`+(r.sections||[]).map(x=>`<details class="interp-item ${escapeHtml(x.status||'info')}" ${x.status==='bad'?'open':''}><summary><span class="interp-icon" aria-hidden="true">${icon[x.status]||'i'}</span>${escapeHtml(x.title_nl||x.key)}</summary><p>${escapeHtml(x.text_nl||'')}</p></details>`).join('')+((r.next_steps_nl||[]).length?`<div class="interp-next"><b>Volgende stappen</b><ul>${r.next_steps_nl.map(t=>`<li>${escapeHtml(t)}</li>`).join('')}</ul></div>`:'');
  }catch(e){if(state.activeRun===id){root.innerHTML=`<p class="muted">Geen interpretatie beschikbaar: ${escapeHtml(e.message)}</p>`;banner.hidden=true}}
}
function tradeLevels(t){
  const orders=(t.orders||[]).filter(o=>o.reduce_only===true&&Number.isFinite(Number(o.price)));
  const pick=re=>orders.filter(o=>re.test(String(o.submitted_via||''))||(re.source.includes('stop')?o.type==='STOP':o.type==='LIMIT')).sort((a,b)=>Number(a.created_at||0)-Number(b.created_at||0));
  const sl=pick(/stop/i),tp=pick(/take|profit/i);
  return {sl:sl.length?Number(sl.at(-1).price):null,slFirst:sl.length?Number(sl[0].price):null,tp:tp.length?Number(tp.at(-1).price):null,slHit:sl.some(o=>o.status==='EXECUTED'),tpHit:tp.some(o=>o.status==='EXECUTED')};
}
function selectedTradeShapes(){
  const t=state.selectedTrade;if(!t)return[];
  const x0=new Date(Number(t.opened_at||t.entry_timestamp_ms)).toISOString(),x1=new Date(Number(t.closed_at||t.closed_at_ms||t.opened_at)).toISOString(),lv=tradeLevels(t),out=[];
  out.push({type:'rect',xref:'x',yref:'paper',x0,x1,y0:0,y1:1,fillcolor:'rgba(120,160,255,.08)',line:{width:0},layer:'below'});
  if(lv.sl!==null)out.push({type:'line',x0,x1,y0:lv.sl,y1:lv.sl,line:{color:'#ff7878',width:2,dash:'dash'}});
  if(lv.tp!==null)out.push({type:'line',x0,x1,y0:lv.tp,y1:lv.tp,line:{color:'#42cf8f',width:2,dash:'dash'}});
  out.push({type:'line',x0,x1,y0:Number(t.entry_price),y1:Number(t.entry_price),line:{color:'#e9c46a',width:1.5}});
  return out;
}
function selectedTradeAnnotations(){
  const t=state.selectedTrade;if(!t)return[];
  const x1=new Date(Number(t.closed_at||t.closed_at_ms||t.opened_at)).toISOString(),lv=tradeLevels(t),a=[];
  const note=(y,text,color)=>a.push({x:x1,y,text,showarrow:false,xanchor:'left',font:{color,size:11},bgcolor:'rgba(0,0,0,.35)'});
  if(lv.sl!==null)note(lv.sl,` SL ${nice(lv.sl,5)}`,'#ff9a9a');if(lv.tp!==null)note(lv.tp,` TP ${nice(lv.tp,5)}`,'#7ee2b0');note(Number(t.entry_price),` Entry ${nice(t.entry_price,5)}`,'#e9c46a');
  return a;
}
async function explainTrade(t,opened,box){
  const ask=ms=>{const q=new URLSearchParams({symbol:$('symbol').value,timeframe:$('timeframe').value,timestamp_ms:String(ms)});if(state.activeRun)q.set('run_id',state.activeRun);return api('/api/inspect?'+q)};
  try{
    const atFill=await ask(opened);if(state.selectedTrade!==t)return;
    const open=(atFill.events||[]).find(e=>e.event_type==='POSITION_OPEN'&&String(e.trade_id||'')===String(t.id))||(atFill.events||[]).find(e=>e.event_type==='POSITION_OPEN');
    const signal=Number(open?.signal_timestamp_ms||0);
    const atSignal=signal?await ask(signal):atFill;if(state.selectedTrade!==t)return;
    const dec=(atSignal.events||[]).find(e=>e.event_type==='CANDLE_DECISION'&&e.decision==='PASS')||(atSignal.events||[]).find(e=>e.event_type==='CANDLE_DECISION');
    const why=box.querySelector('.td-why');if(!why)return;
    if(!dec){inspect(opened);why.innerHTML='<b>Waarom:</b> Deze strategie heeft voor deze trade geen beslisgegevens opgeslagen; er wordt niets achteraf verzonnen.';return}
    const checks=(dec.checks||[]).map(c=>`<li><span class="chip ${/PASS|TRUE/i.test(c.status||c.result||'')?'good':/FAIL|FALSE/i.test(c.status||c.result||'')?'bad':'idle'}">${escapeHtml(c.status||c.result||'')}</span> ${escapeHtml(c.name||c.condition||'Voorwaarde')} ${c.value!==undefined?`<small>${escapeHtml(JSON.stringify(c.value??c.actual))} ${escapeHtml(c.operator||'')} ${escapeHtml(JSON.stringify(c.threshold??''))}</small>`:''}</li>`).join('');
    const inds=(dec.indicators||[]).map(x=>`${escapeHtml(x.name)} ${typeof x.value==='number'?nice(x.value,5):escapeHtml(String(x.value))}`).join(' · ');
    why.innerHTML=`<b>Waarom:</b> signaalcandle ${escapeHtml(dec.timestamp_utc||new Date(Number(dec.timestamp_ms)).toISOString())} · ${escapeHtml(dec.reason||dec.decision||'')}${inds?` · ${inds}`:''}${checks?`<ul class="td-checks">${checks}</ul>`:''}`;
    box.querySelector('[data-td="log"]').onclick=()=>{inspect(Number(dec.timestamp_ms));showDockTab('inspector')};
    inspect(Number(dec.timestamp_ms));
  }catch(e){const why=box.querySelector('.td-why');if(why)why.innerHTML=`<b>Waarom:</b> beslisgegevens konden niet worden geladen (${escapeHtml(e.message)}).`}
}
async function selectTrade(t,i,row){
  state.selectedTrade=t;document.querySelectorAll('#trades tr').forEach(r=>r.classList.toggle('selected',r===row));
  const lv=tradeLevels(t),side=/short|sell/i.test(t.type||t.side||'')?'Short':'Long',opened=Number(t.opened_at||0),closed=Number(t.closed_at||0);
  const ev=(state.runData?.events||[]).filter(e=>String(e.trade_id||'')===String(t.id)||(Number(e.timestamp_ms)<=opened&&Number(e.timestamp_ms)>=opened-3*(candleHalfWidths[$('timeframe').value]||30000)*2&&(e.event_type==='CANDLE_DECISION'||e.event_type==='ORDER_SUBMITTED')));
  const decision=ev.filter(e=>e.event_type==='CANDLE_DECISION'||e.reason).pop();
  const why='beslisgegevens laden…';
  const exitWhy=lv.tpHit?'take profit geraakt':lv.slHit?'stop loss geraakt':'gesloten (reden niet vastgelegd)';
  const d=$('trade-detail');d.hidden=false;
  d.innerHTML=`<div class="td-grid"><div><span class="td-k">Trade</span><b>#${i+1} ${side}</b></div><div><span class="td-k">Entry</span><b>${nice(t.entry_price,5)}</b><small>${opened?new Date(opened).toISOString().slice(0,16).replace('T',' '):''}</small></div><div><span class="td-k">Exit</span><b>${nice(t.exit_price,5)}</b><small>${closed?new Date(closed).toISOString().slice(0,16).replace('T',' '):''} · ${exitWhy}</small></div><div><span class="td-k">Stop loss</span><b class="neg">${lv.sl===null?'geen':nice(lv.sl,5)}</b>${lv.slFirst!==null&&lv.slFirst!==lv.sl?`<small>eerst ${nice(lv.slFirst,5)}, later verplaatst</small>`:''}</div><div><span class="td-k">Take profit</span><b class="pos">${lv.tp===null?'geen':nice(lv.tp,5)}</b></div><div><span class="td-k">Resultaat</span><b class="${Number(t.PNL??t.pnl)>=0?'pos':'neg'}">${nice(t.PNL??t.pnl,2)}</b><small>na kosten ${nice(t.fee,2)}</small></div></div><p class="td-why"><b>Waarom:</b> ${why}</p><div class="inline"><button type="button" class="secondary" data-td="replay">Afspelen vanaf signaal</button><button type="button" class="secondary" data-td="log">Beslislogboek openen</button></div>`;
  d.querySelector('[data-td="replay"]').onclick=()=>jumpTrade(t);
  d.querySelector('[data-td="log"]').onclick=()=>{inspect(Number(decision?.timestamp_ms||opened));showDockTab('inspector')};
  if(state.candles?.length&&!state.replaySession){renderChart(state.visibleCandles||state.candles);const pad=Math.max((closed-opened)*1.5,30*(candleHalfWidths[$('timeframe').value]||30000)*2);try{Plotly.relayout('chart',{'xaxis.range':[new Date(opened-pad).toISOString(),new Date((closed||opened)+pad).toISOString()],'yaxis.autorange':true})}catch{}}
  explainTrade(t,opened,d);
  document.dispatchEvent(new CustomEvent('lab:trade-selected',{detail:{trade:t,runId:state.activeRun}}));
}
function renderComparison(data){
  const root=$('comparison');const a=data.first||data.a||{},b=data.second||data.b||{};const ma=a.metrics||a,mb=b.metrics||b;
  const keys=[...new Set([...Object.keys(ma||{}),...Object.keys(mb||{})])].filter(k=>typeof (ma?.[k]??mb?.[k])==='number');
  if(!keys.length){root.innerHTML=`<pre>${escapeHtml(JSON.stringify(data,null,2))}</pre>`;return}
  root.innerHTML=`<table class="tbl"><thead><tr><th>Statistiek</th><th class="num">Run A</th><th class="num">Run B</th><th class="num">Verschil</th></tr></thead><tbody>${keys.map(k=>`<tr><td>${escapeHtml(k)}</td><td class="num">${nice(ma?.[k],3)}</td><td class="num">${nice(mb?.[k],3)}</td><td class="num">${Number.isFinite(mb?.[k]-ma?.[k])?nice(mb[k]-ma[k],3):'—'}</td></tr>`).join('')}</tbody></table>`;
}
window.labTester={focus(profile,dock,sub){
  if(!state.layoutReady){state.pendingFocus=[profile,dock,sub];return}
  if(profile&&layoutPresets[profile]&&localStorage.getItem('strategyLabLayoutPresetV1')!==profile){$('layout-profile').value=profile;activateLayoutProfile(profile,{scroll:false})}
  if(dock)showDockTab(dock);
  setTimeout(()=>{if(state.chartStale&&$('chart').offsetParent&&state.candles?.length){state.chartStale=false;renderChart(state.candles)}},150);
  if(sub==='trades'||sub==='journaal')requestAnimationFrame(()=>document.querySelector('[data-panel="dock"]')?.scrollIntoView({block:'start',behavior:'smooth'}));
  else if(sub)window.scrollTo({top:0});
},showDockTab,reloadStrategies:async(select)=>{const st=await api('/api/strategies');state.strategies=st.strategies;const s=$('strategy'),cur=select||s.value;s.innerHTML='';st.strategies.forEach(x=>{const o=document.createElement('option');o.value=x.name;o.textContent=x.name+(x.managed_by_lab?' · Lab':'');s.appendChild(o)});if([...s.options].some(o=>o.value===cur))s.value=cur;s.dispatchEvent(new Event('change'))}};

init().catch(e=>{ $('health').textContent='App-start fout';status(e.message,'error'); });
