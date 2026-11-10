import sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()

def rep(old, new, count=1):
    global s
    assert s.count(old) >= 1, old[:80]
    s = s.replace(old, new, count)

def swap(start, end, new):
    global s
    a = s.index(start); b = s.index(end, a)
    s = s[:a] + new + s[b:]

# 1. layouts: new panel set, per-profile storage
swap('const baseLayout={', 'function migrateToolbarHeight(layout){', '''const baseLayout={panels:['market-controls','settings','chart','results','dock','optimization-settings'],sizes:{'market-controls':{columns:12,rows:2},settings:{columns:3,rows:9},chart:{columns:6,rows:9},results:{columns:3,rows:9},dock:{columns:12,rows:7},'optimization-settings':{columns:4,rows:9}},positions:{'market-controls':{column:1,row:1},settings:{column:1,row:3},chart:{column:4,row:3},results:{column:10,row:3},dock:{column:1,row:12},'optimization-settings':{column:4,row:3}},hidden:['optimization-settings']};
const MAX_GRID_ROW=40;
const LAYOUT_KEY='strategyLabLayoutV3:';
const layoutPresets={
  default:baseLayout,
  grafiek:{...baseLayout,sizes:{...baseLayout.sizes,chart:{columns:9,rows:11},results:{columns:3,rows:11}},positions:{...baseLayout.positions,chart:{column:1,row:3},results:{column:10,row:3},dock:{column:1,row:14}},hidden:['settings','optimization-settings']},
  optimization:{...baseLayout,sizes:{...baseLayout.sizes,settings:{columns:3,rows:9},'optimization-settings':{columns:5,rows:9},results:{columns:4,rows:9}},positions:{...baseLayout.positions,settings:{column:1,row:3},'optimization-settings':{column:4,row:3},results:{column:9,row:3},dock:{column:1,row:12}},hidden:['chart']}
};
''')

# 2. prepareDashboardWidgets: toolbar + optimization widget only
swap('function prepareDashboardWidgets(){', 'const widgetNames=', '''function prepareDashboardWidgets(){
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
  if(name==='analytics')requestAnimationFrame(()=>document.querySelectorAll('.analytics-plot').forEach(x=>{try{Plotly.Plots.resize(x)}catch{}}));
}
''')
rep("const widgetNames={'market-controls':'Markt, run en acties',settings:'Instellingen',chart:'Grafiek',inspector:'Entry Inspector',results:'Resultaten',trades:'Trades',analytics:'Analyse','optimization-settings':'Optimalisatie-instellingen','optimization-results':'Optimalisatieresultaten',portfolio:'Portfolio',comparison:'Vergelijking',logs:'Journaal'};",
    "const widgetNames={'market-controls':'Strategie, markt en periode',settings:'Instellingen',chart:'Grafiek',results:'Resultaten',dock:'Trades, beslislogboek en journaal','optimization-settings':'Optimalisatie-instellingen'};")

# 3. layout persistence per profile
swap("  const saved=JSON.parse(localStorage.getItem('strategyLabWidgetLayoutV2')||'null'),activeProfile=localStorage.getItem('strategyLabLayoutPresetV1');",
     "\n}", '''  const activeProfile=localStorage.getItem('strategyLabLayoutPresetV1')||'default';
  const saved=JSON.parse(localStorage.getItem(LAYOUT_KEY+activeProfile)||'null');
  applyLayout(saved||layoutPresets[activeProfile]||layoutPresets.default);
  localStorage.setItem('strategyLabLayoutPresetV1',activeProfile);syncWindowMenu();refreshProfiles(activeProfile);''')
rep("function persistLayout(){const layout=readLayout();localStorage.setItem('strategyLabWidgetLayoutV2',JSON.stringify(layout));return layout}",
    "function persistLayout(){const layout=readLayout();localStorage.setItem(LAYOUT_KEY+(localStorage.getItem('strategyLabLayoutPresetV1')||'default'),JSON.stringify(layout));return layout}")
rep("async function activateLayoutProfile(key){if(layoutPresets[key]){applyLayout(layoutPresets[key]);",
    "async function activateLayoutProfile(key,{reset=false,scroll=true}={}){if(layoutPresets[key]){const own=!reset&&JSON.parse(localStorage.getItem(LAYOUT_KEY+key)||'null');applyLayout(own||layoutPresets[key]);")
