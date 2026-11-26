function i18nText(key, fallback = '') {
  return (i18nStrings && i18nStrings[key]) || fallback || key;
}
function applyI18nStrings(lang, strings) {
  currentUiLang = lang || 'el';
  i18nStrings = strings || {};
  if (document.documentElement) document.documentElement.lang = currentUiLang;
  const topSel = document.getElementById('ui-lang');
  if (topSel && topSel.value !== currentUiLang) topSel.value = currentUiLang;
  const setSel = document.getElementById('settings-ui-lang');
  if (setSel && setSel.value !== currentUiLang) setSel.value = currentUiLang;
  if (typeof document.querySelectorAll === 'function') {
    for (const el of document.querySelectorAll('[data-i18n]')) {
      const k = el.getAttribute('data-i18n');
      if (k && i18nStrings[k]) el.textContent = i18nStrings[k];
    }
    for (const el of document.querySelectorAll('[data-i18n-placeholder]')) {
      const k = el.getAttribute('data-i18n-placeholder');
      if (k && i18nStrings[k]) el.placeholder = i18nStrings[k];
    }
  }
  const modeSel = document.getElementById('mode');
  if (modeSel && modeSel.options) {
    for (const opt of modeSel.options) {
      const k = 'mode_' + opt.value;
      if (i18nStrings[k]) opt.textContent = i18nStrings[k];
    }
  }
  modeLabels = {
    auto: i18nText('mode_auto', 'Αυτόματο'),
    assistant: i18nText('mode_assistant', 'Βοηθός'),
    ask: i18nText('mode_ask', 'Ερώτηση'),
    architect: i18nText('mode_architect', 'Αρχιτέκτονας'),
    code: i18nText('mode_code', 'Κώδικας'),
    debug: i18nText('mode_debug', 'Debug'),
  };
  modeHints = {
    assistant: i18nText('hint_mode_assistant', 'έρευνα, έγγραφα και υπενθυμίσεις'),
    auto: i18nText('hint_mode_auto', 'σχεδιάζει, υλοποιεί και ελέγχει ανάλογα με το αίτημά σου'),
    ask: i18nText('hint_mode_ask', 'απαντάει μόνο από όσα διαβάζει — δεν αλλάζει τίποτα'),
    architect: i18nText('hint_mode_architect', 'σχεδιάζει το πλάνο (PLAN.md), δεν γράφει κώδικα'),
    code: i18nText('hint_mode_code', 'αλλάζει, τρέχει, διορθώνει — πλήρης δουλειά'),
    debug: i18nText('hint_mode_debug', 'ψάχνει σφάλματα με tests και logs'),
  };
  const mh = document.getElementById('mode-hint');
  if (mh && modeSel) mh.textContent = modeHints[modeSel.value] || '';
  const advChk = document.getElementById('advanced-view');
  updateViewModeBadge(!!(advChk && advChk.checked));
  const busyEl = document.getElementById('busy-badge');
  if (busyEl) busyEl.textContent = busy ? i18nText('badge_busy', '⚡ εργάζεται…') : i18nText('badge_idle', '💤 αδρανής');
  updateOrganizationSelectionLabel();
  if (typeof StudioUI !== 'undefined') StudioUI.refreshNavTooltips();
}
async function loadI18n(lang) {
  try {
    const r = await api('/api/i18n', lang ? {lang} : {});
    if (r && r.strings) applyI18nStrings(r.lang || lang || 'el', r.strings);
  } catch (_) {}
}
async function saveModelRouting() {
  const fpEl = document.getElementById('fast-provider');
  const fastEl = document.getElementById('fast-model');
  const spEl = document.getElementById('smart-provider');
  const smartEl = document.getElementById('smart-model');
  const fbpEl = document.getElementById('fallback-provider');
  const fbmEl = document.getElementById('fallback-model');
  const payload = {
    fast_provider: (fpEl?.value || '').trim(),
    fast_model: (fastEl?.value || '').trim(),
    smart_provider: (spEl?.value || '').trim(),
    smart_model: (smartEl?.value || '').trim(),
  };
  if (fbpEl) payload.fallback_provider = (fbpEl.value || '').trim();
  if (fbmEl) payload.fallback_model = (fbmEl.value || '').trim();
  const r = await api('/api/preferences', payload);
  if (r && r.error) { showNotice('err', r.error); return; }
  showNotice('ok', '✅ Smart Model Routing & Fallback αποθηκεύτηκαν');
}
async function changeUiLanguage(lang) {
  if (!lang) return;
  const r = await api('/api/preferences', {ui_language: lang});
  if (r && r.error) { showNotice('err', r.error); return; }
  await loadI18n(lang);
  try {
    await Promise.allSettled([loadAgenda(), loadRoutines(), loadLearning(learningOffset), loadPlugins(), loadProviders(), refreshMobileBridge()]);
    const st = await api('/api/status');
    if (st && !st.error) updateBadges(st);
  } catch (_) {}
}
function updateViewModeBadge(isPro) {
  const btn = document.getElementById('view-mode-toggle');
  if (btn) btn.textContent = isPro ? i18nText('view_pro', '⚙️ Pro') : i18nText('view_simple', '✨ Απλή');
}
let approvalSignature = '';
function renderApprovals(list) {
  const rows = Array.isArray(list) ? list : [];
  const signature = JSON.stringify(rows);
  if (signature === approvalSignature) return;
  approvalSignature = signature;
  const panel = document.getElementById('permission-panel'); panel.replaceChildren();
  for (const row of rows) {
    const box = document.createElement('div'); box.className = 'step ' + (row.risk==='dangerous' ? 'bad':'');
    box.style.margin = '10px 16px';
    const title = document.createElement('strong'); title.textContent = row.risk==='dangerous' ? 'Επικίνδυνη εντολή — χρειάζεται έγκριση' : 'Χρειάζεται η έγκρισή σου';
    const details = document.createElement('pre'); details.className='out';
    if (row.details.diff) colorDiff(details, row.details.diff);
    else details.textContent = row.details.command || JSON.stringify(row.details,null,2);
    const root = document.createElement('div'); root.className='hint'; root.textContent=row.project;
    box.append(title,root,details);
    for (const [text,allow,always] of [['Να τρέξει / Εφαρμογή',true,false],['Όχι',false,false],['Πάντα αυτή η εντολή',true,true]]) {
      if (always && (row.risk==='dangerous' || row.kind!=='command')) continue;
      const button = document.createElement('button'); button.className='btn'; button.style.width='auto'; button.style.margin='6px 6px 0 0'; button.textContent=text;
      button.onclick=async()=>{button.disabled=true;await api('/api/approval',{id:row.id,allow,always});}; box.appendChild(button);
    }
    panel.appendChild(box);
  }
}
async function saveBudget(){const body={daily_budget:Number(document.getElementById('daily-budget').value)};const values=['input','cached','output'].map(k=>document.getElementById('price-'+k).value);if(values.every(x=>x!==''))body.prices=Object.fromEntries(['input','cached','output'].map((k,i)=>[k,Number(values[i])]));const r=await api('/api/preferences',body);if(r.error)showNotice('err',r.error);}
async function savePreferences() {
  const result = await api('/api/preferences',{autonomous:document.getElementById('autonomous').checked,cautious_edits:document.getElementById('cautious-edits').checked,supports_vision:document.getElementById('supports-vision').checked,advanced_view:document.getElementById('advanced-view').checked,checkpoints:document.getElementById('checkpoints').checked});
  if (result.error) showNotice('err',result.error);
}

