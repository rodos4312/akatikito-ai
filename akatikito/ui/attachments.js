// Attachments, Drag & Drop drop-zone overlay, and Ctrl+V screenshot paste.
let attachments = [];
let attachmentContext = '';
let _dragDepth = 0;

function showAttachments() {
  const panel = document.getElementById('attachment-panel');
  if (!panel) return;
  panel.replaceChildren();
  for (const row of attachments) {
    const item = document.createElement('button');
    item.className = 'attach-chip';
    const kb = row.size ? ' (' + Math.max(1, Math.round(row.size / 1024)) + ' KB)' : '';
    item.textContent = (row.image ? '🖼️ ' : '📎 ') + row.name + kb + ' ×';
    item.title = 'Αφαίρεση συνημμένου';
    item.onclick = () => {
      attachments = attachments.filter(x => x !== row);
      showAttachments();
    };
    panel.appendChild(item);
  }
}

async function attachFiles(files) {
  const list = Array.from(files || []);
  if (!list.length) return;
  if (!connected) {
    showNotice('err', 'Σύνδεσε πρώτα πάροχο/project πριν επισυνάψεις αρχεία.');
    return;
  }
  if (busy) {
    showNotice('err', 'Περίμενε να ολοκληρωθεί η τρέχουσα απάντηση πριν επισυνάψεις αρχεία.');
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
    if (!file || typeof file.size !== 'number') continue;
    if (file.size === 0) {
      showNotice('err', 'Το αρχείο «' + (file.name || 'χωρίς όνομα') + '» είναι κενό (0 bytes) ή είναι φάκελος.');
      continue;
    }
    if (file.size > 8 * 1024 * 1024) {
      showNotice('err', 'Όριο αρχείου 8 MiB: ' + (file.name || ''));
      continue;
    }
    let data = '';
    try {
      data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
        reader.onerror = () => reject(new Error('Αδυναμία ανάγνωσης αρχείου (μήπως έσυρες φάκελο;)'));
        reader.readAsDataURL(file);
      });
    } catch (err) {
      showNotice('err', err.message || 'Αδυναμία ανάγνωσης αρχείου.');
      continue;
    }
    if (!data) {
      showNotice('err', 'Δεν διαβάστηκε περιεχόμενο από το «' + (file.name || '') + '».');
      continue;
    }
    let uploadName = file.name || '';
    if (!uploadName) {
      const mime = String(file.type || '').toLowerCase();
      if (mime === 'image/jpeg') uploadName = 'pasted.jpg';
      else if (mime === 'image/webp') uploadName = 'pasted.webp';
      else uploadName = 'pasted.png';
    }
    const result = await api('/api/attachments/upload', {name: uploadName, base64: data, project, session_id});
    if (context !== currentProject + '|' + currentSession) return;
    if (result.error) {
      showNotice('err', result.error);
    } else if (attachments.length < 8) {
      attachments.push(result);
      added++;
      showAttachments();
    }
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

function _hasDraggedFiles(e) {
  const types = e && e.dataTransfer && e.dataTransfer.types;
  if (!types) return false;
  return Array.from(types).includes('Files');
}

function _setDropOverlay(visible) {
  const el = document.getElementById('drop-overlay');
  if (!el) return;
  el.hidden = !visible;
}

document.addEventListener('dragenter', e => {
  if (!_hasDraggedFiles(e)) return;
  e.preventDefault();
  _dragDepth++;
  _setDropOverlay(true);
});

document.addEventListener('dragover', e => {
  if (!_hasDraggedFiles(e)) return;
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  _setDropOverlay(true);
});

document.addEventListener('dragleave', e => {
  if (_dragDepth <= 0) return;
  _dragDepth = Math.max(0, _dragDepth - 1);
  if (_dragDepth === 0) _setDropOverlay(false);
});

document.addEventListener('drop', e => {
  const hasFiles = e && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0;
  _dragDepth = 0;
  _setDropOverlay(false);
  if (!hasFiles) return;
  e.preventDefault();
  attachFiles(e.dataTransfer.files);
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

// Support pasting screenshots/images directly with Ctrl+V without hijacking text pastes (e.g. Excel/Word)
document.addEventListener('paste', e => {
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
  if (!connected || busy) { showNotice('err', 'Συνδέσου πρώτα και περίμενε να ολοκληρωθεί η εργασία.'); return; }
  const project = currentProject, session_id = currentSession;
  attachmentContext = project + '|' + session_id;
  const r = await api('/api/attachments/screenshot', {project, session_id});
  if (r.error) { showNotice('err', r.error); return; }
  if (attachments.length < 8) {
    attachments.push(r);
    showAttachments();
    showNotice('ok', 'Προστέθηκε στιγμιότυπο οθόνης (' + r.name + ') στα συνημμένα.');
  }
}
