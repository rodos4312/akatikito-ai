// Attachments, MEU-style preview cards/lightbox, Drag & Drop (OS + in-app @file), and Ctrl+V paste.
let attachments = [];
let attachmentContext = '';
let _dragDepth = 0;

const _INTERNAL_FILE_MIME = 'application/x-akatikito-file';
const _TEXT_PREVIEW_EXTS = new Set([
  'txt', 'md', 'py', 'js', 'ts', 'jsx', 'tsx', 'html', 'htm', 'css', 'json',
  'csv', 'log', 'yml', 'yaml', 'toml', 'xml', 'sql', 'sh', 'bat', 'ps1', 'ini',
]);

function _extLabel(row) {
  if (row && row.image) return 'IMG';
  const name = String((row && row.name) || '');
  const dot = name.lastIndexOf('.');
  if (dot >= 0 && dot < name.length - 1) return name.slice(dot + 1).toUpperCase().slice(0, 4);
  return 'FILE';
}

function _sizeLabel(row) {
  if (row && row.pasted) {
    const kb = row.size ? Math.max(1, Math.round(row.size / 1024)) + ' KB' : '';
    return kb ? 'Επικόλληση · ' + kb : 'Επικόλληση';
  }
  if (!row || !row.size) return '';
  if (row.size < 1024) return row.size + ' B';
  if (row.size < 1024 * 1024) return Math.max(1, Math.round(row.size / 1024)) + ' KB';
  return (row.size / (1024 * 1024)).toFixed(1) + ' MB';
}

function _ensureLightbox() {
  if (typeof document === 'undefined' || !document.body) return null;
  let el = document.getElementById('attach-lightbox');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'attach-lightbox';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.onclick = (e) => { if (e && e.target === el) closeAttachmentPreview(); };
  document.body.appendChild(el);
  return el;
}

function closeAttachmentPreview() {
  const el = typeof document !== 'undefined' ? document.getElementById('attach-lightbox') : null;
  if (el && el.classList && el.classList.contains('show')) {
    el.classList.remove('show');
    return true;
  }
  return false;
}

function openAttachmentPreview(row) {
  if (!row) return;
  if (!row.thumbDataUrl && !row.previewText && row.path && typeof openProjectFile === 'function') {
    openProjectFile(row.path);
    return;
  }
  const lb = _ensureLightbox();
  if (!lb) return;
  lb.replaceChildren();
  const box = document.createElement('div');
  box.className = 'lightbox-box';
  const head = document.createElement('div');
  head.className = 'lightbox-head';
  const title = document.createElement('span');
  title.textContent = (row.image ? '🖼️ ' : '📄 ') + (row.name || 'Προεπισκόπηση');
  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:6px;align-items:center';
  if (row.previewText && typeof copyText === 'function') {
    const cp = document.createElement('button');
    cp.className = 'btn';
    cp.type = 'button';
    cp.style.cssText = 'width:auto;padding:3px 9px;font-size:12px';
    cp.textContent = '📋 Αντιγραφή';
    cp.onclick = () => copyText(row.previewText);
    actions.appendChild(cp);
  }
  const closeBtn = document.createElement('button');
  closeBtn.className = 'btn';
  closeBtn.type = 'button';
  closeBtn.style.cssText = 'width:auto;padding:3px 9px;font-size:12px';
  closeBtn.textContent = '✕ Κλείσιμο';
  closeBtn.onclick = closeAttachmentPreview;
  actions.appendChild(closeBtn);
  head.append(title, actions);

  const body = document.createElement('div');
  body.className = 'lightbox-body';
  if (row.thumbDataUrl) {
    const img = document.createElement('img');
    img.src = row.thumbDataUrl;
    img.alt = row.name || 'Εικόνα';
    body.appendChild(img);
  } else {
    const pre = document.createElement('pre');
    pre.textContent = row.previewText || 'Δεν υπάρχει διαθέσιμη προεπισκόπηση.';
    body.appendChild(pre);
  }
  box.append(head, body);
  lb.appendChild(box);
  lb.classList.add('show');
}

