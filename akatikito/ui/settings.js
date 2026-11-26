async function stopAgent() { await api("/api/stop", {}); }

async function newSession() {
  const s = await api("/api/session/new", {});
  if (s.error) { showNotice("err", s.error); return; }
  clearStream(); clearFileView(); chat.innerHTML = "";
  lastSeq = s.last_seq || lastSeq;
  updateBadges(s);
  loadSessions();
}

async function setMode(m) {
  const r = await api("/api/mode", {mode: m});
  if (r.error) { showNotice("err", r.error); return; }
  if (r.label) document.getElementById("mode-badge").textContent = r.label;
  document.getElementById("mode-hint").textContent = modeHints[m] || "";
}

async function setModel(m) { 
  const result = await api("/api/model", {model: m});
  if (result.ok) {
    // Ανανέωσε τα badges με το νέο μοντέλο
    const status = await api("/api/status");
    providerModels[status.provider] = status.model;
    if (activeProvider === status.provider) { document.getElementById("model-other").value = ""; fillModelPick(status.model); }
    updateBadges(status);
  } else if (result.error) showNotice("err", result.error);
}

async function loadSessions() {
  const r = await api("/api/sessions");
  const box = document.getElementById("sessions");
  box.innerHTML = "";
  if (!r.sessions || !r.sessions.length) {
    box.innerHTML = '<span class="hint">' + esc(i18nText('empty_sessions', 'Καμία αποθηκευμένη συνομιλία ακόμα.')) + '</span>';
    return;
  }
  for (const s of r.sessions) {
    const source=s.source?.platform||'desktop',filter=document.getElementById('session-source').value;
    if(filter&&filter!==source)continue;
    const row = document.createElement("div");
    row.className = "sess" + (s.id === currentSession && s.project === currentProject ? " active" : "");
    const open = document.createElement("button");
    open.className = "btn"; open.textContent = (s.pinned ? "📌 " : "") + (source==='desktop'?'':'['+source+'] ')+s.title + " · " + (s.project_name || "");
    open.title = s.project || "";
    open.onclick = async () => {
      if(source!=='desktop'){await viewChannelSession(s);return;}
      const st = await api("/api/session/open", {id: s.id, project:s.project});
      if (st.error) { showNotice("err", st.error); return; }
      connected = st.connected;
      clearStream(); clearFileView(); chat.innerHTML = "";
      renderTranscript(st.transcript || []);
      lastSeq = st.last_seq || lastSeq;
      updateBadges(st);
      await loadSessions();
    };
    const pin = document.createElement("button");
    pin.className = "btn"; pin.style.cssText = "width:32px;min-width:32px;padding:6px 0";
    pin.textContent = s.pinned ? "📌" : "📍"; pin.title = s.pinned ? "Ξεκαρφίτσωμα" : "Καρφίτσωμα στην κορυφή";
    pin.onclick = async () => {
      const st = await api("/api/session/pin", {id: s.id, project: s.project, pinned: !s.pinned});
      if (st.error) showNotice("err", st.error); else await loadSessions();
    };
    const del = document.createElement("button");
    del.className = "btn del"; del.textContent = "🗑";
    if(source!=='desktop'){del.disabled=true;del.title='Νέα συνομιλία με /new στο κανάλι';}
    del.onclick = async () => {
      if (!confirm("Να διαγραφεί η συνομιλία «" + s.title + "»;")) return;
      const st = await api("/api/session/delete", {id:s.id, project:s.project});
      if (st.error) { showNotice("err", st.error); return; }
      if (s.id === currentSession && s.project === currentProject) { clearStream(); chat.replaceChildren(); }
      await loadSessions();
    };
    row.append(open, pin, del);
    box.appendChild(row);
  }
}

async function loadBackups() {
  const r = await api("/api/backups");
  const sel = document.getElementById("backups");
  sel.innerHTML = "";
  for (const b of r.backups || []) {
    const o = document.createElement("option");
    o.value = b.backup; o.textContent = b.backup.replace(/__/g, " · ");
    sel.appendChild(o);
  }
}

async function restoreBackup() {
  const sel = document.getElementById("backups");
  if (!sel.value) return;
  const r = await api("/api/restore", {backup: sel.value});
  showNotice(r.ok ? "ok" : "err", r.ok ? "Επανήλθε: " + r.restored : r.error);
}

function renderAdvancedFeatures(s) {
  const box = document.getElementById("advanced-features-list");
  if (!box || !Array.isArray(s.advanced_catalog)) return;
  box.replaceChildren();
  const active = new Set((s.preferences && s.preferences.advanced_features) || []);
  for (const feat of s.advanced_catalog) {
    const row = document.createElement("label");
    row.style.cssText = "display:flex;align-items:flex-start;gap:8px;cursor:pointer;font-size:12px";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.style.width = "auto";
    cb.checked = !!feat.enabled;
    cb.onchange = async () => {
      if (cb.checked && feat.warning && !window.confirm(feat.warning + "\n\nΝα ενεργοποιηθεί;")) {
        cb.checked = false;
        return;
      }
      const next = new Set(active);
      if (cb.checked) next.add(feat.key); else next.delete(feat.key);
      const r = await api("/api/preferences", {advanced_features: Array.from(next), apply_now: true});
      if (r.error) { showNotice("err", r.error); cb.checked = !cb.checked; return; }
      updateBadges(r);
      showNotice("ok", (cb.checked ? "Ενεργοποιήθηκε: " : "Απενεργοποιήθηκε: ") + feat.label);
    };
    const txt = document.createElement("span");
    txt.innerHTML = "<strong>" + esc(feat.label) + "</strong><br><span class='hint'>" + esc(feat.description || "") + "</span>";
    row.append(cb, txt);
    box.appendChild(row);
  }
}