let learningProject='';
let learningOffset=0;
function learningButton(parent,label,action){
  const button=document.createElement('button');button.className='btn';button.textContent=label;
  button.style.width='auto';button.onclick=action;parent.appendChild(button);return button;
}
async function learningAction(action,id,text,review){
  const result=await api('/api/learning/action',{project:learningProject,action,id,review,...(text===undefined?{}:{text})});
  if(result.error){showNotice('err',result.error);return;}
  if(result.note)showNotice('ok',result.note);
  await loadLearning(learningOffset);
}
async function loadLearning(offset=0){
  const result=await api('/api/learning',{offset});
  let panel=document.getElementById('learning-content');panel.replaceChildren();
  if(result.error){panel.textContent=result.error;return;}
  learningProject=result.project;learningOffset=offset;
  const heading=text=>{const el=document.createElement('p');el.textContent=text;panel.appendChild(el);};
  const card=text=>{const el=document.createElement('div');el.className='hint';el.style.whiteSpace='pre-wrap';el.style.marginBottom='12px';el.textContent=text;panel.appendChild(el);return el;};
  const profile=result.profile||{},skills=result.skills||{};
  cachedSkills = Array.isArray(skills.catalog) ? skills.catalog : [];
  heading(i18nText('mem_proposals_hdr', 'Προτάσεις μνήμης — ενεργοποιούνται μόνο μετά την έγκριση'));
  for(const row of (profile.proposals||[])){
    const scope=row.target==='global'?'⚠ ΓΕΝΙΚΗ ΜΝΗΜΗ — ισχύει σε ΟΛΑ τα projects\n':'Προφίλ χρήστη\n';
    const box=card(scope+row.topic+' · '+(row.status==='unconfirmed'?'Ανεπιβεβαίωτη υπόθεση':'Επιβεβαιωμένο στοιχείο'));
    if(row.before?.length){const old=document.createElement('p');old.textContent='Αντικαθιστά: '+row.before.map(x=>x.text).join(' · ');box.appendChild(old);}
    const input=document.createElement('textarea');input.value=row.text;input.maxLength=4000;input.style.width='100%';box.appendChild(input);
    learningButton(box,i18nText('btn_approve','Έγκριση'),()=>learningAction('memory_approve',row.id,input.value,row.review_hash));
    learningButton(box,i18nText('btn_reject','Απόρριψη'),()=>learningAction('memory_reject',row.id,undefined,row.review_hash));
  }
  heading(i18nText('mem_active_hdr', 'Ενεργή μνήμη')+' · '+(profile.total||0));
  for(const row of(profile.entries||[])){
    const box=card(row.topic+' ['+(row.status==='unconfirmed'?'UNCONFIRMED':'confirmed')+']\n'+row.text);
    learningButton(box,i18nText('mem_forget_btn', 'Αφαίρεση από ενεργή μνήμη'),()=>learningAction('memory_forget',row.id));
  }
  if(offset>0)learningButton(panel,i18nText('btn_prev','Προηγούμενες'),()=>loadLearning(Math.max(0,offset-30)));
  if(profile.next!==null && profile.next!==undefined)learningButton(panel,i18nText('btn_next','Επόμενες'),()=>loadLearning(profile.next));
  learningButton(panel,i18nText('mem_consolidate_btn', 'Τακτοποίηση βέβαιων διπλοτύπων'),()=>learningAction('consolidate',''));
  panel=document.getElementById('skills-content');panel.replaceChildren();
  heading(i18nText('sk_proposals_hdr', 'Προτάσεις συνταγών — πρώτα διάβασε όλα τα βήματα'));
  for(const row of(skills.proposals||[])){
    const modeBadge=row.mode==='refine'?(row.merged_from?` [🔄 Συγχώνευση παρόμοιου '${row.merged_from}']`:' [🔄 Βελτίωση υπάρχοντος]'):'';
    const catBadge=row.category?` [${row.category}]`:'';
    const box=card(row.name+catBadge+modeBadge+' · '+(row.description||'')+' · '+(row.scope==='project'?'project':'global'));
    const preview=document.createElement('pre');preview.textContent=row.body.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/,'');preview.style.whiteSpace='pre-wrap';box.appendChild(preview);
    learningButton(box,i18nText('btn_approve','Έγκριση συνταγής'),()=>learningAction('skill_approve',row.id,undefined,row.signature));
    learningButton(box,i18nText('btn_reject','Απόρριψη'),()=>learningAction('skill_reject',row.id,undefined,row.signature));
  }
  const renderSkillCard = row => {
    const cat=row.category?` [${row.category}]`:'';
    const uses=row.uses?` · ${i18nText('sk_lbl_uses','χρήσεις')}: ${row.uses}`:'';
    const tel=row.telemetry?` · ✓${row.telemetry.successes||0}/✗${row.telemetry.failures||0}${row.telemetry.needs_healing?' ⚠️ patch':''}`:'';
    const trigs=(row.triggers&&row.triggers.length)?` · triggers: ${row.triggers.join(', ')}`:'';
    const tools=(row.required_tools&&row.required_tools.length)?` · ${i18nText('sk_lbl_tools','εργαλεία')}: ${row.required_tools.join(', ')}`:'';
    const srcMap={
      builtin: i18nText('sk_src_builtin','ενσωματωμένο skill'),
      user: i18nText('sk_src_user','δικό σου / AI (global)'),
      project: i18nText('sk_src_project','δικό σου / AI (project)'),
      'legacy-project': i18nText('sk_src_project','δικό σου / AI (project)'),
    };
    const srcLabel=srcMap[row.source]||i18nText('sk_src_local','τοπικό');
    const desc = row.source==='builtin' ? i18nText('sk_bi_desc_'+row.name, row.description) : row.description;
    const box=card('/'+row.name+cat+' · '+desc+' ('+srcLabel+uses+tel+trigs+tools+')');
    const biSteps = row.source==='builtin' ? i18nText('sk_bi_steps_'+row.name, '') : '';
    if(biSteps || (Array.isArray(row.steps)&&row.steps.length)){
      const details=document.createElement('div');details.className='out';details.style.marginTop='6px';
      let txt=biSteps || row.steps.map((s,i)=>`${i+1}. ${s}`).join('\n');
      if(Array.isArray(row.notes)&&row.notes.length)txt+='\n\n'+i18nText('sk_lbl_notes','Παγίδες / Σημειώσεις')+':\n'+row.notes.map(n=>'- '+n).join('\n');
      if(row.template)txt+='\n\n'+i18nText('sk_lbl_template','Πρότυπο / Snippet')+':\n'+row.template;
      details.textContent=txt;box.appendChild(details);
    }
    const actions=document.createElement('div');actions.style.cssText='display:flex;gap:6px;flex-wrap:wrap;margin-top:6px';
    learningButton(actions,i18nText('sk_run_btn', '▶ Εκτέλεση στο Chat'),()=>runSkillInChat(row.name));
    if(row.source!=='builtin'){
      learningButton(actions,i18nText('sk_btn_edit','✏️ Επεξεργασία'),()=>editOwnerSkill(row));
      learningButton(actions,i18nText('sk_btn_delete','🗑 Διαγραφή'),()=>deleteOwnerSkill(row.name));
    }
    box.appendChild(actions);
  };
  const allCatalog = skills.catalog || [];
  const customSkills = allCatalog.filter(r => r.source !== 'builtin');
  const builtinSkills = allCatalog.filter(r => r.source === 'builtin');
  heading(i18nText('sk_custom_hdr', '🧑‍💻 Δικά σου & AI Skills') + ' · ' + customSkills.length);
  if(customSkills.length){
    for(const row of customSkills) renderSkillCard(row);
  }else{
    card(i18nText('sk_custom_empty', 'Δεν έχεις προσθέσει ή μάθει ακόμα δικά σου skills — πάτα «📥 Εισαγωγή SKILL.md» ή άφησε τον Ακατίκητο να προτείνει νέα από την εργασία σου.'));
  }
  heading(i18nText('sk_builtin_hdr', '📦 Ενσωματωμένα Skills') + ' · ' + builtinSkills.length);
  for(const row of builtinSkills) renderSkillCard(row);
  panel=document.getElementById('learning-content');
  heading('Ιστορικό αναθεωρήσεων: '+(profile.revision_count||0));
  for(const row of(profile.history||[])){
    const before=Array.isArray(row.before)?row.before:[row.before];
    const previous=before.filter(x=>x?.text).map(x=>x.text).join(' · ');
    card(({revision:'Διόρθωση',accept:'Έγκριση',reject:'Απόρριψη',forget:'Αφαίρεση από ενεργή μνήμη',
      consolidate_exact_duplicates:'Συγχώνευση βέβαιων διπλοτύπων',legacy_import:'Εισαγωγή παλιών σημειώσεων',
      superseded_proposal:'Αντικατάσταση πρότασης'}[row.action]||'Αναθεώρηση')+
      (previous?'\nΠριν: '+previous:'')+(row.after?.text?'\nΜετά: '+row.after.text:''));
  }
}
function toggleMcpForm(forceOpen) {
  const f = document.getElementById('mcp-form');
  if (!f) return;
  const open = typeof forceOpen === 'boolean' ? forceOpen : (f.style.display === 'none');
  f.style.display = open ? 'flex' : 'none';
}
function toggleMcpImport() {
  const b = document.getElementById('mcp-import-box');
  if (b) b.style.display = b.style.display === 'none' ? 'flex' : 'none';
}
async function importMcpJson() {
  const el = document.getElementById('mcp-import-json');
  const raw = (el?.value || '').trim();
  if (!raw) { showNotice('err', 'Επικόλλησε πρώτα το JSON του MCP server.'); return; }
  const r = await api('/api/mcp/import', {json: raw});
  if (r.error) { showNotice('err', r.error); return; }
  if (el) el.value = '';
  toggleMcpImport();
  showNotice('ok', 'Εισήχθησαν MCP servers: ' + (r.imported || []).join(', ') + '. Πάτησε Σύνδεση για έγκριση.');
  await poll();
}
function formatMcpArgs(arr) {
  return (Array.isArray(arr) ? arr : []).map(a => {
    const s = String(a);
    return /\s/.test(s) ? '"' + s.replace(/"/g, '\\"') + '"' : s;
  }).join(' ');
}
function editMcpServer(name, cfg) {
  if (!cfg) return;
  toggleMcpForm(true);
  const setVal = (id, val) => { const e = document.getElementById(id); if (e) e.value = val; };
  setVal('mcp-preset', '');
  setVal('mcp-name', name || '');
  setVal('mcp-transport', cfg.transport === 'http' || cfg.transport === 'streamable-http' ? 'http' : 'stdio');
  setVal('mcp-command', cfg.command || '');
  setVal('mcp-args', formatMcpArgs(cfg.args || []));
  setVal('mcp-url', cfg.url || '');
  setVal('mcp-readonly', (cfg.readOnlyTools || []).join(', '));
  setVal('mcp-env', (cfg.envKeys || []).map(k => k + '=').join('\n'));
  setVal('mcp-headers', (cfg.headerKeys || []).map(k => k + ': ').join('\n'));
  const ac = document.getElementById('mcp-autoconnect');
  if (ac) ac.checked = !!cfg.autoConnect;
  onMcpTransport();
}
function onMcpTransport() {
  const t = document.getElementById('mcp-transport')?.value || 'stdio';
  const stdio = document.getElementById('mcp-stdio-fields'), http = document.getElementById('mcp-http-fields');
  if (stdio) stdio.style.display = t === 'stdio' ? 'flex' : 'none';
  if (http) http.style.display = t === 'http' ? 'flex' : 'none';
}
function applyMcpPreset(preset) {
  const name = document.getElementById('mcp-name'), tr = document.getElementById('mcp-transport'),
        cmd = document.getElementById('mcp-command'), args = document.getElementById('mcp-args'),
        url = document.getElementById('mcp-url'), ro = document.getElementById('mcp-readonly'),
        envEl = document.getElementById('mcp-env'), hdrEl = document.getElementById('mcp-headers');
  if (!preset) return;
  if (envEl) envEl.value = '';
  if (hdrEl) hdrEl.value = '';
  if (preset === 'github') {
    name.value = 'github'; tr.value = 'stdio'; cmd.value = 'npx.cmd';
    args.value = '-y @modelcontextprotocol/server-github';
    ro.value = 'search_repositories, get_file_contents, list_issues, list_pull_requests';
    if (envEl) envEl.value = 'GITHUB_PERSONAL_ACCESS_TOKEN=';
  } else if (preset === 'notion') {
    name.value = 'notion'; tr.value = 'stdio'; cmd.value = 'npx.cmd';
    args.value = '-y @notionhq/notion-mcp-server';
    ro.value = 'API-post-search, API-retrieve-a-page, API-get-block-children';
    if (envEl) envEl.value = 'NOTION_TOKEN=';
  } else if (preset === 'playwright') {
    name.value = 'playwright'; tr.value = 'stdio'; cmd.value = 'npx.cmd';
    args.value = '-y @playwright/mcp@latest';
    ro.value = 'browser_snapshot, browser_take_screenshot';
  } else if (preset === 'sqlite') {
    name.value = 'sqlite'; tr.value = 'stdio'; cmd.value = 'uvx';
    args.value = 'mcp-server-sqlite --db-path data.db';
    ro.value = 'read_query, list_tables, describe_table';
  } else if (preset === 'filesystem') {
    name.value = 'filesystem'; tr.value = 'stdio'; cmd.value = 'npx.cmd';
    const projArg = currentProject || '.';
    const quotedProj = /\s/.test(projArg) ? '"' + projArg.replace(/"/g, '\\"') + '"' : projArg;
    args.value = '-y @modelcontextprotocol/server-filesystem ' + quotedProj;
    ro.value = 'read_file, list_directory, search_files';
  } else if (preset === 'fetch') {
    name.value = 'web-fetch'; tr.value = 'stdio'; cmd.value = 'npx.cmd';
    args.value = '-y @modelcontextprotocol/server-fetch'; ro.value = 'fetch';
  } else if (preset === 'memory') {
    name.value = 'memory-graph'; tr.value = 'stdio'; cmd.value = 'npx.cmd';
    args.value = '-y @modelcontextprotocol/server-memory'; ro.value = 'read_graph, search_nodes';
  } else if (preset === 'http-local') {
    name.value = 'local-http'; tr.value = 'http'; url.value = 'http://127.0.0.1:8765/mcp'; ro.value = '';
  }
  onMcpTransport();
}
function parseMcpArgs(raw) {
  const out = [];
  let cur = '';
  let quote = '';
  let hasToken = false;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (quote) {
      if (ch === '\\' && quote === '"' && i + 1 < raw.length && raw[i + 1] === '"') {
        cur += raw[++i];
      } else if (ch === quote) {
        quote = '';
      } else {
        cur += ch;
      }
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      hasToken = true;
    } else if (/\s/.test(ch)) {
      if (hasToken) { out.push(cur); cur = ''; hasToken = false; }
    } else {
      cur += ch;
      hasToken = true;
    }
  }
  if (hasToken) out.push(cur);
  return out;
}
function parseKeyValLines(raw, sep) {
  const out = {};
  for (const line of String(raw || '').split(/\r?\n/)) {
    const idx = line.indexOf(sep);
    if (idx <= 0) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + sep.length).trim();
    if (k) out[k] = v;
  }
  return out;
}
async function saveMcpServer() {
  const name = (document.getElementById('mcp-name')?.value || '').trim();
  const transport = document.getElementById('mcp-transport')?.value || 'stdio';
  const command = (document.getElementById('mcp-command')?.value || '').trim();
  const rawArgs = (document.getElementById('mcp-args')?.value || '').trim();
  const url = (document.getElementById('mcp-url')?.value || '').trim();
  const ro = (document.getElementById('mcp-readonly')?.value || '').split(',').map(x => x.trim()).filter(Boolean);
  const args = rawArgs ? parseMcpArgs(rawArgs) : [];
  const env = parseKeyValLines(document.getElementById('mcp-env')?.value || '', '=');
  const headers = parseKeyValLines(document.getElementById('mcp-headers')?.value || '', ':');
  const autoConnect = !!document.getElementById('mcp-autoconnect')?.checked;
  const r = await api('/api/mcp/save', {name, config: {transport, command, args, url, env, headers, readOnlyTools: ro, autoConnect}});
  if (r.error) { showNotice('err', r.error); return; }
  showNotice('ok', 'Αποθηκεύτηκε ο MCP server «' + name + '». Μπορείς τώρα να πατήσεις Σύνδεση.');
  toggleMcpForm(false);
  await poll();
}
async function removeMcpServer(name) {
  if (!confirm('Να αφαιρεθεί ο MCP server «' + name + '»;')) return;
  const r = await api('/api/mcp/remove', {name});
  if (r.error) showNotice('err', r.error);
  else { showNotice('ok', 'Αφαιρέθηκε ο MCP server «' + name + '».'); await poll(); }
}
function toggleSkillForm() {
  const f = document.getElementById('skill-form');
  if (f) f.style.display = f.style.display === 'none' ? 'flex' : 'none';
}
function triggerLearnCommand() {
  showTab('sess');
  const el = document.getElementById('prompt');
  if (el) {
    el.value = '/learn ';
    grow(el);
    el.focus();
  }
}
function runSkillInChat(name) {
  showTab('sess');
  const el = document.getElementById('prompt');
  if (el) {
    el.value = '/' + name + ' ';
    grow(el);
    el.focus();
  }
}
function editOwnerSkill(row) {
  const f = document.getElementById('skill-form');
  if (f) f.style.display = 'flex';
  document.getElementById('skill-name').value = row.name || '';
  document.getElementById('skill-desc').value = row.description || '';
  document.getElementById('skill-scope').value = (row.source === 'project' || row.source === 'legacy-project') ? 'project' : 'user';
  if (document.getElementById('skill-category')) document.getElementById('skill-category').value = row.category || '';
  if (document.getElementById('skill-triggers')) document.getElementById('skill-triggers').value = (row.triggers || []).join(', ');
  if (document.getElementById('skill-tools')) document.getElementById('skill-tools').value = (row.required_tools || []).join(', ');
  document.getElementById('skill-steps').value = (row.steps || []).map((s, i) => `${i + 1}. ${s}`).join('\n');
  if (document.getElementById('skill-notes')) document.getElementById('skill-notes').value = (row.notes || []).join('\n');
  if (document.getElementById('skill-template')) document.getElementById('skill-template').value = row.template || '';
}
async function deleteOwnerSkill(name) {
  if (!confirm('Να διαγραφεί η συνταγή /' + name + ';')) return;
  const r = await api('/api/learning/action', {project: learningProject || currentProject || filesProject, action: 'skill_delete', name});
  if (r.error) { showNotice('err', r.error); return; }
  showNotice('ok', r.note || ('Διαγράφηκε η συνταγή /' + name));
  await loadLearning(learningOffset);
}
async function createOwnerSkill() {
  const name = (document.getElementById('skill-name')?.value || '').trim();
  const description = (document.getElementById('skill-desc')?.value || '').trim();
  const scope = document.getElementById('skill-scope')?.value || 'user';
  const category = (document.getElementById('skill-category')?.value || '').trim();
  const triggers = (document.getElementById('skill-triggers')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
  const required_tools = (document.getElementById('skill-tools')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
  const notes = (document.getElementById('skill-notes')?.value || '').split(/\r?\n/).map(s => s.replace(/^[-*•]\s*/, '').trim()).filter(Boolean);
  const template = (document.getElementById('skill-template')?.value || '').trim();
  const steps = (document.getElementById('skill-steps')?.value || '').split(/\r?\n/).map(s => s.replace(/^\d+[.)]\s*/, '').trim()).filter(Boolean);
  const r = await api('/api/learning/action', {project: learningProject || currentProject || filesProject, action: 'skill_create', name, description, scope, category, triggers, required_tools, notes, template, steps});
  if (r.error) { showNotice('err', r.error); return; }
  showNotice('ok', r.note || 'Η συνταγή αποθηκεύτηκε.');
  document.getElementById('skill-name').value = '';
  document.getElementById('skill-desc').value = '';
  document.getElementById('skill-steps').value = '';
  if (document.getElementById('skill-triggers')) document.getElementById('skill-triggers').value = '';
  if (document.getElementById('skill-tools')) document.getElementById('skill-tools').value = '';
  if (document.getElementById('skill-notes')) document.getElementById('skill-notes').value = '';
  if (document.getElementById('skill-template')) document.getElementById('skill-template').value = '';
  toggleSkillForm();
  await loadLearning(learningOffset);
}

async function runSkillsTidy() {
  const proj = learningProject || currentProject || filesProject;
  const r = await api('/api/learning/action', {project: proj, action: 'skills_tidy'});
  if (r.error) { showNotice('err', r.error); return; }
  const panel = document.getElementById('skills-tidy-panel');
  if (!panel) return;
  panel.replaceChildren();
  panel.style.display = 'flex';
  const rows = Array.isArray(r.proposals) ? r.proposals : [];
  if (!rows.length) {
    const ok = document.createElement('div');
    ok.className = 'hint';
    ok.textContent = '✅ Δεν βρέθηκαν διπλά, παλιά ή αντιφατικά skills.';
    panel.appendChild(ok);
    return;
  }
  for (const item of rows) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;justify-content:space-between;padding:4px 0';
    const msg = document.createElement('span');
    msg.textContent = '• ' + String(item.message || '');
    row.appendChild(msg);
    if (item.kind === 'stale' && Array.isArray(item.skills) && item.skills[0]) {
      const btn = document.createElement('button');
      btn.className = 'btn';
      btn.style.width = 'auto';
      btn.textContent = 'Αρχειοθέτηση';
      btn.onclick = async () => {
        const res = await api('/api/learning/action', {project: proj, action: 'skill_archive', name: item.skills[0], user_approved: true});
        if (res.error) { showNotice('err', res.error); return; }
        showNotice('ok', res.note || 'Αρχειοθετήθηκε.');
        await runSkillsTidy();
      };
      row.appendChild(btn);
    }
    panel.appendChild(row);
  }
}

