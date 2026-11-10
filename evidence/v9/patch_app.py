p='C:/AI/Jesse/Jesse-Audit/strategy_lab/static/app.js'
s=open(p,encoding='utf-8').read()
def rep(old,new,count=1):
    global s
    assert s.count(old)>=1,old[:60]
    s=s.replace(old,new,count)

rep("  if (!response.ok) throw new Error(data.detail || JSON.stringify(data));\n  return data;",
"  if (!response.ok) { const d=data.detail; const err=new Error(typeof d==='string'?d:Array.isArray(d)?d.map(x=>x.msg||JSON.stringify(x)).join('; '):d?JSON.stringify(d):JSON.stringify(data)); err.status=response.status; throw err; }\n  return data;")

rep("function status(message, kind='') { $('run-status').textContent=message; $('run-status').className=`status ${kind}`; }",
r"""function errorHint(message){
  const m=String(message||'');
  if(/not found|\b404\b/i.test(m))return 'Dit API-eindpunt bestaat niet op de server. Herstart de backend of controleer of frontend en backend dezelfde versie draaien.';
  if(/\b409\b|al een|already|bezig|busy/i.test(m))return 'Er draait al een run of job. Annuleer die of wacht tot hij klaar is.';
  if(/failed to fetch|network|timeout|verbinding|ECONN/i.test(m))return 'Geen verbinding met de lokale server. Controleer of de backend draait en probeer opnieuw.';
  if(/data|candle|dataset|periode|period/i.test(m))return 'Controleer symbool, periode en of er Dukascopy-data beschikbaar is (Data → Overzicht).';
  return 'Kopieer de melding hierboven bij een bugrapport; details staan ook in Onderzoeken → Journaal.';
}
window.labErrorHint=errorHint;
function status(message, kind='') { const el=$('run-status'); el.textContent=message; el.className=`status ${kind}`; if(kind==='error'){const h=document.createElement('div');h.className='err-hint';h.textContent=errorHint(message);el.append(h)} }
let runTimer=null;
function stopBusy(){clearInterval(runTimer);runTimer=null}
function busyStatus(label,startedMs){
  stopBusy();const el=$('run-status');el.textContent='';el.className='status busy';
  const t=document.createElement('span'),b=document.createElement('button');
  b.type='button';b.id='run-cancel';b.className='secondary';b.textContent='Annuleren';b.onclick=cancelRun;
  el.append(t,' ',b);
  const paint=()=>{const s=Math.max(0,Math.round((Date.now()-startedMs)/1000));t.textContent=`${label} · ${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`};
  paint();runTimer=setInterval(paint,1000);
}
async function cancelRun(){
  const b=$('run-cancel');if(b){b.disabled=true;b.textContent='Annuleren…'}
  state.cancelRequested=true;
  try{await api('/api/runs/cancel',{method:'POST'})}
  catch(e){state.cancelRequested=false;stopBusy();if(e.status===409)status('Geen lopende run om te annuleren.');else status(e.status===404?'Annuleren wordt door deze server nog niet ondersteund (POST /api/runs/cancel ontbreekt).':e.message,'error');if(!state.runBusy){$('top-run').disabled=false;$('top-optimize').disabled=false}}
}
async function watchActiveRun(){
  let a;try{a=await api('/api/runs/active')}catch{return}
  if(!a||a.running!==true||state.runBusy)return;
  $('top-run').disabled=true;$('top-optimize').disabled=true;
  busyStatus(`${a.strategy||'Jesse'} draait op de server`,Date.now()-(Number(a.elapsed_s)||0)*1000);
  const timer=setInterval(async()=>{
    let cur;try{cur=await api('/api/runs/active')}catch{return}
    if(cur.running===true)return;
    clearInterval(timer);stopBusy();$('top-run').disabled=false;$('top-optimize').disabled=false;
    try{await loadRuns()}catch{}
    status(state.cancelRequested?'Geannuleerd.':'Run op de server is afgerond; kies hem in de runlijst.');state.cancelRequested=false;
  },3000);
}""")

