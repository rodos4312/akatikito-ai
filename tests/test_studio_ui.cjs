// Studio UI contract tests: SVG vector nav, collapsible sidebar, Mobile tab, Settings Master-Detail.
'use strict';

const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

class ClassList {
  constructor() { this.set = new Set(); }
  add(c) { this.set.add(c); }
  remove(c) { this.set.delete(c); }
  toggle(c, force) {
    if (typeof force === 'boolean') {
      if (force) this.set.add(c); else this.set.delete(c);
      return force;
    }
    if (this.set.has(c)) { this.set.delete(c); return false; }
    this.set.add(c); return true;
  }
  contains(c) { return this.set.has(c); }
}

class Element {
  constructor(tag = 'DIV', id = '') {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.children = [];
    this.attrs = new Map();
    this.style = { setProperty() {} };
    this.dataset = {};
    this.classList = new ClassList();
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.listeners = {};
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  removeAttribute(k) { this.attrs.delete(k); }
  appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChildren(...cs) { this.children = []; this.append(...cs); }
  addEventListener(k, fn) { this.listeners[k] = fn; }
  focus() { document.activeElement = this; }
  click() { if (this.onclick) this.onclick(); }
}

const ids = new Map();
const nav = new Element('DIV');
const docListeners = {};

function makeEl(id, tag = 'DIV') {
  const el = new Element(tag, id);
  ids.set(id, el);
  return el;
}

const TABS = [
  ['sess', 'tab_sess', 'Συνομιλίες'],
  ['agenda', 'tab_agenda', 'Ατζέντα & Jobs'],
  ['providers', 'tab_providers', 'Πάροχοι / Μοντέλα'],
  ['plugins', 'tab_plugins', 'Πρόσθετα'],
  ['skills', 'tab_skills', 'Skills'],
  ['memory', 'tab_memory', 'Μνήμη'],
  ['mobile', 'tab_mobile', 'Κινητό'],
  ['set', 'tab_set', 'Ρυθμίσεις'],
];
for (const [key, i18nKey, text] of TABS) {
  const b = makeEl('tab-' + key, 'BUTTON');
  b.setAttribute('data-i18n', i18nKey);
  b.textContent = text;
  nav.appendChild(b);
}
for (const pane of ['general', 'routing', 'security', 'voice', 'budget']) {
  makeEl('set-pane-' + pane, 'DIV');
  makeEl('set-nav-' + pane, 'BUTTON');
}
for (const id of [
  'sidebar-toggle', 'settings-sidebar-collapsed', 'fallback-provider',
  'fallback-model', 'settings-ui-theme', 'settings-font-size',
  'font-size-val', 'settings-font-val',
]) {
  makeEl(id);
}

const body = new Element('BODY');
const document = {
  body,
  activeElement: null,
  getElementById(id) {
    if (ids.has(id)) return ids.get(id);
    for (const el of ids.values()) {
      const stack = [...el.children];
      while (stack.length) {
        const cur = stack.pop();
        if (cur.id === id) return cur;
        stack.push(...cur.children);
      }
    }
    return null;
  },
  createElement(tag) { return new Element(tag); },
  createElementNS(_ns, tag) { return new Element(tag); },
  addEventListener(k, fn) { docListeners[k] = fn; },
};

const apiCalls = [];
const context = {
  document,
  globalThis: null,
  currentFontSize: 14,
  api: async (url, payload) => { apiCalls.push({ url, payload }); return { ok: true }; },
  console,
};
context.globalThis = context;
vm.createContext(context);

const studioCode = fs.readFileSync(path.join(__dirname, '../akatikito/ui/studio.js'), 'utf8');
vm.runInContext(studioCode, context);

(async () => {
  context.StudioUI.boot();

  // 1. All 8 tabs have crisp SVG icon + data-i18n preserved on label span
  for (const [key, i18nKey] of TABS) {
    const btn = document.getElementById('tab-' + key);
    assert.equal(btn.getAttribute('role'), 'tab');
    assert.equal(btn.getAttribute('aria-controls'), 'view-' + key);
    assert.equal(btn.getAttribute('data-i18n'), null);
    assert.equal(btn.children.length, 2);
    assert.equal(btn.children[0].children[0].tagName, 'SVG');
    assert.equal(btn.children[1].id, 'studio-label-' + key);
    assert.equal(btn.children[1].getAttribute('data-i18n'), i18nKey);
  }

  // 2. Memory badge updates label span without destroying SVG icon
  context.StudioUI.updateMemoryTabLabel('🧠 Memory', 3);
  assert.equal(document.getElementById('tab-memory').children[0].children[0].tagName, 'SVG');
  assert.equal(document.getElementById('studio-label-memory').textContent, 'Memory · 3');

  // 3. Collapsible sidebar toggles class, ARIA, checkbox, and persists preference
  await context.StudioUI.toggleSidebar();
  assert.equal(body.classList.contains('sidebar-collapsed'), true);
  assert.equal(document.getElementById('sidebar-toggle').getAttribute('aria-expanded'), 'false');
  assert.equal(document.getElementById('sidebar-toggle').textContent, '☰');
  assert.equal(apiCalls.at(-1).url, '/api/preferences');
  assert.equal(apiCalls.at(-1).payload.sidebar_collapsed, true);

  // 4. Settings Master-Detail pane switching
  context.StudioUI.selectSettingsPane('routing');
  assert.equal(document.getElementById('set-pane-routing').style.display, '');
  assert.equal(document.getElementById('set-pane-general').style.display, 'none');
  assert.equal(document.getElementById('set-nav-routing').classList.contains('active'), true);

  // 5. Fallback + font size 20px sync
  context.StudioUI.syncSettingsControls({
    fallback_provider: 'OpenRouter',
    fallback_model: 'deepseek/deepseek-chat',
    preferences: { ui_font_size: 20, ui_theme: 'light', sidebar_collapsed: false },
  });
  assert.equal(document.getElementById('fallback-provider').value, 'OpenRouter');
  assert.equal(document.getElementById('fallback-model').value, 'deepseek/deepseek-chat');
  assert.equal(document.getElementById('font-size-val').textContent, '20px');
  assert.equal(body.classList.contains('sidebar-collapsed'), false);

  // 6. HTML integrity checks: no duplicate #drop-overlay, Studio assets linked
  const html = fs.readFileSync(path.join(__dirname, '../akatikito/ui.html'), 'utf8');
  assert.equal((html.match(/id="drop-overlay"/g) || []).length, 1);
  assert(html.includes('/ui/studio.css'));
  assert(html.includes('/ui/studio.js'));
  assert(html.includes('id="tab-mobile"'));
  assert(html.includes('id="view-mobile"'));
  assert(html.includes('id="settings-md-list"'));
  assert(html.includes('id="fallback-provider"'));

  console.log('STUDIO UI OK');
})().catch((err) => { console.error(err); process.exitCode = 1; });