let mcpSignature='';
function updateBadges(s) {
  const fpEl = document.getElementById('fast-provider');
  const fEl = document.getElementById('fast-model');
  const spEl = document.getElementById('smart-provider');
  const sEl = document.getElementById('smart-model');
  if (fpEl && document.activeElement !== fpEl && typeof s.fast_provider === 'string') fpEl.value = s.fast_provider;
  if (fEl && document.activeElement !== fEl && typeof s.fast_model === 'string') fEl.value = s.fast_model;
  if (spEl && document.activeElement !== spEl && typeof s.smart_provider === 'string') spEl.value = s.smart_provider;
  if (sEl && document.activeElement !== sEl && typeof s.smart_model === 'string') sEl.value = s.smart_model;
  const learning=s.learning||{};
  const memLabel = i18nText('tab_memory', '🧠 Μνήμη');
  if (typeof StudioUI !== 'undefined' && StudioUI.updateMemoryTabLabel) {
    StudioUI.updateMemoryTabLabel(memLabel, learning.pending);
  } else {
    document.getElementById('tab-memory').textContent=learning.pending ? memLabel + ' · ' + learning.pending : memLabel;
  }
  renderGateway(s.gateway||{});
  document.getElementById('learning-status').textContent=learning.error||(
    learning.pending ? learning.pending+' pending' : i18nText('mem_status_default', 'Οι μνήμες και τα skills είναι τοπικά. Οι νέες προτάσεις χρειάζονται έγκριση.'));
  const mcp=s.mcp||{},mcpKey=JSON.stringify([mcp,s.busy]);
  if(mcpKey!==mcpSignature){
    mcpSignature=mcpKey;const panel=document.getElementById('mcp-servers');panel.replaceChildren();
    renderDynamicMcpList(mcp);
    for(const name of (mcp.configured||[])){
      const cfg = mcp.configs?.[name] || {};
      const active=(mcp.connected||[]).includes(name);
      const srvTools = mcp.server_tools?.[name] || [];
      const card=document.createElement('div');card.style.cssText='display:flex;flex-direction:column;gap:4px;padding:8px;border:1px solid var(--border);border-radius:8px;background:var(--panel)';
      const row=document.createElement('div');row.style.cssText='display:flex;gap:6px;align-items:center';
      const button=document.createElement('button');
      button.className='btn';button.style.flex='1';
      const statusDot = active ? '🟢 ' : '⚪ ';
      const toolCount = active ? ` (${srvTools.length} εργαλεία)` : '';
      const autoTag = cfg.autoConnect ? ' 🔁' : '';
      button.textContent=statusDot+name+autoTag+toolCount+(active?' — Αποσύνδεση':' — Σύνδεση');button.disabled=s.busy;
      button.onclick=async()=>{const r=await api(active?'/api/mcp/disconnect':'/api/mcp/connect',{name});if(r.error)showNotice('err',r.error);};
      const editBtn=document.createElement('button');editBtn.className='btn';editBtn.style.width='36px';editBtn.textContent='✏️';editBtn.title='Επεξεργασία MCP server';editBtn.disabled=s.busy;
      editBtn.onclick=()=>editMcpServer(name,cfg);
      const del=document.createElement('button');del.className='btn danger';del.style.width='36px';del.textContent='🗑';del.title='Διαγραφή MCP server';del.disabled=s.busy;
      del.onclick=()=>removeMcpServer(name);
      row.append(button,editBtn,del);card.appendChild(row);
      const metaParts = [cfg.transport === 'http' ? (cfg.url || 'HTTP') : ((cfg.command || '') + ' ' + (cfg.args || []).join(' ')).trim()];
      if ((cfg.envKeys || []).length) metaParts.push('env: ' + cfg.envKeys.join(', '));
      if ((cfg.headerKeys || []).length) metaParts.push('headers: ' + cfg.headerKeys.join(', '));
      const meta=document.createElement('div');meta.className='hint';meta.style.fontSize='11px';meta.textContent=metaParts.filter(Boolean).join(' · ');
      card.appendChild(meta);
      if (active && srvTools.length) {
        const det=document.createElement('details');
        const sum=document.createElement('summary');sum.className='hint';sum.style.cursor='pointer';sum.textContent='🧰 Ενεργά εργαλεία ('+srvTools.length+')';
        const list=document.createElement('div');list.className='out';list.style.cssText='margin-top:4px;font-size:11.5px;white-space:pre-wrap';
        list.textContent=srvTools.map(t=>(t.readonly?'🔒 ':'⚡ ')+t.name+(t.description?' — '+t.description:'')).join('\n');
        det.append(sum,list);card.appendChild(det);
      }
      for(const warning of (mcp.warnings?.[name]||[])){
        const text=document.createElement('p');text.className='hint';text.style.color='var(--red)';text.textContent='⚠️ '+warning;card.appendChild(text);
      }
      panel.appendChild(card);
    }
  }

  document.body?.classList.toggle('simple-view',!s.preferences?.advanced_view);
  updateViewModeBadge(!!s.preferences?.advanced_view);
  if (s.preferences?.ui_language && s.preferences.ui_language !== currentUiLang) {
    loadI18n(s.preferences.ui_language);
  }
  document.getElementById('checkpoints').checked=s.preferences?.checkpoints!==false;
  document.getElementById('advanced-view').checked=!!s.preferences?.advanced_view;
  if(document.activeElement!==document.getElementById('daily-budget'))document.getElementById('daily-budget').value=s.preferences?.daily_budget||0;
  const ledger=s.usage_ledger||{},cost=row=>row?.unknown?'Άγνωστο κόστος ('+(row.calls||0)+' κλήσεις)':('$'+Number(row?.cost||0).toFixed(4));
  document.getElementById('usage-cost').textContent='Εκτίμηση συνομιλίας: '+cost(ledger.session)+' · σήμερα: '+cost(ledger.day);
  document.getElementById('session-cost').textContent=document.getElementById('usage-cost').textContent;

  document.getElementById('supports-vision').checked=!!s.preferences?.supports_vision;
  if(attachmentContext && attachmentContext!==(s.project+'|'+s.session_id)){attachments=[];showAttachments();}
  const reminderPanel=document.getElementById('reminder-panel');reminderPanel.replaceChildren();
  for(const row of (s.reminders||[])){const item=document.createElement('div');item.className='hint';item.textContent='Υπενθύμιση: '+row.date+' '+row.time+' '+row.title+' ';const done=document.createElement('button');done.textContent='Έγινε';done.disabled=s.busy;done.onclick=()=>api('/api/reminder/done',{id:row.id});item.appendChild(done);reminderPanel.appendChild(item);}
  if (Array.isArray(s.agenda)) renderAgendaList(s.agenda);
  const children=document.getElementById('subtask-panel');children.replaceChildren();for(const row of(s.subtasks||[])){const line=document.createElement('div');line.className='hint';line.textContent='Υποεργασία: '+row.task+' · '+row.status+' · '+(row.tokens?.prompt_tokens||0)+' in / '+(row.tokens?.completion_tokens||0)+' out';children.appendChild(line);}
  const taskPanel=document.getElementById('task-panel');taskPanel.replaceChildren();
  for(const row of (s.todos||[])){const item=document.createElement('div');item.className='hint';item.textContent=({pending:'○',in_progress:'◉',done:'✓'}[row.status]||'○')+' '+row.text;taskPanel.appendChild(item);}

  renderApprovals(s.approvals);
  document.getElementById('autonomous').checked=!!s.preferences?.autonomous;
  document.getElementById('cautious-edits').checked=!!s.preferences?.cautious_edits;
  if (s.preferences?.ui_theme && s.preferences.ui_theme !== currentTheme) applyTheme(s.preferences.ui_theme);
  if (s.preferences?.ui_font_size && s.preferences.ui_font_size !== currentFontSize) applyFontSize(s.preferences.ui_font_size);
  if (typeof StudioUI !== 'undefined' && StudioUI.syncSettingsControls) StudioUI.syncSettingsControls(s);
  if (Array.isArray(s.recent_projects)) recentProjects = s.recent_projects;
  document.getElementById('autonomous').disabled=!!s.busy;
  document.getElementById('cautious-edits').disabled=!!s.busy;
  updateFiles(s);
  const origin = document.getElementById("app-origin");
  origin.textContent = s.app_root ? i18nText('lbl_app_prefix', 'Εφαρμογή: ') + s.app_root : "";
  origin.title = s.app_root || "";
  document.getElementById("proj-label").textContent = s.project || "—";
  document.getElementById("proj-label").title = s.project || "";
  const mLabel = s.mode ? (modeLabels[s.mode] || s.mode_label || "—") : (s.mode_label || "—");
  document.getElementById("mode-badge").textContent = s.auto_mode && s.mode ? (i18nText('mode_auto', 'Αυτόματο') + ' · ' + mLabel) : mLabel;
  currentModel = s.model || ""; currentProvider = s.provider || "";
  currentSession = s.session_id || ""; currentProject = s.project || "";
  const prefix = s.connected ? (s.busy ? i18nText('lbl_working_prefix', 'Δουλεύει: ') : i18nText('lbl_model_prefix', 'Μοντέλο: ')) : i18nText('lbl_last_prefix', 'Τελευταίο: ');
  document.getElementById("model-badge").textContent = prefix + [currentProvider, currentModel].filter(Boolean).join(" · ");
  document.getElementById("model-badge").title = prefix + [currentProvider, currentModel].filter(Boolean).join(" · ");
  const modelSel = document.getElementById("model");
  const choices = [...new Set([currentModel, ...(suggested[currentProvider] || [])].filter(Boolean))];
  if (JSON.stringify([...modelSel.options].map(o => o.value)) !== JSON.stringify(choices)) {
    modelSel.replaceChildren(...choices.map(m => new Option(m, m)));
  }
  modelSel.value = currentModel; modelSel.disabled = !!s.busy || !s.connected;
  document.getElementById("mode").value = s.auto_mode ? "auto" : (s.mode || "code");
  document.getElementById("mode").disabled = !!s.busy;
  document.getElementById("mode-hint").textContent = modeHints[s.auto_mode ? "auto" : s.mode] || "";
  updateUpgrade(s);
  const b = document.getElementById("busy-badge");
  b.textContent = s.busy ? i18nText('badge_busy', '⚡ εργάζεται…') : i18nText('badge_idle', '💤 αδρανής');
  b.classList.toggle("on", !s.busy && s.connected);
  const t = s.tokens || {};
  document.getElementById("tokens").textContent =
    `tokens: ${(t.prompt_tokens||0)} in / ${(t.completion_tokens||0)} out · cache ${(t.cache_hit_tokens||0)} hit`;
  const usage = s.turn_usage || {};
  document.getElementById("context-hint").textContent = usage.model_calls
    ? `Τρέχον/τελευταίο αίτημα: ${usage.prompt_tokens || 0} in / ${usage.completion_tokens || 0} out · ${usage.model_calls} κλήσεις · ${usage.recall_calls || 0} ανακτήσεις.`
    : "Συνοπτική κατάσταση εργασίας · ακριβής ανάκτηση όταν χρειάζεται.";
  renderVoice(s);
  if (s.detected_verify) document.getElementById("verify-hint").textContent = "Εντοπίστηκε: " + s.detected_verify;
}