rep("localStorage.setItem('strategyLabLayoutPresetV1',key);persistLayout();window.scrollTo({top:0,behavior:'smooth'});return}try{",
    "localStorage.setItem('strategyLabLayoutPresetV1',key);persistLayout();if(scroll)window.scrollTo({top:0,behavior:'smooth'});return}try{")
rep("$('restore-layout').onclick=()=>activateLayoutProfile('default');",
    "$('restore-layout').onclick=()=>activateLayoutProfile(layoutPresets[localStorage.getItem('strategyLabLayoutPresetV1')]?localStorage.getItem('strategyLabLayoutPresetV1'):'default',{reset:true});")
rep("localStorage.setItem('strategyLabWidgetLayoutV2',JSON.stringify(layout));localStorage.setItem('strategyLabLayoutPresetV1',name);",
    "localStorage.setItem(LAYOUT_KEY+name,JSON.stringify(layout));localStorage.setItem('strategyLabLayoutPresetV1',name);")
rep("s.innerHTML='<option value=\"default\">Standaard</option><option value=\"backtest\">Backtest</option><option value=\"inspector\">Inspector</option><option value=\"optimization\">Optimalisatie</option>'",
    "s.innerHTML='<option value=\"default\">Standaard</option><option value=\"grafiek\">Grafiek</option><option value=\"optimization\">Optimalisatie</option>'")

# 4. results tab activation -> dock
rep("function activateResultsTab(name){document.querySelectorAll('[data-result-panel]').forEach(p=>p.classList.remove('hidden'));if(name==='analytics')requestAnimationFrame(()=>document.querySelectorAll('.analytics-plot').forEach(x=>{try{Plotly.Plots.resize(x)}catch{}}))}",
    "function activateResultsTab(name){if(name&&name!=='summary')showDockTab(name)}")

# 5. params with Dutch labels
rep("const title=document.createElement('span');title.className='param-title';title.textContent=p.label||p.name;label.appendChild(title);",
    "const meta=window.paramLabel?window.paramLabel(p.name,p,$('strategy').value):{label:p.label||p.name};p={...p,label:meta.label||p.label||p.name,description:p.description||[meta.help,meta.suggest?`Voorstel: ${meta.suggest}.`:'',meta.example?`Effect: ${meta.example}`:''].filter(Boolean).join(' '),unit:p.unit||meta.unit,simple:p.simple??meta.simple};if(!p.option_labels&&window.PARAM_OPTION_LABELS){const ol=window.PARAM_OPTION_LABELS[`${$('strategy').value}.${p.name}`]||window.PARAM_OPTION_LABELS[p.name];if(ol)p.option_labels=ol}\n    const title=document.createElement('span');title.className='param-title';title.textContent=p.label+(p.unit&&!/\\(/.test(p.label)?` (${p.unit})`:'');label.appendChild(title);const key=document.createElement('code');key.className='param-key';key.textContent=p.name;title.appendChild(key);")

# 6. events + interpretation after run load
rep("renderTrades(state.runData.trades||[]);renderPerformanceAnalytics();",
    "renderTrades(state.runData.trades||[]);renderPerformanceAnalytics();renderInterpretation(id,manifest);document.dispatchEvent(new CustomEvent('lab:run',{detail:{runId:id,action:manifest.action||(Array.isArray(state.runData.all_trials)?'optimize':'backtest'),manifest,result:state.runData}}));")
rep("tr.querySelector('button').onclick=()=>jumpTrade(t);body.appendChild(tr);",
    "tr.querySelector('button').onclick=e=>{e.stopPropagation();selectTrade(t,i,tr)};tr.onclick=()=>selectTrade(t,i,tr);tr.tabIndex=0;tr.onkeydown=e=>{if(e.key==='Enter')selectTrade(t,i,tr)};body.appendChild(tr);")
rep("<td><button class=\"secondary\">Spring</button></td>", "<td><button class=\"secondary\">Toon</button></td>")
rep("<td>${escapeHtml(t.type||t.side||'')}</td>", "<td>${/short|sell/i.test(t.type||t.side||'')?'Short':'Long'}</td>")

# 7. chart: candle click opens decision log; selected trade SL/TP shapes
rep("graph.on('plotly_click',ev=>{const point=ev.points[0];const ms=Number(point.customdata);if(Number.isFinite(ms))inspect(ms);});",
    "graph.on('plotly_click',ev=>{const point=ev.points[0];const ms=Number(point.customdata);if(Number.isFinite(ms)){inspect(ms);showDockTab('inspector')}});")