rep("""  $('top-run').disabled=true;$('top-optimize').disabled=true;status(action==='optimize'?'Jesse optimizer draait…':'Dukascopy-data naar Jesse sturen…','busy');
  try{""","""  $('top-run').disabled=true;$('top-optimize').disabled=true;state.runBusy=true;state.cancelRequested=false;busyStatus(action==='optimize'?'Jesse optimizer draait':'Backtest draait (Dukascopy-data naar Jesse)',Date.now());
  try{""")
rep("const data=await api('/api/runs',{method:'POST',body:JSON.stringify(payload)});state.activeRun=data.manifest.run_id;",
    "const data=await api('/api/runs',{method:'POST',body:JSON.stringify(payload)});stopBusy();state.activeRun=data.manifest.run_id;")
rep("  }catch(e){status(e.message,'error');if(/shortposities/","  }catch(e){stopBusy();if(state.cancelRequested||/geannuleerd|cancelled|canceled/i.test(e.message)){status('Geannuleerd.');}else status(e.message,'error');if(/shortposities/")
rep("}finally{$('top-run').disabled=false;$('top-optimize').disabled=false;}\n}","}finally{state.runBusy=false;state.cancelRequested=false;stopBusy();$('top-run').disabled=false;$('top-optimize').disabled=false;}\n}")

rep("for(const id of ['run-select','compare-a','compare-b']){const current=$(id).value;const first=id==='run-select';$(id).innerHTML=first?'<option value=\"\">Geen backtestrun</option>':'<option value=\"\">'+(id.endsWith('a')?'Run A':'Run B')+'</option>';",
    "for(const id of ['run-select','compare-a','compare-b','compare-c','compare-d']){if(!$(id))continue;const current=$(id).value;const first=id==='run-select';$(id).innerHTML=first?'<option value=\"\">Geen backtestrun</option>':'<option value=\"\">Run '+id.slice(-1).toUpperCase()+(/[cd]$/.test(id)?' (optioneel)':'')+'</option>';")

