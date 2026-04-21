/**
 * FinAnalytics AI — sidebar shared loader.
 *
 * Como usar:
 *   <script src="/static/sidebar.js"></script>
 *
 * O que faz:
 *   1. Faz fetch de /static/sidebar.html (browser cache reaproveita).
 *   2. Encontra o primeiro `.fa-sidebar` no DOM e substitui o conteudo
 *      pelo carregado — paginas existentes nao precisam refator alem
 *      do <script> tag.
 *   3. Marca como `.active` o link cujo `href` bate com `location.pathname`
 *      (preferindo match exato, fallback para prefixo).
 *   4. Restaura o estado open/collapsed do localStorage `fa_sidebar_open`.
 *   5. Expoe `window.faSidebar.toggle()` para o botao "Menu".
 *
 * Convivencia com o JS antigo das paginas:
 *   - Paginas antigas tinham `function toggleSidebar()` proprio. Mantemos
 *     isso funcional via wrapper `window.toggleSidebar = window.faSidebar.toggle`
 *     se nao existir ainda.
 */
(function () {
  'use strict';

  var SIDEBAR_URL = '/static/sidebar.html';
  var STORAGE_KEY = 'fa_sidebar_open';
  var STYLE_ID = 'fa-sidebar-injected-styles';

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    // Estilos compartilhados injetados em todas as 39 paginas:
    //   - Sprint UI C: section headers (.fa-sb-section)
    //   - Sprint UI E: mobile responsive (sidebar overlay <768px,
    //     topbar compacto, modal fullscreen, tabela scroll)
    s.textContent = [
      // Section headers (Sprint UI C)
      '.fa-sb-section{font-size:10px;font-weight:700;color:#3a5570;text-transform:uppercase;letter-spacing:.12em;padding:14px 14px 4px;white-space:nowrap;overflow:hidden;opacity:0;transition:opacity .15s}',
      '.fa-sidebar.open .fa-sb-section{opacity:1}',
      '.fa-sb-section:first-of-type{padding-top:10px}',
      // Backdrop overlay (Sprint UI E mobile)
      '.fa-sb-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:150;display:none;animation:fa-fade-in .2s ease-out}',
      '@keyframes fa-fade-in{from{opacity:0}to{opacity:1}}',
      '@media (max-width:768px){',
        '.fa-sidebar{width:0 !important;transition:width .25s ease-out}',
        '.fa-sidebar.open{width:240px !important;box-shadow:6px 0 24px rgba(0,0,0,.6)}',
        '.fa-sidebar.open + .fa-sb-backdrop, body.sb-open .fa-sb-backdrop{display:block}',
        '.fa-sidebar.open .fa-sb-link span,.fa-sidebar.open .fa-sb-section{opacity:1}',
        '.fa-page-content{margin-left:0 !important}',
        'body.sb-open .fa-page-content{margin-left:0 !important}',
        // Topbar compacto: esconde email, encolhe logo
        '.fa-topbar{padding:0 10px !important;gap:8px !important}',
        '.fa-topbar .fa-user-chip span,.fa-topbar #fa-username,.fa-topbar #topbarEmail{display:none !important}',
        '.fa-logo-text{font-size:14px !important}',
        '.fa-logout-btn{padding:3px 8px !important;font-size:13px !important}',
        '.fa-logout-btn span,.fa-logout-btn{gap:4px}',
        // Modais fullscreen
        '.modal-overlay{padding:0 !important}',
        '.modal{width:100% !important;max-width:100% !important;height:100vh;max-height:100vh !important;border-radius:0 !important;border:none !important;overflow-y:auto}',
        // Tabelas com scroll horizontal
        '.tbl-wrap,.metrics-table{overflow-x:auto;-webkit-overflow-scrolling:touch}',
        // Main padding reduzido
        '.main{padding:14px !important}',
        '.page-title{font-size:22px !important}',
        // Notificacoes panel compacto
        '.fa-notif-panel{width:calc(100vw - 20px) !important;right:-10px !important}',
      '}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function ensureBackdrop() {
    if (document.querySelector('.fa-sb-backdrop')) return;
    var bd = document.createElement('div');
    bd.className = 'fa-sb-backdrop';
    bd.onclick = function () {
      document.querySelector('.fa-sidebar').classList.remove('open');
      document.body.classList.remove('sb-open');
    };
    document.body.appendChild(bd);
  }

  function applyToggleState() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) return;
    var open = localStorage.getItem(STORAGE_KEY) === '1';
    if (open) {
      sb.classList.add('open');
      document.body.classList.add('sb-open');
    } else {
      sb.classList.remove('open');
      document.body.classList.remove('sb-open');
    }
  }

  function toggle() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) return;
    var open = sb.classList.toggle('open');
    document.body.classList.toggle('sb-open', open);
    localStorage.setItem(STORAGE_KEY, open ? '1' : '0');
  }

  function markActive() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) return;
    var path = window.location.pathname.replace(/\/+$/, '') || '/';
    var links = sb.querySelectorAll('a.fa-sb-link');
    var bestMatch = null;
    var bestScore = -1;
    links.forEach(function (a) {
      a.classList.remove('active');
      var href = (a.getAttribute('href') || '').replace(/\/+$/, '') || '/';
      var score = -1;
      if (href === path) {
        score = 1000;  // match exato — vence sempre
      } else if (path.indexOf(href + '/') === 0 && href !== '/') {
        score = href.length;  // prefixo: o mais especifico vence
      }
      if (score > bestScore) {
        bestScore = score;
        bestMatch = a;
      }
    });
    if (bestMatch) bestMatch.classList.add('active');
  }

  async function inject() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) {
      console.warn('[sidebar.js] Nenhum elemento .fa-sidebar encontrado nesta pagina.');
      return;
    }
    try {
      ensureStyles();
      ensureBackdrop();
      var r = await fetch(SIDEBAR_URL, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      sb.innerHTML = await r.text();
      markActive();
      applyToggleState();
    } catch (e) {
      console.error('[sidebar.js] Falha ao carregar sidebar:', e);
    }
  }

  window.faSidebar = { toggle: toggle, reload: inject, markActive: markActive };

  // Aliases para compatibilidade com paginas antigas (toggleSidebar inline).
  // Definimos APOS load para nao sobrescrever se a pagina ja tiver versao propria
  // que conflite com nosso comportamento.
  if (typeof window.toggleSidebar === 'undefined') {
    window.toggleSidebar = toggle;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