function updateUpgrade(s) {
  const u = s.self_update;
  const advList = (s.preferences && s.preferences.advanced_features) || [];
  const allowUpgrade = advList.includes("self_update") || !!u;
  const startBtn = document.getElementById("upgrade-start");
  if (startBtn) {
    startBtn.style.display = allowUpgrade ? "" : "none";
    startBtn.disabled = !!u || !!s.busy || !s.connected;
  }
  document.getElementById("upgrade-panel").style.display = u ? "block" : "none";
  document.getElementById("upgrade-apply").disabled = !u || !u.ready || !!s.busy || !!s.restart_required;
  document.getElementById("upgrade-state").textContent = u
    ? (s.restart_required ? "Εφαρμόστηκε — κλείσε και άνοιξε την εφαρμογή." : "Υποψήφιο · " + u.changed.length + " αρχεία · " + (u.ready ? "οι έλεγχοι πέρασαν" : "χρειάζεται έλεγχος")) : "";
  if (s.restart_required) showNotice("ok", "Η αναβάθμιση εφαρμόστηκε. Κλείσε και άνοιξε την εφαρμογή.");
  renderAdvancedFeatures(s);
}
async function startUpgrade() {
  const s = await api("/api/upgrade/start", {});
  if (s.error) { showNotice("err", s.error); return; }
  clearStream(); chat.replaceChildren(); lastSeq = s.last_seq || lastSeq; updateBadges(s);
  showNotice("ok", s.notice);
}
async function checkUpgrade() {
  const s = await api("/api/upgrade/check", {});
  if (s.error) showNotice("err", s.error);
}
async function applyUpgrade() {
  const s = await api("/api/upgrade/apply", {});
  showNotice(s.error ? "err" : "ok", s.error || s.notice);
}
async function cancelUpgrade() {
  const s = await api("/api/upgrade/cancel", {});
  if (s.error) { showNotice("err", s.error); return; }
  clearStream(); chat.replaceChildren(); renderTranscript(s.transcript || []); updateBadges(s);
  showNotice("ok", s.notice);
}

// ---------- ρυθμίσεις ----------
async function loadProviders() {
  const r = await api("/api/providers");
  const sel = document.getElementById("provider");
  sel.innerHTML = "";
  for (const p of r.providers || []) {
    const o = document.createElement("option");
    o.value = p; o.textContent = p;
    sel.appendChild(o);
  }
  if (r.current) sel.value = r.current;
  providerModels = r.models || {};
  if (r.model) providerModels[sel.value] = r.model;
  document.getElementById("base_url").value = r.base_url || "";
  document.getElementById("project").value = r.project || "";
  document.getElementById("max_steps").value = r.max_steps || 50;
  document.getElementById("verify").value = r.verify || "";

  suggestedModels = (r.suggested || {})[sel.value] || [];
  document.getElementById("provider-hint").textContent = (r.hints || {})[sel.value] || "";
  fillModelPick();
  // αποθηκευμένα κλειδιά
  savedKeys = r.keys || {};
  let html = "";
  for (const [name, short] of Object.entries(savedKeys)) {
    html += `<div class="keyrow"><span><span class="dot">●</span> ${esc(name)}</span><span>${esc(short)}</span></div>`;
  }
  document.getElementById("saved-keys").innerHTML = html ? "<h3 class='sec'>" + esc(i18nText('saved_keys_hdr', 'Αποθηκευμένα κλειδιά')) + "</h3>" + html : "";
  if (r.warning) showNotice("err", r.warning);
  onProvider();
  renderProvidersMasterList();
}

function onProvider() {
  const p = document.getElementById("provider").value;
  if (activeProvider && activeProvider !== p) {
    const model = document.getElementById("model-other").value.trim() || document.getElementById("model-pick").value;
    if (model) providerModels[activeProvider] = model;
    document.getElementById("api_key").value = "";
  }
  activeProvider = p;
  const saved = savedKeys[p];
  document.getElementById("key-hint").textContent = saved
    ? `Αποθηκευμένο κλειδί: ${saved} — άφησέ το κενό για να το χρησιμοποιήσει.`
    : "Δεν υπάρχει αποθηκευμένο κλειδί για αυτόν τον πάροχο — γράψε ένα παρακάτω.";
  document.getElementById("model-other").value = "";
  document.getElementById("custom-url-row").style.display = p === "Custom" ? "flex" : "none";
  document.getElementById("provider-hint").textContent = hints[p] || "";
  suggestedModels = suggested[p] || [];
  document.getElementById("model-pick").replaceChildren();
  fillModelPick(providerModels[p] || "");
}
let hints = {}, suggested = {}, savedKeys = {};
function fillModelPick(preferred = "") {
  const sel = document.getElementById("model-pick");
  const previous = preferred || document.getElementById("model-other").value.trim() || sel.value;
  const choices = [...new Set([previous, ...suggestedModels].filter(Boolean))];
  sel.replaceChildren(...choices.map(m => new Option(m, m)));
  if (previous) sel.value = previous;
}

async function loadModels() {
  const p = document.getElementById("provider").value;
  const base = document.getElementById("base_url").value;
  const key = document.getElementById("api_key").value;
  const r = await api("/api/models", {provider: p, base_url: base, api_key: key});
  if (r.error) { showNotice("err", r.error); return; }
  suggestedModels = r.models;
  fillModelPick();
  showNotice("ok", `Βρέθηκαν ${r.models.length} μοντέλα.`);
}

