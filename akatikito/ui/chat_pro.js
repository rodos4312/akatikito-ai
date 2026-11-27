// Akatikito Chat Pro — Hermes/MEU-inspired grouped tree activity trail,
// syntax-highlighted rich Markdown, live streaming markdown, and jump-to-bottom pill.
(function (root) {
  'use strict';

  const SPIN_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
  const KW_SET = new Set([
    'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'def', 'default',
    'elif', 'else', 'export', 'extends', 'false', 'finally', 'for', 'from', 'function', 'if',
    'import', 'in', 'interface', 'let', 'new', 'none', 'null', 'pass', 'raise', 'return',
    'self', 'static', 'struct', 'switch', 'this', 'throw', 'true', 'try', 'type', 'typeof',
    'undefined', 'var', 'void', 'while', 'with', 'yield', 'True', 'False', 'None', 'and', 'or', 'not',
  ]);

  let activeTrail = null;
  let pendingTurnFiles = [];
  let spinTimer = null;
  let spinIdx = 0;

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function highlightCode(raw, lang) {
    const src = String(raw || '');
    const l = String(lang || '').toLowerCase();
    if (l === 'html' || l === 'xml' || l === 'svg') {
      return escHtml(src).replace(/(&lt;\/?[a-zA-Z0-9_-]+|\/?&gt;)/g, '<span class="hl-tag">$1</span>');
    }
    const re = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)(?=\s*\()|\b([A-Za-z_]\w*)\b/g;
    let out = '', last = 0, m;
    while ((m = re.exec(src)) !== null) {
      if (m.index > last) out += escHtml(src.slice(last, m.index));
      const tok = escHtml(m[0]);
      if (m[1]) out += `<span class="hl-com">${tok}</span>`;
      else if (m[2]) out += `<span class="hl-str">${tok}</span>`;
      else if (m[3]) out += `<span class="hl-num">${tok}</span>`;
      else if (m[4]) out += `<span class="hl-fn">${tok}</span>`;
      else if (m[5] && KW_SET.has(m[5])) out += `<span class="hl-kw">${tok}</span>`;
      else out += tok;
      last = re.lastIndex;
    }
    if (last < src.length) out += escHtml(src.slice(last));
    return out;
  }

  function renderInline(raw) {
    const codeTokens = [];
    let s = String(raw || '').replace(/`([^`\n]+)`/g, (_, inner) => {
      const idx = codeTokens.push(`<code>${escHtml(inner)}</code>`) - 1;
      return `\x00IC_${idx}\x00`;
    });
    s = escHtml(s);
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>');
    s = s.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    for (let i = 0; i < codeTokens.length; i++) {
      s = s.replace(`\x00IC_${i}\x00`, () => codeTokens[i]);
    }
    return s;
  }

  function renderCodeBlock(lang, body) {
    const clean = body.trimEnd();
    const snippets = Array.isArray(root.codeSnippets) ? root.codeSnippets : null;
    const idx = snippets ? snippets.push(clean) - 1 : -1;
    const btn = idx >= 0 ? `<button onclick="copyCodeBlock(${idx})">📋 Αντιγραφή</button>` : '';
    return `<div class="code-block"><div class="code-head"><span>${escHtml(lang || 'code')}</span>${btn}</div><pre><code>${highlightCode(clean, lang)}</code></pre></div>`;
  }

  function renderTableBlock(lines) {
    const rows = lines.map((r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
    if (rows.length < 2 || !rows[1].every((c) => /^:?-+:?$/.test(c))) return null;
    const head = '<tr>' + rows[0].map((c) => `<th>${renderInline(c)}</th>`).join('') + '</tr>';
    const body = rows.slice(2).map((r) => '<tr>' + r.map((c) => `<td>${renderInline(c)}</td>`).join('') + '</tr>').join('');
    return `<div class="table-wrap"><table class="md-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  }

  function consumeList(lines, start, ordered) {
    const re = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*•]\s+(.*)$/;
    const items = [];
    let i = start;
    while (i < lines.length) {
      const m = lines[i].match(re);
      if (!m) break;
      const item = m[1].replace(/^\[([ xX])\]\s+/, (_, chk) => (chk.trim() ? '☑ ' : '☐ '));
      items.push(`<li>${renderInline(item)}</li>`);
      i++;
    }
    const tag = ordered ? 'ol' : 'ul';
    return { html: `<${tag}>${items.join('')}</${tag}>`, next: i };
  }

  function isSpecialStart(line) {
    return /^```/.test(line) || /^#{1,4}\s+/.test(line) || /^[-*_]{3,}\s*$/.test(line) ||
      /^>\s?/.test(line) || /^\s*[-*•]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line) || /^\|.+\|$/.test(line.trim());
  }

  function renderMarkdown(text, opts) {
    const streaming = !!(opts && opts.streaming);
    const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
    const blocks = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = line.match(/^```([a-zA-Z0-9_+-]*)/);
      if (fence) {
        const buf = [];
        i++;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
        if (i < lines.length) i++;
        blocks.push(renderCodeBlock(fence[1], buf.join('\n')));
        continue;
      }
      const hdr = line.match(/^(#{1,4})\s+(.*)$/);
      if (hdr) { blocks.push(`<h${hdr[1].length}>${renderInline(hdr[2])}</h${hdr[1].length}>`); i++; continue; }
      if (/^[-*_]{3,}\s*$/.test(line)) { blocks.push('<hr>'); i++; continue; }
      if (/^\|.+\|$/.test(line.trim())) {
        const tLines = [];
        while (i < lines.length && /^\|.+\|$/.test(lines[i].trim())) { tLines.push(lines[i]); i++; }
        const tbl = renderTableBlock(tLines);
        blocks.push(tbl || `<p>${tLines.map(renderInline).join('<br>')}</p>`);
        continue;
      }
      if (/^>\s?/.test(line)) {
        const q = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) { q.push(renderInline(lines[i].replace(/^>\s?/, ''))); i++; }
        blocks.push(`<blockquote>${q.join('<br>')}</blockquote>`);
        continue;
      }
      if (/^\s*[-*•]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
        const res = consumeList(lines, i, /^\s*\d+[.)]\s+/.test(line));
        blocks.push(res.html); i = res.next; continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !isSpecialStart(lines[i])) { para.push(renderInline(lines[i])); i++; }
      blocks.push(`<p>${para.join('<br>')}</p>`);
    }
    const caret = streaming ? '<span class="stream-caret" aria-hidden="true"></span>' : '';
    return `<div class="md-body">${blocks.join('')}${caret}</div>`;
  }

  function startSpinner() {
    if (spinTimer || typeof setInterval !== 'function') return;
    spinTimer = setInterval(() => {
      if (!activeTrail || !activeTrail.active || !activeTrail.root.parentNode) { stopSpinner(); return; }
      spinIdx = (spinIdx + 1) % SPIN_FRAMES.length;
      activeTrail.spinEl.textContent = SPIN_FRAMES[spinIdx];
      const sec = Math.max(0, (Date.now() - activeTrail.startedAt) / 1000);
      activeTrail.elapsedEl.textContent = sec < 10 ? sec.toFixed(1) + 's' : Math.round(sec) + 's';
    }, 95);
  }

  function stopSpinner() {
    if (spinTimer && typeof clearInterval === 'function') clearInterval(spinTimer);
    spinTimer = null;
  }

  function ensureTrail(chatEl) {
    if (activeTrail && activeTrail.active && activeTrail.root.parentNode === chatEl) return activeTrail;
    const box = document.createElement('div');
    box.className = 'turn-trail';
    const head = document.createElement('div');
    head.className = 'trail-head';
    const chev = document.createElement('span'); chev.className = 'trail-chev'; chev.textContent = '▾';
    const spinEl = document.createElement('span'); spinEl.className = 'trail-spin'; spinEl.textContent = SPIN_FRAMES[0];
    const titleEl = document.createElement('span'); titleEl.className = 'trail-title'; titleEl.textContent = 'Δραστηριότητα agent';
    const metaEl = document.createElement('span'); metaEl.className = 'trail-meta';
    const stepEl = document.createElement('span');
    const tokEl = document.createElement('span');
    const elapsedEl = document.createElement('span');
    metaEl.append(stepEl, tokEl, elapsedEl);
    head.append(chev, spinEl, titleEl, metaEl);
    head.onclick = () => {
      const collapsed = box.classList.toggle('collapsed');
      chev.textContent = collapsed ? '▸' : '▾';
    };
    const body = document.createElement('div'); body.className = 'trail-body';
    const rail = document.createElement('div'); rail.className = 'trail-rail';
    body.appendChild(rail);
    box.append(head, body);
    const statusEl = chatEl.querySelector?.('.status');
    if (statusEl && typeof chatEl.insertBefore === 'function') chatEl.insertBefore(box, statusEl);
    else chatEl.appendChild(box);
    activeTrail = {
      root: box, body, rail, spinEl, titleEl, stepEl, tokEl, elapsedEl, chev,
      active: true, toolCount: 0, lastToolRow: null, startedAt: Date.now(), hasError: false,
    };
    startSpinner();
    return activeTrail;
  }

  function makeToolActionBtns(data) {
    const bar = document.createElement('div');
    bar.className = 'trail-actions';
    const target = String(data.path || '');
    const defs = [
      ['📄 ' + target, () => root.openProjectFile?.(target)],
      ['📂 Windows', () => root.openFileInOs?.(target, false)],
      ['📁 Φάκελος', () => root.openFileInOs?.(target, true)],
    ];
    for (const [label, fn] of defs) {
      const btn = document.createElement('button');
      btn.className = 'btn';
      btn.textContent = label;
      btn.onclick = fn;
      bar.appendChild(btn);
    }
    return bar;
  }

  function attachToolResult(trail, data) {
    const row = trail.lastToolRow;
    if (!row) return;
    const text = String(data.result || '');
    const failed = /^(\[OWNER_DENIED\]|ΣΦΑΛΜΑ|ERROR|returncode=[1-9])/i.test(text);
    if (row._dot) {
      row._dot.className = 'trail-dot ' + (failed ? 'bad' : 'ok');
      row._dot.textContent = failed ? '✗' : '✓';
    }
    if (failed) trail.hasError = true;
    if (data.path && ['edit_file', 'write_file', 'replace_symbol', 'apply_patch', 'convert_data_file', 'create_word_document', 'create_excel_sheet', 'create_pdf_document'].includes(data.name)) {
      row.appendChild(makeToolActionBtns(data));
      const p = String(data.path);
      const action = data.name === 'edit_file' || data.name === 'replace_symbol' || data.name === 'apply_patch' ? 'ενημερώθηκε' : 'δημιουργήθηκε';
      const existing = pendingTurnFiles.find((x) => x.path === p);
      if (existing) existing.action = action;
      else pendingTurnFiles.push({ path: p, action });
    }
    if (data.diff && typeof root.formatColoredDiff === 'function') {
      const diffWrap = document.createElement('div');
      diffWrap.innerHTML = root.formatColoredDiff(data.diff);
      row.appendChild(diffWrap);
    }
    if (text) {
      const det = document.createElement('details'); det.className = 'trail-details';
      const sum = document.createElement('summary'); sum.textContent = failed ? 'Αποτέλεσμα (σφάλμα)' : 'Αποτέλεσμα';
      const out = document.createElement('div'); out.className = 'trail-out'; out.textContent = text.slice(0, 1200);
      det.append(sum, out);
      row.appendChild(det);
    }
    trail.lastToolRow = null;
  }

  function addTrailRow(trail, dotCls, dotTxt, html, extraClass) {
    const row = document.createElement('div');
    row.className = 'trail-row' + (extraClass ? ' ' + extraClass : '');
    const line = document.createElement('div'); line.className = 'trail-line';
    const dot = document.createElement('span'); dot.className = 'trail-dot ' + dotCls; dot.textContent = dotTxt;
    const body = document.createElement('span'); body.innerHTML = html;
    line.append(dot, body);
    row.appendChild(line);
    row._dot = dot;
    trail.rail.appendChild(row);
    return row;
  }

  function appendTrailStep(chatEl, kind, data) {
    if (!chatEl || !['reasoning', 'thinking', 'tool_call', 'tool_result', 'repair', 'verify', 'compacted', 'recovery', 'stopped', 'limit', 'give_up', 'checkpoint_error', 'fatal'].includes(kind)) {
      return false;
    }
    const pin = typeof root.isChatNearBottom === 'function' ? root.isChatNearBottom() : true;
    const trail = ensureTrail(chatEl);
    if (kind === 'tool_result') {
      attachToolResult(trail, data || {});
      root.scrollChatIfPinned?.(pin);
      return true;
    }
    if (kind === 'reasoning') {
      const txt = String(data?.text || '');
      const approx = Math.max(1, Math.round(txt.length / 4));
      const row = document.createElement('div'); row.className = 'trail-row trail-thinking';
      const det = document.createElement('details'); det.className = 'trail-details';
      const sum = document.createElement('summary'); sum.textContent = `🧠 Σκέψη μοντέλου (~${approx} tok)`;
      const out = document.createElement('div'); out.className = 'trail-out'; out.textContent = txt;
      det.append(sum, out); row.appendChild(det); trail.rail.appendChild(row);
    } else if (kind === 'tool_call') {
      trail.toolCount++;
      trail.titleEl.textContent = `Ενέργειες (${trail.toolCount})`;
      const label = typeof root.friendlyToolCall === 'function' ? root.friendlyToolCall(data.name, data.arguments) : escHtml(data.name);
      trail.lastToolRow = addTrailRow(trail, 'running', '●', label);
    } else if (kind === 'thinking') {
      addTrailRow(trail, '', '💭', escHtml(data?.text || ''));
    } else if (kind === 'verify') {
      const ok = !!data?.ok;
      if (!ok) trail.hasError = true;
      const r = addTrailRow(trail, ok ? 'ok' : 'bad', ok ? '✓' : '✗', ok ? 'Ο έλεγχος πέρασε' : 'Ο έλεγχος απέτυχε');
      if (!ok && data?.output) {
        const out = document.createElement('div'); out.className = 'trail-out'; out.textContent = String(data.output).slice(-1000);
        r.appendChild(out);
      }
    } else if (kind === 'repair') {
      const lbl = { investigate: 'Εντοπισμός αιτίας', diagnosis: 'Διάγνωση', passed: 'Επαληθεύτηκε η διόρθωση' }[data?.phase] || 'Διόρθωση';
      addTrailRow(trail, '', '🛠', `<b>${escHtml(lbl)}</b> — ${escHtml(data?.text || '')}`);
    } else if (kind === 'compacted') {
      const b = Math.round(Number(data?.before_chars || 0) / 100) / 10;
      const a = Math.round(Number(data?.after_chars || 0) / 100) / 10;
      const pct = Number(data?.saved_pct || 0);
      addTrailRow(trail, 'ok', '🗜', `<b>Συμπίεση context</b> (${escHtml(data?.reason === 'auto' ? 'αυτόματη' : 'χειροκίνητη')}) · ${b}k → ${a}k chars (-${pct}%)`);
    } else if (kind === 'recovery') {
      addTrailRow(trail, 'ok', '🩺', `<b>Αυτόματη ανάκαμψη</b> — ${escHtml(data?.text || '')}`);
    } else {
      const msg = kind === 'stopped' ? 'Σταμάτησα όπως ζήτησες'
        : kind === 'limit' ? 'Έφτασα το όριο βημάτων'
        : kind === 'give_up' ? `Ο έλεγχος απέτυχε ${data?.attempts || ''} φορές`
        : escHtml(data?.text || kind);
      trail.hasError = kind !== 'stopped';
      addTrailRow(trail, kind === 'stopped' ? '' : 'bad', kind === 'stopped' ? '⏹' : '⚠', msg);
    }
    if (trail.body && typeof trail.body.scrollHeight === 'number') {
      trail.body.scrollTop = trail.body.scrollHeight;
    }
    root.scrollChatIfPinned?.(pin);
    return true;
  }

  function updateActiveTrail(stepLabel, tokLabel) {
    if (!activeTrail || !activeTrail.active) return;
    if (stepLabel) activeTrail.stepEl.textContent = stepLabel;
    if (tokLabel) activeTrail.tokEl.textContent = tokLabel;
  }

  function sealActiveTrail(tokLabel) {
    if (!activeTrail || !activeTrail.active) return;
    activeTrail.active = false;
    stopSpinner();
    activeTrail.spinEl.textContent = activeTrail.hasError ? '⚠' : '✓';
    activeTrail.spinEl.className = 'trail-spin ' + (activeTrail.hasError ? 'err' : 'done');
    activeTrail.titleEl.textContent = activeTrail.toolCount > 0
      ? `Ολοκληρώθηκε · ${activeTrail.toolCount} ${activeTrail.toolCount === 1 ? 'ενέργεια' : 'ενέργειες'}`
      : 'Συλλογισμός ολοκληρώθηκε';
    if (tokLabel) activeTrail.tokEl.textContent = tokLabel;
    activeTrail = null;
  }

  function formatTimeNow() {
    const d = new Date();
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  function initJumpToBottom() {
    const ws = document.getElementById('chat-workspace');
    const chatEl = document.getElementById('chat');
    if (!ws || !chatEl || document.getElementById('jump-bottom-btn')) return;
    const btn = document.createElement('button');
    btn.id = 'jump-bottom-btn';
    btn.type = 'button';
    btn.textContent = '↓ Επιστροφή κάτω';
    btn.onclick = () => {
      chatEl.scrollTop = chatEl.scrollHeight;
      btn.classList.remove('show');
    };
    ws.appendChild(btn);
    if (typeof chatEl.addEventListener === 'function') {
      chatEl.addEventListener('scroll', () => {
        const dist = chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight;
        btn.classList.toggle('show', dist > 120);
      }, { passive: true });
    }
  }

  let todoCollapsed = false;
  let todoSig = '';
  function renderTodos(todos) {
    const panel = document.getElementById('task-panel');
    if (!panel) return;
    const rows = Array.isArray(todos) ? todos : [];
    const sig = JSON.stringify([rows, todoCollapsed]);
    if (sig === todoSig && panel.querySelector?.('.pro-dock-card')) return;
    todoSig = sig;
    panel.replaceChildren();
    if (!rows.length) return;
    const done = rows.filter((r) => r.status === 'done' || r.status === 'completed').length;
    const pct = Math.round((done * 100) / rows.length);
    const card = document.createElement('div');
    card.className = 'pro-dock-card' + (todoCollapsed ? ' collapsed' : '');
    const head = document.createElement('div'); head.className = 'pro-dock-head';
    const chev = document.createElement('span'); chev.className = 'trail-chev'; chev.textContent = todoCollapsed ? '▸' : '▾';
    const ttl = document.createElement('span'); ttl.textContent = `Σχέδιο εργασίας (${done}/${rows.length})`;
    const meta = document.createElement('span'); meta.className = 'pro-dock-meta'; meta.textContent = `${pct}%`;
    head.append(chev, ttl, meta);
    head.onclick = () => { todoCollapsed = !todoCollapsed; todoSig = ''; renderTodos(rows); };
    const track = document.createElement('div'); track.className = 'pro-progress-track';
    const fill = document.createElement('div'); fill.className = 'pro-progress-fill'; fill.style.width = `${pct}%`;
    track.appendChild(fill);
    const body = document.createElement('div'); body.className = 'pro-dock-body';
    for (const r of rows) {
      const st = r.status === 'done' || r.status === 'completed' ? 'done' : r.status === 'in_progress' ? 'active' : 'pending';
      const item = document.createElement('div'); item.className = 'todo-row ' + st;
      const ico = document.createElement('span'); ico.className = 'todo-ico';
      ico.textContent = st === 'done' ? '✓' : st === 'active' ? '◉' : '○';
      const txt = document.createElement('span'); txt.textContent = String(r.text || '');
      item.append(ico, txt); body.appendChild(item);
    }
    card.append(head, track, body);
    panel.appendChild(card);
  }

  let subSig = '';
  function renderSubtasks(subtasks) {
    const panel = document.getElementById('subtask-panel');
    if (!panel) return;
    const rows = Array.isArray(subtasks) ? subtasks : [];
    const sig = JSON.stringify(rows);
    if (sig === subSig && panel.querySelector?.('.pro-dock-card')) return;
    subSig = sig;
    panel.replaceChildren();
    if (!rows.length) return;
    const card = document.createElement('div'); card.className = 'pro-dock-card';
    const head = document.createElement('div'); head.className = 'pro-dock-head';
    head.textContent = `🤖 Υπο-εργασίες (${rows.length})`;
    const body = document.createElement('div'); body.className = 'pro-dock-body';
    const rail = document.createElement('div'); rail.className = 'trail-rail';
    for (const r of rows) {
      const st = String(r.status || '');
      const dotCls = st === 'running' ? 'running' : st === 'complete' || st === 'done' || st === 'completed' ? 'ok' : st === 'error' || st === 'failed' ? 'bad' : '';
      const dotTxt = dotCls === 'ok' ? '✓' : dotCls === 'bad' ? '✗' : '●';
      const tok = (r.tokens?.prompt_tokens || 0) + (r.tokens?.completion_tokens || 0);
      const tokStr = tok > 0 ? ` · ${tok >= 1000 ? (tok / 1000).toFixed(1) + 'k' : tok} tok` : '';
      const liveStr = st === 'running' && r.active_tool ? ` · ${r.active_tool}` : '';
      const secStr = Number(r.elapsed_s) > 0 ? ` · ${Math.round(r.elapsed_s)}s` : '';
      const row = document.createElement('div'); row.className = 'trail-row';
      const line = document.createElement('div'); line.className = 'trail-line';
      const dot = document.createElement('span'); dot.className = 'trail-dot ' + dotCls; dot.textContent = dotTxt;
      const txt = document.createElement('span'); txt.textContent = `${r.task || ''} (${st}${liveStr}${secStr}${tokStr})`;
      line.append(dot, txt); row.appendChild(line); rail.appendChild(row);
    }
    body.appendChild(rail); card.append(head, body); panel.appendChild(card);
  }

  function ensureTopbarWidgets() {
    const busy = document.getElementById('busy-badge');
    const bar = busy && busy.parentNode;
    if (!bar) return {};
    let gauge = document.getElementById('ctx-gauge');
    if (!gauge) {
      gauge = document.createElement('span'); gauge.id = 'ctx-gauge'; gauge.className = 'ctx-gauge';
      gauge.style.display = 'none';
      const b = document.createElement('span'); b.className = 'ctx-bar';
      const f = document.createElement('span'); f.className = 'ctx-fill'; f.id = 'ctx-fill';
      const lbl = document.createElement('span'); lbl.id = 'ctx-lbl';
      b.appendChild(f); gauge.append(b, lbl);
      if (typeof bar.insertBefore === 'function') bar.insertBefore(gauge, busy.nextSibling || null);
      else bar.appendChild(gauge);
    }
    let autoBtn = document.getElementById('auto-pill');
    if (!autoBtn) {
      autoBtn = document.createElement('button'); autoBtn.id = 'auto-pill'; autoBtn.type = 'button'; autoBtn.className = 'auto-pill';
      if (typeof bar.insertBefore === 'function') bar.insertBefore(autoBtn, gauge.nextSibling || null);
      else bar.appendChild(autoBtn);
    }
    return { gauge, autoBtn };
  }

  function _computeCtxGauge(ctx) {
    const realPrompt = Number(ctx.real_prompt_tokens || 0);
    const nativeCtx = Number(ctx.native_context_tokens || 0);
    if (realPrompt > 0 && nativeCtx > 0) {
      const pct = Math.min(100, Math.round((realPrompt * 100) / nativeCtx));
      return { pct, note: `${Math.round(realPrompt / 1000)}k / ${Math.round(nativeCtx / 1000)}k tok` };
    }
    const sent = Number(ctx.active_bytes || ctx.sent_chars || 0);
    const budget = Number(ctx.active_budget_bytes || ctx.budget_chars || ctx.active_max_bytes || 160000);
    const pct = sent > 0 && budget > 0 ? Math.min(100, Math.round((sent * 100) / budget)) : 0;
    return { pct, note: `${Math.round(sent / 1024)} / ${Math.round(budget / 1024)} KB` };
  }

  function syncTopbarControls(s) {
    const { gauge, autoBtn } = ensureTopbarWidgets();
    if (gauge) {
      const ctx = s.context || {}, t = s.tokens || {};
      const { pct, note } = _computeCtxGauge(ctx);
      const hit = Math.min(t.prompt_tokens || 0, t.cache_hit_tokens || 0);
      const cPct = hit > 0 && t.prompt_tokens > 0 ? Math.round((hit * 100) / t.prompt_tokens) : 0;
      gauge.style.display = pct > 0 || cPct > 0 ? 'inline-flex' : 'none';
      gauge.className = 'ctx-gauge' + (pct >= 82 ? ' high' : pct >= 60 ? ' warn' : '');
      const fill = document.getElementById('ctx-fill');
      const lbl = document.getElementById('ctx-lbl');
      if (fill) fill.style.width = `${pct}%`;
      const sessCost = Number(s.usage_ledger?.session?.cost || 0);
      const dayCost = Number(s.usage_ledger?.day?.cost || 0);
      const costSuffix = sessCost > 0 ? ` · $${sessCost.toFixed(4)}` : '';
      const costTitle = (sessCost > 0 || dayCost > 0) ? ` · Κόστος chat: $${sessCost.toFixed(4)} (σήμερα: $${dayCost.toFixed(4)})` : '';
      if (lbl) lbl.textContent = (pct > 0 ? `ctx ${pct}%` : '') + (cPct > 0 ? `${pct > 0 ? ' · ' : ''}cache ${cPct}%` : '') + costSuffix;
      gauge.style.cursor = s.busy ? 'default' : 'pointer';
      gauge.title = `Context: ${pct}% (${note}) · Prompt: ${t.prompt_tokens || 0} · Cache hit: ${hit} (${cPct}%)${costTitle} — κλικ για συμπίεση (/compact)`;
      gauge.onclick = () => { if (!s.busy) runCompact(); };
    }
    if (autoBtn) {
      const on = !!s.preferences?.autonomous;
      autoBtn.className = 'auto-pill' + (on ? ' on' : '');
      autoBtn.textContent = on ? '⚡ Auto' : '🔒 Ask';
      autoBtn.disabled = !!s.busy;
      autoBtn.title = on
        ? 'Αυτόνομες συνηθισμένες εντολές (επικίνδυνες πάντα με έγκριση) — κλικ για αυστηρή λειτουργία'
        : 'Έγκριση σε κάθε εντολή — κλικ για αυτόνομη εκτέλεση συνηθισμένων εντολών';
      autoBtn.onclick = async () => {
        if (s.busy || typeof root.api !== 'function') return;
        const r = await root.api('/api/preferences', { autonomous: !on });
        if (r && r.error) root.showNotice?.('err', r.error);
        else if (r && typeof root.updateBadges === 'function') root.updateBadges(r);
      };
    }
  }

  function syncStatus(s) {
    if (!s || typeof s !== 'object') return;
    renderTodos(s.todos);
    renderSubtasks(s.subtasks);
    syncTopbarControls(s);
    syncMessageQueue(s);
  }

  function syncStreamReasoning(ls) {
    if (!ls || !ls.reasoningBody) return;
    if (ls.reasoning && ls.text && !ls.reasoningCollapsed) {
      ls.reasoningCollapsed = true;
      collapseStreamReasoning(ls);
    }
    if (!ls.reasoningBody.classList?.contains('collapsed')) {
      ls.reasoningBody.textContent = ls.reasoning;
      ls.reasoningBody.scrollTop = ls.reasoningBody.scrollHeight;
    }
  }

  function collapseStreamReasoning(ls) {
    if (!ls || !ls.reasoningBody) return;
    const approx = Math.max(1, Math.round(String(ls.reasoning || '').length / 4));
    const el = ls.reasoningBody;
    el.classList.add('collapsed');
    el.textContent = `▸ Σκέψη μοντέλου (~${approx} tok) — κλικ για προβολή`;
    el.onclick = () => {
      const isCol = el.classList.toggle('collapsed');
      el.textContent = isCol ? `▸ Σκέψη μοντέλου (~${approx} tok) — κλικ για προβολή` : ls.reasoning;
    };
  }

  function formatUserText(text) {
    return escHtml(text).replace(/(^|\s)((?:@|\/)[A-Za-z0-9_.\-/\\]+)/g, '$1<span class="user-ref-chip">$2</span>');
  }

  let fileCache = { project: '', entries: [], loading: false };
  async function loadAtFiles() {
    const proj = String(root.currentProject || '');
    if (!proj || fileCache.loading || (fileCache.project === proj && fileCache.entries.length)) return;
    if (typeof root.api !== 'function') return;
    fileCache.loading = true;
    try {
      const r = await root.api('/api/files/tree', { path: '.', project: proj, limit: 100 });
      fileCache = { project: proj, entries: (r && Array.isArray(r.entries) ? r.entries : []), loading: false };
    } catch (_) { fileCache.loading = false; }
  }

  function updateAtMenu(textarea, menu) {
    const val = String(textarea.value || '');
    const pos = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : val.length;
    const before = val.slice(0, pos);
    const m = before.match(/(?:^|\s)@([^\s@]{0,60})$/);
    if (!m) return false;
    const q = m[1].toLowerCase();
    loadAtFiles();
    const list = fileCache.entries
      .filter((e) => String(e.path || e.name || '').toLowerCase().includes(q))
      .slice(0, 8);
    if (!list.length) return false;
    menu.replaceChildren();
    for (const item of list) {
      const target = item.path || item.name || '';
      const btn = document.createElement('button');
      btn.className = 'cmd-item';
      btn.textContent = (item.type === 'dir' ? '📁 ' : '📄 ') + '@' + target;
      btn.onclick = () => {
        const start = pos - m[1].length - 1;
        const after = val.slice(pos);
        textarea.value = val.slice(0, start) + '@' + target + ' ' + after;
        menu.style.display = 'none';
        textarea.focus?.();
        root.grow?.(textarea);
      };
      menu.appendChild(btn);
    }
    menu.style.display = 'flex';
    return true;
  }

  async function runCompact() {
    if (typeof root.api !== 'function') return null;
    const r = await root.api('/api/compact', { use_llm: true });
    if (r && r.error) { root.showNotice?.('err', r.error); return r; }
    if (r && typeof root.updateBadges === 'function') root.updateBadges(r);
    if (r && r.notice) root.showNotice?.('ok', r.notice);
    return r;
  }

  let queuedMsgs = [];
  let queueContext = '';
  let queuePaused = false;
  let queueDraining = false;

  function ensureQueueElements() {
    const ws = document.getElementById('chat-workspace');
    const bar = document.getElementById('inputbar');
    let dock = document.getElementById('queue-dock');
    if (!dock && ws && bar) {
      dock = document.createElement('div'); dock.id = 'queue-dock';
      if (typeof ws.insertBefore === 'function') ws.insertBefore(dock, bar);
      else ws.appendChild(dock);
    }
    let btn = document.getElementById('queue-btn');
    const sendBtn = document.getElementById('sendbtn');
    if (!btn && bar) {
      btn = document.createElement('button'); btn.id = 'queue-btn'; btn.type = 'button';
      btn.className = 'btn'; btn.textContent = '⏳ Ουρά';
      btn.title = 'Πρόσθεσε το μήνυμα στην ουρά για εκτέλεση μόλις τελειώσει η τρέχουσα εργασία';
      btn.onclick = () => enqueueFromPrompt();
      if (sendBtn && typeof bar.insertBefore === 'function') bar.insertBefore(btn, sendBtn);
      else bar.appendChild(btn);
    }
    return { dock, btn };
  }

  function renderQueueDock() {
    const { dock } = ensureQueueElements();
    if (!dock) return;
    dock.replaceChildren();
    dock.classList.toggle('show', queuedMsgs.length > 0);
    if (!queuedMsgs.length) return;
    const head = document.createElement('div'); head.className = 'queue-head';
    const ttl = document.createElement('span');
    ttl.textContent = `⏳ Σε αναμονή (${queuedMsgs.length})` + (queuePaused ? ' · σε παύση' : '');
    const acts = document.createElement('span'); acts.className = 'queue-head-actions';
    if (queuePaused) {
      const resBtn = document.createElement('button'); resBtn.className = 'btn'; resBtn.textContent = '▶ Συνέχεια';
      resBtn.onclick = () => { queuePaused = false; renderQueueDock(); drainNextQueued(); };
      acts.appendChild(resBtn);
    }
    const clr = document.createElement('button'); clr.className = 'btn'; clr.textContent = 'Καθαρισμός';
    clr.onclick = () => { queuedMsgs = []; queuePaused = false; renderQueueDock(); };
    acts.appendChild(clr);
    head.append(ttl, acts);
    dock.appendChild(head);
    queuedMsgs.forEach((msg, idx) => {
      const row = document.createElement('div'); row.className = 'queue-row';
      const txt = document.createElement('span'); txt.className = 'queue-text';
      txt.textContent = `${idx + 1}. ${msg}`;
      const editBtn = document.createElement('button'); editBtn.className = 'btn'; editBtn.textContent = '✏️';
      editBtn.title = 'Επεξεργασία στο πεδίο γραφής';
      editBtn.onclick = () => {
        const el = document.getElementById('prompt');
        queuedMsgs.splice(idx, 1);
        if (el) { el.value = msg; el.focus?.(); root.grow?.(el); }
        renderQueueDock();
      };
      const delBtn = document.createElement('button'); delBtn.className = 'btn'; delBtn.textContent = '✕';
      delBtn.title = 'Αφαίρεση από την ουρά';
      delBtn.onclick = () => { queuedMsgs.splice(idx, 1); renderQueueDock(); };
      row.append(txt, editBtn, delBtn);
      dock.appendChild(row);
    });
  }

  function enqueueFromPrompt(explicitText) {
    const el = document.getElementById('prompt');
    const text = String(explicitText !== undefined ? explicitText : (el?.value || '')).trim();
    if (!text) return false;
    if (queuedMsgs.length >= 10) { root.showNotice?.('err', 'Η ουρά έχει το όριο των 10 μηνυμάτων.'); return false; }
    queueContext = `${root.currentProject || ''}|${root.currentSession || ''}`;
    queuedMsgs.push(text);
    if (explicitText === undefined && el) { el.value = ''; root.grow?.(el); }
    renderQueueDock();
    root.showNotice?.('ok', `⏳ Προστέθηκε στην ουρά (#${queuedMsgs.length}).`);
    return true;
  }

  async function drainNextQueued() {
    if (queueDraining || queuePaused || !queuedMsgs.length) return false;
    const el = document.getElementById('prompt');
    if (!el || typeof root.send !== 'function') return false;
    queueDraining = true;
    try {
      const next = queuedMsgs.shift();
      renderQueueDock();
      el.value = next;
      root.grow?.(el);
      await root.send();
      return true;
    } finally {
      queueDraining = false;
    }
  }

  function syncMessageQueue(s) {
    const { btn } = ensureQueueElements();
    if (btn) btn.style.display = s.busy ? 'inline-flex' : 'none';
    const ctxKey = `${s.project || ''}|${s.session_id || ''}`;
    if (queueContext && ctxKey !== queueContext && queuedMsgs.length) {
      queuedMsgs = []; queuePaused = false; renderQueueDock();
    }
    if (s.error && queuedMsgs.length) { queuePaused = true; renderQueueDock(); return; }
    if (!s.busy && s.connected && queuedMsgs.length && !queuePaused && !queueDraining) {
      drainNextQueued();
    }
  }

  function _fileExtBadge(filePath) {
    const base = String(filePath || '').split(/[\\/]/).pop() || '';
    const dot = base.lastIndexOf('.');
    return dot >= 0 && dot < base.length - 1 ? base.slice(dot + 1).toUpperCase().slice(0, 4) : 'FILE';
  }

  function attachTurnFileCards(msgEl) {
    if (!msgEl || !pendingTurnFiles.length) return;
    const files = pendingTurnFiles.splice(0, pendingTurnFiles.length);
    const row = document.createElement('div');
    row.className = 'file-cards-row';
    for (const item of files) {
      const card = document.createElement('div');
      card.className = 'file-card';
      const badge = document.createElement('span');
      badge.className = 'attach-badge';
      badge.textContent = _fileExtBadge(item.path);
      const info = document.createElement('div');
      info.className = 'file-card-info';
      const nm = document.createElement('span');
      nm.className = 'file-card-name';
      nm.textContent = item.path;
      const sub = document.createElement('span');
      sub.className = 'file-card-sub';
      sub.textContent = item.action || 'αρχείο';
      info.append(nm, sub);
      const btns = document.createElement('div');
      btns.className = 'file-card-btns';
      const defs = [
        ['👁 Προεπισκόπηση', () => root.openProjectFile?.(item.path)],
        ['↗ Άνοιγμα', () => root.openFileInOs?.(item.path, false)],
        ['📁 Φάκελος', () => root.openFileInOs?.(item.path, true)],
      ];
      for (const [label, fn] of defs) {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        b.onclick = fn;
        btns.appendChild(b);
      }
      card.append(badge, info, btns);
      row.appendChild(card);
    }
    msgEl.appendChild(row);
  }

  root.ChatPro = {
    highlightCode,
    renderMarkdown,
    appendTrailStep,
    attachTurnFileCards,
    updateActiveTrail,
    sealActiveTrail,
    formatTimeNow,
    initJumpToBottom,
    renderTodos,
    renderSubtasks,
    syncTopbarControls,
    syncStatus,
    syncStreamReasoning,
    collapseStreamReasoning,
    formatUserText,
    updateAtMenu,
    runCompact,
    enqueueFromPrompt,
    syncMessageQueue,
    pauseQueue: () => { queuePaused = true; renderQueueDock(); },
    getQueue: () => [...queuedMsgs],
    _setFileCacheForTest: (proj, entries) => { fileCache = { project: proj, entries, loading: false }; },
    resetTrail: () => { stopSpinner(); activeTrail = null; pendingTurnFiles = []; },
  };

  if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
    document.addEventListener('DOMContentLoaded', initJumpToBottom, { once: true });
  }
})(globalThis);