i=s.index("async function compareRuns(){"); j=s.index("\n",i)
s=s[:i]+r"""async function compareRuns(){
  const uniq=[...new Set(['compare-a','compare-b','compare-c','compare-d'].map(i=>$(i)?.value).filter(Boolean))];
  const root=$('comparison');activateResultsTab('comparison');
  if(uniq.length<2){root.innerHTML='<div class="muted">Kies minstens twee verschillende runs (maximaal vier).</div>';return}
  root.textContent='Runs vergelijken…';
  try{
    const runs=await Promise.all(uniq.map(id=>api('/api/runs/'+encodeURIComponent(id)).then(d=>({id,manifest:d.manifest||{},metrics:d.result?.metrics||{}}))));
    renderMultiComparison(runs);
  }catch(e){
    if(uniq.length===2){try{renderComparison(await api(`/api/metrics/compare?first=${uniq[0]}&second=${uniq[1]}`));return}catch(e2){e=e2}}
    root.innerHTML=`<div class="err-box"><b>Vergelijken mislukt</b><pre>${escapeHtml(e.message)}</pre><div class="err-hint">${escapeHtml(errorHint(e.message))}</div></div>`;
  }
}
function renderMultiComparison(runs){
  const root=$('comparison');const L=i=>String.fromCharCode(65+i);
  const num=(m,k)=>typeof m[k]==='number'&&Number.isFinite(m[k]);
  const keys=[...new Set(runs.flatMap(r=>Object.keys(r.metrics)))].filter(k=>runs.some(r=>num(r.metrics,k)));
  const differs=vals=>new Set(vals.map(v=>JSON.stringify(v??null))).size>1;
  const row=(label,vals,fmtv=v=>v??'—')=>{const d=differs(vals);return `<tr class="${d?'diff-row':''}"><td>${escapeHtml(label)}</td>${vals.map(v=>`<td class="${d?'diff':''}" title="${escapeHtml(typeof v==='object'?JSON.stringify(v):v??'')}">${escapeHtml(fmtv(v))}</td>`).join('')}<td class="num">${d?'verschilt':'gelijk'}</td></tr>`};
  const grp=t=>`<tr class="grp"><th colspan="${runs.length+2}">${escapeHtml(t)}</th></tr>`;
  const params=[...new Set(runs.flatMap(r=>Object.keys(r.manifest.parameters||{})))];
  const shaDiff=differs(runs.map(r=>r.manifest.strategy_sha256));
  const two=runs.length===2;
  let h=`<div class="${shaDiff?'notice warn':'muted'}" style="margin-bottom:8px">${shaDiff?'Let op: de strategiecode (sha256) verschilt tussen deze runs; je vergelijkt dus verschillende versies van de strategie.':'Alle runs gebruiken dezelfde strategiecode (sha256).'}</div>`;
  h+=`<table class="tbl"><thead><tr><th>Statistiek</th>${runs.map((r,i)=>`<th class="num" title="${escapeHtml(r.id)}">Run ${L(i)}</th>`).join('')}<th class="num">${two?'Verschil (B − A)':'Spreiding (max − min)'}</th></tr></thead><tbody>`;
  h+=grp('Versie en instellingen');
  h+=row('Strategie',runs.map(r=>r.manifest.strategy));
  h+=row('Strategie-sha256',runs.map(r=>r.manifest.strategy_sha256),v=>v?String(v).slice(0,12):'—');
  h+=row('Symbool · timeframe',runs.map(r=>`${r.manifest.symbol||'—'} · ${r.manifest.timeframe||'—'}`));
  h+=row('Periode',runs.map(r=>`${r.manifest.start_date||'—'} → ${r.manifest.end_date||'—'}`));
  h+=row('Startkapitaal · fee',runs.map(r=>`${nice(r.manifest.starting_balance,0)} · ${r.manifest.fee??'—'}`));
  h+=row('Actie',runs.map(r=>r.manifest.action));
  for(const p of params)h+=row(`Parameter ${p}`,runs.map(r=>(r.manifest.parameters||{})[p]),v=>v===undefined?'—':typeof v==='object'?JSON.stringify(v):String(v));
  if(!params.length)h+=`<tr><td>Parameters</td><td colspan="${runs.length+1}" class="muted">Geen afwijkende parameters vastgelegd (standaardwaarden van de strategie).</td></tr>`;
  h+=grp('Resultaten (historische backtest)');
  for(const k of keys){const v=runs.map(r=>num(r.metrics,k)?r.metrics[k]:null);const f=v.filter(x=>x!==null);const spread=f.length>1?(two?v[1]-v[0]:Math.max(...f)-Math.min(...f)):null;
    h+=`<tr><td>${escapeHtml(k)}</td>${v.map(x=>`<td class="num">${x===null?'—':nice(x,3)}</td>`).join('')}<td class="num">${spread===null||!Number.isFinite(spread)?'—':nice(spread,3)}</td></tr>`}
  root.innerHTML=h+'</tbody></table>';
}"""+s[j:]

rep("init().catch(e=>{ $('health').textContent='App-start fout';status(e.message,'error'); });","init().then(()=>watchActiveRun()).catch(e=>{ $('health').textContent='App-start fout';status(e.message,'error'); });")
open(p,'w',encoding='utf-8').write(s)

p='C:/AI/Jesse/Jesse-Audit/strategy_lab/static/index.html'
s=open(p,encoding='utf-8').read()
rep('<option value="">Run B</option></select><button id="compare" class="secondary">Vergelijk</button></div>','<option value="">Run B</option></select><select id="compare-c" aria-label="Run C"><option value="">Run C (optioneel)</option></select><select id="compare-d" aria-label="Run D"><option value="">Run D (optioneel)</option></select><button id="compare" class="secondary">Vergelijk</button></div>')
rep('Kies twee runs om hun statistieken naast elkaar te zien.','Kies 2 tot 4 runs om statistieken, strategieversie (sha256) en parameters naast elkaar te zien.')
open(p,'w',encoding='utf-8').write(s)
print('patched')
