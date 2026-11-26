let connected = false, busy = false, lastSeq = 0, pollTimer = null;
let modelOther = "", suggestedModels = [], modeLabels = {}, modeHints = {};
let providerModels = {}, activeProvider = "", currentModel = "", currentProvider = "";
let currentSession = "", currentProject = "", polling = false, sending = false, noticeTimer = null, lastNotice = "";

let filesContext = '', filesProject = '', filesSession = '', filesSignature = '';
let fileRequest = 0, fileReading = false, fileView = null, fileBack = [];

const apiToken = decodeURIComponent(((window.location?.hash || '').match(/(?:^#|&)token=([^&]+)/) || [,''])[1]);
async function api(path, body) {
  const headers = {'X-Akatikito-Token':apiToken};
  const opt = body ? {method:"POST", headers:{...headers,"Content-Type":"application/json"}, body: JSON.stringify(body)} : {headers};
  const r = await fetch(path, opt);
  return r.json();
}

function showTab(name) {
  if (name === 'llm') name = 'providers';
  const allTabs = ['sess','agenda','providers','plugins','skills','memory','mobile','set'];
  if (!allTabs.includes(name)) return;
  for (const key of allTabs) {
    const btn = document.getElementById('tab-' + key);
    const view = document.getElementById('view-' + key);
    if (btn) {
      btn.classList.toggle('active', name === key);
      btn.setAttribute?.('aria-selected', String(name === key));
    }
    if (view) view.style.display = name === key ? 'flex' : 'none';
  }
  document.getElementById('management').style.display = name === 'sess' ? 'none' : 'block';
  document.getElementById('chat-workspace').style.display = name === 'sess' ? 'flex' : 'none';
  document.getElementById('files-panel').style.display = name === 'sess' ? '' : 'none';
  document.getElementById('files-toggle').disabled = name !== 'sess';
  if (name === 'agenda') { loadAgenda().catch(() => {}); loadScheduledJobs().catch(() => {}); }
  if (name === 'skills' || name === 'memory') loadLearning().catch(e => showNotice('err', e.message));
  if (name === 'memory' && typeof loadMemory === 'function') loadMemory().catch(e => showNotice('err', e.message));
  if (name === 'plugins') loadPlugins().catch(e => showNotice('err', e.message));
  if (name === 'mobile' && typeof refreshMobileBridge === 'function') refreshMobileBridge().catch(() => {});
}

function grow(el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 140) + "px"; }
function onKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (e.target && String(e.target.value || "").trim() === "/compact" && typeof ChatPro !== "undefined" && ChatPro.runCompact) {
      e.target.value = ""; grow(e.target); ChatPro.runCompact(); return;
    }
    send();
  }
}

async function pasteInto(el) {
  const start = el.selectionStart ?? el.value.length;
  const end = el.selectionEnd ?? start;
  try {
    const payload = el && el.id === "prompt"
      ? {allow_image: true, project: typeof currentProject !== "undefined" ? currentProject : "", session_id: typeof currentSession !== "undefined" ? currentSession : ""}
      : {};
    const r = await api("/api/paste", payload);
    if (r.error) throw new Error(r.error);
    if (!r.text && r.attachment && typeof addPastedAttachment === "function") {
      addPastedAttachment(r.attachment);
      return;
    }
    if (el && el.id === "prompt" && r.text && typeof ChatUX !== "undefined" && ChatUX.maybeAttachLargePaste) {
      if (await ChatUX.maybeAttachLargePaste(r.text)) return;
    }
    el.focus();
    el.setRangeText(r.text || "", start, end, "end");
    el.dispatchEvent(new Event("input", {bubbles:true}));
  } catch (err) { showNotice("err", "Δεν έγινε επικόλληση: " + err.message); }
}
async function pasteKey() { await pasteInto(document.getElementById("api_key")); }
async function copyText(text) {
  try {
    const r = await api("/api/copy", {text: String(text || "")});
    if (!r.ok) throw new Error(r.error || "Το πρόχειρο δεν είναι διαθέσιμο.");
    showNotice("ok", "Αντιγράφηκε — κάνε επικόλληση όπου θέλεις.");
  } catch (err) { showNotice("err", "Δεν έγινε αντιγραφή: " + err.message); }
}

function toggleFiles(open) {
  const panel = document.getElementById('files-panel');
  panel.hidden = typeof open === 'boolean' ? !open : !panel.hidden;
  document.getElementById('files-toggle').ariaExpanded = String(!panel.hidden);
}

function clearFileView() {
  fileRequest++; fileView = null; fileBack = []; fileReading = false;
  document.getElementById('file-preview').hidden = true;
  document.getElementById('file-text').textContent = '';
  document.getElementById('files-error').textContent = '';
  document.getElementById('file-path').value = '';
  document.getElementById('open-file').disabled = false;
}

function renderFileList(files) {
  const rows = Array.isArray(files) ? files : [];
  const signature = JSON.stringify(rows);
  if (signature === filesSignature) return;
  filesSignature = signature;
  const list = document.getElementById('file-list'); list.replaceChildren();
  document.getElementById('files-count').textContent = String(rows.length);
  if (!rows.length) {
    const hint = document.createElement('span'); hint.className = 'hint';
    hint.textContent = i18nText('empty_files', 'Άνοιξε ένα αρχείο. Εδώ εμφανίζονται και οι επιτυχημένες αλλαγές του agent.');
    list.appendChild(hint);
  }
  for (const row of rows) {
    const button = document.createElement('button'); button.className = 'btn file-row';
    button.draggable = true;
    button.ondragstart = (e) => { if (e.dataTransfer) { e.dataTransfer.setData('application/x-akatikito-file', row.path); e.dataTransfer.setData('text/plain', '@' + row.path); } };
    const name = document.createElement('span'); name.className = 'file-name'; name.textContent = row.path;
    const action = document.createElement('span'); action.className = 'file-action'; action.textContent = row.action || '';
    button.append(name, action); button.onclick = () => openProjectFile(row.path); list.appendChild(button);
  }
}

function updateFiles(s) {
  const project = s.files_project || s.project || s.app_root || '';
  const session = s.session_id || '';
  const key = JSON.stringify([project, session]);
  if (key !== filesContext) { clearFileView(); filesSignature = ''; filesContext = key; }
  filesProject = project; filesSession = session;
  document.getElementById('files-root').textContent = project || 'Φάκελος εφαρμογής';
  renderFileList(s.files || []);
}

function showFile(data, history) {
  fileView = data; fileBack = history;
  document.getElementById('file-preview').hidden = false;
  document.getElementById('file-title').textContent = data.path;
  const kindPill = document.getElementById('file-kind-pill');
  if (kindPill) {
    const ext = String(data.path || '').includes('.') ? String(data.path).split('.').pop().toUpperCase().slice(0, 8) : '';
    const lbl = data.kind === 'image' ? 'IMG' : data.kind === 'csv' ? 'CSV' : data.kind === 'markdown' ? 'MD' : ext;
    kindPill.textContent = lbl;
    kindPill.style.display = lbl ? 'inline-block' : 'none';
  }
  document.getElementById('file-path').value = data.path;
  const textEl = document.getElementById('file-text');
  const richEl = document.getElementById('file-rich');
  const mdBtn = document.getElementById('file-md-toggle');
  textEl.textContent = data.text || '';
  textEl.scrollTop = 0;
  if (richEl) { richEl.replaceChildren(); richEl.style.display = 'none'; }
  if (mdBtn) mdBtn.style.display = 'none';
  textEl.style.display = '';
  if (data.kind === 'image' && data.data_uri && richEl) {
    const img = document.createElement('img');
    img.src = data.data_uri;
    img.alt = data.path;
    img.style.maxWidth = '100%';
    img.style.borderRadius = '6px';
    img.style.display = 'block';
    richEl.appendChild(img);
    richEl.style.display = 'block';
    textEl.style.display = 'none';
    document.getElementById('file-info').textContent = `Εικόνα (${data.mime || ''}) · ${data.size} bytes`;
  } else if (data.kind === 'csv' && Array.isArray(data.headers) && richEl) {
    const tbl = document.createElement('table');
    tbl.style.width = '100%';
    tbl.style.borderCollapse = 'collapse';
    tbl.style.fontSize = '11.5px';
    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    for (const h of data.headers) {
      const th = document.createElement('th');
      th.textContent = String(h);
      th.style.textAlign = 'left';
      th.style.padding = '4px 6px';
      th.style.borderBottom = '1px solid var(--border)';
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    tbl.appendChild(thead);
    const tbody = document.createElement('tbody');
    for (const row of (data.rows || [])) {
      const tr = document.createElement('tr');
      for (const cell of row) {
        const td = document.createElement('td');
        td.textContent = String(cell);
        td.style.padding = '3px 6px';
        td.style.borderBottom = '1px solid var(--border)';
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    tbl.appendChild(tbody);
    richEl.appendChild(tbl);
    richEl.style.display = 'block';
    textEl.style.display = 'none';
    document.getElementById('file-info').textContent = `Πίνακας CSV · ${(data.rows || []).length} γραμμές · ${data.size} bytes`;
  } else {
    if (data.kind === 'markdown' && mdBtn) mdBtn.style.display = '';
    document.getElementById('file-info').textContent = data.size === 0 ? 'Κενό αρχείο' :
      `Γραμμές ${data.line_start}–${data.line_end} · ${data.encoding} · ${data.size} bytes`;
  }
  document.getElementById('file-prev').disabled = !history.length;
  document.getElementById('file-next').disabled = data.next == null;
  renderFileList(data.files || []); toggleFiles(true);
}

function toggleMarkdownPreview() {
  const richEl = document.getElementById('file-rich');
  const textEl = document.getElementById('file-text');
  if (!richEl || !textEl || !fileView) return;
  if (richEl.style.display === 'block') {
    richEl.style.display = 'none';
    textEl.style.display = '';
    return;
  }
  richEl.replaceChildren();
  const lines = String(fileView.text || '').split(/\r?\n/);
  let inCode = false, codeBuf = [];
  const flushCode = () => {
    if (!codeBuf.length) return;
    const pre = document.createElement('pre');
    pre.textContent = codeBuf.join('\n');
    pre.style.background = 'var(--panel2)';
    pre.style.padding = '6px';
    pre.style.borderRadius = '6px';
    pre.style.overflowX = 'auto';
    richEl.appendChild(pre);
    codeBuf = [];
  };
  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (inCode) { flushCode(); inCode = false; } else { inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    const m = line.match(/^(#{1,3})\s+(.*)$/);
    if (m) {
      const h = document.createElement('h' + Math.min(4, m[1].length + 1));
      h.textContent = m[2];
      h.style.margin = '6px 0 3px';
      richEl.appendChild(h);
    } else if (/^\s*[-*]\s+/.test(line)) {
      const p = document.createElement('div');
      p.textContent = '• ' + line.replace(/^\s*[-*]\s+/, '');
      p.style.paddingLeft = '8px';
      richEl.appendChild(p);
    } else if (line.trim()) {
      const p = document.createElement('p');
      p.textContent = line;
      p.style.margin = '3px 0';
      richEl.appendChild(p);
    }
  }
  flushCode();
  richEl.style.display = 'block';
  textEl.style.display = 'none';
}

async function searchLocalDocs() {
  const input = document.getElementById('doc-search-input');
  const box = document.getElementById('doc-search-results');
  if (!input || !box) return;
  const q = (input.value || '').trim();
  if (!q) { box.style.display = 'none'; box.replaceChildren(); return; }
  box.style.display = 'flex';
  box.replaceChildren();
  const wait = document.createElement('span');
  wait.className = 'hint';
  wait.textContent = 'Αναζήτηση σε τοπικά έγγραφα…';
  box.appendChild(wait);
  try {
    const r = await api('/api/docs/search', {query: q, limit: 8});
    box.replaceChildren();
    if (r.error) { const e = document.createElement('span'); e.className = 'hint'; e.textContent = r.error; box.appendChild(e); return; }
    const results = r.results || [];
    if (!results.length) {
      const none = document.createElement('span');
      none.className = 'hint';
      none.textContent = 'Δεν βρέθηκαν αποτελέσματα (ευρετηριασμένα: ' + (r.stats?.total || 0) + ').';
      box.appendChild(none);
      return;
    }
    for (const item of results) {
      const row = document.createElement('div');
      row.style.padding = '4px 6px';
      row.style.borderRadius = '6px';
      row.style.background = 'var(--panel2)';
      row.style.cursor = 'pointer';
      row.onclick = () => {
        if (['pdf', 'docx', 'xlsx'].includes(item.kind)) {
          useQuickPrompt('Διάβασε και συνόψισε το αρχείο: ' + item.path);
        } else {
          openProjectFile(item.path);
        }
      };
      const title = document.createElement('strong');
      title.textContent = item.path + ' [' + (item.kind || '') + ']';
      title.style.display = 'block';
      row.appendChild(title);
      if (item.snippet) {
        const sn = document.createElement('div');
        sn.className = 'hint';
        sn.textContent = item.snippet;
        row.appendChild(sn);
      }
      box.appendChild(row);
    }
  } catch (err) {
    box.replaceChildren();
    const e = document.createElement('span'); e.className = 'hint'; e.textContent = err.message || String(err); box.appendChild(e);
  }
}

async function readFileRequest(path, offset, version, history, pick=false) {
  if (!pick && (!path || !path.trim())) return;
  const request = ++fileRequest, context = filesContext;
  fileReading = true;
  document.getElementById('open-file').disabled = true;
  document.getElementById('files-error').textContent = '';
  try {
    const data = await api(pick ? '/api/files/pick' : '/api/files/open', pick ? {} :
      {path, offset, version, project:filesProject, session_id:filesSession});
    if (request !== fileRequest || context !== filesContext) return;
    if (data.cancelled) return;
    if (data.error) throw new Error(data.error);
    if (data.project !== filesProject || (data.session_id || '') !== filesSession) return;
    showFile(data, history);
  } catch (err) {
    if (request === fileRequest && context === filesContext) document.getElementById('files-error').textContent = err.message;
  } finally {
    if (request === fileRequest) { fileReading = false; document.getElementById('open-file').disabled = false; }
  }
}
async function chooseFile() { if (!fileReading) return readFileRequest('', 0, '', [], true); }
async function openProjectFile(path) { return readFileRequest(path, 0, '', []); }
async function refreshFile() { if (fileView) return openProjectFile(fileView.path); }
async function filePage(next) {
  if (!fileView || fileReading) return;
  const history = [...fileBack]; let offset;
  if (next) { if (fileView.next == null) return; history.push(fileView.offset); offset = fileView.next; }
  else { if (!history.length) return; offset = history.pop(); }
  return readFileRequest(fileView.path, offset, fileView.version, history);
}
function insertFilePath() {
  if (!fileView) return;
  const prompt = document.getElementById('prompt');
  const start = prompt.selectionStart ?? prompt.value.length, end = prompt.selectionEnd ?? start;
  prompt.setRangeText('Αρχείο: ' + fileView.path + '\n', start, end, 'end');
  prompt.focus(); grow(prompt);
}

function useQuickPrompt(text) {
  const prompt = document.getElementById('prompt');
  prompt.value = text;
  prompt.focus();
  prompt.selectionStart = prompt.selectionEnd = text.length;
  grow(prompt);
}

let treeOpen = false, treePath = '.', treeVersion = '';
let orgSelection = new Map(), orgContext = null, orgPlan = null, orgUndo = false, orgDraft = 0;
function currentBoundContext() { return {project: filesProject || currentProject || '', session_id: currentSession || ''}; }
function sameBoundContext(c) { return !!c && JSON.stringify(c) === JSON.stringify(currentBoundContext()); }
function orgBound() {
  const now = currentBoundContext();
  if (!orgContext || JSON.stringify(now) !== JSON.stringify(orgContext)) {
    orgSelection.clear(); orgPlan = null; orgContext = now;
  }
  return {...now};
}
function invalidateOrganizationPlan() {
  orgDraft++; orgPlan = null;
  const panel = document.getElementById('organization-preview');
  if (panel) panel.replaceChildren();
  const btn = document.getElementById('organization-apply');
  if (btn) btn.disabled = true;
}
function updateOrganizationSelectionLabel() {
  const el = document.getElementById('organization-selected');
  if (el) el.textContent = i18nText('lbl_selected', 'Επιλεγμένα: ') + orgSelection.size;
}
function clearOrganizationSelection() {
  orgBound(); orgSelection.clear(); invalidateOrganizationPlan(); updateOrganizationSelectionLabel();
  if (treeOpen) loadProjectTree(treePath || '.');
}
function showOrganizationForm(action) {
  orgBound(); invalidateOrganizationPlan();
  const form = document.getElementById('organization-form');
  if (!form) return;
  form.hidden = false;
  document.getElementById('organization-action').value = action;
  document.getElementById('organization-destination-row').style.display = action === 'trash' ? 'none' : 'flex';
  document.getElementById('organization-mkdir-row').style.display = ['move', 'copy'].includes(action) ? 'flex' : 'none';
  document.getElementById('organization-destination').value = action === 'rename' ? '{name}' : (treePath === '.' ? '' : treePath);
  document.getElementById('organization-destination-label').textContent =
    action === 'rename' ? 'Όνομα ή μοτίβο: {name}, {stem}, {ext}, {index}' :
    action === 'mkdir' ? 'Νέος φάκελος (διαδρομή μέσα στο project)' :
    'Φάκελος προορισμού (μέσα στο project)';
  document.getElementById('organization-create-parent').checked = false;
}
function buildOrganizationOperations() {
  const action = document.getElementById('organization-action').value;
  const value = (document.getElementById('organization-destination').value || '').trim();
  const rows = [...orgSelection.values()];
  if (action === 'mkdir') {
    if (!value) throw Error('Δώσε όνομα φακέλου.');
    return [{action: 'mkdir', destination: value}];
  }
  if (!rows.length) throw Error('Επίλεξε πρώτα αρχεία ή φακέλους από την Περιήγηση φακέλου.');
  if (action === 'trash') return rows.map(row => ({action: 'trash', source: row.path}));
  if (!value) throw Error('Δώσε προορισμό ή μοτίβο ονόματος.');
  const ops = [];
  if (['move', 'copy'].includes(action) && document.getElementById('organization-create-parent').checked) {
    ops.push({action: 'mkdir', destination: value});
  }
  rows.forEach((row, i) => {
    let destination;
    if (action === 'rename') {
      const name = row.name;
      const dot = row.type === 'file' ? name.lastIndexOf('.') : -1;
      const stem = dot > 0 ? name.slice(0, dot) : name;
      const ext = dot > 0 ? name.slice(dot) : '';
      const next = value.replace(/\{(name|stem|ext|index)\}/g, (_, key) => ({name, stem, ext, index: String(i + 1)})[key]);
      if (/[\\/]/.test(next) || !next) throw Error('Η μετονομασία χρειάζεται απλό όνομα, χωρίς διαδρομή.');
      const parent = row.path.split('/').slice(0, -1).join('/');
      destination = (parent ? parent + '/' : '') + next;
    } else {
      destination = value.replace(/[\\/]+$/, '') + '/' + row.name;
    }
    ops.push({action: action === 'rename' ? 'move' : action, source: row.path, destination});
  });
  return ops;
}
function renderOrganizationPreview(plan, undo = false) {
  orgPlan = {...plan, context: currentBoundContext()};
  orgUndo = undo;
  const panel = document.getElementById('organization-preview');
  if (!panel) return;
  panel.replaceChildren();
  const names = {move: 'Μετακίνηση / Μετονομασία', copy: 'Αντιγραφή', mkdir: 'Νέος φάκελος', trash: 'Στον κάδο με επαναφορά'};
  const head = document.createElement('strong');
  head.textContent = undo ? 'Προεπισκόπηση επαναφοράς' : 'Προεπισκόπηση αλλαγών';
  panel.appendChild(head);
  for (const row of plan.operations || []) {
    const line = document.createElement('div');
    line.className = 'hint';
    line.textContent = (names[row.action] || row.action) + ': ' + (row.source || '') + ' → ' + (row.destination || 'Ασφαλής κάδος') + (row.files ? ' · ' + row.files + ' αρχεία · ' + row.bytes + ' B' : '');
    panel.appendChild(line);
  }
  const btn = document.getElementById('organization-apply');
  if (btn) {
    btn.textContent = undo ? 'Επαναφορά ακριβώς αυτών' : 'Εφαρμογή ακριβώς αυτών';
    btn.disabled = false;
  }
}
async function previewOrganization() {
  try {
    const context = orgBound();
    const operations = buildOrganizationOperations();
    const draft = orgDraft;
    const plan = await api('/api/files/plan', {...context, operations});
    if (plan.error) throw Error(plan.error);
    if (!sameBoundContext(context) || draft !== orgDraft) throw Error('Το σχέδιο ή το project άλλαξε. Ζήτησε νέα προεπισκόπηση.');
    renderOrganizationPreview(plan, false);
  } catch (e) {
    invalidateOrganizationPlan();
    showNotice('err', e.message || String(e));
  }
}
async function applyOrganization() {
  const plan = orgPlan;
  if (!plan) return;
  const btn = document.getElementById('organization-apply');
  try {
    if (!sameBoundContext(plan.context)) throw Error('Η προεπισκόπηση ανήκει σε προηγούμενη συνομιλία.');
    if (btn) btn.disabled = true;
    const r = await api(orgUndo ? '/api/files/undo' : '/api/files/apply', {...plan.context, id: plan.id, review: plan.review});
    if (r.error) throw Error(r.error);
    invalidateOrganizationPlan();
    orgSelection.clear();
    updateOrganizationSelectionLabel();
    await loadProjectTree('.');
    await loadOrganizationHistory();
    showNotice('ok', r.message || 'Η οργάνωση ολοκληρώθηκε.');
  } catch (e) {
    invalidateOrganizationPlan();
    showNotice('err', e.message || String(e));
    await loadOrganizationHistory();
  }
}
async function loadOrganizationHistory() {
  try {
    const ctx = orgBound();
    const r = await api('/api/files/history', ctx);
    const panel = document.getElementById('organization-history');
    if (!panel) return;
    panel.replaceChildren();
    if (r.error) { const err = document.createElement('span'); err.className = 'hint'; err.textContent = r.error; panel.appendChild(err); return; }
    const labels = {complete: 'Ολοκληρώθηκε', undone: 'Αναιρέθηκε', rolled_back: 'Ακυρώθηκε με επαναφορά', recovery_required: 'Χρειάζεται ανάκτηση', preparing: 'Προετοιμασία', applying: 'Διακόπηκε κατά την εφαρμογή', undoing: 'Διακόπηκε κατά την επαναφορά'};
    const list = (r.history || []).slice().reverse();
    if (!list.length) { const empty = document.createElement('span'); empty.className = 'hint'; empty.textContent = 'Δεν υπάρχει ιστορικό οργάνωσης ακόμα.'; panel.appendChild(empty); return; }
    for (const row of list) {
      const card = document.createElement('div');
      card.style.cssText = 'border:1px solid var(--border);border-radius:6px;padding:5px 7px;background:var(--bg);display:flex;flex-direction:column;gap:3px;font-size:11px';
      const title = document.createElement('strong'); title.textContent = row.id.slice(0, 10) + ' · ' + (labels[row.state] || row.state);
      card.appendChild(title);
      if (row.error) { const er = document.createElement('div'); er.style.color = 'var(--red)'; er.textContent = row.error; card.appendChild(er); }
      for (const op of (row.operations || []).slice(0, 6)) {
        const line = document.createElement('div'); line.className = 'hint';
        line.textContent = (op.source || '') + ' → ' + (op.destination || 'Κάδος');
        card.appendChild(line);
      }
      if (row.state !== 'undone') {
        const undoBtn = document.createElement('button'); undoBtn.className = 'btn'; undoBtn.style.cssText = 'width:auto;padding:3px 7px;font-size:11px;align-self:flex-start';
        undoBtn.textContent = '↩ Προεπισκόπηση επαναφοράς';
        undoBtn.onclick = async () => {
          try {
            invalidateOrganizationPlan();
            const draft = orgDraft;
            const prev = await api('/api/files/undo-plan', {...orgBound(), id: row.id});
            if (prev.error) throw Error(prev.error);
            if (draft !== orgDraft) throw Error('Το σχέδιο άλλαξε.');
            document.getElementById('organization-form').hidden = false;
            renderOrganizationPreview(prev, true);
          } catch (e) { showNotice('err', e.message || String(e)); }
        };
        card.appendChild(undoBtn);
      }
      panel.appendChild(card);
    }
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function toggleProjectTree(forceOpen) {
  const box = document.getElementById('project-tree-box');
  if (!box) return;
  treeOpen = typeof forceOpen === 'boolean' ? forceOpen : !treeOpen;
  box.style.display = treeOpen ? 'flex' : 'none';
  if (treeOpen) await loadProjectTree('.');
}
async function loadProjectTree(dirPath = '.', offset = 0, version = '') {
  const box = document.getElementById('project-tree-box');
  if (!box) return;
  orgBound();
  treePath = dirPath || '.';
  const r = await api('/api/files/tree', {path: treePath, project: filesProject, offset, limit: 100, version});
  box.replaceChildren();
  if (r.error) {
    const err = document.createElement('span'); err.className = 'hint'; err.style.color = 'var(--red)'; err.textContent = r.error;
    box.appendChild(err); return;
  }
  treeVersion = r.version || '';
  if (r.path && r.path !== '.') {
    const parts = r.path.split('/'); parts.pop();
    const upPath = parts.length ? parts.join('/') : '.';
    const up = document.createElement('button'); up.className = 'tree-row';
    up.textContent = '⬅ .. (Επιστροφή στον γονικό φάκελο)';
    up.onclick = () => loadProjectTree(upPath);
    box.appendChild(up);
  }
  for (const item of r.entries || []) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;align-items:center;gap:4px';
    const chk = document.createElement('input');
    chk.type = 'checkbox'; chk.style.width = 'auto'; chk.checked = orgSelection.has(item.path);
    chk.setAttribute('aria-label', 'Επιλογή ' + item.name);
    chk.onchange = () => {
      orgBound();
      if (chk.checked) orgSelection.set(item.path, {...item});
      else orgSelection.delete(item.path);
      invalidateOrganizationPlan();
      updateOrganizationSelectionLabel();
      const panel = document.getElementById('organization-panel');
      if (panel) panel.open = true;
    };
    const row = document.createElement('button'); row.className = 'tree-row'; row.style.flex = '1';
    const icon = document.createElement('span'); icon.textContent = item.type === 'dir' ? '📁' : '📄';
    const name = document.createElement('span'); name.style.flex = '1'; name.style.overflowWrap = 'anywhere'; name.textContent = item.name;
    row.append(icon, name);
    if (item.type === 'file') {
      const sz = document.createElement('span'); sz.className = 'sz';
      sz.textContent = item.size > 1024 ? Math.round(item.size / 1024) + ' KB' : item.size + ' B';
      row.appendChild(sz);
      row.onclick = () => openProjectFile(item.path);
    } else {
      row.onclick = () => loadProjectTree(item.path);
    }
    wrap.append(chk, row);
    box.appendChild(wrap);
  }
  if (r.next !== null && r.next !== undefined) {
    const more = document.createElement('button'); more.className = 'btn'; more.style.fontSize = '11px';
    more.textContent = 'Επόμενα αρχεία →';
    more.onclick = () => loadProjectTree(treePath, r.next, r.version || '');
    box.appendChild(more);
  }
  if (offset > 0) {
    const topBtn = document.createElement('button'); topBtn.className = 'btn'; topBtn.style.fontSize = '11px';
    topBtn.textContent = '⬅ Από την αρχή';
    topBtn.onclick = () => loadProjectTree(treePath, 0, '');
    box.appendChild(topBtn);
  }
  if (r.partial) {
    const note = document.createElement('span'); note.className = 'hint';
    note.textContent = 'Μερική προβολή λόγω ορίου μεγέθους/χρόνου.';
    box.appendChild(note);
  }
  if (!(r.entries || []).length) {
    const empty = document.createElement('span'); empty.className = 'hint'; empty.textContent = 'Κενός φάκελος.';
    box.appendChild(empty);
  }
  updateOrganizationSelectionLabel();
}

async function pickFolder() {
  const r = await api("/api/pick-folder", {});
  if (r && r.path) {
    document.getElementById("project").value = r.path;
  } else if (r && r.error) {
    showNotice("err", r.error);
  }
}

// Ίδιος μηχανισμός προχείρου και συντομεύσεων, χωρίς άδειες WebView.
document.addEventListener("keydown", function(e) {
  if (e.key === "Escape") {
    if (typeof closeAttachmentPreview === "function" && closeAttachmentPreview()) return;
    if (typeof hideDropOverlay === "function" && hideDropOverlay()) return;
    const pal = document.getElementById("cmd-palette");
    if (pal && pal.style.display === "flex") { closeCommandPalette(); return; }
    const pm = document.getElementById("proj-menu");
    if (pm && pm.style.display === "flex") { pm.style.display = "none"; return; }
    const sm = document.getElementById("slash-menu");
    if (sm && sm.style.display === "flex") { sm.style.display = "none"; return; }
    if (busy) { stopAgent(); return; }
  }
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
  const k = (e.key || "").toLowerCase();
  if (k === "k" && !e.shiftKey) {
    e.preventDefault();
    openCommandPalette();
    return;
  }
  if (k === "b" && !e.shiftKey) { e.preventDefault(); toggleFiles(); return; }
  if (k === "e" && e.shiftKey) { e.preventDefault(); exportChatMarkdown(); return; }
  const el = document.activeElement;
  const editable = el && !el.readOnly && !el.disabled &&
    (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && ["text","password","search","url","email","tel"].includes(el.type)));
  if (k === "v" && editable) { e.preventDefault(); pasteInto(el); }
  if (k === "c") {
    const selection = typeof ChatUX !== "undefined" && ChatUX.getSelectedText
      ? ChatUX.getSelectedText(el)
      : (editable && typeof el.selectionStart === "number" && el.selectionStart !== el.selectionEnd
          ? el.value.slice(el.selectionStart, el.selectionEnd)
          : (window.getSelection ? window.getSelection().toString() : ""));
    if (selection) { e.preventDefault(); copyText(selection); }
  }
  if (k === "x" && editable && typeof el.selectionStart === "number" && el.selectionStart !== el.selectionEnd) {
    e.preventDefault();
    const picked = el.value.slice(el.selectionStart, el.selectionEnd);
    copyText(picked);
    el.setRangeText("", el.selectionStart, el.selectionEnd, "start");
    el.dispatchEvent(new Event("input", {bubbles:true}));
  }
});

function esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

// ---------- chat rendering ----------
const chat = document.getElementById("chat");

function isChatNearBottom() {
  if (!chat || typeof chat.scrollHeight !== "number" || typeof chat.clientHeight !== "number") return true;
  return (chat.scrollHeight - (chat.scrollTop || 0) - chat.clientHeight) < 80;
}
function scrollChatIfPinned(wasPinned) {
  if (wasPinned !== false && chat) chat.scrollTop = chat.scrollHeight;
}

let liveStream = null;
function clearStream() {
  if (liveStream) liveStream.node.remove();
  liveStream = null;
}
function streamEvent(kind, data) {
  const pin = isChatNearBottom();
  if (kind === "stream_start") {
    clearStream();
    const node = document.createElement("div"); node.className = "stream-preview";
    const label = document.createElement("div"); label.className = "hint";
    label.textContent = "🧠 Σκέφτεται… " + (data.model || "");
    const reasoningBody = document.createElement("div"); reasoningBody.className = "stream-reasoning";
    const body = document.createElement("div"); body.style.whiteSpace = "pre-wrap";
    node.append(label, reasoningBody, body); chat.appendChild(node);
    liveStream = {id:data.stream_id, model:data.model || "", node, label, reasoningBody, body, reasoning:"", text:"", status:"active"};
  } else if (liveStream && liveStream.id === data.stream_id) {
    if (kind === "stream_reasoning") {
      liveStream.reasoning += data.text || "";
      liveStream.reasoningBody.textContent = liveStream.reasoning;
      liveStream.reasoningBody.scrollTop = liveStream.reasoningBody.scrollHeight;
      if (!liveStream.text) liveStream.label.textContent = "🧠 Σκέφτεται… " + liveStream.model;
    } else if (kind === "stream_delta") {
      liveStream.text += data.text || "";
      if (liveStream.reasoning && !liveStream.reasoningCollapsed && liveStream.reasoningBody) {
        liveStream.reasoningCollapsed = true;
        if (typeof ChatPro !== "undefined" && ChatPro.collapseStreamReasoning) {
          ChatPro.collapseStreamReasoning(liveStream);
        }
      }
      if (typeof ChatPro !== "undefined" && ChatPro.renderMarkdown) {
        liveStream.body.innerHTML = ChatPro.renderMarkdown(liveStream.text, {streaming: true});
      } else {
        liveStream.body.textContent = liveStream.text;
      }
      liveStream.label.textContent = "✍️ Γράφει… " + liveStream.model;
    } else if (kind === "stream_end") {
      liveStream.status = data.status;
      if (data.status === "tools") { clearStream(); return; }
      liveStream.label.textContent = data.status === "error" ? "Η απάντηση διακόπηκε — ατελές κείμενο"
        : data.status === "stopped" ? "Σταμάτησε — ατελές κείμενο" : "Η απάντηση ολοκληρώνεται…";
    }
  }
  scrollChatIfPinned(pin);
}
function syncStream(state) {
  if (!state) return;
  const pin = isChatNearBottom();
  if (!liveStream || liveStream.id !== state.id) streamEvent("stream_start", {stream_id:state.id, model:state.model});
  liveStream.reasoning = state.reasoning || "";
  liveStream.text = state.text || "";
  if (typeof ChatPro !== "undefined" && ChatPro.syncStreamReasoning) ChatPro.syncStreamReasoning(liveStream);
  else if (liveStream.reasoningBody) liveStream.reasoningBody.textContent = liveStream.reasoning;
  if (liveStream.text && typeof ChatPro !== "undefined" && ChatPro.renderMarkdown) {
    liveStream.body.innerHTML = ChatPro.renderMarkdown(liveStream.text, {streaming: state.status === "active"});
  } else {
    liveStream.body.textContent = liveStream.text;
  }
  if (liveStream.text) liveStream.label.textContent = "✍️ Γράφει… " + (liveStream.model || "");
  else if (liveStream.reasoning) liveStream.label.textContent = "🧠 Σκέφτεται… " + (liveStream.model || "");
  if (state.status !== "active") streamEvent("stream_end", {stream_id:state.id, status:state.status});
  scrollChatIfPinned(pin);
}

function addMsg(role, text, model = "", provider = "", usage = null) {
  const pin = role === "user" ? true : isChatNearBottom();
  const div = document.createElement("div");
  const richMd = role === "assistant" && typeof ChatPro !== "undefined" && ChatPro.renderMarkdown;
  div.className = "msg " + role + (richMd ? " md-rich" : "");
  div.dataset.key = role + "|" + String(text || "").trim();  // ταυτότητα για να μη διπλασιάζεται
  const richUser = role === "user" && typeof ChatPro !== "undefined" && ChatPro.formatUserText;
  div.innerHTML = role === "assistant" ? (richMd ? ChatPro.renderMarkdown(text) : fmt(text)) : (richUser ? ChatPro.formatUserText(text) : esc(text));
  if (role === "user" && typeof attachments !== "undefined" && Array.isArray(attachments) && attachments.length && typeof renderSentAttachments === "function") {
    renderSentAttachments(div, attachments);
  }
  if (role === "assistant") {          // κουμπί αντιγραφής κάτω από κάθε απάντηση
    if (typeof ChatPro !== "undefined" && ChatPro.attachTurnFileCards) ChatPro.attachTurnFileCards(div);
    if (model) {
      const label = document.createElement("span");
      label.className = "model-label";
      label.textContent = [provider, model].filter(Boolean).join(" · ");
      if (richMd && ChatPro.formatTimeNow) {
        const tm = document.createElement("span"); tm.className = "model-time"; tm.textContent = ChatPro.formatTimeNow();
        label.appendChild(tm);
      }
      div.prepend(label);
    }
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = "📄 Αντιγραφή";
    b.style.cssText = "width:auto;padding:2px 8px;font-size:11px;margin-top:6px;opacity:.75";
    b.onclick = () => copyText(text);
    div.appendChild(b);
    const spk = document.createElement("button");
    spk.className = "btn";
    spk.textContent = "🔊 Ακρόαση";
    spk.style.cssText = "width:auto;padding:2px 8px;font-size:11px;margin:6px 0 0 6px;opacity:.75";
    spk.onclick = () => speakAnswer(text, spk);
    div.appendChild(spk);
    if (usage && ((usage.prompt_tokens || 0) + (usage.completion_tokens || 0) > 0)) {
      const tot = (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
      const parts = [(tot >= 1000 ? (tot / 1000).toFixed(1) + 'k' : String(tot)) + ' tok'];
      if (usage.cache_hit_pct > 0) parts.push(usage.cache_hit_pct + '% cache');
      if (typeof usage.cost_eur === 'number') parts.push(usage.cost_eur.toFixed(4).replace('.', ',') + ' €');
      const badge = document.createElement('span');
      badge.className = 'hint';
      badge.style.cssText = 'margin-left:8px;font-size:11px;opacity:.8';
      badge.textContent = '⚡ ' + parts.join(' · ');
      div.appendChild(badge);
    }
  }
  chat.appendChild(div);
  scrollChatIfPinned(pin);
  return div;
}

let codeSnippets = [];