async function connect(confirmDestination=false) {
  const p = document.getElementById("provider").value;
  let base = document.getElementById("base_url").value;
  if (p !== "Custom") base = "";
  const model = (document.getElementById("model-other").value || document.getElementById("model-pick").value || "").trim();
  const r = await api("/api/connect", {
    provider: p, base_url: base, model: model,
    api_key: document.getElementById("api_key").value,
    project: document.getElementById("project").value,
    max_steps: document.getElementById("max_steps").value,
    verify: document.getElementById("verify").value,
    confirm_key_destination:confirmDestination === true,
  });
  if (r.requires_key_confirmation && !confirmDestination) {
    if (confirm('Να χρησιμοποιηθεί το αποθηκευμένο κλειδί στη διεύθυνση '+r.destination+';')) return connect(true);
    return;
  }
  if (r.error) { showNotice("err", r.error); return; }
  acceptConnection(r,p);
}

function acceptConnection(r,p){
  connected = true;
  providerModels[p] = r.model;
  document.getElementById("api_key").value = "";
  clearStream(); clearFileView(); chat.innerHTML = "";
  lastSeq = r.last_seq || 0;
  updateBadges(r);
  showTab("sess");
  loadSessions(); loadBackups();
  showNotice("ok", "Συνδέθηκες — όλα τα κλειδιά σου είναι αποθηκευμένα.");
}

