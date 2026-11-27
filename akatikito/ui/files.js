function copyCodeBlock(idx) {
  if (codeSnippets[idx] !== undefined) copyText(codeSnippets[idx]);
}
function fmt(text) {
  let h = esc(text);
  const blocks = [];
  h = h.replace(/```([a-zA-Z0-9_+-]*)\r?\n?([\s\S]*?)```/g, (_, lang, code) => {
    const clean = code.replace(/&lt;/g,"<").replace(/&gt;/g,">").replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,"&").trimEnd();
    const idx = codeSnippets.push(clean) - 1;
    const token = `\x00CODEBLOCK_${blocks.length}\x00`;
    blocks.push(`<div class="code-block"><div class="code-head"><span>${lang || "κώδικας"}</span><button onclick="copyCodeBlock(${idx})">📋 Αντιγραφή κώδικα</button></div><pre><code>${code.trimEnd()}</code></pre></div>`);
    return token;
  });
  // Πίνακες Markdown
  h = h.replace(/(?:^\|.+\|(?:\r?\n|$))+/gm, table => {
    const rows = table.trim().split(/\r?\n/).map(r => r.replace(/^\||\|$/g, "").split("|").map(c => c.trim()));
    if (rows.length >= 2 && rows[1].every(c => /^:?-+:?$/.test(c))) {
      const head = "<tr>" + rows[0].map(c => `<th>${c}</th>`).join("") + "</tr>";
      const body = rows.slice(2).map(r => "<tr>" + r.map(c => `<td>${c}</td>`).join("") + "</tr>").join("");
      return `<table class="md-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;
    }
    return table;
  });
  h = h.replace(/^#### (.*)$/gm, "<h4>$1</h4>");
  h = h.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  h = h.replace(/^## (.*)$/gm, "<h2>$1</h2>");
  h = h.replace(/^# (.*)$/gm, "<h1>$1</h1>");
  h = h.replace(/^&gt; (.*)$/gm, "<blockquote>$1</blockquote>");
  h = h.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  h = h.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  h = h.replace(/^[-•] (.*)$/gm, "• $1");
  for (let i = 0; i < blocks.length; i++) {
    h = h.replace(`\x00CODEBLOCK_${i}\x00`, () => blocks[i]);
  }
  return h;
}

function friendlyToolCall(name, rawArgs) {
  let a = {};
  try { a = typeof rawArgs === "string" ? JSON.parse(rawArgs || "{}") : (rawArgs || {}); } catch (_) {}
  const map = {
    list_files: () => `📂 Εξερεύνηση φακέλου <b>${esc(a.path || ".")}</b>`,
    read_file: () => `📖 Ανάγνωση αρχείου <b>${esc(a.path || "")}</b>${a.outline ? " (δομή)" : ""}`,
    search_project: () => `🔍 Αναζήτηση στο project για <b>«${esc(a.keyword || "")}»</b>`,
    search_in_file: () => `🔎 Αναζήτηση στο <b>${esc(a.path || "")}</b> για <b>«${esc(a.keyword || "")}»</b>`,
    edit_file: () => `✍️ Ενημέρωση κώδικα στο <b>${esc(a.path || (Array.isArray(a.files) ? a.files.length + " αρχεία" : ""))}</b>`,
    write_file: () => `📝 Δημιουργία αρχείου <b>${esc(a.path || "")}</b>`,
    run_command: () => `💻 Εκτέλεση εντολής: <code>${esc(String(a.command || "").slice(0, 120))}</code>`,
    read_document: () => `📄 Ανάγνωση εγγράφου <b>${esc(a.path || "")}</b>`,
    create_word_document: () => `📝 Δημιουργία εγγράφου Word <b>${esc(a.path || "")}</b>`,
    create_excel_sheet: () => `📊 Δημιουργία φύλλου Excel <b>${esc(a.path || "")}</b>`,
    create_pdf_document: () => `📄 Δημιουργία εγγράφου PDF <b>${esc(a.path || "")}</b>`,
    web_search: () => `🌐 Αναζήτηση στο Internet για <b>«${esc(a.query || "")}»</b>`,
    web_fetch: () => `🌐 Ανάγνωση ιστοσελίδας <b>${esc(String(a.url || "").slice(0, 80))}</b>`,
    browser_fetch: () => `🧭 Άνοιγμα σελίδας με Chromium (JavaScript) <b>${esc(String(a.url || "").slice(0, 80))}</b>${a.screenshot_path ? ` + 📸 ${esc(a.screenshot_path)}` : ""}`,
    web_news: () => `📰 Αναζήτηση Ειδήσεων / RSS για <b>«${esc(a.query || "Κύρια θέματα")}»</b>`,
    youtube_transcript: () => `🎬 Ανάγνωση υποτίτλων YouTube: <b>${esc(String(a.url || "").slice(0, 80))}</b>`,
    github_search: () => `🐙 Αναζήτηση στο GitHub για <b>«${esc(a.query || "")}»</b>`,
    github_read: () => `🐙 Ανάγνωση από GitHub: <b>${esc([a.owner, a.repo, a.path].filter(Boolean).join("/"))}</b>`,
    add_reminder: () => `⏰ Νέα υπενθύμιση: <b>${esc(a.title || "")}</b> (${esc(a.date || "")})`,
    list_reminders: () => `📅 Έλεγχος υπενθυμίσεων`,
    done_reminder: () => `✅ Ολοκλήρωση υπενθύμισης`,
    update_todos: () => `📋 Ενημέρωση λίστας εργασιών (${Array.isArray(a.todos) ? a.todos.length : 0} βήματα)`,
    run_subtask: () => `🤖 Ανάθεση υποεργασίας: <b>${esc(String(a.task || "").slice(0, 90))}</b>`,
    run_subtasks: () => `🤖 Παράλληλοι βοηθοί (${Array.isArray(a.tasks) ? a.tasks.length : 0} υποεργασίες)`,
    use_skill: () => `📘 Φόρτωση συνταγής <b>/${esc(a.name || "")}</b>`,
    record_diagnosis: () => `🩺 Καταγραφή διάγνωσης σφάλματος: <b>${esc(String(a.cause || "").slice(0, 100))}</b>`,
    complete_task: () => `🏁 Ολοκλήρωση εργασίας (${esc(a.outcome || "completed")})`,
    recall_context: () => `🗂️ Ανάκτηση λεπτομερειών από το ιστορικό`,
    propose_memory: () => `🧠 Πρόταση αποθήκευσης στη μνήμη (${esc(a.topic || "")})`,
    propose_skill: () => `✨ Πρόταση συνταγής <b>/${esc(a.name || "")}</b>`,
    patch_skill: () => `🔧 Χειρουργική βελτίωση συνταγής <b>/${esc(a.name || "")}</b>`,
    search_past_sessions: () => `🕰️ Αναζήτηση σε παλιές συνομιλίες για <b>«${esc(a.query || "")}»</b>`,
    system_diagnostics: () => `💻 Έλεγχος διαγνωστικών συστήματος & δίσκου (${esc(a.check || "overview")})`,
    calculate_expression: () => `🧮 Ακριβής υπολογισμός: <code>${esc(a.expression || "")}</code>${a.vat_rate ? ` (ΦΠΑ ${esc(a.vat_rate)}%)` : ""}`,
    convert_data_file: () => `🔄 Μετατροπή δεδομένων <b>${esc(a.source_path || "")}</b> σε ${esc(a.target_format || "markdown")}`,
    git_status_diff: () => `🌿 Έλεγχος αλλαγών Git (status & diff)${a.path ? ` στο <b>${esc(a.path)}</b>` : ""}`,
  };
  if (map[name]) return map[name]();
  if (String(name || "").startsWith("mcp_")) return `🔌 Εξωτερικό εργαλείο MCP: <b>${esc(name)}</b>`;
  return `🔧 <b>${esc(name)}</b>`;
}

let livePhase = "", liveStep = "";
function fmtTok(n) { const v = Number(n || 0); return v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(v); }
function formatBusyBadge(s) {
  const t = (s && s.tokens) || {}, tot = (t.prompt_tokens || 0) + (t.completion_tokens || 0);
  const hit = Math.min(t.prompt_tokens || 0, t.cache_hit_tokens || 0);
  const cPct = hit > 0 && t.prompt_tokens > 0 ? ' · ' + Math.round(hit * 100 / t.prompt_tokens) + '% cache' : '';
  const base = s && s.busy ? i18nText('badge_busy', '⚡ εργάζεται…') : i18nText('badge_idle', '💤 αδρανής');
  return base + (s && s.busy && liveStep ? ' · ' + liveStep : '') + (tot > 0 ? ' · ' + fmtTok(tot) + ' tok' + cPct : '');
}
function addStep(kind, data) {
  if (kind === "step") {
    liveStep = data.step && data.of ? `βήμα ${data.step}/${data.of}` : (data.step ? `βήμα ${data.step}` : "");
    livePhase = "🧠 σκέφτομαι…";
    return;
  }
  if (kind === "plan_step") { livePhase = "🧠 σκέφτομαι…"; return; }
  if (kind === "act_step") { livePhase = `🔧 εκτελώ ${data.count || ""} εργαλεία…`; return; }
  if (kind === "verify_step") { livePhase = "✅ επαληθεύω…"; return; }
  if (kind === "report_step") { livePhase = "✍️ συντάσσω απάντηση…"; return; }
  if (typeof ChatPro !== "undefined" && ChatPro.appendTrailStep && ChatPro.appendTrailStep(chat, kind, data)) return;
  const pin = typeof isChatNearBottom === "function" ? isChatNearBottom() : true;
  const wrap = document.createElement("div");
  wrap.className = "steps";
  chat.appendChild(wrap);
  const div = document.createElement("div");
  div.className = "step";
  let html = "";
  if (false) html = "";
  else if (kind === "reasoning") html = `<details><summary>🧠 σκέψη του μοντέλου</summary><div class="out">${esc(data.text)}</div></details>`;
  else if (kind === "thinking") html = `💭 ${esc(data.text)}`;
  else if (kind === "mode") html = `🎭 λειτουργία: <b>${esc(data.label || "")}</b>`;
  else if (kind === "memories") { if (typeof renderMemoriesStep !== "function") return; renderMemoriesStep(div, data); wrap.appendChild(div); return; }
  else if (kind === "model") html = `🤖 μοντέλο: <b>${esc(data.model || "")}</b>`;
  else if (kind === "tool_call") {
    const friendly = friendlyToolCall(data.name, data.arguments);
    const raw = String(data.arguments || "").slice(0, 600);
    html = `${friendly}${raw && raw !== "{}" ? `<details style="margin-top:4px"><summary class="hint" style="cursor:pointer">λεπτομέρειες</summary><div class="out">${esc(raw)}</div></details>` : ""}`;
  }
  else if (kind === "tool_result") {
    const head = document.createElement("div");
    head.textContent = "↳ αποτέλεσμα";
    if (data.path && ["edit_file","write_file","convert_data_file","create_word_document","create_excel_sheet","create_pdf_document"].includes(data.name)) {
      const targetPath = String(data.path);
      const btn = document.createElement("button");
      btn.className = "btn";
      btn.style.cssText = "width:auto;display:inline-block;padding:1px 8px;font-size:11px;margin-left:6px";
      btn.textContent = "📄 " + targetPath;
      btn.onclick = () => openProjectFile(targetPath);
      head.appendChild(btn);
      const osBtn = document.createElement("button");
      osBtn.className = "btn";
      osBtn.style.cssText = "width:auto;display:inline-block;padding:1px 7px;font-size:11px;margin-left:4px";
      osBtn.textContent = i18nText('btn_open_os', '📂 Άνοιγμα στα Windows');
      osBtn.onclick = () => openFileInOs(targetPath, false);
      head.appendChild(osBtn);
      const revBtn = document.createElement("button");
      revBtn.className = "btn";
      revBtn.style.cssText = "width:auto;display:inline-block;padding:1px 7px;font-size:11px;margin-left:4px";
      revBtn.textContent = i18nText('btn_reveal_os', '📁 Φάκελος');
      revBtn.onclick = () => openFileInOs(targetPath, true);
      head.appendChild(revBtn);
    }
    const outDiv = document.createElement("div");
    outDiv.className = "out";
    outDiv.textContent = String(data.result || "").slice(0, 1200);
    div.append(head, outDiv);
    if (data.diff) {
      const diffWrap = document.createElement("div");
      diffWrap.innerHTML = formatColoredDiff(data.diff);
      div.appendChild(diffWrap);
    }
    wrap.appendChild(div);
    if (typeof scrollChatIfPinned === "function") scrollChatIfPinned(pin); else chat.scrollTop = chat.scrollHeight;
    return;
  }
  else if (kind === "repair") html = `🛠 <b>${esc({investigate:"Εντοπισμός αιτίας",diagnosis:"Διάγνωση",passed:"Επαληθεύτηκε η διόρθωση"}[data.phase] || "Διόρθωση")}</b><div class="out">${esc(data.text || "")}</div>`;
  else if (kind === "verify") {
    html = data.ok ? `✅ ο έλεγχος πέρασε` : `❌ ο έλεγχος απέτυχε<div class="out">${esc(String(data.output || "").slice(-1000))}</div>`;
    div.classList.add(data.ok ? "ok" : "bad");
  }
  else if (kind === "stopped") html = `⏹ σταμάτησα όπως ζήτησες`;
  else if (kind === "limit") html = `⚠ έφτασα το όριο βημάτων`;
  else if (kind === "give_up") html = `⚠ σταμάτησα: ο έλεγχος απέτυχε ${data.attempts} φορές`;
  else if (kind === "checkpoint_error") { html=`⚠️ Δεν αποθηκεύτηκε checkpoint: ${esc(data.text)}`;div.classList.add("bad"); }
  else if (kind === "fatal") { html = `⚠️ ${esc(data.text)}`; div.classList.add("bad"); }
  else return;
  if(['fatal','give_up','limit','stopped','checkpoint_error'].includes(kind)||(kind==='verify'&&!data.ok))wrap.classList.add('important-step');
  if(['reasoning','thinking','tool_call','repair'].includes(kind))wrap.classList.add('live-step');
  div.innerHTML = html;
  wrap.appendChild(div);
  if (typeof scrollChatIfPinned === "function") scrollChatIfPinned(pin); else chat.scrollTop = chat.scrollHeight;
}

function setStatus(text) {
  const pin = typeof isChatNearBottom === "function" ? isChatNearBottom() : true;
  const existing = document.querySelectorAll(".status");
  if (!text) { existing.forEach(e => e.remove()); return; }
  const html = `<span class="pulse"></span>${text}`;
  const last = chat.children[chat.children.length - 1];
  if (existing.length === 1 && existing[0] === last) {
    if (existing[0].innerHTML !== html) existing[0].innerHTML = html;
    return;
  }
  existing.forEach(e => e.remove());
  const div = document.createElement("div");
  div.className = "status";
  div.innerHTML = html;
  chat.appendChild(div);
  if (typeof scrollChatIfPinned === "function") scrollChatIfPinned(pin); else chat.scrollTop = chat.scrollHeight;
}

// ---------- polling ----------
async function poll() {
  if (polling || sending) return;
  polling = true;
  try {
    const s = await api("/api/poll", {since: lastSeq});
    lastSeq = s.last_seq ?? lastSeq;
    if (s.busy && Array.isArray(s.transcript) && s.transcript.length > chat.querySelectorAll(".msg").length) {
      renderTranscript(s.transcript);
    }
    for (const ev of s.events || []) {
      if (ev.kind.startsWith("stream_")) { streamEvent(ev.kind, ev.data); continue; }
      if (ev.kind === "user") continue;      // το γράφουμε ήδη στο send()
      if (ev.kind === "answer") continue;    // γράφεται στο done
      if (ev.kind === "done") continue;
      addStep(ev.kind, ev.data);
    }
    if (s.stream && (s.busy || ["error", "stopped"].includes(s.stream.status))) syncStream(s.stream);
    const refreshSessions = busy && !s.busy || (s.events || []).some(ev => ev.kind === "done");
    busy = s.busy;
    updateBadges(s);
    if (refreshSessions) await loadSessions();
    if (busy) {
      const t = s.tokens || {}, tot = (t.prompt_tokens || 0) + (t.completion_tokens || 0);
      const hit = Math.min(t.prompt_tokens || 0, t.cache_hit_tokens || 0);
      const cacheBit = hit > 0 && t.prompt_tokens > 0 ? ` · ${Math.round(hit * 100 / t.prompt_tokens)}% cache` : "";
      const stepTag = liveStep ? ` (${liveStep})` : "";
      const tokTag = tot > 0 ? ` · ⚡ ${fmtTok(tot)} tok (${fmtTok(t.prompt_tokens)} in / ${fmtTok(t.completion_tokens)} out${cacheBit})` : "";
      if (typeof ChatPro !== "undefined" && ChatPro.updateActiveTrail) ChatPro.updateActiveTrail(liveStep, tot > 0 ? `⚡ ${fmtTok(tot)} tok${cacheBit}` : "");
      setStatus(`${esc((livePhase || "🧠 σκέφτομαι…") + stepTag)} ${esc(s.model || "")}${esc(tokTag)}`);
      document.getElementById("stopbtn").style.display = "";
      document.getElementById("stopside").style.display = "";
      const sb = document.getElementById("sendbtn");
      if (sb) { sb.disabled = false; sb.textContent = "🧭 Οδηγία"; sb.title = "Στείλε οδηγία εν κινήσει χωρίς να σταματήσεις την εργασία"; }
    } else {
      const t = s.tokens || {}, tot = (t.prompt_tokens || 0) + (t.completion_tokens || 0);
      if (typeof ChatPro !== "undefined" && ChatPro.sealActiveTrail) ChatPro.sealActiveTrail(tot > 0 ? `Σ ${fmtTok(tot)} tok` : "");
      livePhase = ""; liveStep = "";
      setStatus(null);
      document.getElementById("stopbtn").style.display = "none";
      document.getElementById("stopside").style.display = "none";
      const sb = document.getElementById("sendbtn");
      if (sb) { sb.disabled = false; sb.textContent = i18nText('btn_send', 'Αποστολή ➤'); sb.title = ""; }
      if (liveStream && liveStream.status === "complete") clearStream();
      const last = (s.transcript || []).filter(m => m.role === "assistant").pop();
      if (last) renderTranscript(s.transcript || []);  // idempotent: προσθέτει μόνο τα νέα
    }
    if (s.error) showNotice("err", s.error);
    if (s.notice && s.notice !== lastNotice) { showNotice("ok", s.notice); lastNotice = s.notice; }
  } catch (err) {
    // Μην εμφανίζεις επανειλημμένα το ίδιο σφάλμα δικτύου — μόνο αν άλλαξε.
    const msg = "Δεν επικοινωνεί η εφαρμογή: " + (err.message || err);
    if (msg !== lastNotice) { showNotice("err", msg); lastNotice = msg; }
  } finally { polling = false; }
}

function formatColoredDiff(diffText) {
  if (!diffText) return "";
  const lines = String(diffText).split(/\r?\n/).map(line => {
    const safe = esc(line);
    if (line.startsWith("+++") || line.startsWith("---")) return `<div style="color:var(--muted);font-weight:600">${safe}</div>`;
    if (line.startsWith("@@")) return `<div style="color:var(--accent);background:rgba(137,180,250,0.08);padding:1px 4px">${safe}</div>`;
    if (line.startsWith("+")) return `<div style="color:#a6e3a1;background:rgba(166,227,161,0.12);padding:0 4px">${safe}</div>`;
    if (line.startsWith("-")) return `<div style="color:#f38ba8;background:rgba(243,139,168,0.12);padding:0 4px">${safe}</div>`;
    return `<div style="padding:0 4px">${safe}</div>`;
  }).join("");
  return `<details open style="margin-top:6px"><summary class="hint" style="cursor:pointer;color:var(--accent)">🔍 Αλλαγές κώδικα (Diff)</summary><div class="out" style="font-family:monospace;font-size:11.5px;line-height:1.45;max-height:240px;overflow:auto">${lines}</div></details>`;
}

async function quickUndoCheckpoint(id) {
  if (!confirm("Να αναιρεθούν αμέσως οι αλλαγές αρχείων αυτής της απάντησης;")) return;
  const r = await api("/api/checkpoint/undo", {id});
  showNotice(r.error ? "err" : "ok", r.error || "Οι αλλαγές αναιρέθηκαν επιτυχώς.");
  await poll();
}

function renderTranscript(list) {
  // Η θέση κρατά και επαναλαμβανόμενα ίδια μηνύματα στην ίδια συνομιλία.
  const shown = chat.querySelectorAll(".msg").length;
  for (const m of list.slice(shown)) {
    const div=addMsg(m.role,m.text,m.model||'',m.provider||'',m.usage||null);
    if(m.checkpoint) {
      const button=document.createElement('button');button.className='btn';button.style.cssText='width:auto;padding:2px 8px;font-size:11px;margin:6px 0 0 6px';button.textContent='Αλλαγές / Αναίρεση';button.onclick=()=>viewCheckpoint(m.checkpoint);div.appendChild(button);
      const quickUndo=document.createElement('button');quickUndo.className='btn';quickUndo.style.cssText='width:auto;padding:2px 8px;font-size:11px;margin:6px 0 0 6px;border-color:var(--red)';quickUndo.textContent='↩ Άμεση Αναίρεση';quickUndo.onclick=()=>quickUndoCheckpoint(m.checkpoint);div.appendChild(quickUndo);
    }
  }
}

function colorDiff(pre, text) {
  const raw = String(text || '');
  pre.replaceChildren();
  for (const line of raw.split(/\r?\n/)) {
    const span = document.createElement('span');
    span.textContent = line + '\n';
    if (line.startsWith('+') && !line.startsWith('+++')) span.style.color = '#8ede9b';
    else if (line.startsWith('-') && !line.startsWith('---')) span.style.color = '#ff9a9a';
    else if (line.startsWith('@@')) span.style.color = '#93c5fd';
    pre.appendChild(span);
  }
  if (!pre.textContent && raw) pre.textContent = raw;
}

async function viewCheckpoint(id) {
  const result=await api('/api/checkpoint/diff',{id});
  if(result.error){showNotice('err',result.error);return;}
  const dialog=document.createElement('dialog');dialog.style.cssText='position:fixed;inset:0;margin:auto;width:80vw;max-width:1100px;max-height:85vh;padding:18px;border:1px solid #41475b;border-radius:10px;background:#1d202b;color:white';
  const pre=document.createElement('pre');pre.style.cssText='max-height:65vh;overflow:auto;white-space:pre-wrap';colorDiff(pre,(result.changes||[]).map(x=>x.path+'\n'+x.diff).join('\n\n')||'Καμία αλλαγή αρχείων.');
  const close=document.createElement('button');close.className='btn';close.style.cssText='width:auto;margin:10px 10px 0 0';close.textContent='Κλείσιμο';close.onclick=()=>dialog.remove();
  const undo=document.createElement('button');undo.className='btn';undo.style.cssText='width:auto;margin-top:10px';undo.textContent='Αναίρεση αυτών των αλλαγών';undo.onclick=async()=>{if(!confirm('Να επανέλθουν τα αρχεία πριν από αυτό το μήνυμα;'))return;const r=await api('/api/checkpoint/undo',{id});showNotice(r.error?'err':'ok',r.error||'Οι αλλαγές αναιρέθηκαν.');dialog.remove();};
  dialog.append(pre,close,undo);document.body.append(dialog);dialog.showModal();
}

let activeSpeechBtn = null;
function speakAnswer(text, btn) {
  const synth = window.speechSynthesis;
  if (!synth || typeof SpeechSynthesisUtterance === 'undefined') {
    showNotice('err', 'Η φωνητική ανάγνωση δεν είναι διαθέσιμη σε αυτό το σύστημα.');
    return;
  }
  if (synth.speaking && activeSpeechBtn === btn) {
    synth.cancel();
    btn.textContent = '🔊 Ακρόαση';
    activeSpeechBtn = null;
    return;
  }
  synth.cancel();
  if (activeSpeechBtn) activeSpeechBtn.textContent = '🔊 Ακρόαση';
  const clean = String(text || '').replace(/```[\s\S]*?```/g, ' [μπλοκ κώδικα] ').replace(/[*#`_~>]/g, ' ').trim();
  if (!clean) return;
  const u = new SpeechSynthesisUtterance(clean);
  const hasGreek = /[\u0370-\u03ff\u1f00-\u1fff]/.test(clean);
  u.lang = hasGreek ? 'el-GR' : 'en-US';
  activeSpeechBtn = btn;
  btn.textContent = '⏹ Διακοπή';
  u.onend = () => { if (activeSpeechBtn === btn) { btn.textContent = '🔊 Ακρόαση'; activeSpeechBtn = null; } };
  u.onerror = () => { if (activeSpeechBtn === btn) { btn.textContent = '🔊 Ακρόαση'; activeSpeechBtn = null; } };
  synth.speak(u);
}

