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
    syncSettingsControls,
    isSidebarCollapsed: () => sidebarCollapsed,
    activeSettingsPane: () => activeSettingsPane,
  };
  root.toggleSidebar = toggleSidebar;
  root.selectSettingsPane = selectSettingsPane;

  if (typeof document.addEventListener === 'function') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  }
})(globalThis);