let pluginView=null,pluginDraft=null;
function uiText(tag,text,parent){const el=document.createElement(tag);el.textContent=text;parent.appendChild(el);return el;}
function renderGateway(g){
  const panel=document.getElementById('gateway-state');
  if(!panel)return;
  const dotGw = document.getElementById('dot-gateway');
  if (dotGw && dotGw.classList) dotGw.classList.toggle('on', !!g.running);
  panel.textContent=g.running?i18nText('gw_running','Gateway ενεργό')+' · '+(g.provider||'')+' / '+(g.model||''):i18nText('gw_paused','Gateway σε παύση');
  for(const [name,row] of Object.entries(g.channels||{}))uiText('p',name+': '+row.state+(row.message?' · '+row.message:''),panel);
  if(g.queue?.held)uiText('p','Μηνύματα σε αναμονή σύνδεσης ή από παλιές ρυθμίσεις: '+g.queue.held+'. Διατηρούνται και δεν εκτελούνται σε διαφορετικό project/bot.',panel);
  const issues=g.queue?.issues||[];if(issues.length)uiText('p','Εργασίες που χρειάζονται προσοχή: '+issues.length+'. Αβέβαιες εργασίες δεν επαναλαμβάνονται αυτόματα· /new στο κανάλι για νέα συνεδρία.',panel);
}
async function pluginAction(body){const r=await api('/api/plugins/action',body);if(r.error)throw Error(r.error);return r;}
async function loadPlugins(){
  const r=await api('/api/plugins');if(r.error){showNotice('err',r.error);return;}
  pluginView=r;renderGateway(r.gateway||{});
  const toolPanel=document.getElementById('tool-plugins-catalog');
  if(toolPanel){
    toolPanel.replaceChildren();
    for(const tp of (r.tool_plugins||[])){
      const tpName = i18nText('tp_name_' + tp.id, tp.name);
      const tpDesc = i18nText('tp_desc_' + tp.id, tp.description || '');
      const card=uiText('div',`${tpName} (${tp.tool_name}) · ${tp.enabled?i18nText('lbl_enabled','🟢 Ενεργό'):i18nText('lbl_disabled','⚪ Απενεργοποιημένο')}`,toolPanel);
      card.className='field';
      uiText('p',tpDesc,card);
      learningButton(card,tp.enabled?i18nText('btn_disable','Απενεργοποίηση'):i18nText('btn_enable','Ενεργοποίηση'),async()=>{
        try{await pluginAction({action:'toggle_tool_plugin',id:tp.id,enabled:!tp.enabled});await loadPlugins();}
        catch(e){showNotice('err',e.message);}
      });
    }
  }
  const panel=document.getElementById('plugin-catalog');panel.replaceChildren();
  const rows=[...(r.available||[]),...(r.installed||[]).filter(x=>!(r.available||[]).some(a=>a.id===x.id))];
  renderDynamicChannelsList(rows, r.installed||[], r.channels||{});
  for(const row of rows){
    const card=uiText('div',row.name+' · '+row.version,panel);card.className='field';
    if (card.dataset) card.dataset.channelId = row.id;
    if (selectedChannelFilter && selectedChannelFilter !== row.id) card.style.display = 'none';
    uiText('p',i18nText('ch_desc_default', row.description||'Κανάλι επικοινωνίας'),card);
    const installed=(r.installed||[]).find(x=>x.id===row.id);
    if(installed){
      uiText('p','Εγκατεστημένο · '+installed.source,card);
      learningButton(card,'Ρύθμιση '+row.name,()=>configureChannel(row.id));
      learningButton(card,'Απεγκατάσταση',async()=>{if(!confirm('Να αφαιρεθεί το πρόσθετο '+row.name+'; Το ιστορικό παραμένει.'))return;try{await pluginAction({action:'remove',id:row.id});await loadPlugins();}catch(e){showNotice('err',e.message);}});
    }else learningButton(card,i18nText('btn_preview_install','Προεπισκόπηση / Εγκατάσταση'),()=>previewPlugin(row.source));
  }
}
async function previewPlugin(source){
  try{
    const r=await pluginAction({action:'preview',source});pluginDraft=r;
    const panel=document.getElementById('plugin-review');panel.replaceChildren();
    uiText('h3','Έλεγχος πρόσθετου: '+r.manifest.name,panel);
    uiText('p','Προέλευση: '+r.source+' · SHA-256: '+r.review,panel);
    uiText('p','Δηλωμένα δικαιώματα: '+r.manifest.permissions.join(' · '),panel);
    uiText('p','Το πρόσθετο είναι εκτελέσιμος κώδικας με τα δικαιώματα του Akatikito. Εγκατάστησέ το μόνο αν εμπιστεύεσαι την προέλευση και τον κώδικα.',panel);
    for(const [name,body] of Object.entries(r.parts)){uiText('strong',name,panel);const pre=uiText('pre',body,panel);pre.style.cssText='white-space:pre-wrap;max-height:240px;overflow:auto;font-size:11px';}
    learningButton(panel,'Εγκρίνω και εγκαθιστώ αυτή την έκδοση',async()=>{
      try{await pluginAction({action:'install',id:r.id,review:r.review});panel.replaceChildren();pluginDraft=null;await loadPlugins();showNotice('ok','Εγκαταστάθηκε. Ρύθμισε το κανάλι πριν ξεκινήσεις το gateway.');}catch(e){showNotice('err',e.message);}
    });
  }catch(e){showNotice('err',e.message);}
}
function configureChannel(id){
  const row=pluginView.config?.[id]||{};const panel=document.getElementById('plugin-config');panel.replaceChildren();
  uiText('h3','Σύνδεση '+id,panel);
  const field=(name,label,value='',type='text')=>{const box=uiText('div','',panel);box.className='field';uiText('label',label,box);const el=document.createElement('input');el.id='channel-'+name;el.type=type;el.value=value;box.appendChild(el);return el;};
  const token=field('token','Bot token'+(row.has_token?' — κενό για να κρατήσεις το υπάρχον':''),'','password');
  const users=field('users','Επιτρεπόμενα αριθμητικά ID χρηστών, με κόμμα',(row.allowed_users||[]).join(','));
  const chats=field('chats','ID γκρουπ/καναλιών με κόμμα — κενό: μόνο προσωπικά chats',(row.allowed_chats||[]).join(','));
  const project=field('project','Φάκελος project',row.project||currentProject||document.getElementById('project').value);
  const label=uiText('label','Λειτουργία agent',panel),mode=document.createElement('select');label.appendChild(mode);
  for(const [value,text] of [['ask','Ερώτηση — μόνο ανάγνωση'],['assistant','Βοηθός'],['auto','Αυτόματο'],['code','Κώδικας'],['debug','Debug']]){const opt=document.createElement('option');opt.value=value;opt.textContent=text;mode.appendChild(opt);}mode.value=row.mode||'ask';
  const enabled=field('enabled','Ενεργό στην επόμενη εκκίνηση gateway','','checkbox');enabled.checked=Boolean(row.enabled);
  uiText('p',id==='discord'?'Δημιούργησε bot στο Discord Developer Portal, ενεργοποίησε Message Content Intent και πρόσθεσέ το στον server. Με Developer Mode → Copy ID βρίσκεις τα αριθμητικά ID.':'Δημιούργησε bot στο @BotFather. Βάλε το δικό σου αριθμητικό Telegram ID στη λίστα χρηστών. Τα usernames δεν αρκούν.',panel);
  uiText('p','Χρησιμοποιείται ο αποθηκευμένος πάροχος/μοντέλο κατά την εκκίνηση του gateway. Αρχεία και φωνητικά από κανάλια δεν υποστηρίζονται ακόμη.',panel);
  learningButton(panel,'Αποθήκευση σύνδεσης',async()=>{
    const split=x=>x.split(',').map(s=>s.trim()).filter(Boolean);
    try{await pluginAction({action:'configure',id,config:{token:token.value,allowed_users:split(users.value),allowed_chats:split(chats.value),project:project.value,mode:mode.value,enabled:enabled.checked}});token.value='';panel.replaceChildren();await loadPlugins();showNotice('ok','Αποθηκεύτηκε. Πάτησε Έναρξη gateway όταν θέλεις.');}catch(e){showNotice('err',e.message);}
  });
}
async function gatewayAction(action){try{await pluginAction({action});await loadPlugins();}catch(e){showNotice('err',e.message);}}
async function viewChannelSession(session){
  const r=await api('/api/channel/session',{id:session.id,project:session.project});if(r.error){showNotice('err',r.error);return;}
  let overlay=document.getElementById('channel-session-overlay');overlay.replaceChildren();overlay.style.cssText='position:fixed;inset:30px;z-index:90;background:var(--panel);border:1px solid var(--border);padding:20px;overflow:auto;display:block';
  uiText('h2',r.title+' · '+r.source.platform,overlay);uiText('p','Προεπισκόπηση ιστορικού. Η συνέχεια γίνεται από το αντίστοιχο κανάλι.',overlay);
  learningButton(overlay,'Κλείσιμο',()=>{overlay.style.display='none';});learningButton(overlay,'Ανανέωση',()=>viewChannelSession(session));
  for(const row of r.transcript||[]){uiText('strong',row.role==='user'?'Χρήστης':'Akatikito',overlay);const text=uiText('pre',row.text,overlay);text.style.whiteSpace='pre-wrap';}
}

