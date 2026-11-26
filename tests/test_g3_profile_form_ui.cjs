// G3 Node UI test: New Profile form validates adult PIN confirmation and sends exact fields to /api/profiles/action.
'use strict';

const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

class Element {
  constructor(tag = 'DIV', id = '') {
    this.tagName = tag;
    this.id = id;
    this.value = '';
    this.checked = false;
    this.textContent = '';
    this.children = [];
    this.style = { setProperty() {} };
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    this.attrs = new Map();
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.get(k) ?? null; }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChildren(...cs) { this.children = []; this.append(...cs); }
  addEventListener() {}
}

const ids = new Map();
function makeEl(id, tag = 'INPUT') {
  const el = new Element(tag, id);
  ids.set(id, el);
  return el;
}

for (const id of [
  'fp-name', 'fp-role', 'fp-pin', 'fp-pin-confirm', 'fp-admin-pin',
  'family-profile-select', 'family-profile-status',
]) {
  makeEl(id);
}

const apiCalls = [];
const notices = [];
const document = {
  body: new Element('BODY'),
  activeElement: null,
  getElementById: (id) => ids.get(id) || null,
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  addEventListener: () => {},
};

const ctx = {
  document,
  globalThis: null,
  api: async (url, payload) => {
    apiCalls.push({ url, payload });
    if (url === '/api/profiles') return { active: { id: 'primary', role: 'admin' }, profiles: [] };
    return { ok: true, active: { id: 'primary', role: 'admin' }, profiles: [] };
  },
  showNotice: (kind, msg) => notices.push({ kind, msg }),
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
const studioCode = fs.readFileSync(path.join(__dirname, '../akatikito/ui/studio.js'), 'utf8');
vm.runInContext(studioCode, ctx);

(async () => {
  assert.equal(typeof ctx.StudioUI.createFamilyProfile, 'function', 'createFamilyProfile must exist on StudioUI');

  // 1. Adult role with mismatched PIN confirmation -> rejected client-side, no API call
  ids.get('fp-name').value = 'Μαρία';
  ids.get('fp-role').value = 'admin';
  ids.get('fp-pin').value = '4321';
  ids.get('fp-pin-confirm').value = '9999';
  ids.get('fp-admin-pin').value = '2468';
  await ctx.StudioUI.createFamilyProfile();
  assert.equal(apiCalls.length, 0, 'mismatched PIN confirmation must not call API');
  assert.equal(notices[notices.length - 1].kind, 'err');

  // 2. Adult role with matching PIN + primary admin PIN -> posts exact payload
  ids.get('fp-pin-confirm').value = '4321';
  await ctx.StudioUI.createFamilyProfile();
  const postCall = apiCalls.find((c) => c.url === '/api/profiles/action');
  assert.ok(postCall, 'expected POST /api/profiles/action');
  assert.equal(postCall.payload.action, 'create');
  assert.equal(postCall.payload.display_name, 'Μαρία');
  assert.equal(postCall.payload.role, 'admin');
  assert.equal(postCall.payload.pin, '4321');
  assert.equal(postCall.payload.admin_pin, '2468');
  assert.match(postCall.payload.id, /^[a-z][a-z0-9_-]{1,31}$/);

  // 3. Child role without own PIN (only primary admin PIN) -> succeeds
  apiCalls.length = 0;
  ids.get('fp-name').value = 'Nikos';
  ids.get('fp-role').value = 'restricted';
  ids.get('fp-pin').value = '';
  ids.get('fp-pin-confirm').value = '';
  ids.get('fp-admin-pin').value = '2468';
  await ctx.StudioUI.createFamilyProfile();
  const childCall = apiCalls.find((c) => c.url === '/api/profiles/action');
  assert.ok(childCall, 'expected POST /api/profiles/action for child');
  assert.equal(childCall.payload.role, 'restricted');
  assert.equal(childCall.payload.pin, '');
  assert.equal(childCall.payload.admin_pin, '2468');

  // 4. Verify HTML contains the form controls
  const html = fs.readFileSync(path.join(__dirname, '../akatikito/ui.html'), 'utf8');
  for (const id of ['fp-name', 'fp-role', 'fp-pin', 'fp-pin-confirm', 'fp-admin-pin']) {
    assert.ok(html.includes(`id="${id}"`), `ui.html missing #${id}`);
  }
  console.log('OK: G3 new profile form UI tests passed');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
