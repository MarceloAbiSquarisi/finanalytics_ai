/**
 * FinAnalytics AI — accessibility helpers (Sprint UI E).
 *
 * Carregar via:  <script src="/static/a11y.js"></script>
 *
 * O que faz (todos auto-init):
 *   1. Injeta link "Pular para conteudo" no inicio do body (visivel ao Tab).
 *   2. Auto-marca <main> ou .main com role="main" e id="main-content".
 *   3. Adiciona aria-label em botoes-icone vazios (apenas SVG).
 *   4. Aplica focus visible global (3px outline azul) com :focus-visible.
 *   5. Garante <html lang="pt-BR"> se ausente.
 *
 * API:
 *   FAA11y.trapFocus(containerEl) -> () => release
 *     Mantem foco dentro de container (modal). Tab cicla; Shift+Tab inverte.
 */
(function (global) {
  'use strict';

  function ensureStyles() {
    if (document.getElementById('fa-a11y-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-a11y-styles';
    s.textContent = [
      '.fa-skip-link{position:absolute;top:-40px;left:8px;background:#00d4ff;color:#0a1628;padding:8px 14px;font-weight:700;border-radius:0 0 6px 6px;text-decoration:none;z-index:99999;transition:top .1s}',
      '.fa-skip-link:focus{top:0;outline:none}',
      '*:focus-visible{outline:2px solid #00d4ff !important;outline-offset:2px !important;border-radius:3px}',
      'button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid #00d4ff !important;outline-offset:2px !important}',
      '@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important;scroll-behavior:auto !important}}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function ensureLang() {
    var html = document.documentElement;
    if (!html.lang) html.lang = 'pt-BR';
  }

  function injectSkipLink() {
    if (document.querySelector('.fa-skip-link')) return;
    var main = document.querySelector('main, .main, #main-content');
    if (!main) return;
    if (!main.id) main.id = 'main-content';
    if (!main.hasAttribute('role')) main.setAttribute('role', 'main');
    if (!main.hasAttribute('tabindex')) main.setAttribute('tabindex', '-1');
    var link = document.createElement('a');
    link.className = 'fa-skip-link';
    link.href = '#' + main.id;
    link.textContent = 'Pular para o conteúdo';
    document.body.insertBefore(link, document.body.firstChild);
  }

  function labelIconButtons() {
    // Heuristica: botoes sem texto visivel (apenas SVG) recebem aria-label
    // baseado em title, dataset.label, ou onclick handler name.
    var btns = document.querySelectorAll('button:not([aria-label]):not([aria-labelledby])');
    Array.prototype.forEach.call(btns, function (b) {
      var txt = (b.textContent || '').trim();
      if (txt) return; // tem texto visivel
      var label = b.title || b.dataset.label || '';
      if (!label) {
        var oc = b.getAttribute('onclick') || '';
        var m = oc.match(/^([\w$.]+)\s*\(/);
        if (m) label = m[1].replace(/^\w/, function(c){return c.toUpperCase();});
      }
      if (label) b.setAttribute('aria-label', label);
    });
  }

  var FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

  function trapFocus(container) {
    if (!container) return function () {};
    var prev = document.activeElement;
    function onKey(e) {
      if (e.key !== 'Tab') return;
      var nodes = container.querySelectorAll(FOCUSABLE);
      if (!nodes.length) return;
      var first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    }
    document.addEventListener('keydown', onKey);
    var first = container.querySelector(FOCUSABLE);
    if (first) first.focus();
    return function release() {
      document.removeEventListener('keydown', onKey);
      if (prev && prev.focus) prev.focus();
    };
  }

  function init() {
    ensureLang();
    ensureStyles();
    injectSkipLink();
    labelIconButtons();
  }

  global.FAA11y = { init: init, trapFocus: trapFocus, labelIconButtons: labelIconButtons };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);