function _buildAttachmentCard(row) {
  const card = document.createElement('div');
  if (typeof card.append !== 'function') {
    const chip = document.createElement('button');
    chip.className = 'attach-chip';
    chip.textContent = (row.image ? '🖼️ ' : '📎 ') + (row.name || 'αρχείο') + ' ×';
    chip.title = 'Αφαίρεση συνημμένου';
    chip.onclick = () => { attachments = attachments.filter((x) => x !== row); showAttachments(); };
    return chip;
  }
  card.className = 'attach-card';

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'attach-card-main';
  main.title = (row.name || '') + ' — κλικ για προεπισκόπηση';
  main.onclick = () => openAttachmentPreview(row);

  if (row.thumbDataUrl) {
    const img = document.createElement('img');
    img.className = 'attach-thumb';
    img.src = row.thumbDataUrl;
    img.alt = row.name || 'image';
    main.appendChild(img);
  } else {
    const badge = document.createElement('span');
    badge.className = 'attach-badge';
    badge.textContent = _extLabel(row);
    main.appendChild(badge);
  }

  const meta = document.createElement('span');
  meta.className = 'attach-meta';
  const nm = document.createElement('span');
  nm.className = 'attach-name';
  nm.textContent = row.pasted ? 'Κείμενο επικόλλησης' : (row.name || 'αρχείο');
  const sub = document.createElement('span');
  sub.className = 'attach-sub';
  sub.textContent = _sizeLabel(row);
  meta.append(nm, sub);
  main.appendChild(meta);

  const rm = document.createElement('button');
  rm.type = 'button';
  rm.className = 'attach-card-rm';
  rm.title = 'Αφαίρεση συνημμένου';
  rm.textContent = '×';
  rm.onclick = (e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    attachments = attachments.filter((x) => x !== row);
    showAttachments();
  };

  card.append(main, rm);
  return card;
}

function showAttachments() {
  const panel = document.getElementById('attachment-panel');
  if (!panel) return;
  panel.replaceChildren();
  for (const row of attachments) {
    panel.appendChild(_buildAttachmentCard(row));
  }
}

function renderSentAttachments(msgEl, rows) {
  if (!msgEl || !Array.isArray(rows) || !rows.length) return;
  const strip = document.createElement('div');
  strip.className = 'sent-attach-row';
  for (const row of rows) {
    const snap = { ...row };
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'sent-attach-pill';
    pill.title = 'Προεπισκόπηση ' + (snap.name || '');
    if (snap.thumbDataUrl) {
      const img = document.createElement('img');
      img.src = snap.thumbDataUrl;
      img.alt = snap.name || '';
      pill.appendChild(img);
    }
    const lbl = document.createElement('span');
    lbl.textContent = (snap.thumbDataUrl ? '' : '📎 ') + (snap.pasted ? 'Κείμενο επικόλλησης' : (snap.name || 'αρχείο'));
    pill.appendChild(lbl);
    pill.onclick = () => openAttachmentPreview(snap);
    strip.appendChild(pill);
  }
  if (typeof msgEl.prepend === 'function') msgEl.prepend(strip);
  else msgEl.appendChild(strip);
}

function _readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('Αδυναμία ανάγνωσης αρχείου (μήπως έσυρες φάκελο;)'));
    reader.readAsDataURL(file);
  });
}

function _readFilePreviewText(file) {
  const name = String(file.name || '');
  const dot = name.lastIndexOf('.');
  const ext = dot >= 0 ? name.slice(dot + 1).toLowerCase() : '';
  if (!_TEXT_PREVIEW_EXTS.has(ext) || file.size > 256 * 1024 || typeof FileReader === 'undefined') {
    return Promise.resolve('');
  }
  return new Promise((resolve) => {
    const reader = new FileReader();
    if (typeof reader.readAsText !== 'function') { resolve(''); return; }
    reader.onload = () => resolve(String(reader.result || '').slice(0, 12000));
    reader.onerror = () => resolve('');
    reader.readAsText(file);
  });
}

