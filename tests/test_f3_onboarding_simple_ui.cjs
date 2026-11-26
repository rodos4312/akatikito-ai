// F3 Node UI test: Simple First-Run paths (Cloud AI account + Local Ollama/LM Studio + Advanced + 3 example buttons).
'use strict';

const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

class El {
  constructor(tag = 'DIV', id = '') {
    this.tagName = tag;
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.innerHTML = '';
    this.disabled = false;
    this.inert = false;
    this.style = { display: '' };
    this.children = [];
    this.attrs = new Map();
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.get(k) ?? null; }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChildren(...cs) { this.children = []; this.append(...cs); }
  focus() {}
  addEventListener() {}
}

const ids = new Map();
function make(id, tag = 'DIV') {
  const el = new El(tag, id);
  ids.set(id, el);
  return el;
}

for (const id of [
  'setup-dialog', 'setup-progress', 'setup-page-0', 'setup-page-1', 'setup-page-2', 'setup-page-3',
  'setup-back', 'setup-next', 'setup-finish', 'setup-later', 'setup-project', 'setup-provider',
  'setup-url', 'setup-key', 'setup-key-hint', 'setup-model', 'setup-models', 'setup-test',
  'setup-summary', 'setup-error', 'setup-simple', 'setup-examples', 'tab-set', 'max_steps',
  'verify', 'prompt', 'files-panel', 'sidebar', 'main',
]) {
  make(id);
}

const apiCalls = [];
let localDetectResult = {
  found: [{ name: 'Ollama', base_url: 'http://127.0.0.1:11434/v1', models: ['llama3.2'] }],
  install_links: { ollama: 'https://ollama.com', lm_studio: 'https://lmstudio.ai' },
};

const document = {
  body: new El('BODY'),
  getElementById: (id) => ids.get(id) || null,
  createElement: (t) => new El(t),
  addEventListener: () => {},
};

const ctx = {
  document,
  Option: function Option(text, value) { const o = new El('OPTION'); o.textContent = text; o.value = value; return o; },
  busy: false,
  savedKeys: {},
  suggested: { OpenAI: ['gpt-4o-mini'], DeepSeek: ['deepseek-chat'], 'Ollama (τοπικό)': ['llama3.2'] },
  providerModels: {},
  i18nText: (k, fb) => fb || k,
  showNotice: () => {},
  acceptConnection: () => {},
  loadProviders: async () => {},
  changeUiLanguage: async (lang) => { apiCalls.push({ url: '/api/i18n', payload: { lang } }); },
  useQuickPrompt: (txt) => { ids.get('prompt').value = txt; },
  api: async (url, payload) => {
    apiCalls.push({ url, payload });
    if (url === '/api/providers') {
      return {
        providers: ['OpenAI', 'Anthropic (Claude)', 'Google Gemini', 'DeepSeek', 'Ollama (τοπικό)', 'Custom'],
        current: 'OpenAI',
        project: 'C:/Users/Demo/Documents/Akatikito',
        onboarding: { complete: false },
      };
    }
    if (url === '/api/onboarding/detect-local') return localDetectResult;
    if (url === '/api/connect') return { connected: true, onboarding: { complete: true }, provider: payload.provider, model: payload.model };
    return { ok: true };
  },
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../akatikito/onboarding.js'), 'utf8'), ctx);

(async () => {
  assert.equal(typeof ctx.setupChoosePath, 'function', 'setupChoosePath must exist in onboarding.js');
  assert.equal(typeof ctx.setupDetectLocal, 'function', 'setupDetectLocal must exist in onboarding.js');
  assert.equal(typeof ctx.setupRunExample, 'function', 'setupRunExample must exist in onboarding.js');

  await ctx.openSetup();
  // 1. Simple path: Cloud AI account
  ctx.setupChoosePath('cloud');
  assert.equal(ctx.setupSimplePath, 'cloud');
  assert.ok(ctx.SETUP_CLOUD_LINKS.OpenAI.startsWith('https://'));
  assert.ok(ctx.SETUP_CLOUD_LINKS['Anthropic (Claude)'].startsWith('https://'));
  assert.ok(ctx.SETUP_CLOUD_LINKS['Google Gemini'].startsWith('https://'));

  // 2. Simple path: Local AI (Ollama / LM Studio detection — never auto-downloads)
  await ctx.setupChoosePath('local');
  assert.equal(ctx.setupSimplePath, 'local');
  assert.ok(apiCalls.some((c) => c.url === '/api/onboarding/detect-local'));
  assert.equal(ids.get('setup-model').value, 'llama3.2');

  // 3. When no local server is running -> shows links, no auto-download
  localDetectResult = { found: [], install_links: { ollama: 'https://ollama.com', lm_studio: 'https://lmstudio.ai' } };
  const missing = await ctx.setupDetectLocal();
  assert.equal(missing.found.length, 0);
  assert.ok(missing.install_links.ollama.includes('ollama.com'));

  // 4. Advanced path -> preserves existing 4-step wizard
  ctx.setupChoosePath('advanced');
  assert.equal(ctx.setupSimplePath, 'advanced');

  // 5. 3 example buttons populate prompt with 1 click
  ctx.setupRunExample(0);
  assert.ok(ids.get('prompt').value.length > 5);
  console.log('OK: F3 simple onboarding UI paths passed');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
