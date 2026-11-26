// Node UI tests: Mouse-first partial Copy/Paste, Ctrl+C focus bug fix,
// floating selection bar, right-click context menu, and Large Paste -> .txt chip.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');

class El {
  constructor(tag = 'DIV') {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.style = {};
    this.attrs = {};
    this.classList = {
      toggle: (cls, force) => {
        const set = new Set(this.className.split(/\s+/).filter(Boolean));
        const on = typeof force === 'boolean' ? force : !set.has(cls);
        if (on) set.add(cls); else set.delete(cls);
        this.className = [...set].join(' ');
        return on;
      },
      add: (cls) => this.classList.toggle(cls, true),
      remove: (cls) => this.classList.toggle(cls, false),
      contains: (cls) => this.className.split(/\s+/).includes(cls),
    };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  contains(node) {
    let cur = node;
    while (cur) { if (cur === this) return true; cur = cur.parentNode; }
    return false;
  }
  focus() { document.activeElement = this; }
  select() { this.selectionStart = 0; this.selectionEnd = this.value.length; }
  addEventListener() {}
  dispatchEvent() {}
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
  }
  cloneNode(deep) {
    const copy = new El(this.tagName);
    copy.className = this.className;
    copy.textContent = this.textContent;
    if (deep) {
      for (const ch of this.children) copy.appendChild(ch.cloneNode(true));
    }
    return copy;
  }
  querySelectorAll(sel) {
    const classes = sel.split(',').map((s) => s.trim().replace(/^\./, ''));
    const out = [];
    const walk = (n) => {
      for (const c of n.children) {
        if (classes.some((cls) => c.classList.contains(cls))) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
  get innerTextCombined() {
    if (!this.children.length) return this.textContent;
    return this.children.map((c) => c.innerTextCombined).join(' ');
  }
}

const body = new El('body');
const chatEl = new El('div'); chatEl.id = 'chat';
const msgEl = new El('div'); msgEl.className = 'msg assistant';
const labelEl = new El('span'); labelEl.className = 'model-label'; labelEl.textContent = 'OpenRouter';
const bodyP = new El('p'); bodyP.textContent = 'Εδώ είναι το κομμάτι που θέλω να αντιγράψω.';
const copyAllBtn = new El('button'); copyAllBtn.className = 'btn'; copyAllBtn.textContent = 'Αντιγραφή';
msgEl.append(labelEl, bodyP, copyAllBtn);
chatEl.appendChild(msgEl);

const promptEl = new El('textarea'); promptEl.id = 'prompt';
promptEl.value = 'αρχικό κείμενο';
promptEl.selectionStart = promptEl.selectionEnd = promptEl.value.length;
body.append(chatEl, promptEl);

const listeners = {};
const document = {
  body,
  activeElement: promptEl,
  getElementById(id) {
    const walk = (n) => {
      if (n.id === id) return n;
      for (const ch of n.children || []) { const hit = walk(ch); if (hit) return hit; }
      return null;
    };
    return walk(body);
  },
  createElement(tag) { return new El(tag); },
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
};

let currentSelection = { text: '', anchorNode: null };
const copied = [];
const pastedTargets = [];
const uploads = [];
const notices = [];
const attachments = [];
let showAttachmentsCalls = 0;

const ctx = {
  document,
  window: {
    innerWidth: 1200,
    innerHeight: 800,
    getSelection: () => ({
      toString: () => currentSelection.text,
      rangeCount: currentSelection.text ? 1 : 0,
      anchorNode: currentSelection.anchorNode,
      getRangeAt: () => ({ getBoundingClientRect: () => ({ left: 200, top: 180, width: 120, height: 18, bottom: 198 }) }),
    }),
  },
  connected: true,
  currentProject: 'D:\\proj',
  currentSession: 'sess1',
  attachments,
  showAttachments: () => { showAttachmentsCalls++; },
  showNotice: (k, m) => { notices.push({ k, m }); },
  grow: () => {},
  copyText: (t) => { copied.push(t); },
  pasteInto: (el) => { pastedTargets.push(el.id); },
  api: async (url, payload) => {
    uploads.push({ url, payload });
    return { name: payload.name, path: 'attachments/' + payload.name, size: Buffer.from(payload.base64, 'base64').length };
  },
  setTimeout,
  clearTimeout,
  Buffer,
  Date,
  Event: class Event { constructor(t) { this.type = t; } },
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../akatikito/ui/chat_ux.js'), 'utf8'), ctx);

const { ChatUX } = ctx;

(async () => {
  // 1. Ctrl+C bug fix: prompt is focused with collapsed caret, user selected text in chat -> returns chat selection!
  currentSelection = { text: 'κομμάτι που θέλω', anchorNode: bodyP };
  assert.equal(ChatUX.getSelectedText(promptEl), 'κομμάτι που θέλω');

  // When user actually selects inside prompt, prompt selection wins
  promptEl.selectionStart = 0;
  promptEl.selectionEnd = 6;
  assert.equal(ChatUX.getSelectedText(promptEl), 'αρχικό');
  promptEl.selectionStart = promptEl.selectionEnd = promptEl.value.length;

  // 2. Floating selection toolbar appears on chat text selection, copies partial text & quotes into prompt
  ChatUX.updateSelectionBar();
  const bar = document.getElementById('sel-copy-bar');
  assert(bar && bar.classList.contains('show'), 'floating selection toolbar visible');
  bar.children[0].onclick(); // 📋 Αντιγραφή
  assert.equal(copied.pop(), 'κομμάτι που θέλω');

  ChatUX.updateSelectionBar();
  bar.children[1].onclick(); // 💬 Παράθεση
  assert(promptEl.value.includes('> κομμάτι που θέλω'), 'selection quoted into prompt');

  // 3. Right-click context menu on chat selection offers Copy + Quote + Paste into chat
  let prevented = false;
  ChatUX.showContextMenu({ target: bodyP, clientX: 250, clientY: 220, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  const menu = document.getElementById('chat-ctx-menu');
  assert(menu && menu.classList.contains('show'));
  const labels = menu.children.map((c) => (c.children[0] ? c.children[0].textContent : '---'));
  assert(labels.some((l) => l.includes('Αντιγραφή')));
  assert(labels.some((l) => l.includes('Παράθεση')));
  assert(labels.some((l) => l.includes('Επικόλληση στο chat')));
  // Click "Αντιγραφή" -> copies exact selected substring
  menu.children[0].onclick();
  assert.equal(copied.pop(), 'κομμάτι που θέλω');

  // 4. Right-click inside #prompt offers Paste and calls pasteInto(prompt)
  currentSelection = { text: '', anchorNode: null };
  ChatUX.showContextMenu({ target: promptEl, clientX: 300, clientY: 700, preventDefault: () => {} });
  const pasteBtn = menu.children.find((c) => c.children[0] && c.children[0].textContent.includes('Επικόλληση'));
  assert(pasteBtn, 'Paste item present in prompt context menu');
  pasteBtn.onclick();
  assert.equal(pastedTargets.pop(), 'prompt');

  // 5. Large Paste (>3000 chars with Greek UTF-8) converts into .txt attachment chip; short stays inline
  const shortOk = await ChatUX.maybeAttachLargePaste('μικρό κείμενο');
  assert.equal(shortOk, false);
  const hugeGreek = 'Καλημέρα Ακατοίκητο! '.repeat(220); // > 4000 chars
  assert(hugeGreek.length > ChatUX.LARGE_PASTE_THRESHOLD);
  const largeOk = await ChatUX.maybeAttachLargePaste(hugeGreek);
  assert.equal(largeOk, true);
  assert.equal(uploads.length, 1);
  assert.equal(uploads[0].url, '/api/attachments/upload');
  assert.equal(Buffer.from(uploads[0].payload.base64, 'base64').toString('utf8'), hugeGreek);
  assert.equal(attachments.length, 1);
  assert.equal(attachments[0].pasted, true);
  assert.equal(showAttachmentsCalls, 1);

  console.log('CHAT COPY PASTE UX OK');
})().catch((err) => { console.error(err); process.exitCode = 1; });