function _resolveUploadName(file) {
  if (file.name) return file.name;
  const mime = String(file.type || '').toLowerCase();
  if (mime === 'image/jpeg') return 'pasted.jpg';
  if (mime === 'image/webp') return 'pasted.webp';
  return 'pasted.png';
}

async function _uploadOneAttachment(file, project, session_id, context) {
  if (!file || typeof file.size !== 'number') return false;
  if (file.size === 0) {
    showNotice('err', 'Το αρχείο «' + (file.name || 'χωρίς όνομα') + '» είναι κενό (0 bytes) ή είναι φάκελος.');
    return false;
  }
  if (file.size > 8 * 1024 * 1024) {
    showNotice('err', 'Όριο αρχείου 8 MiB: ' + (file.name || ''));
    return false;
  }
  let dataUrl = '';
  try {
    dataUrl = await _readFileAsDataUrl(file);
  } catch (err) {
    showNotice('err', err.message || 'Αδυναμία ανάγνωσης αρχείου.');
    return false;
  }
  const base64 = dataUrl.split(',')[1] || '';
  if (!base64) {
    showNotice('err', 'Δεν διαβάστηκε περιεχόμενο από το «' + (file.name || '') + '».');
    return false;
  }
  const previewText = await _readFilePreviewText(file);
  const uploadName = _resolveUploadName(file);
  const result = await api('/api/attachments/upload', { name: uploadName, base64, project, session_id });
  if (context !== currentProject + '|' + currentSession) return false;
  if (result.error) { showNotice('err', result.error); return false; }
  if (attachments.length >= 8) return false;
  if (result.image || String(file.type || '').startsWith('image/')) {
    result.thumbDataUrl = dataUrl;
    result.image = true;
  }
  if (previewText) result.previewText = previewText;
  attachments.push(result);
  showAttachments();
  return true;
}

async function attachFiles(files) {
  const list = Array.from(files || []);
  if (!list.length) return;
  if (!connected) {
    showNotice('err', 'Σύνδεσε πρώτα πάροχο/project πριν επισυνάψεις αρχεία.');
    return;
  }
  const project = currentProject, session_id = currentSession;
  const context = project + '|' + session_id;
  attachmentContext = context;
  let added = 0;
  for (const file of list) {
    if (attachments.length >= 8) {
      showNotice('err', 'Όριο 8 συνημμένων ανά μήνυμα.');
      break;
    }
    if (await _uploadOneAttachment(file, project, session_id, context)) added++;
  }
  if (added > 0) {
    showNotice('ok', added === 1 ? '📎 Επισυνάφθηκε 1 αρχείο.' : '📎 Επισυνάφθηκαν ' + added + ' αρχεία.');
    const promptEl = document.getElementById('prompt');
    if (promptEl && typeof promptEl.focus === 'function') promptEl.focus();
  }
}

function chooseAttachments() {
  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = true;
  input.onchange = () => attachFiles(input.files);
  input.click();
}

function _insertFileRef(relPath) {
  const clean = String(relPath || '').trim();
  const promptEl = document.getElementById('prompt');
  if (!clean || !promptEl) return;
  const token = '@' + clean + ' ';
  const cur = String(promptEl.value || '');
  const start = typeof promptEl.selectionStart === 'number' ? promptEl.selectionStart : cur.length;
  const end = typeof promptEl.selectionEnd === 'number' ? promptEl.selectionEnd : start;
  const sep = start > 0 && !/\s$/.test(cur.slice(0, start)) ? ' ' : '';
  if (typeof promptEl.setRangeText === 'function') {
    promptEl.setRangeText(sep + token, start, end, 'end');
  } else {
    promptEl.value = cur.slice(0, start) + sep + token + cur.slice(end);
  }
  if (typeof promptEl.focus === 'function') promptEl.focus();
  if (typeof grow === 'function') grow(promptEl);
  showNotice('ok', '📎 Προστέθηκε αναφορά ' + token.trim());
}

