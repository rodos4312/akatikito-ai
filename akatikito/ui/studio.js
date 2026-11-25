// Akatikito Studio UI: crisp SVG navigation, collapsible sidebar, Settings Master-Detail, Mobile tab.
(function (root) {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const NAV_PATHS = {
    sess: 'M4 5h16v11H9l-5 4z',
    agenda: 'M5 5h14v15H5zM8 2v5m8-5v5M5 10h14M8 14h3m3 0h2m-8 4h3',
    providers: 'M12 3v3m-5 1h10a3 3 0 0 1 3 3v8H4v-8a3 3 0 0 1 3-3zM8 13h1m6 0h1M9 17h6',
    plugins: 'M8 3v5m8-5v5M6 8h12v4a6 6 0 0 1-12 0zM12 18v3',
    skills: 'M4 4h6a3 3 0 0 1 3 3v13a4 4 0 0 0-4-3H4zM20 4h-4a3 3 0 0 0-3 3m0 13a4 4 0 0 1 4-3h3z',
    memory: 'M9 4a4 4 0 0 0-6 4 4 4 0 0 0 0 7 4 4 0 0 0 6 5m6-16a4 4 0 0 1 6 4 4 4 0 0 1 0 7 4 4 0 0 1-6 5M12 3v18M6 10h3m6 4h3',
    mobile: 'M7 3h10v18H7zM10 6h4m-3 12h2',
    set: 'M12 3l2 3 4-1 1 4 3 3-3 3-1 4-4-1-2 3-2-3-4 1-1-4-3-3 3-3 1-4 4 1zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
  };
  const NAV_ORDER = ['sess', 'agenda', 'providers', 'plugins', 'skills', 'memory', 'mobile', 'set'];
  const SETTINGS_PANES = ['general', 'routing', 'security', 'voice', 'budget'];

  let sidebarCollapsed = false;
  let activeSettingsPane = 'general';
  let searchTimer = null;

  function byId(id) {
    return document.getElementById(id);
  }

  function stripLeadingEmoji(text) {
    return String(text || '')
      .replace(/^[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\uFE0F\u200D\s]+/u, '')
      .trim() || String(text || '').trim();
  }

  function makeSvg(pathData) {
    if (typeof document.createElementNS !== 'function') return null;
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.75');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', pathData);
    svg.appendChild(path);
    return svg;
  }

  function initNavIcons() {
    const firstTab = byId('tab-sess');
    const nav = firstTab && firstTab.parentElement;
    if (nav && typeof nav.setAttribute === 'function') {
      nav.setAttribute('role', 'tablist');
      nav.setAttribute('aria-orientation', 'vertical');
    }
    for (const key of NAV_ORDER) {
      const btn = byId('tab-' + key);
      if (!btn || byId('studio-label-' + key)) continue;
      const i18nKey = typeof btn.getAttribute === 'function' ? btn.getAttribute('data-i18n') : '';
      const rawText = stripLeadingEmoji(btn.textContent || key);
      if (i18nKey && typeof btn.removeAttribute === 'function') btn.removeAttribute('data-i18n');
      btn.replaceChildren();
      if (typeof btn.setAttribute === 'function') {
        btn.setAttribute('role', 'tab');
        btn.setAttribute('aria-controls', 'view-' + key);
        btn.setAttribute('aria-label', rawText);
        btn.setAttribute('title', rawText);
      }
      const iconWrap = document.createElement('span');
      iconWrap.className = 'studio-nav-icon';
      if (typeof iconWrap.setAttribute === 'function') iconWrap.setAttribute('aria-hidden', 'true');
      const svg = makeSvg(NAV_PATHS[key] || NAV_PATHS.sess);
      if (svg) iconWrap.appendChild(svg);
      const lbl = document.createElement('span');
      lbl.className = 'studio-nav-label';
      lbl.id = 'studio-label-' + key;
      if (i18nKey && typeof lbl.setAttribute === 'function') lbl.setAttribute('data-i18n', i18nKey);
      lbl.textContent = rawText;
      btn.append(iconWrap, lbl);
    }
    if (nav && typeof nav.addEventListener === 'function' && !nav._studioKeyBound) {
      nav._studioKeyBound = true;
      nav.addEventListener('keydown', (e) => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
        const items = NAV_ORDER.map((k) => byId('tab-' + k)).filter(Boolean);
        const idx = items.indexOf(document.activeElement);
        if (idx < 0) return;
        e.preventDefault();
        const next = e.key === 'Home' ? 0
          : e.key === 'End' ? items.length - 1
          : (idx + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items[next].focus?.();
        items[next].click?.();
      });
    }
  }

  function updateMemoryTabLabel(labelText, pendingCount) {
    const clean = stripLeadingEmoji(labelText || 'Μνήμη');
    const finalText = pendingCount ? `${clean} · ${pendingCount}` : clean;
    const span = byId('studio-label-memory');
    const btn = byId('tab-memory');
    if (span) {
      span.textContent = finalText;
      if (btn && typeof btn.setAttribute === 'function') {
        btn.setAttribute('title', finalText);
        btn.setAttribute('aria-label', finalText);
      }
    } else if (btn) {
      btn.textContent = pendingCount ? `${labelText} · ${pendingCount}` : labelText;
    }
  }

  function refreshNavTooltips() {
    for (const key of NAV_ORDER) {
      const lbl = byId('studio-label-' + key);
      const btn = byId('tab-' + key);
      if (!lbl || !btn) continue;
      const clean = stripLeadingEmoji(lbl.textContent || '');
      if (clean && clean !== lbl.textContent) lbl.textContent = clean;
      if (typeof btn.setAttribute === 'function') {
        btn.setAttribute('title', clean);
        btn.setAttribute('aria-label', clean);
      }
    }
  }

  function applySidebarCollapsed(collapsed) {
    sidebarCollapsed = collapsed === true;
    document.body?.classList?.toggle('sidebar-collapsed', sidebarCollapsed);
    const btn = byId('sidebar-toggle');
    if (btn) {
      btn.textContent = sidebarCollapsed ? '☰' : '⇤';
      if (typeof btn.setAttribute === 'function') {
        btn.setAttribute('aria-expanded', String(!sidebarCollapsed));
        btn.setAttribute('title', sidebarCollapsed ? 'Επέκταση πλαϊνού μενού (Ctrl+\\)' : 'Σύμπτυξη πλαϊνού μενού (Ctrl+\\)');
      }
    }
    const chk = byId('settings-sidebar-collapsed');
    if (chk) chk.checked = sidebarCollapsed;
  }

  async function toggleSidebar(explicit) {
    const next = typeof explicit === 'boolean' ? explicit : !sidebarCollapsed;
    applySidebarCollapsed(next);
    if (typeof root.api === 'function') {
      try {
        await root.api('/api/preferences', { sidebar_collapsed: next });
      } catch (_) {}
    }
  }

  function selectSettingsPane(which) {
    const target = SETTINGS_PANES.includes(which) ? which : 'general';
    activeSettingsPane = target;
    for (const key of SETTINGS_PANES) {
      const pane = byId('set-pane-' + key);
      const nav = byId('set-nav-' + key);
      if (pane) pane.style.display = key === target ? '' : 'none';
      if (nav) {
        nav.classList?.toggle('active', key === target);
        if (typeof nav.setAttribute === 'function') nav.setAttribute('aria-selected', String(key === target));
      }
    }
    if (target === 'budget') loadUsageInsights().catch(() => {});
  }

  async function loadUsageInsights() {
    const box = byId('usage-insights-box');
    if (!box || typeof root.api !== 'function') return;
    const r = await root.api('/api/usage/insights', {});
    if (r.error) { box.textContent = r.error; return; }
    const fmtTok = (n) => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n || 0);
    const totalLine = r.has_prices
      ? `Μήνας ${r.month} · Σύνολο: $${Number(r.total_cost || 0).toFixed(4)} (${r.calls} κλήσεις)`
      : `Μήνας ${r.month} · ${fmtTok((r.prompt_tokens || 0) + (r.completion_tokens || 0))} tokens (${r.calls} κλήσεις · χωρίς ορισμένες τιμές)`;
    const lines = [totalLine];
    if (r.sparkline) lines.push(`Ανά μέρα:    ${r.sparkline}`);
    if (r.cache_hit_pct > 0 || r.cache_saved > 0) {
      const saveTxt = r.cache_saved > 0 ? ` (εξοικονόμηση ~$${Number(r.cache_saved).toFixed(4)})` : '';
      lines.push(`Cache hit:   ${r.cache_hit_pct}%${saveTxt}`);
    }
    if (r.projected_month_cost) lines.push(`Πρόβλεψη:    ~$${Number(r.projected_month_cost).toFixed(2)} / μήνα`);
    if ((r.by_model || []).length) {
      lines.push('Ανά μοντέλο: ' + r.by_model.slice(0, 4).map((m) =>
        `${m.name} ${m.cost > 0 ? '$' + m.cost.toFixed(4) : fmtTok(m.tokens) + ' tok'}`
      ).join(' · '));
    }
    if ((r.by_project || []).length) {
      lines.push('Ανά project: ' + r.by_project.slice(0, 4).map((p) =>
        `${p.name} ${p.cost > 0 ? '$' + p.cost.toFixed(4) : fmtTok(p.tokens) + ' tok'}`
      ).join(' · '));
    }
    box.textContent = lines.join('\n');
  }

  function syncSettingsControls(s) {
    if (!s || typeof s !== 'object') return;
    const prefs = s.preferences || {};
    if (typeof prefs.sidebar_collapsed === 'boolean' && prefs.sidebar_collapsed !== sidebarCollapsed) {
      applySidebarCollapsed(prefs.sidebar_collapsed);
    }
    const fbp = byId('fallback-provider');
    const fbm = byId('fallback-model');
    if (fbp && document.activeElement !== fbp && typeof s.fallback_provider === 'string') fbp.value = s.fallback_provider;
    if (fbm && document.activeElement !== fbm && typeof s.fallback_model === 'string') fbm.value = s.fallback_model;
    const themeSel = byId('settings-ui-theme');
    if (themeSel && prefs.ui_theme && document.activeElement !== themeSel) themeSel.value = prefs.ui_theme;
    const fontRange = byId('settings-font-size');
    const fontVal = byId('font-size-val');
    const fontLbl = byId('settings-font-val');
    const sz = Number(prefs.ui_font_size || root.currentFontSize || 14);
    if (fontRange && document.activeElement !== fontRange) fontRange.value = String(sz);
    if (fontVal) fontVal.textContent = sz + 'px';
    if (fontLbl) fontLbl.textContent = sz + 'px';
  }

  function searchSessionsFullText(query) {
    clearTimeout(searchTimer);
    const sessionsBox = byId('sessions');
    if (!sessionsBox || !sessionsBox.parentElement) return;
    let hitsBox = byId('sessions-search-hits');
    const q = String(query || '').trim();
    if (q.length < 2) {
      if (hitsBox) { hitsBox.replaceChildren(); hitsBox.style.display = 'none'; }
      return;
    }
    if (!hitsBox) {
      hitsBox = document.createElement('div');
      hitsBox.id = 'sessions-search-hits';
      hitsBox.style.cssText = 'display:none;flex-direction:column;gap:4px;border-top:1px solid var(--border);padding-top:6px;margin-top:4px';
      sessionsBox.parentElement.appendChild(hitsBox);
    }
    searchTimer = setTimeout(async () => {
      if (typeof root.api !== 'function') return;
      try {
        const r = await root.api('/api/sessions/search', { query: q, limit: 10 });
        hitsBox.replaceChildren();
        const rows = (r && r.results) || [];
        if (!rows.length) { hitsBox.style.display = 'none'; return; }
        const hdr = document.createElement('div');
        hdr.className = 'hint';
        hdr.textContent = `Μέσα σε κείμενα (${rows.length}):`;
        hitsBox.appendChild(hdr);
        for (const item of rows) {
          const btn = document.createElement('button');
          btn.className = 'btn';
          btn.style.cssText = 'text-align:left;padding:6px 8px;font-size:11.5px;display:flex;flex-direction:column;gap:2px';
          const title = document.createElement('strong');
          title.textContent = (item.pinned ? '📌 ' : '') + (item.title || item.id);
          const snip = document.createElement('span');
          snip.className = 'hint';
          snip.textContent = item.snippet || '';
          btn.append(title, snip);
          btn.onclick = async () => {
            const st = await root.api('/api/session/open', { id: item.id, project: item.project });
            if (st.error) { root.showNotice?.('err', st.error); return; }
            if (typeof root.clearStream === 'function') root.clearStream();
            if (typeof root.clearFileView === 'function') root.clearFileView();
            const chatEl = byId('chat');
            if (chatEl) chatEl.innerHTML = '';
            root.renderTranscript?.(st.transcript || []);
            root.updateBadges?.(st);
            root.loadSessions?.();
          };
          hitsBox.appendChild(btn);
        }
        hitsBox.style.display = 'flex';
      } catch (_) {}
    }, 220);
  }

  function bindGlobalShortcuts() {
    if (typeof document.addEventListener !== 'function' || document._studioShortcutBound) return;
    document._studioShortcutBound = true;
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key === '\\') {
        e.preventDefault();
        toggleSidebar();
      }
    });
  }

  function boot() {
    initNavIcons();
    selectSettingsPane(activeSettingsPane);
    bindGlobalShortcuts();
  }

  root.StudioUI = {
    boot,
    initNavIcons,
    stripLeadingEmoji,
    updateMemoryTabLabel,
    refreshNavTooltips,
    applySidebarCollapsed,
    toggleSidebar,
    selectSettingsPane,
    loadUsageInsights,
    syncSettingsControls,
    searchSessionsFullText,
    isSidebarCollapsed: () => sidebarCollapsed,
    activeSettingsPane: () => activeSettingsPane,
  };
  root.toggleSidebar = toggleSidebar;
  root.selectSettingsPane = selectSettingsPane;

  if (typeof document.addEventListener === 'function') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  }
})(globalThis);
