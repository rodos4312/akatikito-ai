// Node UI tests: MEU-style attachment cards with image thumbnails, lightbox preview,
// attach while busy, in-app @file drag-drop, and assistant deliverable file cards.
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
    this.hidden = false;
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
  prepend(c) { c.parentNode = this; this.children.unshift(c); }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  focus() {}
}

const body = new El('body');
const attachPanel = new El('div'); attachPanel.id = 'attachment-panel';
const dropOverlay = new El('div'); dropOverlay.id = 'drop-overlay'; dropOverlay.hidden = true;
const promptEl = new El('textarea'); promptEl.id = 'prompt';
const chatEl = new El('div'); chatEl.id = 'chat';
body.append(attachPanel, dropOverlay, promptEl, chatEl);

const listeners = {};
const document = {
  body,
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

class FakeFileReader {
  readAsDataURL(file) {
    this.result = 'data:' + (file.type || 'application/octet-stream') + ';base64,' + Buffer.from(file._content || '').toString('base64');
    if (this.onload) this.onload();
  }
  readAsText(file) {
    this.result = String(file._content || '');
    if (this.onload) this.onload();
  }
}

const openedProject = [];
const openedOs = [];
const copied = [];

const ctx = {
  document,
  window: { addEventListener() {} },
  FileReader: FakeFileReader,
  connected: true,
  busy: true, // Must still allow attaching files while busy!
  currentProject: 'D:\\proj',
  currentSession: 'sess1',
  grow: () => {},
  showNotice: () => {},
  copyText: (t) => copied.push(t),
  openProjectFile: (p) => openedProject.push(p),
  openFileInOs: (p, reveal) => openedOs.push({ p, reveal }),
  api: async (_url, payload) => ({
    name: payload.name,
    path: 'attachments/' + payload.name,
    size: Buffer.from(payload.base64, 'base64').length,
    image: /\.(png|jpg|webp)$/i.test(payload.name),
  }),
  setInterval: () => 1,
  clearInterval: () => {},
  Date,
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../akatikito/ui/attachments.js'), 'utf8'), ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../akatikito/ui/chat_pro.js'), 'utf8'), ctx);

(async () => {
  // 1. Attach both an image and a code file even while busy === true
  await ctx.attachFiles([
    { name: 'screen.png', type: 'image/png', size: 120, _content: 'PNGDATA' },
    { name: 'paroxos.html', type: 'text/html', size: 42, _content: '<html><body>hi</body></html>' },
  ]);
  assert.equal(attachPanel.children.length, 2, '2 attachment cards rendered while busy');

  // Image card has real <img> thumbnail; HTML card has HTML badge
  const imgCard = attachPanel.children[0];
  const imgMain = imgCard.children[0];
  assert.equal(imgMain.children[0].tagName, 'IMG', 'image attachment renders thumbnail');
  assert(imgMain.children[0].src.startsWith('data:image/png;base64,'));

  const htmlCard = attachPanel.children[1];
  const htmlMain = htmlCard.children[0];
  assert.equal(htmlMain.children[0].textContent, 'HTML', 'code attachment renders ext badge');

  // 2. Clicking card main opens preview lightbox; clicking × removes without opening
  htmlMain.onclick();
  const lb = document.getElementById('attach-lightbox');
  assert(lb && lb.classList.contains('show'), 'lightbox preview opened');
  assert(lb.children[0].children[1].children[0].textContent.includes('<html>'));
  ctx.closeAttachmentPreview();
  assert(!lb.classList.contains('show'));

  imgCard.children[1].onclick({ stopPropagation() {} }); // × button
  assert.equal(attachPanel.children.length, 1, '× removed image attachment');

  // 3. In-app drag from Files panel drops @path into prompt without uploading
  const dropHandlers = listeners.drop || [];
  assert(dropHandlers.length > 0);
  dropHandlers[0]({
    preventDefault() {},
    dataTransfer: {
      types: ['application/x-akatikito-file'],
      getData: (t) => (t === 'application/x-akatikito-file' ? 'paroxos_v8.html' : ''),
      files: [],
    },
  });
  assert(promptEl.value.includes('@paroxos_v8.html'), 'in-app drop inserts @file reference');

  // 4. Deliverable File Cards on assistant message after edit_file / write_file turn
  ctx.ChatPro.appendTrailStep(chatEl, 'tool_call', { name: 'edit_file', arguments: '{"path":"paroxos_v8.html"}' });
  ctx.ChatPro.appendTrailStep(chatEl, 'tool_result', { name: 'edit_file', path: 'paroxos_v8.html', result: 'OK' });
  const assistantMsg = new El('div');
  ctx.ChatPro.attachTurnFileCards(assistantMsg);
  const cardsRow = assistantMsg.children.find((c) => c.className === 'file-cards-row');
  assert(cardsRow && cardsRow.children.length === 1, 'file card rendered on assistant reply');
  const btns = cardsRow.children[0].children[2].children;
  btns[0].onclick(); // Preview
  btns[1].onclick(); // Open OS
  btns[2].onclick(); // Reveal folder
  assert.deepEqual(openedProject, ['paroxos_v8.html']);
  assert.deepEqual(openedOs, [
    { p: 'paroxos_v8.html', reveal: false },
    { p: 'paroxos_v8.html', reveal: true },
  ]);

  console.log('CHAT ATTACHMENTS & FILE CARDS UX OK');
})().catch((err) => { console.error(err); process.exitCode = 1; });
