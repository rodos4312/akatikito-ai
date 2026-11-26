// Chat UX Pro: mouse-first partial Copy/Paste, floating selection toolbar,
// right-click context menu, and MEU-style large paste -> .txt attachment.
(function (root) {
  'use strict';

  const LARGE_PASTE_THRESHOLD = 3000;
  let _selTimer = null;
  let _lastSelectionText = '';

  function _isEditable(el) {
    if (!el || el.readOnly || el.disabled) return false;
    const tag = String(el.tagName || '').toUpperCase();
    if (tag === 'TEXTAREA') return true;
    if (tag === 'INPUT') {
      const t = String(el.type || 'text').toLowerCase();
      return ['text', 'password', 'search', 'url', 'email', 'tel'].includes(t);
    }
    return false;
  }

  function _getWindowSelectionText() {
    try {
      const sel = typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null;
      return sel ? String(sel.toString() || '') : '';
    } catch (_) {
      return '';
    }
  }

  function _getSelectedText(activeEl) {
    const el = activeEl || (typeof document !== 'undefined' ? document.activeElement : null);
    if (_isEditable(el) && typeof el.selectionStart === 'number' && el.selectionStart !== el.selectionEnd) {
      return String(el.value || '').slice(el.selectionStart, el.selectionEnd);
    }
    return _getWindowSelectionText();
  }

  function _quoteIntoPrompt(text) {
    const clean = String(text || '').trim();
    const promptEl = typeof document !== 'undefined' ? document.getElementById('prompt') : null;
    if (!clean || !promptEl) return;
    const quoted = clean.split('\n').map((line) => '> ' + line).join('\n') + '\n\n';
    const cur = String(promptEl.value || '');
    promptEl.value = cur ? (cur.endsWith('\n') ? cur + quoted : cur + '\n' + quoted) : quoted;
    if (typeof promptEl.focus === 'function') promptEl.focus();
    if (typeof promptEl.selectionStart === 'number') {
      promptEl.selectionStart = promptEl.selectionEnd = promptEl.value.length;
    }
    if (typeof grow === 'function') grow(promptEl);
    if (typeof promptEl.dispatchEvent === 'function' && typeof Event === 'function') {
      promptEl.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function _cleanMessageText(msgEl) {
    if (!msgEl) return '';
    if (typeof msgEl.cloneNode === 'function') {
      const clone = msgEl.cloneNode(true);
      if (typeof clone.querySelectorAll === 'function') {
        const drop = clone.querySelectorAll('.model-label, .model-time, .btn, .msg-actions, .code-head button, .file-cards-row');
        drop.forEach((n) => { if (n && n.remove) n.remove(); });
      }
      return String(clone.textContent || '').trim();
    }
    return String(msgEl.textContent || '').trim();
  }

  function _ensureSelBar() {
    if (typeof document === 'undefined' || !document.body) return null;
    let bar = document.getElementById('sel-copy-bar');
    if (bar) return bar;
    bar = document.createElement('div');
    bar.id = 'sel-copy-bar';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Επιλεγμένο κείμενο');

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.textContent = '📋 Αντιγραφή';
    copyBtn.onmousedown = (e) => { if (e && e.preventDefault) e.preventDefault(); };
    copyBtn.onclick = () => {
      const txt = _getWindowSelectionText() || _lastSelectionText;
      if (txt && typeof copyText === 'function') copyText(txt);
      hideSelectionBar();
    };

    const quoteBtn = document.createElement('button');
    quoteBtn.type = 'button';
    quoteBtn.textContent = '💬 Παράθεση';
    quoteBtn.onmousedown = (e) => { if (e && e.preventDefault) e.preventDefault(); };
    quoteBtn.onclick = () => {
      const txt = _getWindowSelectionText() || _lastSelectionText;
      if (txt) _quoteIntoPrompt(txt);
      hideSelectionBar();
    };

    bar.append(copyBtn, quoteBtn);
    document.body.appendChild(bar);
    return bar;
  }

  function hideSelectionBar() {
    const bar = typeof document !== 'undefined' ? document.getElementById('sel-copy-bar') : null;
    if (bar && bar.classList) bar.classList.remove('show');
  }

  function _nodeInsideChat(node) {
    const chatEl = typeof document !== 'undefined' ? document.getElementById('chat') : null;
    if (!chatEl || !node) return false;
    let cur = node.nodeType === 3 ? node.parentNode : node;
    while (cur) {
      if (cur === chatEl) return true;
      cur = cur.parentNode;
    }
    return false;
  }

  function updateSelectionBar() {
    const sel = typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null;
    const text = sel ? String(sel.toString() || '').trim() : '';
    if (!text || !sel || !sel.rangeCount || !_nodeInsideChat(sel.anchorNode)) {
      hideSelectionBar();
      return;
    }
    _lastSelectionText = sel.toString();
    const bar = _ensureSelBar();
    if (!bar) return;
    let left = 24, top = 64;
    try {
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      if (rect && (rect.width > 0 || rect.height > 0)) {
        const vw = typeof window !== 'undefined' && window.innerWidth ? window.innerWidth : 1200;
        left = Math.max(12, Math.min(vw - 220, Math.round(rect.left + rect.width / 2 - 90)));
        top = rect.top > 56 ? Math.round(rect.top - 42) : Math.round(rect.bottom + 8);
      }
    } catch (_) {
      // Fallback coordinates already set.
    }
    bar.style.left = left + 'px';
    bar.style.top = top + 'px';
    bar.classList.add('show');
  }

  function _ensureContextMenu() {
    if (typeof document === 'undefined' || !document.body) return null;
    let menu = document.getElementById('chat-ctx-menu');
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'chat-ctx-menu';
    menu.setAttribute('role', 'menu');
    document.body.appendChild(menu);
    return menu;
  }

  function hideContextMenu() {
    const menu = typeof document !== 'undefined' ? document.getElementById('chat-ctx-menu') : null;
    if (menu && menu.classList) menu.classList.remove('show');
  }

  function _addCtxItem(menu, label, kbd, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ctx-item';
    btn.setAttribute('role', 'menuitem');
    const lbl = document.createElement('span');
    lbl.textContent = label;
    btn.appendChild(lbl);
    if (kbd) {
      const hint = document.createElement('span');
      hint.className = 'ctx-kbd';
      hint.textContent = kbd;
      btn.appendChild(hint);
    }
    btn.onmousedown = (e) => { if (e && e.preventDefault) e.preventDefault(); };
    btn.onclick = () => { hideContextMenu(); onClick(); };
    menu.appendChild(btn);
  }

  function _addCtxSep(menu) {
    const sep = document.createElement('div');
    sep.className = 'ctx-sep';
    menu.appendChild(sep);
  }

  function _cutEditable(el) {
    if (!_isEditable(el) || typeof el.selectionStart !== 'number') return;
    const start = el.selectionStart, end = el.selectionEnd;
    if (start === end) return;
    const picked = String(el.value || '').slice(start, end);
    if (typeof copyText === 'function') copyText(picked);
    if (typeof el.setRangeText === 'function') {
      el.setRangeText('', start, end, 'start');
    } else {
      el.value = String(el.value || '').slice(0, start) + String(el.value || '').slice(end);
    }
    if (typeof el.dispatchEvent === 'function' && typeof Event === 'function') {
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function _findClosestMsg(el) {
    let cur = el && el.nodeType === 3 ? el.parentNode : el;
    while (cur) {
      if (cur.classList && cur.classList.contains('msg')) return cur;
      cur = cur.parentNode;
    }
    return null;
  }

  function _buildContextMenuItems(menu, target) {
    const editable = _isEditable(target);
    const selected = editable ? _getSelectedText(target) : _getWindowSelectionText();
    const inChat = _nodeInsideChat(target);
    const msgEl = inChat ? _findClosestMsg(target) : null;
    const promptEl = typeof document !== 'undefined' ? document.getElementById('prompt') : null;

    if (selected) {
      _addCtxItem(menu, '📋 Αντιγραφή', 'Ctrl+C', () => {
        if (typeof copyText === 'function') copyText(selected);
      });
      if (editable) {
        _addCtxItem(menu, '✂️ Αποκοπή', 'Ctrl+X', () => _cutEditable(target));
      } else if (inChat) {
        _addCtxItem(menu, '💬 Παράθεση στο chat', '', () => _quoteIntoPrompt(selected));
      }
    }

    if (editable) {
      if (selected) _addCtxSep(menu);
      _addCtxItem(menu, '📥 Επικόλληση', 'Ctrl+V', () => {
        if (typeof pasteInto === 'function') pasteInto(target);
      });
      _addCtxItem(menu, '🔘 Επιλογή όλων', 'Ctrl+A', () => {
        if (typeof target.focus === 'function') target.focus();
        if (typeof target.select === 'function') target.select();
      });
      return true;
    }

    if (inChat) {
      if (selected) _addCtxSep(menu);
      if (msgEl) {
        _addCtxItem(menu, '📄 Αντιγραφή απάντησης', '', () => {
          const body = _cleanMessageText(msgEl);
          if (body && typeof copyText === 'function') copyText(body);
        });
      }
      if (promptEl) {
        _addCtxItem(menu, '📥 Επικόλληση στο chat', 'Ctrl+V', () => {
          if (typeof pasteInto === 'function') pasteInto(promptEl);
        });
      }
      return true;
    }
    return Boolean(selected);
  }

  function showContextMenu(event) {
    if (!event) return false;
    const target = event.target;
    const menu = _ensureContextMenu();
    if (!menu) return false;
    menu.replaceChildren();
    if (!_buildContextMenuItems(menu, target) || !menu.children.length) {
      hideContextMenu();
      return false;
    }
    if (event.preventDefault) event.preventDefault();
    hideSelectionBar();
    const vw = typeof window !== 'undefined' && window.innerWidth ? window.innerWidth : 1200;
    const vh = typeof window !== 'undefined' && window.innerHeight ? window.innerHeight : 800;
    const x = Math.max(8, Math.min(vw - 220, Number(event.clientX || 0)));
    const y = Math.max(8, Math.min(vh - 180, Number(event.clientY || 0)));
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    menu.classList.add('show');
    return true;
  }

  function _utf8ToBase64(text) {
    if (typeof Buffer !== 'undefined') return Buffer.from(String(text || ''), 'utf8').toString('base64');
    const bytes = new TextEncoder().encode(String(text || ''));
    let bin = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(bin);
  }

  async function maybeAttachLargePaste(text) {
    const raw = String(text || '');
    if (raw.length <= LARGE_PASTE_THRESHOLD) return false;
    if (typeof connected === 'undefined' || !connected) return false;
    if (typeof api !== 'function' || typeof attachments === 'undefined' || !Array.isArray(attachments)) return false;
    if (attachments.length >= 8) return false;
    const stamp = Date.now().toString(36);
    const name = 'pasted_content_' + stamp + '.txt';
    try {
      const result = await api('/api/attachments/upload', {
        name,
        base64: _utf8ToBase64(raw),
        project: typeof currentProject !== 'undefined' ? currentProject : '',
        session_id: typeof currentSession !== 'undefined' ? currentSession : '',
      });
      if (!result || result.error) return false;
      result.previewText = raw.slice(0, 12000);
      result.pasted = true;
      attachments.push(result);
      if (typeof showAttachments === 'function') showAttachments();
      const kb = Math.max(1, Math.round(raw.length / 1024));
      if (typeof showNotice === 'function') {
        showNotice('ok', '📄 Μεγάλη επικόλληση (' + kb + ' KB) → συνημμένο ' + (result.name || name));
      }
      return true;
    } catch (_) {
      return false;
    }
  }

  function _onDocKeyUp(e) {
    if (e && e.key === 'Escape') { hideSelectionBar(); hideContextMenu(); return; }
    if (e && (e.shiftKey || e.key === 'Shift')) updateSelectionBar();
  }

  function _onDocMouseDown(e) {
    const menu = document.getElementById('chat-ctx-menu');
    if (menu && menu.classList && menu.classList.contains('show') && !menu.contains(e.target)) {
      hideContextMenu();
    }
    const bar = document.getElementById('sel-copy-bar');
    if (bar && bar.classList && bar.classList.contains('show') && !bar.contains(e.target)) {
      hideSelectionBar();
    }
  }

  function _fallbackInsertPlain(target, plain) {
    const start = target.selectionStart ?? target.value.length;
    const end = target.selectionEnd ?? start;
    if (typeof target.setRangeText === 'function') target.setRangeText(plain, start, end, 'end');
    else target.value += plain;
    if (typeof grow === 'function') grow(target);
  }

  function _onDocPaste(e) {
    const target = e && e.target;
    if (!target || target.id !== 'prompt') return;
    const cd = e.clipboardData;
    const plain = cd && typeof cd.getData === 'function' ? String(cd.getData('text/plain') || '') : '';
    if (plain.length <= LARGE_PASTE_THRESHOLD || typeof connected === 'undefined' || !connected) return;
    e.preventDefault();
    void maybeAttachLargePaste(plain).then((ok) => {
      if (!ok) _fallbackInsertPlain(target, plain);
    });
  }

  function _bindListeners() {
    if (typeof document === 'undefined' || !document.addEventListener) return;
    document.addEventListener('mouseup', () => {
      if (_selTimer) clearTimeout(_selTimer);
      _selTimer = setTimeout(updateSelectionBar, 30);
    });
    document.addEventListener('keyup', _onDocKeyUp);
    document.addEventListener('mousedown', _onDocMouseDown);
    document.addEventListener('contextmenu', showContextMenu);
    document.addEventListener('paste', _onDocPaste);
    const chatEl = document.getElementById('chat');
    if (chatEl && chatEl.addEventListener) {
      chatEl.addEventListener('scroll', () => { hideSelectionBar(); hideContextMenu(); }, { passive: true });
    }
  }

  _bindListeners();

  root.ChatUX = {
    LARGE_PASTE_THRESHOLD,
    getSelectedText: _getSelectedText,
    quoteIntoPrompt: _quoteIntoPrompt,
    cleanMessageText: _cleanMessageText,
    updateSelectionBar,
    hideSelectionBar,
    showContextMenu,
    hideContextMenu,
    maybeAttachLargePaste,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
