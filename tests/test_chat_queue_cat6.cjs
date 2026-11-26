// Category 6 Node UI tests: Chat Message Queue (enqueue while busy, edit/delete, FIFO auto-drain, pause on error, session isolation).
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
    this.dataset = {};
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
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  insertBefore(c, ref) {
    c.parentNode = this;
    const idx = this.children.indexOf(ref);
    if (idx >= 0) this.children.splice(idx, 0, c);
    else this.children.push(c);
  }
  focus() {}
}

const ws = new El('div'); ws.id = 'chat-workspace';
const bar = new El('div'); bar.id = 'inputbar';
const promptEl = new El('textarea'); promptEl.id = 'prompt';
const sendBtn = new El('button'); sendBtn.id = 'sendbtn';
bar.append(promptEl, sendBtn);
ws.appendChild(bar);

const roots = [ws];
const document = {
  getElementById(id) {
    const walk = (n) => {
      if (n.id === id) return n;
      for (const ch of n.children || []) { const hit = walk(ch); if (hit) return hit; }
      return null;
    };
    for (const r of roots) { const hit = walk(r); if (hit) return hit; }
    return null;
  },
  createElement(tag) { return new El(tag); },
  addEventListener() {},
};

const sentTexts = [];
const ctx = {
  document,
  currentProject: 'D:\\projA',
  currentSession: 'sess1',
  grow: () => {},
  showNotice: () => {},
  send: async () => { sentTexts.push(promptEl.value); promptEl.value = ''; },
  setInterval: () => 1,
  clearInterval: () => {},
  Date,
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../akatikito/ui/chat_pro.js'), 'utf8'), ctx);

const { ChatPro } = ctx;

(async () => {
  // 1. Busy state shows ⏳ Ουρά button and allows enqueueing 3 messages
  ChatPro.syncMessageQueue({ busy: true, connected: true, project: 'D:\\projA', session_id: 'sess1' });
  const qBtn = document.getElementById('queue-btn');
  assert(qBtn && qBtn.style.display === 'inline-flex', 'queue button visible while busy');

  promptEl.value = 'First follow-up';
  qBtn.onclick();
  promptEl.value = 'Second follow-up';
  qBtn.onclick();
  promptEl.value = 'Third follow-up';
  qBtn.onclick();
  assert.deepEqual([...ChatPro.getQueue()], ['First follow-up', 'Second follow-up', 'Third follow-up']);
  assert.equal(promptEl.value, '');

  // 2. Edit pulls item #2 into prompt, Delete removes item
  const dock = document.getElementById('queue-dock');
  assert(dock.classList.contains('show'));
  const secondRow = dock.children[2]; // head=0, row0=1, row1=2
  secondRow.children[1].onclick(); // ✏️ edit
  assert.equal(promptEl.value, 'Second follow-up');
  assert.deepEqual([...ChatPro.getQueue()], ['First follow-up', 'Third follow-up']);
  promptEl.value = '';

  // 3. Error pauses queue instead of firing blindly
  ChatPro.syncMessageQueue({ busy: false, connected: true, error: 'rate limited', project: 'D:\\projA', session_id: 'sess1' });
  assert.equal(sentTexts.length, 0, 'queue pauses on error');

  // Resume button drains first item in FIFO order
  const resumeBtn = dock.children[0].children[1].children[0];
  assert(resumeBtn.textContent.includes('Συνέχεια'));
  resumeBtn.onclick();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(sentTexts, ['First follow-up']);
  assert.deepEqual([...ChatPro.getQueue()], ['Third follow-up']);

  // 4. Transition to idle without error auto-drains remaining item
  ChatPro.syncMessageQueue({ busy: false, connected: true, project: 'D:\\projA', session_id: 'sess1' });
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(sentTexts, ['First follow-up', 'Third follow-up']);
  assert.deepEqual([...ChatPro.getQueue()], []);

  // 5. Session/project switch wipes queued items from old session (zero cross-project leak!)
  ChatPro.enqueueFromPrompt('Task for projA');
  assert.equal(ChatPro.getQueue().length, 1);
  ChatPro.syncMessageQueue({ busy: true, connected: true, project: 'D:\\projB', session_id: 'sess2' });
  assert.equal(ChatPro.getQueue().length, 0, 'queue cleared on project/session switch');

  console.log('CHAT QUEUE CAT6 OK');
})().catch((err) => { console.error(err); process.exitCode = 1; });
