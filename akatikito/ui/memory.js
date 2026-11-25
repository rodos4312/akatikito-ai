// Memory screen (MEMORY-PLAN M6): search, correct, forget, judge, approve suggestions, tidy, export.
let memoryProject = '', memoryOffset = 0;

function memEl(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function memButton(parent, label, onclick) {
  const b = memEl('button', label, 'btn');
  b.style.width = 'auto'; b.onclick = onclick; parent.appendChild(b); return b;
}
function memKindLabel(kind) {
  return i18nText('mem_kind_' + kind, {core: 'Προφίλ', fact: 'Γεγονότα', belief: 'Υποθέσεις', episode: 'Επεισόδια'}[kind] || kind);
}
async function memoryAction(action, id, extra) {
  const result = await api('/api/memory/action', {project: memoryProject, action, id, ...(extra || {})});
  if (result.error) { showNotice('err', result.error); return result; }
  if (result.note) showNotice('ok', result.note);
  if (action === 'export' && result.markdown) {
    const box = document.getElementById('memory-extra');
    box.replaceChildren(memEl('pre', result.markdown, 'out'));
    return result;
  }
  if (action === 'tidy') { renderTidy(result.suggestions || []); return result; }
  await loadMemory(memoryOffset);
  return result;
}
function renderTidy(list) {
  const box = document.getElementById('memory-extra');
  box.replaceChildren(memEl('p', list.length ? i18nText('mem_tidy_found', '🧹 Προτάσεις τακτοποίησης: {n}').replace('{n}', list.length)
    : i18nText('mem_tidy_none', '🧹 Όλα τακτοποιημένα — δεν βρέθηκαν διπλές, παλιές ή αντιφατικές μνήμες.')));
  for (const s of list) {
    const card = memEl('div', s.note + '\n• ' + s.texts.join('\n• '), 'hint');
    card.style.whiteSpace = 'pre-wrap';
    const row = memEl('div'); row.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin-top:6px';
    for (const id of s.ids) memButton(row, i18nText('mem_btn_forget', 'Ξέχασε') + ' #' + id, () => memoryAction('forget', id));
    card.appendChild(row); box.appendChild(card);
  }
}
function memoryCard(row) {
  const card = memEl('div', undefined, 'hint');
  card.style.cssText = 'white-space:pre-wrap;margin-bottom:8px';
  const scope = row.scope === 'project' ? i18nText('mem_scope_project', 'Αυτού του project') : i18nText('mem_scope_global', 'Γενικές');
  const unconfirmed = row.status === 'unconfirmed' ? ' · ΑΝΕΠΙΒΕΒΑΙΩΤΗ' : '';
  const used = row.use_count ? ` · 🧠 ${row.use_count}` : '';
  const votes = (row.helpful || row.unhelpful) ? ` · 👍${row.helpful} 👎${row.unhelpful}` : '';
  card.appendChild(memEl('div', `${memKindLabel(row.kind)} · ${scope}${unconfirmed}${used}${votes}`, 'muted'));
  const text = memEl('div', (row.topic ? row.topic + ': ' : '') + row.text);
  card.appendChild(text);
  if (Array.isArray(row.matched) && row.matched.length) card.appendChild(memEl('div', '↳ ' + row.matched.join(', '), 'muted'));
  const actions = memEl('div'); actions.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin-top:6px';
  memButton(actions, i18nText('mem_btn_edit', '✏️ Διόρθωση'), () => {
    const input = memEl('textarea'); input.value = row.text; input.maxLength = 4000; input.style.width = '100%';
    text.replaceWith(input);
    memButton(actions, i18nText('mem_btn_save', '💾 Αποθήκευση'), () => memoryAction('edit', row.id, {text: input.value}));
  });
  memButton(actions, '👍', () => memoryAction('helpful', row.id));
  memButton(actions, '👎', () => memoryAction('unhelpful', row.id));
  memButton(actions, i18nText('mem_btn_history', '🕘 Ιστορικό'), async () => {
    const r = await api('/api/memory', {history: row.id});
    if (r.error) { showNotice('err', r.error); return; }
    const lines = (r.history || []).map(h => `${new Date(h.at * 1000).toLocaleString()} · ${h.action}` +
      (h.before ? `\n  πριν: ${h.before}` : '') + (h.after ? `\n  μετά: ${h.after}` : ''));
    card.appendChild(memEl('div', lines.join('\n') || '—', 'out'));
  });
  memButton(actions, i18nText('mem_btn_forget', 'Ξέχασε'), () => memoryAction('forget', row.id));
  memButton(actions, i18nText('mem_btn_forget_hard', 'Ξέχασε οριστικά'), () => {
    if (confirm(i18nText('mem_confirm_hard', 'Να σβηστεί οριστικά; Θα χαθούν και οι παλιές εκδοχές της· δεν αναιρείται.'))) memoryAction('forget_hard', row.id);
  });
  card.appendChild(actions);
  return card;
}
function proposalCard(p) {
  const card = memEl('div', undefined, 'hint');
  card.style.cssText = 'white-space:pre-wrap;margin-bottom:8px';
  card.appendChild(memEl('div', memKindLabel(p.kind) + (p.topic ? ' · ' + p.topic : ''), 'muted'));
  if (p.before) card.appendChild(memEl('div', '↻ ' + p.before, 'muted'));
  const input = memEl('textarea'); input.value = p.text; input.maxLength = 4000; input.style.width = '100%';
  card.appendChild(input);
  if (p.keywords) card.appendChild(memEl('div', '🔑 ' + p.keywords, 'muted'));
  const actions = memEl('div'); actions.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin-top:6px';
  memButton(actions, i18nText('btn_approve', 'Έγκριση'), () => memoryAction('proposal_approve', p.id, {text: input.value}));
  memButton(actions, i18nText('btn_reject', 'Απόρριψη'), () => memoryAction('proposal_reject', p.id));
  card.appendChild(actions);
  return card;
}
async function loadMemory(offset = 0) {
  const panel = document.getElementById('memory-content');
  if (!panel) return;
  const result = await api('/api/memory', {
    query: document.getElementById('mem-query')?.value || '', offset,
    scope: document.getElementById('mem-scope')?.value || 'all', kind: document.getElementById('mem-kind')?.value || ''});
  panel.replaceChildren();
  if (result.error) { panel.textContent = result.error; return; }
  memoryProject = result.project; memoryOffset = offset;
  if ((result.proposals || []).length) {
    panel.appendChild(memEl('p', i18nText('mem_hdr_suggestions', 'Προτάσεις προς έγκριση') + ' · ' + result.proposals.length));
    for (const p of result.proposals) panel.appendChild(proposalCard(p));
  }
  panel.appendChild(memEl('p', i18nText('mem_hdr_store', 'Κεντρική μνήμη') + ' · ' + (result.total || 0)));
  for (const row of result.rows || []) panel.appendChild(memoryCard(row));
  const nav = memEl('div'); nav.style.cssText = 'display:flex;gap:6px';
  if (offset > 0) memButton(nav, i18nText('btn_prev', 'Προηγούμενες'), () => loadMemory(Math.max(0, offset - 30)));
  if (result.next !== null && result.next !== undefined) memButton(nav, i18nText('btn_next', 'Επόμενες'), () => loadMemory(result.next));
  panel.appendChild(nav);
}
// Chat line for the memories the agent was given in this task (event "memories").
function renderMemoriesStep(div, data) {
  const label = i18nText('mem_used', '🧠 χρησιμοποίησα {n} μνήμες').replace('{n}', data.count || 0);
  const details = memEl('details');
  details.appendChild(memEl('summary', label));
  for (const item of data.items || []) {
    const row = memEl('div', '• ' + item.text + (item.why && item.why.length ? '  ↳ ' + item.why.join(', ') : ''), 'out');
    const vote = memEl('span'); vote.style.marginLeft = '6px';
    memButton(vote, '👍', () => api('/api/memory/action', {project: memoryProject || currentProject, action: 'helpful', id: item.id}));
    memButton(vote, '👎', () => api('/api/memory/action', {project: memoryProject || currentProject, action: 'unhelpful', id: item.id}));
    row.appendChild(vote); details.appendChild(row);
  }
  div.appendChild(details);
}
