// Node UI tests for Akatikito Chat Pro (Hermes/MEU-inspired trail, markdown, syntax highlight, jump-to-bottom).
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
    this._html = '';
    this.style = {};
    this.dataset = {};
    this.scrollHeight = 100;
    this.clientHeight = 100;
    this.scrollTop = 0;
    this._listeners = {};
    this.classList = {
      toggle: (cls, force) => {
        const set = new Set(this.className.split(/\s+/).filter(Boolean));
        const has = set.has(cls);
        const on = typeof force === 'boolean' ? force : !has;
        if (on) set.add(cls); else set.delete(cls);
        this.className = [...set].join(' ');
        return on;
      },
      add: (cls) => this.classList.toggle(cls, true),
      remove: (cls) => this.classList.toggle(cls, false),
      contains: (cls) => this.className.split(/\s+/).includes(cls),
    };
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  prepend(c) { c.parentNode = this; this.children.unshift(c); }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  insertBefore(c, ref) {
    c.parentNode = this;
    const idx = this.children.indexOf(ref);
    if (idx >= 0) this.children.splice(idx, 0, c);
    else this.children.push(c);
  }
  querySelector(sel) {
    if (sel === '.status') return this.children.find((c) => c.className === 'status') || null;
    if (sel === '.pro-dock-card') return this.children.find((c) => c.classList.contains('pro-dock-card')) || null;
    return null;
  }
  addEventListener(name, fn) { this._listeners[name] = fn; }
}

const topbar = new El('div');
const busyBadge = new El('span'); busyBadge.id = 'busy-badge'; topbar.appendChild(busyBadge);
const byId = new Map([
  ['chat', new El('div')],
  ['chat-workspace', new El('div')],
  ['task-panel', new El('div')],
  ['subtask-panel', new El('div')],
  ['slash-menu', new El('div')],
  ['busy-badge', busyBadge],
]);
const document = {
  getElementById(id) {
    if (byId.has(id)) return byId.get(id);
    const find = (node) => {
      if (node.id === id) return node;
      for (const ch of node.children || []) { const hit = find(ch); if (hit) return hit; }
      return null;
    };
    return find(topbar);
  },
  createElement(tag) { return new El(tag); },
  addEventListener() {},
};