let currentTheme = 'dark', currentFontSize = 14, recentProjects = [], cachedSkills = [], cachedRoutines = [];
function applyTheme(theme) {
  currentTheme = theme === 'light' ? 'light' : 'dark';
  if (document.body && typeof document.body.setAttribute === 'function') {
    document.body.setAttribute('data-theme', currentTheme);
  }
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = currentTheme === 'light' ? '🌙' : '☀️';
}
async function toggleTheme() {
  const next = currentTheme === 'light' ? 'dark' : 'light';
  applyTheme(next);
  try { await api('/api/preferences', {ui_theme: next}); } catch (_) {}
}
function applyFontSize(size) {
  const n = Math.max(12, Math.min(20, parseInt(size || 14, 10) || 14));
  currentFontSize = n;
  if (document.documentElement?.style) document.documentElement.style.setProperty('--ui-font', n + 'px');
  const v = document.getElementById('font-size-val'), sv = document.getElementById('settings-font-val'), sr = document.getElementById('settings-font-size');
  if (v) v.textContent = n + 'px';
  if (sv) sv.textContent = n + 'px';
  if (sr && document.activeElement !== sr) sr.value = String(n);
}
async function adjustFontSize(delta) {
  const next = Math.max(12, Math.min(20, currentFontSize + delta));
  if (next === currentFontSize) return;
  applyFontSize(next);
  try { await api('/api/preferences', {ui_font_size: next}); } catch (_) {}
}
async function openFileInOs(path, reveal=false) {
  if (!path) return;
  try {
    const r = await api('/api/files/open-os', {path, reveal: !!reveal});
    if (r.error) showNotice('err', r.error);
  } catch (e) { showNotice('err', e.message || String(e)); }
}
function openCurrentFileInOs(reveal=false) {
  if (fileView && fileView.path) openFileInOs(fileView.path, reveal);
}
function toggleRecentProjectsMenu() {
  const menu = document.getElementById('proj-menu');
  if (!menu) return;
  if (menu.style.display === 'flex') { menu.style.display = 'none'; return; }
  menu.replaceChildren();
  const hdr = document.createElement('div');
  hdr.className = 'hint';
  hdr.style.padding = '2px 6px 4px';
  hdr.textContent = i18nText('proj_recent_hdr', 'Πρόσφατα Projects');
  menu.appendChild(hdr);
  const list = [...new Set([currentProject, ...recentProjects].filter(Boolean))];
  for (const proj of list) {
    const btn = document.createElement('button');
    btn.className = 'cmd-item';
    btn.textContent = (proj === currentProject ? '✓ ' : '📁 ') + proj;
    btn.onclick = () => { menu.style.display = 'none'; switchProjectTo(proj); };
    menu.appendChild(btn);
  }
  const sep = document.createElement('div');
  sep.style.borderTop = '1px solid var(--border)';
  sep.style.margin = '4px 0';
  menu.appendChild(sep);
  const browse = document.createElement('button');
  browse.className = 'cmd-item';
  browse.textContent = i18nText('proj_browse_other', '📁 Άνοιγμα άλλου φακέλου…');
  browse.onclick = () => { menu.style.display = 'none'; browseAndSwitchProject(); };
  menu.appendChild(browse);
  menu.style.display = 'flex';
}
async function switchProjectTo(proj) {
  if (!proj || proj === currentProject) return;
  if (busy) { showNotice('err', 'Περίμενε να ολοκληρωθεί η εργασία.'); return; }
  showNotice('ok', 'Αλλαγή project: ' + proj);
  const r = await api('/api/project/switch', {project: proj});
  if (r.error) { showNotice('err', r.error); return; }
  updateBadges(r);
  await loadSessions();
  loadProjectProfile().catch(() => {});
}
async function browseAndSwitchProject() {
  const picked = await api('/api/pick-folder', {});
  if (picked && picked.path) await switchProjectTo(picked.path);
  else if (picked && picked.error) showNotice('err', picked.error);
}
let cmdPaletteItems = [], cmdPaletteIdx = 0;
function buildCommandPaletteItems() {
  const items = [
    {label: i18nText('cmd_new_chat', '＋ Νέα Συνομιλία'), group: 'Chat', run: () => newSession()},
    {label: i18nText('cmd_toggle_theme', '☀️/🌙 Αλλαγή Θέματος (Φωτεινό / Σκοτεινό)'), group: 'UI', run: () => toggleTheme()},
    {label: i18nText('cmd_open_files', '📄 Άνοιγμα / Κλείσιμο Μπάρας Αρχείων'), group: 'Files', run: () => toggleFiles()},
    {label: i18nText('cmd_check_updates', '🔄 Έλεγχος Ενημερώσεων GitHub'), group: 'Update', run: () => { showTab('sess'); checkGithubUpdates(); }},
    {label: i18nText('cmd_health_check', '🩺 Έλεγχος Υγείας Συστήματος'), group: 'System', run: () => { showTab('sess'); loadSystemHealth(); }},
    {label: i18nText('proj_browse_other', '📁 Άνοιγμα άλλου φακέλου…'), group: 'Project', run: () => browseAndSwitchProject()},
    {label: i18nText('tab_providers', '🤖 Πάροχοι / Μοντέλα'), group: 'Tab', run: () => showTab('providers')},
    {label: i18nText('tab_plugins', '🔌 Πρόσθετα'), group: 'Tab', run: () => showTab('plugins')},
    {label: i18nText('tab_skills', '📘 Skills'), group: 'Tab', run: () => showTab('skills')},
    {label: i18nText('tab_memory', '🧠 Μνήμη'), group: 'Tab', run: () => showTab('memory')},
    {label: i18nText('tab_mobile', '📱 Κινητό'), group: 'Tab', run: () => showTab('mobile')},
    {label: i18nText('tab_set', '⚙️ Ρυθμίσεις'), group: 'Tab', run: () => showTab('set')},
  ];
  for (const p of recentProjects) {
    if (p && p !== currentProject) items.push({label: '📁 ' + p, group: 'Project', run: () => switchProjectTo(p)});
  }
  for (const r of cachedRoutines) {
    const nm = r.builtin ? i18nText('rtn_' + r.id, r.name) : r.name;
    items.push({label: '⚡ ' + nm, group: 'Routine', run: () => { showTab('sess'); useQuickPrompt(r.prompt); }});
  }
  for (const sk of cachedSkills) {
    items.push({label: '/' + sk.name + ' — ' + (sk.description || ''), group: 'Skill', run: () => { showTab('sess'); useQuickPrompt('/' + sk.name + ' '); }});
  }
  return items;
}
function openCommandPalette() {
  const pal = document.getElementById('cmd-palette');
  const inp = document.getElementById('cmd-input');
  if (!pal || !inp) return;
  pal.style.display = 'flex';
  inp.value = '';
  cmdPaletteIdx = 0;
  renderCommandPalette('');
  inp.focus();
}
function closeCommandPalette() {
  const pal = document.getElementById('cmd-palette');
  if (pal) pal.style.display = 'none';
}
function renderCommandPalette(query) {
  const listEl = document.getElementById('cmd-list');
  if (!listEl) return;
  const q = (query || '').trim().toLowerCase();
  const all = buildCommandPaletteItems();
  cmdPaletteItems = q ? all.filter(x => (x.label + ' ' + x.group).toLowerCase().includes(q)) : all;
  if (cmdPaletteIdx >= cmdPaletteItems.length) cmdPaletteIdx = 0;
  listEl.replaceChildren();
  cmdPaletteItems.slice(0, 20).forEach((item, idx) => {
    const btn = document.createElement('button');
    btn.className = 'cmd-item' + (idx === cmdPaletteIdx ? ' active' : '');
    const left = document.createElement('span'); left.textContent = item.label;
    const right = document.createElement('span'); right.className = 'hint'; right.textContent = item.group;
    btn.append(left, right);
    btn.onclick = () => { closeCommandPalette(); item.run(); };
    listEl.appendChild(btn);
  });
}
function onCmdKey(e) {
  if (e.key === 'Escape') { e.preventDefault(); closeCommandPalette(); return; }
  if (e.key === 'ArrowDown') { e.preventDefault(); cmdPaletteIdx = (cmdPaletteIdx + 1) % Math.max(1, cmdPaletteItems.length); renderCommandPalette(e.target.value); return; }
  if (e.key === 'ArrowUp') { e.preventDefault(); cmdPaletteIdx = (cmdPaletteIdx - 1 + Math.max(1, cmdPaletteItems.length)) % Math.max(1, cmdPaletteItems.length); renderCommandPalette(e.target.value); return; }
  if (e.key === 'Enter' && cmdPaletteItems[cmdPaletteIdx]) { e.preventDefault(); const fn = cmdPaletteItems[cmdPaletteIdx].run; closeCommandPalette(); fn(); }
}
function updateSlashMenu(textarea) {
  const menu = document.getElementById('slash-menu');
  if (!menu || !textarea) return;
  const val = String(textarea.value || '');
  if (!val.startsWith('/') || val.includes(' ') || val.includes('\n')) {
    if (typeof ChatPro !== 'undefined' && ChatPro.updateAtMenu && ChatPro.updateAtMenu(textarea, menu)) return;
    menu.style.display = 'none'; return;
  }
  const q = val.slice(1).toLowerCase();
  const base = [{name: 'compact', description: '🗜️ Συμπίεση ιστορικού (εξοικονόμηση tokens)'}, {name: 'learn', description: 'Distill current conversation into a reusable Skill'}, ...cachedSkills];
  const matches = base.filter(s => (s.name || '').toLowerCase().includes(q)).slice(0, 8);
  if (!matches.length) { menu.style.display = 'none'; return; }
  menu.replaceChildren();
  for (const sk of matches) {
    const btn = document.createElement('button'); btn.className = 'cmd-item';
    btn.textContent = '/' + sk.name + (sk.description ? ' — ' + sk.description : '');
    btn.onclick = () => {
      if (sk.name === 'compact' && typeof ChatPro !== 'undefined' && ChatPro.runCompact) { textarea.value = ''; menu.style.display = 'none'; ChatPro.runCompact(); return; }
      textarea.value = '/' + sk.name + ' '; menu.style.display = 'none'; textarea.focus(); grow(textarea);
    };
    menu.appendChild(btn);
  }
  menu.style.display = 'flex';
}

