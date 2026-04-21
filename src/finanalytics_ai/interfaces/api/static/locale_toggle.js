/**
 * FinAnalytics AI — locale switcher PT/EN (Sprint UI S).
 *
 * Carregar via:  <script src="/static/locale_toggle.js"></script>
 *
 * Depende de FAI18n (i18n.js). Insere botao "PT/EN" na topbar
 * (.fa-user-chip antes do logout, depois do theme toggle se presente).
 *
 * Click cicla entre PT e EN. Comando "Mudar idioma" registrado
 * automaticamente no FAPalette.
 */
(function (global) {
  'use strict';

  function _ensureStyles() {
    if (document.getElementById('fa-locale-btn-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-locale-btn-styles';
    s.textContent = [
      '.fa-locale-btn{background:transparent;border:1px solid var(--border);border-radius:5px;color:var(--muted);font-size:13px;font-weight:700;letter-spacing:.05em;padding:4px 8px;cursor:pointer;font-family:inherit;text-transform:uppercase;transition:.15s}',
      '.fa-locale-btn:hover{color:var(--accent);border-color:var(--accent)}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _refreshIcon() {
    var btn = document.getElementById('fa-locale-btn');
    if (!btn || !global.FAI18n) return;
    btn.textContent = global.FAI18n.getLocale() === 'pt' ? 'PT' : 'EN';
    btn.title = global.FAI18n.getLocale() === 'pt' ? 'Switch to English' : 'Mudar para português';
    btn.setAttribute('aria-label', btn.title);
  }

  function toggle() {
    if (!global.FAI18n) return;
    var next = global.FAI18n.getLocale() === 'pt' ? 'en' : 'pt';
    var p = global.FAI18n.setLocale(next);
    if (p && p.then) p.then(_refreshIcon); else _refreshIcon();
  }

  function injectButton(target) {
    _ensureStyles();
    if (document.getElementById('fa-locale-btn')) return;
    var btn = document.createElement('button');
    btn.id = 'fa-locale-btn';
    btn.className = 'fa-locale-btn';
    btn.type = 'button';
    btn.addEventListener('click', toggle);
    target = target || document.querySelector('.fa-user-chip') || document.querySelector('.fa-topbar');
    if (!target) return;
    var theme = target.querySelector && target.querySelector('#fa-theme-btn');
    var logout = target.querySelector && target.querySelector('.fa-logout-btn');
    var ref = theme || logout;
    if (ref) target.insertBefore(btn, ref);
    else target.appendChild(btn);
    _refreshIcon();
  }

  function _registerExtras() {
    if (global.FAPalette && global.FAPalette.register) {
      global.FAPalette.register({
        label: 'Mudar idioma (PT/EN)',
        section: 'Acoes',
        onClick: toggle,
      });
    }
  }

  function init() {
    var tries = 0;
    function tryInject() {
      var anchor = document.querySelector('.fa-user-chip') || document.querySelector('.fa-topbar');
      if (anchor && global.FAI18n) { injectButton(anchor); return; }
      if (++tries < 30) setTimeout(tryInject, 100);
    }
    tryInject();
    _registerExtras();
  }

  global.FALocale = { toggle: toggle, injectButton: injectButton };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);