const apiCalls = [];
const ctx = {
  document,
  codeSnippets: [],
  currentProject: 'D:\\demo',
  api: async (url, body) => { apiCalls.push({ url, body }); return { ok: true, preferences: body }; },
  grow: () => {},
  friendlyToolCall: (name) => `🔧 <b>${name}</b>`,
  isChatNearBottom: () => true,
  scrollChatIfPinned: () => {},
  setInterval: () => 1,
  clearInterval: () => {},
  Date,
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../akatikito/ui/chat_pro.js'), 'utf8'), ctx);

const { ChatPro } = ctx;

// 1. Rich Markdown + syntax highlighting + table + task list + live streaming caret
const md = [
  '## Heading Two',
  '',
  '- [x] Done task',
  '- [ ] Open task',
  '',
  '| Col A | Col B |',
  '| --- | --- |',
  '| `x` | **bold** |',
  '',
  '```python',
  'def hello(): # greet',
  '    return "world" + str(42)',
  '```',
].join('\n');

const html = ChatPro.renderMarkdown(md, { streaming: true });
assert(html.includes('<h2>Heading Two</h2>'), 'heading rendered');
assert(html.includes('☑ Done task') && html.includes('☐ Open task'), 'task checkboxes rendered');
assert(html.includes('<table class="md-table">') && html.includes('<strong>bold</strong>'), 'table & bold rendered');
assert(html.includes('hl-kw') && html.includes('hl-str') && html.includes('hl-num') && html.includes('hl-com'), 'syntax highlight spans present');
assert(html.includes('stream-caret'), 'streaming caret rendered');
assert.equal(ctx.codeSnippets.length, 1);

// 2. Grouped Hermes-style turn trail: reasoning + tool_call + tool_result collapse into ONE .turn-trail
const chat = document.getElementById('chat');
assert.equal(ChatPro.appendTrailStep(chat, 'reasoning', { text: 'Inspecting game loop carefully.' }), true);
assert.equal(ChatPro.appendTrailStep(chat, 'tool_call', { name: 'read_file', arguments: '{"path":"a.html"}' }), true);
assert.equal(ChatPro.appendTrailStep(chat, 'tool_result', { name: 'read_file', path: 'a.html', result: 'ok' }), true);
assert.equal(chat.children.length, 1, 'all steps grouped into ONE .turn-trail');
assert(chat.children[0].classList.contains('turn-trail'));
ChatPro.updateActiveTrail('βήμα 2/50', '⚡ 15.0k tok');
ChatPro.sealActiveTrail('Σ 15.0k tok');

// After sealing, a new step starts a fresh .turn-trail
assert.equal(ChatPro.appendTrailStep(chat, 'tool_call', { name: 'run_command', arguments: '{}' }), true);
assert.equal(chat.children.length, 2, 'sealed trail stays separate from next turn');
ChatPro.resetTrail();

// 3. Jump-to-bottom pill shows when scrolled up > 120px and hides on click
const ws = document.getElementById('chat-workspace');
ChatPro.initJumpToBottom();
const jumpBtn = ws.children.find((c) => c.id === 'jump-bottom-btn');
assert(jumpBtn, 'jump-to-bottom button attached');
chat.scrollHeight = 1200; chat.clientHeight = 400; chat.scrollTop = 200;
chat._listeners.scroll();
assert(jumpBtn.classList.contains('show'), 'jump pill visible when scrolled up');
jumpBtn.onclick();
assert.equal(chat.scrollTop, 1200);
assert(!jumpBtn.classList.contains('show'), 'jump pill hides after jumping to bottom');

// 4. Todo Progress Card + Subtasks Tree + Context Gauge + 1-click Autonomy Pill
ChatPro.syncStatus({
  busy: false,
  preferences: { autonomous: true },
  context: { sent_chars: 120000, budget_chars: 160000 },
  tokens: { prompt_tokens: 10000, completion_tokens: 500, cache_hit_tokens: 8000 },
  todos: [{ text: 'Step 1', status: 'done' }, { text: 'Step 2', status: 'in_progress' }],
  subtasks: [{ task: 'Scan CSS', status: 'running', tokens: { prompt_tokens: 1200, completion_tokens: 100 } }],
});
assert(document.getElementById('task-panel').children[0].classList.contains('pro-dock-card'), 'todo card rendered');
assert(document.getElementById('subtask-panel').children[0].classList.contains('pro-dock-card'), 'subtasks tree rendered');
assert(document.getElementById('ctx-lbl').textContent.includes('ctx 75%'), 'context gauge shows 75%');
assert(document.getElementById('ctx-lbl').textContent.includes('cache 80%'), 'context gauge shows cache 80%');
assert(document.getElementById('ctx-gauge').classList.contains('warn'), 'context gauge warns >= 60%');
const autoPill = document.getElementById('auto-pill');
assert.equal(autoPill.textContent, '⚡ Auto');
autoPill.onclick();
assert.equal(apiCalls[0].url, '/api/preferences');
assert.equal(apiCalls[0].body.autonomous, false);

// 5. Auto-collapse live reasoning + @file /skill user chips + @ autocomplete
const ls = { reasoning: 'Deep thought about layout', reasoningBody: new El('div') };
ChatPro.collapseStreamReasoning(ls);
assert(ls.reasoningBody.classList.contains('collapsed') && ls.reasoningBody.textContent.includes('Σκέψη μοντέλου'));
ls.reasoningBody.onclick();
assert(!ls.reasoningBody.classList.contains('collapsed') && ls.reasoningBody.textContent === 'Deep thought about layout');

const uHtml = ChatPro.formatUserText('Check @paroxos_v8.html with /review <script>');
assert(uHtml.includes('<span class="user-ref-chip">@paroxos_v8.html</span>'));
assert(uHtml.includes('<span class="user-ref-chip">/review</span>'));
assert(!uHtml.includes('<script>'));

ChatPro._setFileCacheForTest('D:\\demo', [{ path: 'paroxos_v8.html', name: 'paroxos_v8.html', type: 'file' }]);
const ta = new El('textarea'); ta.value = 'open @par'; ta.selectionStart = 9;
const sm = document.getElementById('slash-menu');
assert.equal(ChatPro.updateAtMenu(ta, sm), true);
assert.equal(sm.children.length, 1);
sm.children[0].onclick();
assert.equal(ta.value, 'open @paroxos_v8.html ');

console.log('CHAT PRO UI OK');