let mobileBridgeActive = false;
async function refreshMobileBridge() {
  try {
    const r = await api('/api/mobile-bridge/status', {});
    renderMobileBridge(r);
  } catch (_) {}
}
function renderMobileBridge(r) {
  mobileBridgeActive = !!(r && r.active);
  const btn = document.getElementById('mobile-bridge-btn'), info = document.getElementById('mobile-bridge-info'), pill = document.getElementById('mobile-status-pill');
  if (btn) btn.textContent = mobileBridgeActive ? i18nText('mobile_btn_stop', '⏹ Απενεργοποίηση Σύνδεσης Κινητού') : i18nText('mobile_btn_start', '📱 Ενεργοποίηση Σύνδεσης Κινητού (QR / Tailscale)');
  if (pill) { pill.textContent = mobileBridgeActive ? '● Ενεργή' : '● Ανενεργή'; pill.classList?.toggle('on', mobileBridgeActive); }
  if (!info) return;
  info.replaceChildren();
  if (!mobileBridgeActive) return;
  const frame = document.createElement('div'); frame.className = 'mobile-qr-frame';
  if (r.qr_data_uri) {
    const img = document.createElement('img'); img.src = r.qr_data_uri; img.alt = 'Mobile Pairing QR Code';
    frame.appendChild(img);
  }
  const via = r.via_tailscale ? i18nText('mobile_via_ts', '🔒 Tailscale (4G/5G & Wi-Fi)') : i18nText('mobile_via_lan', '⚠️ Τοπικό Wi-Fi (LAN HTTP · προτείνεται Tailscale)');
  const scanHint = i18nText('mobile_scan_hint', 'Συνδεδεμένες συσκευές: {n} · Σκάναρε το QR από το Akatikito Mobile!').replace('{n}', String(r.paired_devices || 0));
  const urls = [r.url, (r.lan_url && r.lan_url !== r.url) ? `LAN: ${r.lan_url}` : ''].filter(Boolean).join(' · ');
  const meta = document.createElement('div'); meta.textContent = `${via}\n${urls}\n${scanHint}`; frame.appendChild(meta);
  if (r.pairing_uri) {
    const cp = document.createElement('button'); cp.className = 'btn'; cp.style.width = 'auto'; cp.textContent = '📋 Αντιγραφή συνδέσμου σύζευξης';
    cp.onclick = () => copyText(r.pairing_uri); frame.appendChild(cp);
  }
  info.appendChild(frame);
}
async function toggleMobileBridge() {
  const path = mobileBridgeActive ? '/api/mobile-bridge/stop' : '/api/mobile-bridge/start';
  const r = await api(path, {});
  if (r.error) { showNotice('err', r.error); return; }
  renderMobileBridge(r);
  if (r.active && r.pairing_uri) {
    copyText(r.pairing_uri);
    showNotice('ok', 'Ενεργοποιήθηκε η Σύνδεση Κινητού! Το Pairing URI αντιγράφηκε στο πρόχειρο.');
  }
}