rep("legend:{orientation:'h',y:1.08},dragmode:'pan'}",
    "legend:{orientation:'h',y:1.08},dragmode:'pan',shapes:selectedTradeShapes(),annotations:selectedTradeAnnotations()}")

# 8. auto chart reload when the period changes; data range hint
rep("$('symbol').addEventListener('change',()=>loadMeta()); $('timeframe').addEventListener('change',()=>loadChart());",
    "$('symbol').addEventListener('change',async()=>{await loadMeta();loadChart()}); $('timeframe').addEventListener('change',()=>loadChart());\n  for(const id of ['start','end'])$(id).addEventListener('change',()=>{clearTimeout(state.periodTimer);state.periodTimer=setTimeout(loadChart,350)});\n  applySettingLabels();")
rep("if(!$('end').value||$('end').value<fmt(first)||$('end').value>fmt(last)) $('end').value=fmt(last);",
    "if(!$('end').value||$('end').value<fmt(first)||$('end').value>fmt(last)) $('end').value=fmt(last);\n    const range=$('data-range');if(range){range.textContent=`Data: ${fmt(first)} t/m ${fmt(last)}`;range.dataset.tip='Beschikbare lokale Dukascopy-data voor deze markt (UTC). De periode moet hierbinnen vallen.'}")

# 9. compare as table
rep("$('comparison').textContent=JSON.stringify(data,null,2);activateResultsTab('comparison');",
    "renderComparison(data);activateResultsTab('comparison');")

