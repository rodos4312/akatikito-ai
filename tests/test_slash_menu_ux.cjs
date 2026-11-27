'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function makeEl(tag, id = '') {
  const el = {
    tagName: String(tag || 'DIV').toUpperCase(),
    id,
    className: '',
    textContent: '',
    value: '',
    style: {},
    children: [],
    listeners: {},
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    append(...xs) { for (const x of xs) this.appendChild(x); },
    replaceChildren(...xs) { this.children = []; this.append(...xs); },
    addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); },
    focus() { this.focused = true; },
    closest(sel) {
      if (sel === '#slash-menu' && this.id === 'slash-menu') return this;
      if (sel === '#slash-trigger-btn' && this.id === 'slash-trigger-btn') return this;
      return this.parentNode ? this.parentNode.closest(sel) : null;
    },
  };
  return el;
}

const menu = makeEl('div', 'slash-menu');
const promptEl = makeEl('textarea', 'prompt');
const triggerBtn = makeEl('button', 'slash-trigger-btn');
const docListeners = {};
const byId = { 'slash-menu': menu, prompt: promptEl, 'slash-trigger-btn': triggerBtn };

let sentCount = 0;
let compactCount = 0;

const ctx = {
  console,
  cachedSkills: [
    { name: 'debug', description: 'Διάγνωση σφαλμάτων' },
    { name: 'weekly-project', description: 'Εβδομαδιαία σύνοψη' },
  ],
  ChatPro: {
    runCompact() { compactCount += 1; },
  },
  grow() {},
  send() { sentCount += 1; },
  document: {
    body: makeEl('body'),
    getElementById(id) { return byId[id] || null; },
    createElement(tag) { return makeEl(tag); },
    addEventListener(ev, fn) { (docListeners[ev] ||= []).push(fn); },
  },
  window: {},
};
ctx.globalThis = ctx;
vm.createContext(ctx);
const src = fs.readFileSync(path.join(__dirname, '..', 'akatikito', 'ui', 'chat_ux.js'), 'utf8');
vm.runInContext(src, ctx);

// 1. 1-click toggle opens all 3 categories + skills
assert.equal(ctx.ChatUX.toggleSlashMenu(), true);
assert.equal(menu.style.display, 'flex');
const hdrs = menu.children.filter((c) => c.className === 'slash-cat-hdr').map((c) => c.textContent);
assert.equal(hdrs.length, 3);
assert.ok(hdrs[0].includes('Context'));
assert.ok(hdrs[1].includes('Git'));
assert.ok(hdrs[2].includes('Skills'));

const rows = menu.children.filter((c) => c.className.includes('slash-cmd-row'));
const names = rows.map((r) => r.children[0].textContent);
for (const expected of ['/compact', '/pin', '/unpin', '/pins', '/diff', '/test', '/review', '/learn', '/debug', '/weekly-project']) {
  assert.ok(names.includes(expected), 'missing ' + expected);
}

// 2. Clicking instant /diff fills prompt and calls send() immediately
const diffBtn = rows.find((r) => r.children[0].textContent === '/diff');
diffBtn.onclick();
assert.equal(promptEl.value, '/diff');
assert.equal(sentCount, 1);
assert.equal(menu.style.display, 'none');

// 3. Clicking /compact invokes ChatPro.runCompact() directly
ctx.ChatUX.toggleSlashMenu();
const compactBtn = menu.children.find((c) => c.children[0] && c.children[0].textContent === '/compact');
compactBtn.onclick();
assert.equal(compactCount, 1);
assert.equal(promptEl.value, '');

// 4. Clicking argument command /pin inserts '/pin ' and focuses prompt without sending
ctx.ChatUX.toggleSlashMenu();
const pinBtn = menu.children.find((c) => c.children[0] && c.children[0].textContent === '/pin');
pinBtn.onclick();
assert.equal(promptEl.value, '/pin ');
assert.equal(promptEl.focused, true);
assert.equal(sentCount, 1);

// 5. Filtering by query hides empty categories
ctx.ChatUX.renderSlashMenu(promptEl, menu, ctx.cachedSkills, 'rev');
const filteredHdrs = menu.children.filter((c) => c.className === 'slash-cat-hdr');
assert.equal(filteredHdrs.length, 1);
assert.ok(filteredHdrs[0].textContent.includes('Git'));

// 6. Toggle again closes cleanly
assert.equal(ctx.ChatUX.toggleSlashMenu(), false);
assert.equal(menu.style.display, 'none');

console.log('CATEGORIZED SLASH MENU UX OK');