let selectedChannelFilter = '';
function selectPluginPane(which, channelId) {
  selectedChannelFilter = (which === 'channels' && channelId) ? String(channelId) : '';
  for (const name of ['channels', 'gateway', 'python', 'mcp']) {
    const pane = document.getElementById('plg-pane-' + name);
    const nav = document.getElementById('plg-nav-' + name);
    if (pane) pane.style.display = name === which ? '' : 'none';
    if (nav && nav.classList) nav.classList.toggle('active', name === which && !selectedChannelFilter);
  }
  const chList = document.getElementById('md-channels-list');
  if (chList && chList.children) {
    for (const btn of chList.children) {
      if (btn.classList) btn.classList.toggle('active', which === 'channels' && btn.dataset?.channelId === selectedChannelFilter);
    }
  }
  const cat = document.getElementById('plugin-catalog');
  if (cat && cat.children) {
    for (const card of cat.children) {
      const cid = card.dataset?.channelId || '';
      card.style.display = (!selectedChannelFilter || !cid || cid === selectedChannelFilter) ? '' : 'none';
    }
  }
}
function channelIconFor(id) {
  const k = String(id || '').toLowerCase();
  if (k.includes('telegram')) return '✈️ ';
  if (k.includes('discord')) return '💬 ';
  if (k.includes('mail') || k.includes('gmail')) return '📧 ';
  if (k.includes('slack')) return '💼 ';
  if (k.includes('whatsapp')) return '📱 ';
  if (k.includes('sms')) return '📲 ';
  return '🔌 ';
}
function renderDynamicChannelsList(rows, installedList, channelsCfg) {
  const list = document.getElementById('md-channels-list');
  if (!list) return;
  list.replaceChildren();
  let anyActive = false;
  for (const row of (rows || [])) {
    const inst = (installedList || []).find(x => x.id === row.id);
    const cfg = (channelsCfg || {})[row.id] || {};
    const isOn = Boolean(inst && cfg.enabled);
    if (isOn) anyActive = true;
    const btn = document.createElement('button');
    btn.className = 'md-item' + (selectedChannelFilter === row.id ? ' active' : '');
    if (btn.dataset) btn.dataset.channelId = row.id;
    const lbl = document.createElement('span');
    lbl.textContent = channelIconFor(row.id) + (row.name || row.id);
    const dot = document.createElement('span');
    dot.className = 'md-dot' + (isOn ? ' on' : '');
    dot.title = isOn ? 'Ενεργό' : (inst ? 'Εγκατεστημένο' : 'Διαθέσιμο');
    btn.append(lbl, dot);
    btn.onclick = () => {
      selectPluginPane('channels', row.id);
      if (inst) configureChannel(row.id);
    };
    list.appendChild(btn);
  }
  const dotCh = document.getElementById('dot-channels');
  if (dotCh && dotCh.classList) dotCh.classList.toggle('on', anyActive);
}
function renderDynamicMcpList(mcp) {
  const list = document.getElementById('md-mcp-list');
  if (!list) return;
  list.replaceChildren();
  const configured = mcp?.configured || [];
  const connected = mcp?.connected || [];
  for (const name of configured) {
    const isOn = connected.includes(name);
    const btn = document.createElement('button');
    btn.className = 'md-item';
    const lbl = document.createElement('span');
    lbl.textContent = '🧰 ' + name;
    const dot = document.createElement('span');
    dot.className = 'md-dot' + (isOn ? ' on' : '');
    btn.append(lbl, dot);
    btn.onclick = () => selectPluginPane('mcp');
    list.appendChild(btn);
  }
  const dotMcp = document.getElementById('dot-mcp');
  if (dotMcp && dotMcp.classList) dotMcp.classList.toggle('on', connected.length > 0);
}
function filterPluginsMasterList(query) {
  const q = String(query || '').trim().toLowerCase();
  for (const containerId of ['md-channels-list', 'md-mcp-list']) {
    const box = document.getElementById(containerId);
    if (!box || !box.children) continue;
    for (const row of box.children) {
      const txt = (row.textContent || '').toLowerCase();
      row.style.display = (!q || txt.includes(q)) ? '' : 'none';
    }
  }
}
function renderProvidersMasterList() {
  const list = document.getElementById('providers-md-list');
  const sel = document.getElementById('provider');
  if (!list || !sel || !sel.options) return;
  list.replaceChildren();
  const hdr = document.createElement('div');
  hdr.className = 'md-list-sec';
  hdr.textContent = 'LLM Providers';
  list.appendChild(hdr);
  const current = sel.value;
  for (const opt of sel.options) {
    const name = opt.value;
    const btn = document.createElement('button');
    btn.className = 'md-item' + (name === current ? ' active' : '');
    const lbl = document.createElement('span');
    lbl.textContent = name;
    const dot = document.createElement('span');
    dot.className = 'md-dot' + ((savedKeys && savedKeys[name]) ? ' on' : '');
    btn.append(lbl, dot);
    btn.onclick = () => {
      sel.value = name;
      onProvider();
      renderProvidersMasterList();
    };
    list.appendChild(btn);
  }
}
function filterSessionsList(query) {
  const box = document.getElementById('sessions');
  if (!box || !box.children) return;
  const q = String(query || '').trim().toLowerCase();
  for (const row of box.children) {
    const txt = (row.textContent || '').toLowerCase();
    row.style.display = (!q || txt.includes(q)) ? '' : 'none';
  }
  if (typeof StudioUI !== 'undefined' && StudioUI.searchSessionsFullText) StudioUI.searchSessionsFullText(query);
}
function filterSkillsCards(query) {
  const box = document.getElementById('skills-content');
  if (!box || !box.children) return;
  const q = String(query || '').trim().toLowerCase();
  for (const child of box.children) {
    if (child.tagName === 'P') continue;
    const txt = (child.textContent || '').toLowerCase();
    child.style.display = (!q || txt.includes(q)) ? '' : 'none';
  }
}

