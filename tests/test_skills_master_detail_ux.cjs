// Node UI tests: 2-column Master-Detail Skills UI (selection, filtering, SKILL.md toggle, actions, proposals).
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
      add: (c) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), c])].join(' '); },
      remove: (c) => { this.className = this.className.split(/\s+/).filter((x) => x && x !== c).join(' '); },
      contains: (c) => this.className.split(/\s+/).includes(c),
    };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  contains(node) { let cur = node; while (cur) { if (cur === this) return true; cur = cur.parentNode; } return false; }
  addEventListener() {}
}

const body = new El('body');
const skillsPanel = new El('div'); skillsPanel.id = 'skills-content';
const searchEl = new El('input'); searchEl.id = 'skill-search';
body.append(skillsPanel, searchEl);

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
  addEventListener() {},
};

const actionsLog = [];
const ctx = {
  document,
  window: { innerWidth: 1200, innerHeight: 800, getSelection: () => ({ toString: () => '', rangeCount: 0 }) },
  runSkillInChat: (name) => actionsLog.push(['run', name]),
  editOwnerSkill: (row) => actionsLog.push(['edit', row.name]),
  deleteOwnerSkill: (name) => actionsLog.push(['delete', name]),
  learningAction: (act, id) => actionsLog.push([act, id]),
  setTimeout,
  clearTimeout,
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../akatikito/ui/chat_ux.js'), 'utf8'), ctx);

const { ChatUX } = ctx;

const sampleSkills = {
  catalog: [
    { name: 'my-game-qa', category: 'coding', description: 'Ελέγχει HTML games', source: 'user', steps: ['Τρέξε smoke_check', 'Τρέξε tests'], uses: 4 },
    { name: 'weekly-project', category: 'organization', description: 'Εβδομαδιαία σύνοψη', source: 'builtin', steps: ['Σύλλεξε αλλαγές', 'Γράψε σύνοψη'] },
    { name: 'debug', category: 'debugging', description: 'Εντοπισμός bug', source: 'builtin', steps: ['Αναπαρήγαγε σφάλμα'] },
  ],
  proposals: [
    { id: 'p1', name: 'new-skill', category: 'coding', description: 'Πρόταση AI', body: '---\nname: new-skill\n---\n1. Βήμα Α', signature: 'sig1' },
  ],
};

// 1. Renders 2-column split + proposals below
ChatUX.renderSkillsMasterDetail(skillsPanel, sampleSkills);
const split = skillsPanel.children[0];
assert.equal(split.className, 'skills-split');
const listCol = split.children[0];
const detailCard = split.children[1];
assert.equal(detailCard.children[0].textContent, '/my-game-qa', 'first skill selected by default');
// Custom skill has Run, SKILL.md, Edit, Delete buttons
const customBtns = detailCard.children[4].children.map((b) => b.textContent);
assert(customBtns.some((t) => t.includes('Χρήση στο Chat')));
assert(customBtns.some((t) => t.includes('Επεξεργασία')));
assert(customBtns.some((t) => t.includes('Διαγραφή')));

// Toggle SKILL.md preview
detailCard.children[4].children[1].onclick();
assert(detailCard.children[3].textContent.includes('name: my-game-qa'));

// 2. Click builtin skill '/weekly-project' in left list -> detail updates, no Edit/Delete
const navButtons = listCol.children.filter((c) => c.className.includes('skill-nav-item'));
assert.equal(navButtons.length, 3);
navButtons[1].onclick();
const detail2 = skillsPanel.children[0].children[1];
assert.equal(detail2.children[0].textContent, '/weekly-project');
const biBtns = detail2.children[4].children.map((b) => b.textContent);
assert(!biBtns.some((t) => t.includes('Διαγραφή')), 'builtin skill cannot be deleted');
detail2.children[4].children[0].onclick();
assert.deepEqual(actionsLog.pop(), ['run', 'weekly-project']);

// 3. Search filters left list live and updates active detail
ChatUX.filterSkillsMasterDetail('bug');
const filteredNav = skillsPanel.children[0].children[0].children.filter((c) => c.className.includes('skill-nav-item'));
assert.equal(filteredNav.length, 1);
assert.equal(filteredNav[0].children[0].textContent, '/debug');
assert.equal(skillsPanel.children[0].children[1].children[0].textContent, '/debug');

// 4. Proposal Approve button triggers learningAction
const propBox = skillsPanel.children[1];
assert.equal(propBox.className, 'skills-proposals-box');
propBox.children[1].children[2].children[0].onclick();
assert.deepEqual(actionsLog.pop(), ['skill_approve', 'p1']);

console.log('SKILLS MASTER DETAIL UX OK');