# 10. new helpers appended before init()
helpers = r'''
function applySettingLabels(){
  const labels=window.SETTING_LABELS||{},opts=window.OPTION_LABELS||{};
  for(const [id,meta] of Object.entries(labels)){
    const el=$(id);if(!el)continue;const label=el.closest('label');
    if(label&&meta.label){const first=[...label.childNodes].find(n=>n.nodeType===3&&n.textContent.trim());const span=label.querySelector(':scope>span');const text=meta.label+(meta.unit&&!/\(/.test(meta.label)?` (${meta.unit})`:'');if(first)first.textContent=text;else if(span)span.firstChild.textContent=text+' ';}
    const tip=[meta.help,meta.suggest?`Voorstel: ${meta.suggest}.`:'',meta.example?`Effect: ${meta.example}`:''].filter(Boolean).join(' ');
    if(tip&&label&&!label.querySelector('.info')){const b=document.createElement('button');b.type='button';b.className='info';b.setAttribute('aria-label','Uitleg');b.dataset.tip=tip;b.textContent='i';(label.querySelector(':scope>span')||label).insertBefore(b,el.parentElement===label?el:null)}
    if(label&&meta.simple===false)label.classList.add('advanced-only');
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
async function selectTrade(t,i,row){
  state.selectedTrade=t;document.querySelectorAll('#trades tr').forEach(r=>r.classList.toggle('selected',r===row));
  const lv=tradeLevels(t),side=/short|sell/i.test(t.type||t.side||'')?'Short':'Long',opened=Number(t.opened_at||0),closed=Number(t.closed_at||0);
  const ev=(state.runData?.events||[]).filter(e=>String(e.trade_id||'')===String(t.id)||(Number(e.timestamp_ms)<=opened&&Number(e.timestamp_ms)>=opened-3*(candleHalfWidths[$('timeframe').value]||30000)*2&&(e.event_type==='CANDLE_DECISION'||e.event_type==='ORDER_SUBMITTED')));
  const decision=ev.filter(e=>e.event_type==='CANDLE_DECISION'||e.reason).pop();
  const why=decision?`${escapeHtml(decision.reason||decision.decision||'')}${(decision.indicators||[]).length?' · '+decision.indicators.map(x=>`${escapeHtml(x.name)} ${typeof x.value==='number'?nice(x.value,5):escapeHtml(String(x.value))}`).join(' · '):''}`:'Deze strategie heeft voor deze trade geen beslisgegevens opgeslagen; er wordt niets achteraf verzonnen.';
  const exitWhy=lv.tpHit?'take profit geraakt':lv.slHit?'stop loss geraakt':'gesloten (reden niet vastgelegd)';
  const d=$('trade-detail');d.hidden=false;
  d.innerHTML=`<div class="td-grid"><div><span class="td-k">Trade</span><b>#${i+1} ${side}</b></div><div><span class="td-k">Entry</span><b>${nice(t.entry_price,5)}</b><small>${opened?new Date(opened).toISOString().slice(0,16).replace('T',' '):''}</small></div><div><span class="td-k">Exit</span><b>${nice(t.exit_price,5)}</b><small>${closed?new Date(closed).toISOString().slice(0,16).replace('T',' '):''} · ${exitWhy}</small></div><div><span class="td-k">Stop loss</span><b class="neg">${lv.sl===null?'geen':nice(lv.sl,5)}</b>${lv.slFirst!==null&&lv.slFirst!==lv.sl?`<small>eerst ${nice(lv.slFirst,5)}, later verplaatst</small>`:''}</div><div><span class="td-k">Take profit</span><b class="pos">${lv.tp===null?'geen':nice(lv.tp,5)}</b></div><div><span class="td-k">Resultaat</span><b class="${Number(t.PNL??t.pnl)>=0?'pos':'neg'}">${nice(t.PNL??t.pnl,2)}</b><small>na kosten ${nice(t.fee,2)}</small></div></div><p class="td-why"><b>Waarom:</b> ${why}</p><div class="inline"><button type="button" class="secondary" data-td="replay">Afspelen vanaf signaal</button><button type="button" class="secondary" data-td="log">Beslislogboek openen</button></div>`;
  d.querySelector('[data-td="replay"]').onclick=()=>jumpTrade(t);
  d.querySelector('[data-td="log"]').onclick=()=>{inspect(Number(decision?.timestamp_ms||opened));showDockTab('inspector')};
  if(state.candles?.length&&!state.replaySession){renderChart(state.visibleCandles||state.candles);const pad=Math.max((closed-opened)*1.5,30*(candleHalfWidths[$('timeframe').value]||30000)*2);try{Plotly.relayout('chart',{'xaxis.range':[new Date(opened-pad).toISOString(),new Date((closed||opened)+pad).toISOString()],'yaxis.autorange':true})}catch{}}
  inspect(Number(decision?.timestamp_ms||opened));
  document.dispatchEvent(new CustomEvent('lab:trade-selected',{detail:{trade:t,runId:state.activeRun}}));
}
function renderComparison(data){
  const root=$('comparison');const a=data.first||data.a||{},b=data.second||data.b||{};const ma=a.metrics||a,mb=b.metrics||b;
  const keys=[...new Set([...Object.keys(ma||{}),...Object.keys(mb||{})])].filter(k=>typeof (ma?.[k]??mb?.[k])==='number');
  if(!keys.length){root.innerHTML=`<pre>${escapeHtml(JSON.stringify(data,null,2))}</pre>`;return}
  root.innerHTML=`<table class="tbl"><thead><tr><th>Statistiek</th><th class="num">Run A</th><th class="num">Run B</th><th class="num">Verschil</th></tr></thead><tbody>${keys.map(k=>`<tr><td>${escapeHtml(k)}</td><td class="num">${nice(ma?.[k],3)}</td><td class="num">${nice(mb?.[k],3)}</td><td class="num">${Number.isFinite(mb?.[k]-ma?.[k])?nice(mb[k]-ma[k],3):'—'}</td></tr>`).join('')}</tbody></table>`;
}
window.labTester={focus(profile,dock,sub){
  if(profile&&layoutPresets[profile]&&localStorage.getItem('strategyLabLayoutPresetV1')!==profile){$('layout-profile').value=profile;activateLayoutProfile(profile,{scroll:false})}
  if(dock)showDockTab(dock);
  if(sub==='trades'||sub==='journaal')requestAnimationFrame(()=>document.querySelector('[data-panel="dock"]')?.scrollIntoView({block:'start',behavior:'smooth'}));
  else if(sub)window.scrollTo({top:0});
},showDockTab,reloadStrategies:async(select)=>{const st=await api('/api/strategies');state.strategies=st.strategies;const s=$('strategy'),cur=select||s.value;s.innerHTML='';st.strategies.forEach(x=>{const o=document.createElement('option');o.value=x.name;o.textContent=x.name+(x.managed_by_lab?' · Lab':'');s.appendChild(o)});if([...s.options].some(o=>o.value===cur))s.value=cur;s.dispatchEvent(new Event('change'))}};
'''
rep("\ninit().catch(", helpers + "\ninit().catch(")
open(p, 'w', encoding='utf-8').write(s)
print('ok')