function _hasDraggedFiles(e) {
  const types = e && e.dataTransfer && e.dataTransfer.types;
  if (!types) return false;
  const arr = Array.from(types);
  return arr.includes('Files') || arr.includes(_INTERNAL_FILE_MIME);
}

function _setDropOverlay(visible) {
  const el = document.getElementById('drop-overlay');
  if (!el) return;
  el.hidden = !visible;
}

document.addEventListener('dragenter', (e) => {
  if (!_hasDraggedFiles(e)) return;
  e.preventDefault();
  _dragDepth++;
  _setDropOverlay(true);
});

document.addEventListener('dragover', (e) => {
  if (!_hasDraggedFiles(e)) return;
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  _setDropOverlay(true);
});

document.addEventListener('dragleave', () => {
  if (_dragDepth <= 0) return;
  _dragDepth = Math.max(0, _dragDepth - 1);
  if (_dragDepth === 0) _setDropOverlay(false);
});

document.addEventListener('drop', (e) => {
  const dt = e && e.dataTransfer;
  const internalPath = dt && typeof dt.getData === 'function' ? String(dt.getData(_INTERNAL_FILE_MIME) || '').trim() : '';
  const hasFiles = dt && dt.files && dt.files.length > 0;
  _dragDepth = 0;
  _setDropOverlay(false);
  if (internalPath) {
    e.preventDefault();
    _insertFileRef(internalPath);
    return;
  }
  if (!hasFiles) return;
  e.preventDefault();
  attachFiles(dt.files);
});

function hideDropOverlay() {
  if (_dragDepth > 0 || (document.getElementById('drop-overlay') && !document.getElementById('drop-overlay').hidden)) {
    _dragDepth = 0;
    _setDropOverlay(false);
    return true;
  }
  return false;
}

function addPastedAttachment(meta) {
  if (!meta || meta.error) {
    if (meta && meta.error) showNotice('err', meta.error);
    return;
  }
  if (attachments.length >= 8) {
    showNotice('err', 'Όριο 8 συνημμένων ανά μήνυμα.');
    return;
  }
  attachmentContext = currentProject + '|' + currentSession;
  attachments.push(meta);
  showAttachments();
  showNotice('ok', '🖼️ Επισυνάφθηκε εικόνα από το πρόχειρο (' + (meta.name || 'clipboard.png') + ').');
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('blur', () => { hideDropOverlay(); });
}

// Support pasting screenshots/images directly with Ctrl+V without hijacking text pastes
document.addEventListener('paste', (e) => {
  const targetTag = e && e.target && e.target.tagName ? String(e.target.tagName).toUpperCase() : '';
  if (targetTag === 'INPUT') return;
  const cd = e && e.clipboardData;
  const items = cd && cd.items;
  if (!items) return;
  const plain = typeof cd.getData === 'function' ? String(cd.getData('text/plain') || '').trim() : '';
  if (plain) return;
  const imgFiles = [];
  for (const item of Array.from(items)) {
    if (item.kind === 'file' && String(item.type || '').startsWith('image/')) {
      const f = item.getAsFile();
      if (f) imgFiles.push(f);
    }
  }
  if (imgFiles.length > 0) {
    e.preventDefault();
    attachFiles(imgFiles);
  }
});

async function captureScreenAttachment() {
  if (!connected) { showNotice('err', 'Συνδέσου πρώτα πριν πάρεις στιγμιότυπο.'); return; }
  const project = currentProject, session_id = currentSession;
  attachmentContext = project + '|' + session_id;
  const r = await api('/api/attachments/screenshot', { project, session_id });
  if (r.error) { showNotice('err', r.error); return; }
  if (attachments.length < 8) {
    attachments.push(r);
    showAttachments();
    showNotice('ok', 'Προστέθηκε στιγμιότυπο οθόνης (' + r.name + ') στα συνημμένα.');
  }
}