function toggleSkillImportForm() {
  const f = document.getElementById('skill-import-form');
  if (f) f.style.display = f.style.display === 'none' ? 'flex' : 'none';
}
function loadSkillImportFile(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const ta = document.getElementById('skill-import-md');
    if (ta) ta.value = String(reader.result || '');
  };
  reader.readAsText(file, 'utf-8');
}
async function importExternalSkill() {
  const ta = document.getElementById('skill-import-md');
  const scope = document.getElementById('skill-import-scope')?.value || 'user';
  const md = (ta?.value || '').trim();
  if (!md) { showNotice('err', 'Επικόλλησε ή φόρτωσε ένα αρχείο SKILL.md.'); return; }
  const r = await api('/api/learning/action', {
    action: 'skill_import',
    project: learningProject || currentProject,
    markdown: md,
    scope,
  });
  if (r.error) { showNotice('err', r.error); return; }
  if (ta) ta.value = '';
  toggleSkillImportForm();
  showNotice('ok', r.note || 'Η συνταγή εισήχθη με επιτυχία!');
  await loadLearning(learningOffset);
}

// ---------- εκκίνηση ----------
async function init() {
  const s = await api("/api/status");
  document.getElementById("ver").textContent = "v" + (s.version || "");
  // modes
  const pr = await api('/api/providers');
  hints = pr.hints || {}; suggested = pr.suggested || {};
  await loadProviders();
  const modeSel = document.getElementById("mode");
  modeSel.innerHTML = "";
  const modesList = ["auto", "ask", "architect", "code", "debug", "assistant"];
  modeLabels = {auto:"Αυτόματο", assistant:"Βοηθός", ask:"Ερώτηση", architect:"Αρχιτέκτονας", code:"Κώδικας", debug:"Debug"};
  modeHints = {
    assistant: "έρευνα, έγγραφα και υπενθυμίσεις",
    auto: "σχεδιάζει, υλοποιεί και ελέγχει ανάλογα με το αίτημά σου",
    ask: "απαντάει μόνο από όσα διαβάζει — δεν αλλάζει τίποτα",
    architect: "σχεδιάζει το πλάνο (PLAN.md), δεν γράφει κώδικα",
    code: "αλλάζει, τρέχει, διορθώνει — πλήρης δουλειά",
    debug: "ψάχνει σφάλματα με tests και logs",
  };
  for (const m of modesList) {
    const o = document.createElement("option");
    o.value = m; o.textContent = modeLabels[m];
    modeSel.appendChild(o);
  }
  const setupComplete=pr.onboarding?.complete??Boolean(pr.project&&(pr.model||pr.models?.[pr.current]));
  if(!s.connected&&!setupComplete){updateBadges(s);showTab('providers');await openSetup(pr);}
  else if (s.connected) {
    connected = true;
    renderTranscript(s.transcript || []);
    if (s.stream && s.busy) syncStream(s.stream);
    modeSel.value = s.auto_mode ? "auto" : s.mode;
    document.getElementById("mode-hint").textContent = modeHints[s.mode] || "";
    showTab("sess");
    loadSessions(); loadBackups();
    updateBadges(s);
  } else if (s.project) {
    await connect();
  } else {
    updateBadges(s);
    showTab("providers");
  }
  await loadSessions();
  loadRoutines().catch(() => {});
  loadProjectProfile().catch(() => {});
  pollTimer = setInterval(poll, 250);
}
init().catch(err => showNotice("err", "Δεν ξεκίνησε η εφαρμογή: " + err.message));
