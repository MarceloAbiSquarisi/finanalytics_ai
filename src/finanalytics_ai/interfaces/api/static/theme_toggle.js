/**
 * FinAnalytics AI — theme toggle (Sprint UI O).
 *
 * Carregar o INLINE snippet abaixo no <head> ANTES do theme.css
 * para evitar flash (FOUC):
 *
 *   <script>(function(){try{var t=localStorage.getItem('fa_theme');
 *     if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;}catch(e){}})();</script>
 *
 * Carregar este arquivo via:
 *   <script src="/static/theme_toggle.js"></script>
 *
 * API:
 *   FATheme.get()            -> 'light' | 'dark'
 *   FATheme.set('light'|'dark')
 *   FATheme.toggle()         -> alterna
 *   FATheme.injectButton(el) -> insere botao no container (fallback se topbar nao tem)
 *
 * O botao tambem registra:
 *   - Cmd/Ctrl + Shift + L: toggle (via shortcuts.js se presente)
 *   - Comando "Alternar tema" no FAPalette
 */
(function (global) {
  'use strict';

  var LS_KEY = 'fa_theme';
  var DARK_THEME_COLOR = '#080b10';
  var LIGHT_THEME_COLOR = '#f5f7fa';

  function _apply(theme) {
    document.documentElement.dataset.theme = theme;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'light' ? LIGHT_THEME_COLOR : DARK_THEME_COLOR);
  }

  function get() {
    return document.documentElement.dataset.theme || 'dark';
  }

  function set(theme) {
    if (theme !== 'light' && theme !== 'dark') return;
    _apply(theme);
    try { localStorage.setItem(LS_KEY, theme); } catch (_) {}
    _refreshIcon();
    if (global.FAToast && global.FAToast.info) {
      global.FAToast.info(theme === 'light' ? 'Tema claro ativo' : 'Tema escuro ativo');
    }
  }

  function toggle() {
    set(get() === 'dark' ? 'light' : 'dark');
  }

  function _icon(theme) {
    if (theme === 'light') {
      // Sun
      return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>';
    }
    // Moon
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
  }

  function _refreshIcon() {
    var btn = document.getElementById('fa-theme-btn');
    if (!btn) return;
    var theme = get();
    btn.innerHTML = _icon(theme);
    btn.title = theme === 'light' ? 'Mudar para escuro (Cmd+Shift+L)' : 'Mudar para claro (Cmd+Shift+L)';
    btn.setAttribute('aria-label', btn.title);
  }

  function _ensureStyles() {
    if (document.getElementById('fa-theme-btn-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-theme-btn-styles';
    s.textContent = [
      '.fa-theme-btn{background:transparent;border:1px solid var(--border);border-radius:5px;color:var(--muted);padding:4px 8px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:.15s;font-family:inherit}',
      '.fa-theme-btn:hover{color:var(--gold);border-color:var(--gold)}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function injectButton(target) {
    _ensureStyles();
    if (document.getElementById('fa-theme-btn')) return;
    var btn = document.createElement('button');
    btn.id = 'fa-theme-btn';
    btn.className = 'fa-theme-btn';
    btn.type = 'button';
    btn.addEventListener('click', toggle);
    target = target || document.querySelector('.fa-user-chip') || document.querySelector('.fa-topbar') || document.body;
    // Inserir antes do logout, se existir
    var logout = target.querySelector && target.querySelector('.fa-logout-btn');
    if (logout) {
      target.insertBefore(btn, logout);
    } else {
      target.appendChild(btn);
    }
    _refreshIcon();
  }

  function _registerExtras() {
    // Comando no FAPalette
    if (global.FAPalette && global.FAPalette.register) {
      global.FAPalette.register({
        label: 'Alternar tema (claro/escuro)',
        section: 'Acoes',
        onClick: toggle,
      });
    }
    // Atalho Cmd/Ctrl + Shift + L
    document.addEventListener('keydown', function (e) {
      var mod = e.metaKey || e.ctrlKey;
      if (mod && e.shiftKey && (e.key === 'L' || e.key === 'l')) {
        e.preventDefault();
        toggle();
      }
    });
  }

  function init() {
    // Aplica salvo (caso o snippet inline no <head> nao tenha rodado)
    try {
      var saved = localStorage.getItem(LS_KEY);
      if (saved === 'light' || saved === 'dark') _apply(saved);
    } catch (_) {}
    // Tenta injetar botao na topbar
    var tries = 0;
    function tryInject() {
      var anchor = document.querySelector('.fa-user-chip') || document.querySelector('.fa-topbar');
      if (anchor) { injectButton(anchor); return; }
      if (++tries < 20) setTimeout(tryInject, 100);
    }
    tryInject();
    _registerExtras();
  }

  global.FATheme = {
    get: get,
    set: set,
    toggle: toggle,
    injectButton: injectButton,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);