async function applyPersona(role) {
  if (!role) return;
  const presets = {
    researcher: {mode: 'assistant', prompt: 'Ψάξε στο Internet με το web_search και φτιάξε μια αναλυτική αναφορά με πηγές για: '},
    office: {mode: 'assistant', prompt: 'Φτιάξε ένα καλοσχεδιασμένο έγγραφο Word (.docx) ή φύλλο Excel (.xlsx) με τα εξής στοιχεία: '},
    translator: {mode: 'assistant', prompt: 'Μετάφρασε ή βελτίωσε με επαγγελματικό ύφος το παρακάτω κείμενο:\n\n'},
    coder: {mode: 'code', prompt: 'Έλεγξε τον κώδικα του project, εντόπισε τυχόν προβλήματα και υλοποίησε: '},
  };
  const p = presets[role];
  if (!p) return;
  if (connected && !busy) {
    await setMode(p.mode);
    document.getElementById('mode').value = p.mode;
  }
  useQuickPrompt(p.prompt);
  document.getElementById('persona-pick').value = '';
}

function toggleAgendaForm() {
  const f = document.getElementById('agenda-form');
  if (!f) return;
  const open = f.style.display === 'none';
  f.style.display = open ? 'flex' : 'none';
  if (open) {
    const d = document.getElementById('agenda-date');
    if (d && !d.value) d.value = new Date().toISOString().slice(0, 10);
  }
}
let agendaSignature = '';
function renderAgendaList(tasks) {
  const box = document.getElementById('agenda-list');
  if (!box) return;
  const rows = Array.isArray(tasks) ? tasks : [];
  const sig = JSON.stringify(rows);
  if (sig === agendaSignature) return;
  agendaSignature = sig;
  box.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement('span'); empty.className = 'hint';
    empty.textContent = i18nText('empty_agenda', 'Καμία υπενθύμιση στην ατζέντα.');
    box.appendChild(empty); return;
  }
  for (const t of rows.slice(0, 30)) {
    const item = document.createElement('div');
    item.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;background:var(--panel);padding:5px 8px;border-radius:6px;border:1px solid var(--border)';
    const chk = document.createElement('input'); chk.type = 'checkbox'; chk.checked = !!t.done; chk.style.width = 'auto';
    chk.onchange = async () => { const r = await api('/api/agenda/toggle', {id: t.id, done: chk.checked}); if (r.tasks) renderAgendaList(r.tasks); };
    const lbl = document.createElement('span'); lbl.style.flex = '1'; lbl.style.overflowWrap = 'anywhere';
    if (t.done) lbl.style.textDecoration = 'line-through';
    const rep = (t.repeat && t.repeat !== 'none') ? ' 🔁' : '';
    const autoTag = t.auto_run ? ' 🤖' : '';
    lbl.textContent = `${t.date}${t.time ? ' ' + t.time : ''}${rep}${autoTag} · ${t.title}`;
    const del = document.createElement('button'); del.className = 'btn danger'; del.style.cssText = 'width:24px;padding:2px'; del.textContent = '×';
    del.onclick = async () => { const r = await api('/api/agenda/delete', {id: t.id}); if (r.tasks) renderAgendaList(r.tasks); };
    item.append(chk, lbl, del);
    box.appendChild(item);
  }
}
async function loadAgenda() {
  try {
    const r = await api('/api/agenda/list', {});
    if (r && r.tasks) renderAgendaList(r.tasks);
  } catch (_) {}
}
async function addAgendaTask() {
  const ctx = currentBoundContext();
  const title = (document.getElementById('agenda-title')?.value || '').trim();
  const date = (document.getElementById('agenda-date')?.value || '').trim();
  const time = (document.getElementById('agenda-time')?.value || '').trim();
  const repeat = document.getElementById('agenda-repeat')?.value || 'none';
  const auto_mode = document.getElementById('agenda-mode')?.value || 'assistant';
  const auto_run = !!document.getElementById('agenda-autorun')?.checked;
  if (!title || !date) { showNotice('err', 'Συμπλήρωσε τίτλο και ημερομηνία υπενθύμισης.'); return; }
  const r = await api('/api/agenda/add', {...ctx, title, date, time, repeat, auto_run, auto_mode});
  if (!sameBoundContext(ctx)) { showNotice('err', 'Η συνομιλία ή το project άλλαξε.'); return; }
  if (r.error) { showNotice('err', r.error); return; }
  document.getElementById('agenda-title').value = '';
  if (document.getElementById('agenda-autorun')) document.getElementById('agenda-autorun').checked = false;
  toggleAgendaForm();
  if (r.tasks) renderAgendaList(r.tasks);
  showNotice('ok', auto_run ? 'Η αυτόματη εργασία προγραμματίστηκε στην Ατζέντα.' : 'Η υπενθύμιση αποθηκεύτηκε στην Ατζέντα.');
}
async function loadScheduledJobs() {
  const box = document.getElementById('scheduled-list');
  if (!box) return;
  try {
    const r = await api('/api/scheduled/list', {});
    box.replaceChildren();
    if (r.error) { const er = document.createElement('span'); er.className = 'hint'; er.textContent = r.error; box.appendChild(er); return; }
    const jobs = Array.isArray(r.jobs) ? r.jobs.slice().reverse() : [];
    if (!jobs.length) { const empty = document.createElement('span'); empty.className = 'hint'; empty.textContent = i18nText('empty_scheduled', 'Καμία αυτόματη εκτέλεση ακόμα.'); box.appendChild(empty); return; }
    const statusLabels = {queued: 'Σε αναμονή', running: 'Εκτελείται', completed: 'Ολοκληρώθηκε', failed: 'Απέτυχε', uncertain: 'Χρειάζεται έλεγχο', cancelled: 'Ακυρώθηκε'};
    for (const row of jobs) {
      const card = document.createElement('div');
      card.style.cssText = 'border:1px solid var(--border);border-radius:6px;padding:5px 7px;background:var(--panel);display:flex;flex-direction:column;gap:3px;font-size:11px';
      const head = document.createElement('strong');
      head.textContent = row.title + ' · ' + (statusLabels[row.status] || row.status);
      card.appendChild(head);
      if (row.error) { const er = document.createElement('div'); er.style.color = 'var(--red)'; er.textContent = row.error; card.appendChild(er); }
      const actions = document.createElement('div'); actions.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap';
      if (row.session) {
        const histBtn = document.createElement('button'); histBtn.className = 'btn'; histBtn.style.cssText = 'width:auto;padding:2px 6px;font-size:11px';
        histBtn.textContent = '📜 Ιστορικό';
        histBtn.onclick = async () => {
          const h = await api('/api/scheduled/history', {id: row.id});
          if (h.error) { showNotice('err', h.error); return; }
          const pre = document.createElement('pre'); pre.className = 'out'; pre.style.fontSize = '11px';
          pre.textContent = (h.transcript || []).map(x => (x.role || '') + ': ' + (x.text || '')).join('\n\n') || '(Κενό ιστορικό)';
          card.appendChild(pre);
        };
        actions.appendChild(histBtn);
      }
      if (row.status === 'queued') {
        const cancelBtn = document.createElement('button'); cancelBtn.className = 'btn danger'; cancelBtn.style.cssText = 'width:auto;padding:2px 6px;font-size:11px';
        cancelBtn.textContent = 'Ακύρωση';
        cancelBtn.onclick = async () => { const res = await api('/api/scheduled/action', {id: row.id, action: 'cancel'}); if (res.error) showNotice('err', res.error); else loadScheduledJobs(); };
        actions.appendChild(cancelBtn);
      }
      if (row.status === 'failed' || row.status === 'uncertain') {
        const retryBtn = document.createElement('button'); retryBtn.className = 'btn'; retryBtn.style.cssText = 'width:auto;padding:2px 6px;font-size:11px';
        retryBtn.textContent = '↻ Νέα εκτέλεση';
        retryBtn.onclick = async () => {
          if (!confirm('Η προηγούμενη εργασία μπορεί να έχει ήδη κάνει αλλαγές. Έλεγξες το ιστορικό και θέλεις νέα εκτέλεση;')) return;
          const res = await api('/api/scheduled/action', {id: row.id, action: 'retry'});
          if (res.error) showNotice('err', res.error); else loadScheduledJobs();
        };
        actions.appendChild(retryBtn);
      }
      if (actions.childNodes.length) card.appendChild(actions);
      box.appendChild(card);
    }
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function exportChatMarkdown() {
  try {
    const ctx = currentBoundContext();
    const r = await api('/api/chat/export', ctx);
    if (!sameBoundContext(ctx)) { showNotice('err', 'Η συνομιλία ή το project άλλαξε κατά την εξαγωγή.'); return; }
    if (r.error) { showNotice('err', r.error); return; }
    if (r.cancelled) return;
    showNotice('ok', 'Η συνομιλία αποθηκεύτηκε στο: ' + (r.path || r.full_path || 'exports'));
  } catch (e) {
    showNotice('err', 'Αποτυχία εξαγωγής: ' + (e.message || e));
  }
}
let pendingGithubUpdate = null;
async function checkGithubUpdates() {
  const statusEl = document.getElementById('gh-update-status');
  const installBtn = document.getElementById('gh-update-install');
  if (statusEl) statusEl.textContent = 'Έλεγχος GitHub Releases…';
  if (installBtn) installBtn.style.display = 'none';
  pendingGithubUpdate = null;
  try {
    const r = await api('/api/update/check', {});
    if (r.error) {
      if (statusEl) statusEl.textContent = r.error;
      showNotice('err', r.error);
      return;
    }
    if (r.update_available) {
      pendingGithubUpdate = r;
      if (statusEl) statusEl.textContent = 'Νέα έκδοση διαθέσιμη: v' + r.latest_version + ' (τρέχουσα v' + r.current_version + ')';
      if (installBtn && r.can_auto_install) installBtn.style.display = '';
      showNotice('ok', 'Βρέθηκε νέα έκδοση v' + r.latest_version + ' στο GitHub!');
    } else {
      if (statusEl) statusEl.textContent = '✅ Έχεις την πιο πρόσφατη έκδοση (v' + r.current_version + ').';
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message || String(e);
    showNotice('err', e.message || String(e));
  }
}
async function installGithubUpdate() {
  if (!pendingGithubUpdate) return;
  if (!confirm('Να εγκατασταθεί η έκδοση v' + pendingGithubUpdate.latest_version + ' από το GitHub (με έλεγχο SHA-256 & αυτόματο backup); Τα κλειδιά και οι ρυθμίσεις σου δεν θα επηρεαστούν.')) return;
  const statusEl = document.getElementById('gh-update-status');
  const installBtn = document.getElementById('gh-update-install');
  if (installBtn) installBtn.disabled = true;
  if (statusEl) statusEl.textContent = 'Λήψη, έλεγχος SHA-256 & εφαρμογή ενημέρωσης…';
  try {
    const r = await api('/api/update/apply', {
      zip_url: pendingGithubUpdate.zip_url,
      sha_url: pendingGithubUpdate.sha_url,
      zip_name: pendingGithubUpdate.zip_name,
    });
    if (r.error) {
      if (statusEl) statusEl.textContent = r.error;
      showNotice('err', r.error);
      if (installBtn) installBtn.disabled = false;
      return;
    }
    if (statusEl) statusEl.textContent = '✅ Εγκαταστάθηκε η v' + (r.version || pendingGithubUpdate.latest_version) + ' (' + r.updated_files + ' αρχεία). Κλείσε και ξαναάνοιξε το Akatikito.';
    if (installBtn) installBtn.style.display = 'none';
    showNotice('ok', 'Η ενημέρωση ολοκληρώθηκε! Κλείσε και ξαναάνοιξε το Akatikito.');
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message || String(e);
    showNotice('err', e.message || String(e));
    if (installBtn) installBtn.disabled = false;
  }
}
async function loadSystemHealth() {
  loadProjectProfile();
  const box = document.getElementById('health-box');
  if (!box) return;
  try {
    const r = await api('/api/diagnostics/health', {});
    box.replaceChildren();
    if (r.error) { const er = document.createElement('span'); er.className = 'hint'; er.textContent = r.error; box.appendChild(er); return; }
    const head = document.createElement('strong');
    head.textContent = 'Akatikito v' + (r.app_version || '2.3.0') + ' · ' + (r.system?.os || '') + ' · Python ' + (r.system?.python || '');
    box.appendChild(head);
    for (const item of Object.values(r.checks || {})) {
      const row = document.createElement('div');
      row.textContent = (item.ok ? '✅ ' : '⚠️ ') + item.label;
      box.appendChild(row);
    }
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function runDoctor(checkApi) {
  const box = document.getElementById('doctor-box');
  if (!box) return;
  box.style.display = 'flex';
  box.replaceChildren();
  const loading = document.createElement('span');
  loading.className = 'hint';
  loading.textContent = checkApi ? '🩺 Έλεγχος 23 σημείων + δοκιμή API key…' : '🩺 Εκτέλεση διάγνωσης (μόνο ανάγνωση)…';
  box.appendChild(loading);
  try {
    const r = await api('/api/doctor/diagnose', {check_api: !!checkApi});
    box.replaceChildren();
    if (r.error) { const er = document.createElement('span'); er.className = 'hint'; er.textContent = r.error; box.appendChild(er); return; }
    const head = document.createElement('strong');
    const c = r.counts || {};
    head.textContent = '🩺 ' + (r.badge || '') + ' (' + (c.ok || 0) + ' OK, ' + (c.warn || 0) + ' Προσοχή, ' + (c.error || 0) + ' Σφάλματα)';
    box.appendChild(head);
    for (const item of (r.checks || [])) {
      const row = document.createElement('div');
      row.style.cssText = 'border-top:1px solid var(--border);padding-top:4px;display:flex;flex-direction:column;gap:3px';
      const title = document.createElement('div');
      title.textContent = (item.badge || '') + ' ' + (item.title || '');
      title.style.fontWeight = item.status === 'ok' ? '400' : '600';
      row.appendChild(title);
      if (item.status !== 'ok' || item.id === 'api_key_live') {
        const det = document.createElement('div');
        det.className = 'hint';
        det.textContent = item.details || '';
        row.appendChild(det);
      }
      if (item.fixable) {
        const bar = document.createElement('div');
        bar.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap';
        const acts = (item.actions && item.actions.length) ? item.actions : [{action: '', label: item.fix_label || 'Επισκευή'}];
        for (const a of acts) {
          const btn = document.createElement('button');
          btn.className = 'btn';
          btn.style.cssText = 'font-size:10.5px;padding:2px 7px';
          btn.textContent = '💉 ' + a.label;
          btn.onclick = () => applyDoctorFix(item.id, a.action, item.fix_preview || item.details || '');
          bar.appendChild(btn);
        }
        row.appendChild(bar);
      }
      box.appendChild(row);
    }
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function applyDoctorFix(checkId, action, preview) {
  const msg = (preview ? preview + '\n\n' : '') + 'Να προχωρήσει η επισκευή (θα κρατηθεί backup & εγγραφή στο audit log);';
  if (!window.confirm(msg)) return;
  try {
    const r = await api('/api/doctor/fix', {check_id: checkId, action: action || ''});
    if (!r.ok) { showNotice('err', r.error || 'Απέτυχε η επισκευή.'); return; }
    showNotice('ok', '🩺 ' + (r.summary || 'Ολοκληρώθηκε η επισκευή.'));
    await runDoctor(false);
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function snapshotDoctorConfigs() {
  try {
    const r = await api('/api/doctor/snapshot', {});
    if (!r.ok) { showNotice('err', r.error || 'Απέτυχε το αντίγραφο ρυθμίσεων.'); return; }
    showNotice('ok', '💾 ' + (r.summary || 'Αποθηκεύτηκε αντίγραφο έγκυρων ρυθμίσεων.'));
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function loadProjectProfile() {
  const sel = document.getElementById('project-profile');
  if (!sel) return;
  try {
    const r = await api('/api/project-profile', {});
    if (r && r.profile) sel.value = r.profile;
  } catch (_) {}
}
async function saveProjectProfile(profile) {
  try {
    const r = await api('/api/project-profile', {profile});
    if (r.error) { showNotice('err', r.error); return; }
    showNotice('ok', 'Επίπεδο εμπιστοσύνης project: ' + (r.profile || profile));
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function loadAuditLog() {
  const box = document.getElementById('audit-box');
  if (!box) return;
  box.style.display = 'flex';
  box.replaceChildren();
  try {
    const r = await api('/api/audit', {limit: 40});
    if (r.error) { const er = document.createElement('span'); er.className = 'hint'; er.textContent = r.error; box.appendChild(er); return; }
    const ver = r.verification || {};
    const head = document.createElement('strong');
    head.textContent = ver.valid ? ('✅ SHA-256 Chain OK (' + (ver.count || 0) + ' εγγραφές)') : ('⚠️ ' + (ver.error || 'Σφάλμα ακεραιότητας'));
    box.appendChild(head);
    const list = (r.entries || []).slice(-15).reverse();
    if (!list.length) {
      const empty = document.createElement('span'); empty.className = 'hint'; empty.textContent = 'Δεν υπάρχουν ακόμη καταγραφές audit.'; box.appendChild(empty); return;
    }
    for (const e of list) {
      const row = document.createElement('div');
      row.textContent = ((e.status === 'ok') ? '• ' : '✖ ') + (e.tool || e.action || '') + ' [' + (e.mode || '') + '] ' + (e.summary || '').slice(0, 90);
      box.appendChild(row);
    }
  } catch (e) { showNotice('err', e.message || String(e)); }
}
function toggleRoutineForm() {
  const f = document.getElementById('routine-form');
  if (f) f.style.display = f.style.display === 'none' ? 'flex' : 'none';
}
async function loadRoutines() {
  const box = document.getElementById('routines-list');
  if (!box) return;
  try {
    const r = await api('/api/routines/list', {});
    cachedRoutines = r.routines || [];
    box.replaceChildren();
    for (const item of (r.routines || [])) {
      const row = document.createElement('div');
      row.style.display = 'flex';
      row.style.gap = '4px';
      row.style.alignItems = 'center';
      const btn = document.createElement('button');
      btn.className = 'btn';
      btn.style.flex = '1';
      btn.style.textAlign = 'left';
      btn.style.fontSize = '11.5px';
      btn.style.padding = '5px 7px';
      btn.textContent = '▶ ' + (item.builtin ? i18nText('rtn_' + item.id, item.name) : item.name);
      btn.title = item.prompt;
      btn.onclick = () => { useQuickPrompt(item.prompt); };
      row.appendChild(btn);
      if (!item.builtin) {
        const del = document.createElement('button');
        del.className = 'btn';
        del.style.width = 'auto';
        del.style.padding = '4px 6px';
        del.style.fontSize = '11px';
        del.textContent = '✕';
        del.onclick = () => deleteUserRoutine(item.id);
        row.appendChild(del);
      }
      box.appendChild(row);
    }
  } catch (_) {}
}
async function addUserRoutine() {
  const nameEl = document.getElementById('routine-name');
  const promptEl = document.getElementById('routine-prompt');
  if (!nameEl || !promptEl) return;
  const r = await api('/api/routines/add', {name: nameEl.value, prompt: promptEl.value});
  if (r.error) { showNotice('err', r.error); return; }
  nameEl.value = ''; promptEl.value = '';
  const f = document.getElementById('routine-form'); if (f) f.style.display = 'none';
  showNotice('ok', 'Η ρουτίνα αποθηκεύτηκε!');
  await loadRoutines();
}
async function deleteUserRoutine(id) {
  const r = await api('/api/routines/delete', {id});
  if (r.error) { showNotice('err', r.error); return; }
  await loadRoutines();
}
async function exportDiagnosticsBundle() {
  try {
    const r = await api('/api/diagnostics/export', {});
    if (r.error) { showNotice('err', r.error); return; }
    if (r.cancelled) return;
    showNotice('ok', 'Το πακέτο διαγνωστικών αποθηκεύτηκε: ' + (r.path || r.full_path || 'exports'));
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function toggleSimpleProMode() {
  const chk = document.getElementById('advanced-view');
  if (!chk) return;
  chk.checked = !chk.checked;
  updateViewModeBadge(chk.checked);
  await savePreferences();
}
let currentUiLang = 'el', i18nStrings = {};

async function toggleFileDiff() {
  const box = document.getElementById('file-diff-box');
  const txt = document.getElementById('file-text');
  const btn = document.getElementById('file-diff-toggle');
  if (!box || !currentFile || !currentFile.path) return;
  if (box.style.display !== 'none') {
    box.style.display = 'none';
    if (txt) txt.style.display = '';
    if (btn) btn.textContent = '🔍 Diff';
    return;
  }
  box.style.display = 'flex';
  if (txt) txt.style.display = 'none';
  if (btn) btn.textContent = '📄 Κείμενο';
  box.replaceChildren();
  const loading = document.createElement('span');
  loading.className = 'hint';
  loading.textContent = 'Φόρτωση αλλαγών…';
  box.appendChild(loading);
  try {
    const r = await api('/api/files/diff', {path: currentFile.path});
    box.replaceChildren();
    if (r.error) { const er = document.createElement('span'); er.className = 'hint'; er.textContent = r.error; box.appendChild(er); return; }
    const head = document.createElement('div');
    head.className = 'diff-head';
    const stat = document.createElement('span');
    stat.textContent = (r.is_new ? 'Νέο αρχείο · ' : '') + '+' + (r.added || 0) + ' / -' + (r.removed || 0);
    head.appendChild(stat);
    if (r.backup_id) {
      const rb = document.createElement('button');
      rb.className = 'btn';
      rb.style.cssText = 'width:auto;padding:2px 7px;font-size:11px';
      rb.textContent = '↩️ Επαναφορά πριν την αλλαγή';
      rb.onclick = () => restoreDiffBackup(r.backup_id);
      head.appendChild(rb);
    }
    box.appendChild(head);
    if (!r.has_changes) {
      const noCh = document.createElement('span');
      noCh.className = 'hint';
      noCh.textContent = 'Δεν υπάρχουν διαφορές από το τελευταίο αντίγραφο.';
      box.appendChild(noCh);
      return;
    }
    for (const row of (r.lines || [])) {
      const el = document.createElement('div');
      el.className = 'diff-line' + (row.kind ? ' diff-' + row.kind : '');
      el.textContent = row.text || '';
      box.appendChild(el);
    }
    if (r.truncated) {
      const tr = document.createElement('span');
      tr.className = 'hint';
      tr.textContent = '… (περικοπή στις 600 γραμμές)';
      box.appendChild(tr);
    }
  } catch (e) { showNotice('err', e.message || String(e)); }
}
async function restoreDiffBackup(backupId) {
  if (!backupId || !window.confirm('Να γίνει επαναφορά του αρχείου στην προηγούμενη έκδοση;')) return;
  try {
    const r = await api('/api/restore', {backup: backupId});
    if (r.error) { showNotice('err', r.error); return; }
    showNotice('ok', '↩️ Επαναφέρθηκε το αρχείο: ' + (r.restored || ''));
    const box = document.getElementById('file-diff-box');
    if (box) box.style.display = 'none';
    const txt = document.getElementById('file-text');
    if (txt) txt.style.display = '';
    const btn = document.getElementById('file-diff-toggle');
    if (btn) btn.textContent = '🔍 Diff';
    await refreshFile();
  } catch (e) { showNotice('err', e.message || String(e)); }
}