function showNotice(kind, text) {
  const el = document.getElementById("app-notice");
  clearTimeout(noticeTimer);
  const box = document.createElement("div"); box.className = "notice " + kind;
  box.style.cssText = "display:flex;gap:8px;align-items:flex-start";
  const span = document.createElement("span"); span.style.flex = "1"; span.textContent = text;
  const button = document.createElement("button"); button.className = "btn";
  button.style.cssText = "width:auto;padding:2px 8px;font-size:11px";
  button.textContent = "Αντιγραφή"; button.onclick = () => copyText(text);
  box.append(span, button); el.replaceChildren(box);
  if (kind !== "err") noticeTimer = setTimeout(() => el.replaceChildren(), 8000);
}

let voiceCapture=null,voiceUploading=false,voiceDraft=null,voicePending=null,voiceConfigPending=false,voiceGeneration=0;
let voiceSnapshot={models:{},model:'base',language:'el',job:null};
const voiceHandled=new Set();
function voiceContext(){return [currentProject,currentSession||''];}
function sameVoiceContext(context){return JSON.stringify(context)===JSON.stringify(voiceContext());}
function voiceInsert(text,snapshot=null){
  const field=document.getElementById('prompt');
  if(!text)return;
  const start=snapshot?snapshot.start:(field.selectionStart??field.value.length);
  const end=snapshot?snapshot.end:(field.selectionEnd??start);
  const left=field.value.slice(0,start),right=field.value.slice(end);
  const value=(left&&!/\s$/.test(left)?' ':'')+text+(right&&!/^\s/.test(right)?' ':'');
  field.setRangeText(value,start,end,'end');field.dispatchEvent(new Event('input',{bubbles:true}));grow(field);field.focus();
}
function voiceDraftSnapshot(){const field=document.getElementById('prompt');return {original:field.value,start:field.selectionStart??field.value.length,end:field.selectionEnd??field.value.length,context:voiceContext()};}
function voiceRecordControls(){
  const button=document.getElementById('voice-record');
  const active=!!voiceCapture;
  const processing=voiceUploading||voiceSnapshot.job?.status==='transcribing';
  button.textContent=active?'⏹':'🎙';button.setAttribute?.('aria-pressed',String(active));button.classList.toggle('recording',active);
  button.title=active?'Τέλος υπαγόρευσης':(!voiceSnapshot.models?.[voiceSnapshot.model]?'Κατέβασε το τοπικό μοντέλο από τις Ρυθμίσεις φωνής':'Υπαγόρευση — έως 60 δευτερόλεπτα');
  button.disabled=!active&&(!connected||busy||processing||!voiceSnapshot.models?.[voiceSnapshot.model]);
  const cancel=document.getElementById('voice-cancel-record');cancel.style.display=active||processing?'inline-block':'none';
  document.getElementById('voice-file').disabled=!connected||busy||processing||active||!voiceSnapshot.models?.[voiceSnapshot.model];
}
function voiceRelease(capture){
  clearTimeout(capture.timer);
  for(const track of capture.stream?.getTracks?.()||[])track.stop();
}
function finishVoiceCapture(cancel=false){
  const capture=voiceCapture;if(!capture)return;
  capture.cancelled=cancel;if(cancel)voiceGeneration++;clearTimeout(capture.timer);
  if(capture.recorder&&capture.recorder.state!=='inactive')capture.recorder.stop();
  else if(!capture.recorder){voiceRelease(capture);if(voiceCapture===capture)voiceCapture=null;voiceRecordControls();}
}
async function toggleVoice(){
  if(voiceCapture){finishVoiceCapture(false);return;}
  if(!connected||busy||voiceUploading||!voiceSnapshot.models?.[voiceSnapshot.model])return;
  if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){showNotice('err','Το μικρόφωνο δεν είναι διαθέσιμο εδώ. Μπορείς να φορτώσεις ένα τοπικό ηχητικό από τις Ρυθμίσεις φωνής.');return;}
  const capture={...voiceDraftSnapshot(),generation:++voiceGeneration,chunks:[],bytes:0,cancelled:false,stream:null,recorder:null};voiceCapture=capture;voiceRecordControls();
  try{
    capture.stream=await navigator.mediaDevices.getUserMedia({audio:true});
    if(capture.cancelled||voiceCapture!==capture||!sameVoiceContext(capture.context)){voiceRelease(capture);return;}
    const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(value=>MediaRecorder.isTypeSupported(value));
    capture.recorder=new MediaRecorder(capture.stream,mime?{mimeType:mime}:undefined);
    capture.recorder.ondataavailable=event=>{if(event.data?.size){capture.bytes+=event.data.size;if(capture.bytes>8*1024*1024){finishVoiceCapture(true);showNotice('err','Η εγγραφή ξεπέρασε τα 8 MiB και ακυρώθηκε.');return;}capture.chunks.push(event.data);}};
    capture.recorder.onerror=()=>{finishVoiceCapture(true);showNotice('err','Σταμάτησε η εγγραφή μικροφώνου. Το πρόχειρο παραμένει.');};
    capture.recorder.onstop=async()=>{
      voiceRelease(capture);if(voiceCapture===capture)voiceCapture=null;
      if(!capture.cancelled&&sameVoiceContext(capture.context))await submitVoiceBlob(new Blob(capture.chunks,{type:capture.recorder.mimeType||mime||'audio/webm'}),capture);
      voiceRecordControls();
    };
    capture.recorder.start(250);capture.timer=setTimeout(()=>{if(voiceCapture===capture)finishVoiceCapture(false);},60000);voiceRecordControls();
    document.getElementById('voice-message').textContent='Γράφει το μικρόφωνο. Πάτησε ⏹ όταν τελειώσεις — δεν στέλνεται μήνυμα.';
  }catch(error){
    capture.cancelled=true;voiceRelease(capture);if(voiceCapture===capture)voiceCapture=null;voiceRecordControls();
    showNotice('err',error.name==='NotAllowedError'?'Δεν δόθηκε πρόσβαση στο μικρόφωνο. Το πρόχειρο δεν άλλαξε.':'Δεν ξεκίνησε το μικρόφωνο: '+(error.message||error));
  }
}
async function voiceBlobBase64(blob){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Δεν διαβάστηκε ο ήχος.'));reader.readAsDataURL(blob);});}
async function submitVoiceBlob(blob,snapshot){
  if(!blob.size||blob.size>8*1024*1024){showNotice('err','Χρειάζεται ηχητικό αρχείο έως 8 MiB.');return;}
  voiceUploading=true;voiceRecordControls();
  try{
    const encoded=await voiceBlobBase64(blob);
    if(snapshot.generation!==voiceGeneration)return;
    if(!sameVoiceContext(snapshot.context)){showNotice('err','Άλλαξε η συνομιλία. Η υπαγόρευση δεν στάλθηκε.');return;}
    const reply=await api('/api/voice/transcribe',{base64:encoded,mime:blob.type||'audio/wav',project:snapshot.context[0],session_id:snapshot.context[1]});
    if(reply.error)throw new Error(reply.error);
    if(snapshot.generation!==voiceGeneration){voiceHandled.add(reply.id);await api('/api/voice/cancel',{id:reply.id});return;}
    voiceDraft={...snapshot,id:reply.id};voiceSnapshot={...voiceSnapshot,job:reply};
    if(!sameVoiceContext(snapshot.context)){voiceHandled.add(reply.id);await api('/api/voice/cancel',{id:reply.id});}
  }catch(error){showNotice('err',error.message||String(error));}
  finally{voiceUploading=false;voiceRecordControls();}
}
async function cancelVoice(){
  voiceGeneration++;
  if(voiceCapture)finishVoiceCapture(true);
  const job=voiceSnapshot.job;
  if(job){voiceHandled.add(job.id);await api('/api/voice/cancel',{id:job.id});}
  voicePending=null;voiceDraft=null;document.getElementById('voice-result').textContent='';voiceRecordControls();
  document.getElementById('voice-message').textContent='Η υπαγόρευση ακυρώθηκε. Το πρόχειρο δεν άλλαξε.';
}
function chooseVoiceFile(){
  if(!connected||busy)return;
  const snapshot={...voiceDraftSnapshot(),generation:++voiceGeneration};
  const input=document.createElement('input');input.type='file';input.accept='.wav,.webm,.ogg,.mp4,.m4a';
  input.onchange=()=>{const file=input.files[0];if(file){const types={wav:'audio/wav',webm:'audio/webm',ogg:'audio/ogg',mp4:'audio/mp4',m4a:'audio/mp4'};const type=types[file.name.split('.').pop().toLowerCase()];if(!type){showNotice('err','Μη υποστηριζόμενο ηχητικό.');return;}submitVoiceBlob(new Blob([file],{type}),snapshot);}};input.click();
}
async function configureVoice(){
  if(voiceConfigPending)return;voiceConfigPending=true;
  try{const result=await api('/api/voice/config',{model:document.getElementById('voice-model').value,language:document.getElementById('voice-language').value});if(result.error)throw new Error(result.error);voiceSnapshot=result;}
  catch(error){showNotice('err',error.message||String(error));}
  finally{voiceConfigPending=false;voiceRecordControls();}
}
async function prepareVoice(){
  if(voiceConfigPending)return;
  const result=await api('/api/voice/prepare',{});if(result.error){showNotice('err',result.error);return;}
  voiceSnapshot={...voiceSnapshot,job:result};voiceRecordControls();
}
function renderVoice(state){
  if(!state.voice)return;
  voiceSnapshot=state.voice;const job=voiceSnapshot.job;const active=job&&['preparing','transcribing'].includes(job.status);
  if(voiceCapture&&(!sameVoiceContext(voiceCapture.context)||state.busy))finishVoiceCapture(true);
  const field=document.getElementById('prompt');
  if(job?.kind==='transcribe'&&job.status==='complete'&&!voiceHandled.has(job.id)){
    voiceHandled.add(job.id);if(voiceHandled.size>100)voiceHandled.delete(voiceHandled.values().next().value);
    if(sameVoiceContext(job.context)){
      if(!job.text)document.getElementById('voice-message').textContent='Δεν εντοπίστηκε ομιλία. Το πρόχειρο δεν άλλαξε.';
      else if(voiceDraft?.id===job.id&&field.value===voiceDraft.original){voiceInsert(job.text,voiceDraft);document.getElementById('voice-message').textContent='Η υπαγόρευση προστέθηκε στο πρόχειρο. Έλεγξέ την πριν την Αποστολή.';}
      else {voicePending={text:job.text,context:job.context};document.getElementById('voice-result').textContent=job.text;document.getElementById('voice-message').textContent='Το πρόχειρο άλλαξε. Πρόσθεσε την υπαγόρευση όταν θέλεις.';}
    }
  }
  if(voicePending&&!sameVoiceContext(voicePending.context)){voicePending=null;document.getElementById('voice-result').textContent='';}
  document.getElementById('voice-insert').style.display=voicePending?'inline-block':'none';
  if(voiceCapture)document.getElementById('voice-message').textContent=voiceCapture.recorder?'Γράφει το μικρόφωνο. Πάτησε ⏹ όταν τελειώσεις — δεν στέλνεται μήνυμα.':'Αναμονή για πρόσβαση στο μικρόφωνο…';
  else if(job?.status==='error')document.getElementById('voice-message').textContent=job.error;
  else if(job?.kind==='prepare'&&job.status==='complete')document.getElementById('voice-message').textContent='Το τοπικό μοντέλο είναι έτοιμο για υπαγόρευση.';
  else if(job?.status==='cancelled')document.getElementById('voice-message').textContent='Η εργασία φωνής ακυρώθηκε. Το πρόχειρο παραμένει.';
  else if(active)document.getElementById('voice-message').textContent=job.kind==='prepare'?'Κατεβαίνουν τα αρχεία του τοπικού μοντέλου. Δεν γίνεται κλήση μοντέλου chat.':'Η υπαγόρευση μετατρέπεται σε κείμενο τοπικά…';
  if(!voiceConfigPending){document.getElementById('voice-model').value=voiceSnapshot.model;document.getElementById('voice-language').value=voiceSnapshot.language;}
  for(const id of ['voice-model','voice-language','voice-prepare'])document.getElementById(id).disabled=!!state.busy||!!active||!!voiceCapture||voiceConfigPending;
  document.getElementById('voice-availability').textContent=voiceSnapshot.models?.[voiceSnapshot.model]?i18nText('voice_ready','Έτοιμο — τοπική αναγνώριση, χωρίς αποστολή ήχου σε υπηρεσία.'):'Χρειάζεται λήψη των αρχείων μία φορά. Μετά λειτουργεί χωρίς σύνδεση.';
  voiceRecordControls();
}
function insertPendingVoice(){if(voicePending&&sameVoiceContext(voicePending.context)){voiceInsert(voicePending.text);voicePending=null;document.getElementById('voice-result').textContent='';document.getElementById('voice-insert').style.display='none';}}
window.addEventListener?.('beforeunload',()=>{if(voiceCapture)finishVoiceCapture(true);});

// ---------- actions ----------
async function send() {
  const el = document.getElementById("prompt");
  const text = el.value.trim();
  if(voiceCapture||voiceUploading||voiceSnapshot.job?.status==='transcribing'){showNotice('err','Ολοκλήρωσε ή ακύρωσε πρώτα την υπαγόρευση.');return;}
  if (!text || sending) return;
  if (!connected) { showNotice("err", "Συνδέσου πρώτα στις Ρυθμίσεις."); return; }
  if (busy) {
    sending = true;
    try {
      const r = await api("/api/steer", {text});
      if (r.error) throw new Error(r.error);
      el.value = ""; grow(el);
      addMsg("user", "🧭 " + text);
      showNotice("ok", "🧭 Η οδηγία στάλθηκε — θα ληφθεί υπόψη στο επόμενο βήμα.");
    } catch (err) {
      showNotice("err", err.message || String(err));
    } finally { sending = false; }
    return;
  }
  sending = true;
  let pending = addMsg("user", text);
  el.value = ""; grow(el);
  try {
    const r = await api("/api/send", {text,attachments});
    if (r.error) throw new Error(r.error);
    attachments=[];showAttachments();
    clearStream();
    if (r.conversation_changed) {
      clearStream(); chat.replaceChildren();
      addMsg("user", text);
      lastSeq = r.last_seq ?? lastSeq;
    }
  } catch (err) {
    pending.remove(); el.value = text; grow(el);
    showNotice("err", err.message); addStep("fatal", {text: err.message});
  } finally { sending = false; }
  await poll();
}

