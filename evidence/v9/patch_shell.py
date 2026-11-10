p='C:/AI/Jesse/Jesse-Audit/strategy_lab/static/shell.js'
s=open(p,encoding='utf-8').read()
new=open('C:/AI/Jesse/Jesse-Audit/evidence/v9/validate_new.js',encoding='utf-8').read().replace("${groups}${val.missing ? '' : ''}${tplOpts}","${groups}${tplOpts}")
a=s.index("/* ---------------- Validate ---------------- */")
b=s.index("function tableFrom(")
s=s[:a]+new+s[b:]

def rep(old,new_):
    global s
    assert old in s, old[:50]
    s=s.replace(old,new_,1)

rep("""function tableFrom(rows, cols) {
  rows = rows || []; if (!rows.length) return '<div class="empty" style="padding:14px">Niet getest of onvoldoende data.</div>';""",
"""function tableFrom(rows, cols, empty) {
  rows = rows || []; if (!rows.length) return `<div class="empty" style="padding:14px">${esc(empty || 'Niet getest of onvoldoende data.')}</div>`;""")

rep("""    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ') : `HTTP ${res.status}`);""",
"""    const err = new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ') : d ? JSON.stringify(d) : `HTTP ${res.status}`);
    err.status = res.status; throw err;""")

rep("""  box.textContent = `Fout: ${err.message || err}`;
}""","""  box.innerHTML = errBox('Fout', err.message || String(err)).replace(/^<div class="notice bad err-box">|<\\/div>$/g, '');
}
const errHint = m => window.labErrorHint ? window.labErrorHint(m) : 'Controleer de invoer en probeer opnieuw.';
const errBox = (title, message) => `<div class="notice bad err-box"><b>${esc(title)}</b><pre>${esc(message)}</pre><div class="err-hint">${esc(errHint(message))}</div></div>`;""")

rep("""  go(initial, {silent: true});
""","""  go(initial, {silent: true});
  if (lsGet(VAL_JOB_KEY, null)?.id) setTimeout(() => loadValidate().catch(() => {}), 800);
""")
open(p,'w',encoding='utf-8').write(s)
print('ok')
